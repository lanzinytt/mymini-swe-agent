"""收敛提醒（stall guard）的行为规格。

提醒本身是个启发式，所以更要把边界写死：只提醒一次、写文件就重置、
关掉时完全不影响其他不变量。
"""

from __future__ import annotations

import pytest

from conftest import ScriptedModel, call
from myagent.agent import MAX_STEPS, SUBMIT, Agent
from myagent.model import Completion
from myagent.tools.files import WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.submit import SubmitTool


def make(model, ws, **kw):
    reg = ToolRegistry([WriteFileTool(), SubmitTool()])
    return Agent(model, reg, system_prompt="s", instance_prompt="t", workspace=ws, **kw)


def read_only_steps(n: int) -> list[Completion]:
    """n 步只读（read_file 不存在也不影响：失败也是"没写文件"）。"""
    return [
        Completion(tool_calls=[call("read_file", '{"path": "README.md"}', f"r{i}")])
        for i in range(n)
    ]


def test_nudge_fires_once_after_threshold(ws):
    script = read_only_steps(6) + [Completion(tool_calls=[call("submit", '{"summary": "s"}', "x")])]
    model = ScriptedModel(script)
    agent = make(model, ws, max_steps=10, stall_reminder_after=3)
    result = agent.run()

    nudges = [e for e in result.messages if e["role"] == "user" and "[harness]" in e["content"]]
    # 只提醒一次：第 3 步触发，之后不再重复
    assert (result.stalls, len(nudges)) == (1, 1)


def test_nudge_is_skipped_when_writing(ws):
    script = [
        Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "1"}', f"w{i}")])
        for i in range(4)
    ] + [Completion(tool_calls=[call("submit", '{"summary": "s"}', "x")])]
    result = make(ScriptedModel(script), ws, max_steps=10, stall_reminder_after=2).run()
    assert (result.stalls, result.exit_status) == (0, SUBMIT)


def test_writing_resets_the_streak(ws):
    script = (
        read_only_steps(2)
        + [Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "1"}', "w")])]
        + read_only_steps(2)
        + [Completion(tool_calls=[call("submit", '{"summary": "s"}', "x")])]
    )
    result = make(ScriptedModel(script), ws, max_steps=20, stall_reminder_after=3).run()
    assert result.stalls == 0


def test_disabled_by_zero(ws):
    script = read_only_steps(6)
    result = make(ScriptedModel(script), ws, max_steps=6, stall_reminder_after=0).run()
    assert (result.stalls, result.exit_status) == (0, MAX_STEPS)


def test_nudge_does_not_break_tool_pairing(ws):
    """提醒消息插在 tool 消息之后、下一次请求之前，不能拆散 tool_call/tool 配对。"""
    script = read_only_steps(4) + [Completion(tool_calls=[call("submit", '{"summary": "s"}', "x")])]
    model = ScriptedModel(script)
    result = make(model, ws, max_steps=10, stall_reminder_after=2).run()
    for snapshot in model.snapshots:
        pending = set()
        for m in snapshot:
            if m["role"] == "assistant":
                pending = {c["id"] for c in m.get("tool_calls") or []}
            elif m["role"] == "tool":
                pending.discard(m["tool_call_id"])
        assert not pending, f"unpaired tool calls in request: {pending}"
    assert result.exit_status == SUBMIT
