"""命令行入口：doctor / run / trace / sessions。

每个子命令都对应实验里的一次"留证据"动作：
``doctor`` 是环境证据，``run`` 是任务证据，``trace`` 让报告能引用行号。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from myagent.agent import Agent
from myagent.config import Config
from myagent.environment import detect, which_python_alias
from myagent.events import EventLogger, list_sessions, new_session_path, read_events
from myagent.model import FakeModel, OpenAICompatibleModel
from myagent.prompts import build_instance_prompt, build_system_prompt
from myagent.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.shell import PythonTool, ShellTool
from myagent.tools.submit import SubmitTool

app = typer.Typer(add_completion=False, help="myagent - minimal agent harness")
console = Console()


def _safe_console() -> None:
    """Windows 控制台默认 GBK。

    这里不能把 stdout 改成 utf-8：PowerShell 5.1 仍按 GBK 解码管道，中文会变成
    乱码。正确做法是保留原编码，只把不可编码字符降级，避免 UnicodeEncodeError
    把整个命令打断。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _find_env_file(explicit: Path | None) -> Path | None:
    """依次找 ./.env，再找包所在目录的 .env（src 布局的上一层）。"""
    candidates = [explicit] if explicit else [Path.cwd() / ".env"]
    candidates.append(Path(__file__).resolve().parents[2] / ".env")
    for c in candidates:
        if c is not None and c.is_file():
            return c
    return None


def _load_config(env_file: Path | None) -> Config:
    return Config.from_env(env_file)


def _build_tools(gate=None) -> ToolRegistry:
    return ToolRegistry(
        [
            PythonTool(),
            ShellTool(),
            ReadFileTool(),
            WriteFileTool(),
            ListDirTool(),
            SubmitTool(),
        ],
        gate=gate,
    )


# ---------------------------------------------------------------------- doctor
@app.command()
def doctor(
    probe: bool = typer.Option(False, "--probe", help="真的发一次模型请求，测 tool calling。"),
    report: bool = typer.Option(False, "--report", help="输出 JSON，便于贴进报告。"),
    env_file: Path = typer.Option(None, "--env-file"),
) -> None:
    """自检：解释器、shell、workspace、模型端点、tool calling 支持。"""
    _safe_console()
    env = detect()
    cfg = _load_config(_find_env_file(env_file))
    info: dict = {"environment": env.as_dict(), "config": cfg.redacted()}

    alias = which_python_alias()
    info["python_on_path"] = alias or "<not found>"
    info["python_on_path_ok"] = bool(alias) and Path(alias).is_file() and Path(alias).stat().st_size > 1024

    problems = cfg.problems()
    info["missing"] = problems

    if probe and not problems:
        model = OpenAICompatibleModel(
            model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key, price=cfg.price
        )
        info["model_probe"] = model.probe()
    elif probe:
        info["model_probe"] = {"ok": False, "error": "missing config: " + ", ".join(problems)}

    if report:
        typer.echo(json.dumps(info, ensure_ascii=False, indent=2))
        return

    panel = [
        f"platform    {env.platform} {env.platform_release} ({env.machine})",
        f"python      {env.python_version}  {env.python_executable}",
        f"python PATH {info['python_on_path']}  {'ok' if info['python_on_path_ok'] else 'SUSPECT'}",
        f"shell       {env.shell} {env.shell_version}",
        f"workspace   {env.cwd}",
        "",
        f"provider    {cfg.provider}",
        f"model       {cfg.model or '<empty>'}",
        f"base_url    {cfg.base_url or '<empty>'}",
        f"api_key     {cfg.redacted()['api_key']}",
    ]
    if "model_probe" in info:
        p = info["model_probe"]
        panel.append("")
        if p.get("ok"):
            panel.append(f"probe       OK  {p['latency_ms']} ms  tools={p['tools']}")
        else:
            panel.append(f"probe       FAIL  {p.get('error')}")
    console.print(Panel("\n".join(panel), title="myagent doctor", expand=False))
    if problems:
        console.print(f"[yellow]missing config: {', '.join(problems)}[/yellow]")
        console.print("[dim]copy .env.example to .env and fill the key[/dim]")


