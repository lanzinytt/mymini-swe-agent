"""H2 工具层的五类错误 + run_python 的五个边界。

这些测试的价值在于"把不确定变成确定"：模型看到的每一种失败形状，都是这里
断言过的那一种。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myagent.tools.base import ToolContext, truncate
from myagent.tools.files import ReadFileTool, WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.shell import PythonTool, ShellTool
from myagent.tools.submit import SubmitTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, timeout=30, python_exe=sys.executable)


def registry() -> ToolRegistry:
    return ToolRegistry([PythonTool(), ShellTool(), WriteFileTool(), ReadFileTool(), SubmitTool()])


# ------------------------------------------------------------ registry.execute
def test_unknown_tool(ctx):
    r = registry().execute("nope", "{}", ctx)
    assert (r.ok, "unknown tool" in r.error) == (False, True)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ('{"a": ', "invalid JSON"),
        ("[1, 2]", "must decode to an object"),
        ("null", "must decode to an object"),
    ],
)
def test_bad_arguments_never_raise(ctx, raw, fragment):
    r = registry().execute("run_python", raw, ctx)
    assert (r.ok, fragment in r.error) == (False, True)


def test_tool_exception_becomes_observation(ctx, monkeypatch):
    class Boom:
        name = "boom"
        description = ""
        parameters = {"type": "object", "properties": {}}

        def schema(self):
            return {"type": "function", "function": {"name": "boom", "parameters": self.parameters}}

        def run(self, args, ctx):
            raise RuntimeError("kaboom")

    reg = ToolRegistry([Boom()])
    r = reg.execute("boom", "{}", ctx)
    assert (r.ok, "RuntimeError: kaboom" in r.error) == (False, True)


def test_long_output_is_truncated_with_flag(ctx):
    reg = registry()
    r = reg.execute("run_python", '{"code": "print(\'x\' * 20000)"}', ctx)
    assert r.truncated and len(r.output) < 20_000


def test_truncate_keeps_head_and_tail():
    text, flag = truncate("A" * 100 + "B" * 100, 40)
    assert flag
    assert text.startswith("A" * 20) and text.endswith("B" * 20)
    assert "chars omitted" in text


# --------------------------------------------------------------- run_python
def test_code_and_path_are_mutually_exclusive(ctx):
    reg = registry()
    both = reg.execute("run_python", '{"code": "print(1)", "path": "a.py"}', ctx)
    neither = reg.execute("run_python", "{}", ctx)
    assert (both.ok, neither.ok) == (False, False)
    assert "exactly one" in both.error


def test_run_python_executes_in_workspace(ctx: ToolContext):
    r = registry().execute("run_python", '{"code": "import os; print(os.getcwd())"}', ctx)
    assert r.ok
    assert Path(r.output.strip()).resolve() == ctx.workspace.resolve()


def test_run_python_path_cannot_escape(ctx: ToolContext):
    r = registry().execute("run_python", '{"path": "../../evil.py"}', ctx)
    assert (r.ok, "escapes workspace" in r.error) == (False, True)


def test_run_python_reports_nonzero_exit_as_observation(ctx):
    r = registry().execute("run_python", '{"code": "raise SystemExit(3)"}', ctx)
    assert (r.ok, r.returncode) == (False, 3)
    assert r.as_observation().startswith("ERROR: exit 3")


def test_run_python_merges_stderr(ctx):
    r = registry().execute(
        "run_python", '{"code": "import sys; print(\'out\'); print(\'err\', file=sys.stderr)"}', ctx
    )
    assert r.ok and "out" in r.output and "err" in r.output


def test_run_python_timeout_kills_and_reports(ctx: ToolContext):
    ctx.timeout = 2
    r = registry().execute("run_python", '{"code": "import time; time.sleep(30)"}', ctx)
    assert (r.ok, r.returncode) == (False, 124)
    assert "timeout" in r.output


def test_run_python_writes_script_under_dot_myagent(ctx: ToolContext):
    registry().execute("run_python", '{"code": "print(1)"}', ctx)
    assert list((ctx.workspace / ".myagent" / "tmp").glob("run_*.py"))


def test_silent_failure_gets_a_diagnostic(ctx: ToolContext):
    r = registry().execute("run_python", '{"code": "pass"}', ctx)
    assert r.ok
    r_empty = registry().execute("run_python", '{"path": "missing.py"}', ctx)
    assert "no such file" in r_empty.error


# ------------------------------------------------------------------- 文件工具
def test_write_then_read_roundtrip(ctx: ToolContext):
    reg = registry()
    w = reg.execute("write_file", '{"path": "d/x.txt", "content": "第一行\\n第二行"}', ctx)
    r = reg.execute("read_file", '{"path": "d/x.txt"}', ctx)
    assert w.ok and "第二行" in r.output
    assert r.output.splitlines()[1].strip() == "2| 第二行"


def test_write_outside_workspace_is_rejected(ctx: ToolContext):
    r = registry().execute("write_file", '{"path": "../outside.txt", "content": "x"}', ctx)
    assert (r.ok, "escapes workspace" in r.error) == (False, True)


def test_read_file_range(ctx: ToolContext):
    (ctx.workspace / "n.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    r = registry().execute("read_file", '{"path": "n.txt", "start": 2, "end": 3}', ctx)
    assert [line.split("| ")[1] for line in r.output.splitlines()] == ["b", "c"]


# ------------------------------------------------------------------ submit
def test_submit_sets_context_flag(ctx: ToolContext):
    r = registry().execute("submit", '{"summary": "done"}', ctx)
    assert (r.ok, ctx.submitted, ctx.submission) == (True, True, "done")
