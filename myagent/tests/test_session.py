"""会话恢复：历史一致、文件追加、无副作用。

这里的测试全部是"如果不写这条代码就会悄悄错"的类型：
恢复出空的 system 提示 → 模型立刻跑偏；新建 session 文件 → 原始证据被切断；
中断在 assistant 与 tool 之间 → 下一次请求非法。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import ScriptedModel, call
from myagent.agent import MAX_STEPS, SUBMIT, Agent
from myagent.events import EventLogger, list_sessions, new_session_path, read_events
from myagent.model import Completion
from myagent.session import restore
from myagent.tools.files import WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.submit import SubmitTool


def registry() -> ToolRegistry:
    return ToolRegistry([WriteFileTool(), SubmitTool()])


def build(model, ws, logger, **kw) -> Agent:
    return Agent(
        model,
        registry(),
        system_prompt="SYS-PROMPT",
        instance_prompt="DO-THE-TASK",
        workspace=ws,
        logger=logger,
        **kw,
    )


def test_interrupted_run_then_resume_completes(ws, tmp_path):
    session = new_session_path(ws)
    # 只给它 1 步预算：第一次一定会停在 MaxSteps，且 b.txt 不会出现
    script = [
        Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "1"}', "c1")]),
        Completion(tool_calls=[call("write_file", '{"path": "b.txt", "content": "2"}', "c2")]),
    ]
    with EventLogger(session) as logger:
        first = build(ScriptedModel(script), ws, logger, max_steps=1).run()
    assert first.exit_status == MAX_STEPS
    assert first.steps == 1
    assert not (ws / "b.txt").exists()

    state = restore(session)
    assert state.steps == 1
    assert state.messages[0]["content"] == "SYS-PROMPT"
    assert state.messages[1]["content"] == "DO-THE-TASK"
    assert [m["role"] for m in state.messages] == ["system", "user", "assistant", "tool"]

    # 接着跑：同一个文件追加，不新建
    with EventLogger(session, append=True) as logger:
        resumed = build(
            ScriptedModel([Completion(tool_calls=[call("submit", '{"summary": "done"}', "c3")])]),
            ws, logger, max_steps=4, messages=state.messages,
        )
        resumed.cost = state.cost
        result = resumed.run()

    assert result.exit_status == SUBMIT
    assert len(list_sessions(ws)) == 1, "resume must not create a second session file"
    kinds = [e["kind"] for e in read_events(session)]
    assert kinds.count("run_start") == 2  # 追加了第二次 run_start
    assert kinds[-1] == "run_end"


def test_restore_keeps_tool_pairing(ws):
    session = new_session_path(ws)
    with EventLogger(session) as logger:
        build(
            ScriptedModel([
                Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "1"}', "c1")]),
            ]),
            ws, logger, max_steps=1,
        ).run()
    state = restore(session)
    pending = set()
    for m in state.messages:
        if m["role"] == "assistant":
            pending = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            pending.discard(m["tool_call_id"])
    assert not pending


def test_restore_repairs_a_call_interrupted_before_its_result(tmp_path):
    """模拟崩溃：assistant 写完了，tool 结果还没落盘。"""
    session = tmp_path / "s.jsonl"
    lines = [
        {"kind": "run_start", "system": "S", "instance": "I"},
        {"kind": "model_response", "step": 1, "content": "",
         "tool_calls": [{"id": "x1", "name": "run_shell", "raw_args": "{}"}]},
    ]
    session.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    state = restore(session)
    assert "1 unpaired" in state.trailing_note
    assert state.messages[-1]["role"] == "tool"
    assert state.messages[-1]["tool_call_id"] == "x1"
    assert "interrupted" in state.messages[-1]["content"]


def test_restore_of_empty_or_broken_session(tmp_path):
    session = tmp_path / "empty.jsonl"
    session.write_text("", encoding="utf-8")
    state = restore(session)
    assert (state.messages, state.steps) == ([], 0)
    assert state.trailing_note


def test_resume_carries_cost_forward(ws):
    session = new_session_path(ws)
    with EventLogger(session) as logger:
        build(
            ScriptedModel([Completion(content="", cost=0.25, tool_calls=[call("submit", '{"summary": "s"}', "c1")])]),
            ws, logger, max_steps=2,
        ).run()
    state = restore(session)
    assert state.cost == pytest.approx(0.25)


def test_resume_does_not_duplicate_side_effects(ws):
    """恢复不重放工具：第一次运行已经写过的文件不应被再写一次。"""
    session = new_session_path(ws)
    with EventLogger(session) as logger:
        build(
            ScriptedModel([Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "v1"}', "c1")])]),
            ws, logger, max_steps=1,
        ).run()
    (ws / "a.txt").write_text("changed-by-hand", encoding="utf-8")

    state = restore(session)
    with EventLogger(session, append=True) as logger:
        build(ScriptedModel([Completion(tool_calls=[call("submit", '{"summary": "s"}', "c2")])]),
              ws, logger, max_steps=3, messages=state.messages).run()

    assert (ws / "a.txt").read_text(encoding="utf-8") == "changed-by-hand"
    assert len(list_sessions(ws)) == 1
