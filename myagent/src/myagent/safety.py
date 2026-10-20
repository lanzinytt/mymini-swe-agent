"""安全闸门：在工具入口拦截不可逆操作。

设计上只有一条原则：**拦在入口，不拦在提示词**。提示词能劝住模型，
但它不是确定性的；闸门是代码，每次都执行。

两类规则：

1. **命令模式**：递归删除、格式化磁盘、关机、强推、远程脚本管道。
   这些命令的共同点是"一次就够毁掉工作区或机器"。
2. **路径越界**：绝对路径落在 workspace 之外。工具层本来就会拒绝，
   但闸门在更前面拦住，能让模型更早得到"不要试了"的信号。

宁可误杀吗？不。误杀会让 agent 绕路浪费时间，所以规则写得窄：
只匹配真实的破坏动作，不匹配"提到这个词"。误杀率在
``docs/evidence/safety.md`` 里用真实轨迹统计过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# (编译后的正则, 人类可读的原因)。顺序无关，命中即拦。
COMMAND_RULES: list[tuple[re.Pattern[str], str]] = [
    # 递归删除：rm -rf / rd /s / rmdir /s / Remove-Item -Recurse
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+)+(\S+)"), "recursive delete (rm -r)"),
    (re.compile(r"\b(rd|rmdir)\s+/s\b", re.I), "recursive delete (rd /s)"),
    (re.compile(r"\bRemove-Item\b[^\n]*-Recurse\b", re.I), "recursive delete (Remove-Item -Recurse)"),
    # 格式化 / 分区
    (re.compile(r"\bformat\s+[a-zA-Z]:", re.I), "disk format"),
    (re.compile(r"\bdiskpart\b", re.I), "disk partitioning"),
    (re.compile(r"\bmkfs\.", re.I), "filesystem format"),
    (re.compile(r"\bdd\s+[^\n]*\bof=/dev/", re.I), "raw device write"),
    # 关机 / 重启
    (re.compile(r"\bshutdown\b", re.I), "shutdown"),
    (re.compile(r"\b(reboot|halt|poweroff)\b", re.I), "shutdown"),
    # git 破坏性操作
    (re.compile(r"\bgit\s+push\b[^\n]*\s(--force|-f)\b"), "force push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "git clean -f"),
    # 远程脚本直接进 shell
    (re.compile(r"\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba)?sh\b", re.I), "piped remote script"),
    (re.compile(r"\bInvoke-WebRequest\b[^\n]*\|\s*Invoke-Expression", re.I), "piped remote script"),
    (re.compile(r"\biwr\b[^\n]*\|\s*iex\b", re.I), "piped remote script"),
    (re.compile(r"https?://[^\s\"']+\s*\|\s*(sudo\s+)?(ba)?sh\b", re.I), "piped remote script"),
]

# 只在 shell 类工具上做命令检查；文件工具的路径由 resolve() 负责
SHELL_TOOLS = {"run_shell", "run_python"}


@dataclass
class Blocked:
    reason: str


def _strip_string_literals(text: str) -> str:
    """去掉字符串字面量，只留"真正会被执行"的代码骨架。

    这样 ``print("rm -rf is dangerous")`` 不会被当成删除命令。
    """
    return re.sub(r"""(['"])(?:\\.|(?!\1).)*\1""", '""', text, flags=re.S)


# 这些调用会把字符串参数当成命令执行。字面量出现在它们的实参里时不能放行。
EXEC_SINKS = re.compile(
    r"\b(os\.system|os\.popen|subprocess\.\w+|eval|exec|compile|__import__)\s*\(",
    re.I,
)


class SafetyGate:
    def __init__(self, *, block_outside_workspace: bool = True):
        self.block_outside_workspace = block_outside_workspace
        self.blocked: list[dict] = []

    def check(self, tool: str, args: dict, workspace: Path) -> str:
        """返回拦截原因；空字符串表示放行。"""
        if tool in SHELL_TOOLS:
            reason = self._check_command(args)
            if reason:
                self.blocked.append({"tool": tool, "reason": reason})
                return reason
        if self.block_outside_workspace:
            reason = self._check_paths(args, workspace)
            if reason:
                self.blocked.append({"tool": tool, "reason": reason})
                return reason
        return ""

    # ------------------------------------------------------------------ 命令
    def _check_command(self, args: dict) -> str:
        # run_python 的 `code` 也是命令：写 os.system("rm -rf /") 一样危险
        raw = " ".join(str(args.get(k) or "") for k in ("command", "code", "path") if args.get(k))

        # 1) 先按"代码骨架"检查（去掉字符串字面量），避免 print("rm -rf ...") 误杀
        skeleton = _strip_string_literals(raw)
        for pattern, reason in COMMAND_RULES:
            if pattern.search(skeleton):
                return f"{reason} (matched {pattern.pattern[:40]})"

        # 2) 但如果字符串字面量落在 os.system / subprocess 之类的执行点里，
        #    那它就是会被执行的命令，必须按原文检查。
        if EXEC_SINKS.search(skeleton):
            for pattern, reason in COMMAND_RULES:
                if pattern.search(raw):
                    return f"{reason} inside an exec sink (matched {pattern.pattern[:40]})"
        return ""

    # ------------------------------------------------------------------ 路径
    def _check_paths(self, args: dict, workspace: Path) -> str:
        root = Path(workspace).resolve()
        for key in ("path", "cwd", "file"):
            raw = args.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            p = Path(raw)
            if not p.is_absolute():
                continue
            try:
                target = p.resolve()
            except OSError:
                continue
            if target != root and root not in target.parents:
                return f"absolute path outside workspace: {raw}"
        return ""
