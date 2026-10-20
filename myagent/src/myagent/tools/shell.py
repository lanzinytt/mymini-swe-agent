"""shell 工具：PowerShell / bash 双后端，进程树 kill，强制 UTF-8。

Windows 上有两个真实的坑，都在这里处理：

1. **CLIXML 污染**：PowerShell 5.1 在 stdout 被管道捕获时，会把 progress 流
   序列化成 XML（``<Objs Version="1.1.0.1" ...>``）。模型收到这坨东西会误以为
   命令输出就是它，所以统一前置 ``$ProgressPreference='SilentlyContinue';``。
2. **控制台 GBK**：``subprocess`` 拿到的解码默认跟随控制台代码页。显式
   ``encoding="utf-8"`` + ``PYTHONIOENCODING`` 才能稳定拿到中文。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from myagent.tools.base import BaseTool, ToolContext, ToolResult, truncate


def _kill_tree(proc: subprocess.Popen) -> None:
    """超时后必须连子孙进程一起杀。

    ``subprocess.run(timeout=...)`` 只杀直接子进程，模型起一个
    ``python -c "import time; time.sleep(999)"`` 的子进程仍会活着并占住管道。
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdin_text: str | None = None,
) -> tuple[int, str, bool]:
    """执行命令 → (returncode, 合并后的输出, 是否超时)。stdout/stderr 合并。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=(os.name != "nt"),
    )
    try:
        out, _ = proc.communicate(input=stdin_text, timeout=timeout)
        return proc.returncode, out or "", False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况
            out = ""
        return 124, (out or "") + f"\n[timeout after {timeout}s: process tree killed]", True


class ShellTool(BaseTool):
    name = "run_shell"
    description = (
        "Run a shell command in the workspace. Use it for git, pip, and system tools. "
        "For Python code prefer run_python. Output is merged stdout+stderr and truncated."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command line to execute."},
        },
        "required": ["command"],
    }

    def __init__(self, limit: int = 8_000):
        self.limit = limit

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(ok=False, error="`command` must be a non-empty string")
        if os.name == "nt":
            exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            argv = [
                exe,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                # 关掉 progress 流，否则 stdout 会被 CLIXML 包起来
                "$ProgressPreference='SilentlyContinue';"
                "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                + command,
            ]
        else:
            argv = ["bash", "-lc", command]

        rc, out, timed_out = run_process(argv, cwd=ctx.workspace, timeout=ctx.timeout)
        text, truncated = truncate(out, self.limit)
        return ToolResult(
            ok=(rc == 0 and not timed_out),
            output=text,
            error="timeout" if timed_out else ("" if rc == 0 else f"exit {rc}"),
            returncode=rc,
            truncated=truncated,
            metadata={"limit": self.limit, "argv0": Path(argv[0]).name},
        )


class PythonTool(BaseTool):
    """``run_python``：代码与文件路径二选一，永远在 workspace 里跑。

    五个边界（对应审阅稿第 2 节）：
    1. ``code`` 与 ``path`` 恰好给一个；
    2. ``code`` 落到 ``.myagent/tmp/run_<id>.py``，不污染用户目录；
    3. ``path`` 不许逃出 workspace；
    4. 用 ``ctx`` 里的解释器 + ``-X utf8``，``cwd=workspace``；
    5. 超时杀整个进程树，stdout+stderr 合并，非零退出本身就是 observation。
    """

    name = "run_python"
    description = (
        "Run Python code or a Python file inside the workspace and return merged stdout/stderr. "
        "Provide exactly one of `code` or `path`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute."},
            "path": {"type": "string", "description": "Path to a .py file inside the workspace."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional command-line arguments for `path`.",
            },
        },
        "required": [],
    }

    def __init__(self, limit: int = 8_000):
        self.limit = limit

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        code = args.get("code")
        path = args.get("path")
        if bool(code) == bool(path):
            return ToolResult(
                ok=False,
                error="provide exactly one of `code` or `path`",
            )
        argv_tail = [str(a) for a in (args.get("args") or [])]

        if code:
            tmp_dir = ctx.workspace / ".myagent" / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            script = tmp_dir / f"run_{os.getpid()}_{abs(hash(code)) % 10**8}.py"
            script.write_text(str(code), encoding="utf-8")
            target = script
        else:
            try:
                target = ctx.resolve(str(path))
            except ValueError as e:
                return ToolResult(ok=False, error=str(e))
            if not target.is_file():
                return ToolResult(ok=False, error=f"no such file: {ctx.relative(target)}")
            if target.suffix != ".py":
                return ToolResult(ok=False, error=f"not a .py file: {ctx.relative(target)}")

        argv = [ctx.python_exe, "-X", "utf8", str(target), *argv_tail]
        rc, out, timed_out = run_process(argv, cwd=ctx.workspace, timeout=ctx.timeout)
        text, truncated = truncate(out, self.limit)
        return ToolResult(
            ok=(rc == 0 and not timed_out),
            output=text,
            error="timeout" if timed_out else ("" if rc == 0 else f"exit {rc}"),
            returncode=rc,
            truncated=truncated,
            metadata={"limit": self.limit, "script": ctx.relative(target)},
        )