# ------------------------------------------------------------------------- run
@app.command()
def run(
    task: str = typer.Argument(..., help="任务描述。"),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
    deliverable: str = typer.Option("", "--deliverable", help="验收标准，写进任务提示。"),
    max_steps: int = typer.Option(0, "--max-steps", help="0 表示用 .env 里的默认值。"),
    steps: int = typer.Option(0, "--steps", hidden=True),
    env_file: Path = typer.Option(None, "--env-file"),
    fake: bool = typer.Option(False, "--fake", help="用 FakeModel，零成本冒烟。"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """跑一个任务，轨迹写到 <workspace>/.myagent/sessions/。"""
    _safe_console()
    cfg = _load_config(_find_env_file(env_file))
    ws = workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    tools = _build_tools()
    env = detect(ws)
    system = build_system_prompt(env, tools.names())
    instance = build_instance_prompt(
        task, deliverable or "Satisfy the task. Verify before you submit."
    )

    if fake:
        model = FakeModel()
    else:
        missing = cfg.problems()
        if missing:
            console.print(f"[red]missing config: {', '.join(missing)}[/red]")
            raise typer.Exit(2)
        model = OpenAICompatibleModel(
            model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key, price=cfg.price
        )

    path = new_session_path(ws)
    with EventLogger(path) as logger:
        agent = Agent(
            model,
            tools,
            system_prompt=system,
            instance_prompt=instance,
            workspace=ws,
            max_steps=max_steps or steps or cfg.max_steps,
            max_cost=cfg.max_cost,
            max_seconds=cfg.max_seconds,
            timeout=cfg.tool_timeout,
            logger=logger,
        )
        result = agent.run()
    if not quiet:
        console.print(
            Panel(
                f"exit_status  {result.exit_status}\nsteps        {result.steps}\n"
                f"cost         ${result.cost:.5f}\ntokens       {result.usage}\n"
                f"submission   {result.submission or '-'}\n"
                f"session      {path}",
                title="run finished",
                expand=False,
            )
        )
    typer.echo(json.dumps({"result": result.as_dict(), "session": str(path)}, ensure_ascii=False))


# ----------------------------------------------------------------------- trace
@app.command()
def trace(
    session: Path = typer.Argument(None, help="省略时取最新一条轨迹。"),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
    full: bool = typer.Option(False, "--full", help="不截断内容。"),
) -> None:
    """把轨迹渲染成带行号的表格；报告里引用的就是这个行号。"""
    _safe_console()
    if session is None:
        found = list_sessions(workspace)
        if not found:
            console.print(f"[red]no session under {workspace}[/red]")
            raise typer.Exit(1)
        session = found[-1]
    events = read_events(session)
    width = 400 if full else 90
    for n, e in enumerate(events, 1):
        kind = e.get("kind", "?")
        console.print(f"[dim]{n:>4}[/dim] [cyan]{kind:<14}[/cyan] {_trace_detail(e, width)}")


def _trace_detail(e: dict, width: int) -> str:
    def clip(x: str) -> str:
        x = (x or "").replace("\n", " | ")
        return x if len(x) <= width else x[:width] + "..."

    kind = e.get("kind")
    if kind == "tool_call":
        return f"{e.get('name')}  {clip(str(e.get('raw_args')))}"
    if kind == "tool_result":
        flag = "ok" if e.get("ok") else f"FAIL rc={e.get('returncode')}"
        return f"{str(e.get('name')):<12} {flag:<14} {clip(e.get('output') or e.get('error') or '')}"
    if kind == "model_response":
        calls = ", ".join(c["name"] for c in e.get("tool_calls", [])) or "-"
        return f"step {e.get('step')}  calls=[{calls}]  {clip(e.get('content') or '')}"
    if kind == "run_start":
        return (
            f"model={e.get('model')} max_steps={e.get('max_steps')} "
            f"workspace={e.get('workspace')}"
        )
    if kind == "run_end":
        return (
            f"{e.get('exit_status')}  steps={e.get('steps')} cost={e.get('cost')} "
            f"usage={e.get('usage')}"
        )
    payload = {k: v for k, v in e.items() if k not in {"id", "parent_id", "ts"}}
    return clip(json.dumps(payload, ensure_ascii=False))


@app.command()
def sessions(workspace: Path = typer.Option(Path("."), "--workspace", "-w")) -> None:
    """列出该 workspace 下的轨迹文件。"""
    for p in list_sessions(workspace):
        console.print(f"{p}  [dim]{p.stat().st_size} bytes[/dim]")


if __name__ == "__main__":  # pragma: no cover
    app()
