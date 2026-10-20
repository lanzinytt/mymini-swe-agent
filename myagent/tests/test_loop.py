"""H1 循环的四条不变量。

每个测试都至少覆盖一个"如果不写这条代码就会悄悄错"的点：
少补 tool 消息 → 下一次请求被 API 拒；停止顺序写错 → 超限被 final answer 掩盖。
"""

from __future__ import annotations

import pytest

from conftest import ScriptedModel, call
from myagent.agent import FINAL_ANSWER, MAX_STEPS, SUBMIT, Agent
from myagent.model import Completion
from myagent.tools.files import WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.submit import SubmitTool


def make_agent(model, ws, **kw):
    tools = ToolRegistry([WriteFileTool(), SubmitTool()])
    return Agent(
        model,
        tools,
        system_prompt="sys",
        instance_prompt="task",
        workspace=ws,
        **kw,
    )


def test_one_step_is_one_model_call(ws):
    model = ScriptedModel(
        [Completion(tool_calls=[call("submit", '{"summary": "ok"}')])]
    )
    agent = make_agent(model, ws, max_steps=5)
    result = agent.run()
    assert result.steps == 1
    assert model.snapshots[0][0] == {"role": "system", "content": "sys"}
    assert model.snapshots[0][1] == {"role": "user", "content": "task"}


def test_submit_wins_over_max_steps(ws):
    model = ScriptedModel(
        [Completion(tool_calls=[call("submit", '{"summary": "done"}')])]
    )
    # max_steps=1：should_stop 在 step 之前判断，所以第一次判断时未超限
    result = make_agent(model, ws, max_steps=1).run()
    assert (result.exit_status, result.submission) == (SUBMIT, "done")


def test_max_steps_is_reported_when_model_never_submits(ws):
    script = [
        Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "x"}', f"c{i}")])
        for i in range(10)
    ]
    result = make_agent(ScriptedModel(script), ws, max_steps=3).run()
    assert result.exit_status == MAX_STEPS
    assert result.steps == 3


def test_content_only_reply_ends_run_as_final_answer(ws):
    result = make_agent(ScriptedModel([Completion(content="I am done, trust me")]), ws).run()
    assert result.exit_status == FINAL_ANSWER
    assert result.submission == ""


def test_every_tool_call_gets_exactly_one_tool_message(ws):
    """包括 submit 之后被跳过的调用——否则下一轮 messages 不合法。"""
    model = ScriptedModel(
        [
            Completion(
                tool_calls=[
                    call("write_file", '{"path": "a.txt", "content": "x"}', "id-1"),
                    call("submit", '{"summary": "s"}', "id-2"),
                    call("write_file", '{"path": "b.txt", "content": "y"}', "id-3"),
                ]
            )
        ]
    )
    agent = make_agent(model, ws, max_steps=5)
    result = agent.run()

    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["id-1", "id-2", "id-3"]
    assert "skipped" in tool_msgs[2]["content"]
    assert not (ws / "b.txt").exists()  # 跳过 = 真的没执行


def test_history_is_append_only(ws):
    script = [
        Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "1"}', "c1")]),
        Completion(tool_calls=[call("write_file", '{"path": "a.txt", "content": "2"}', "c2")]),
        Completion(tool_calls=[call("submit", '{"summary": "ok"}', "c3")]),
    ]
    model = ScriptedModel(script)
    agent = make_agent(model, ws, max_steps=10)
    agent.run()
    # 第二次快照必须是第一次快照的超集（旧消息一字未改）
    first, second = model.snapshots[0], model.snapshots[1]
    assert second[: len(first)] == first
    assert len(second) == len(first) + 2  # assistant + tool


def test_tool_error_is_fed_back_not_raised(ws):
    model = ScriptedModel(
        [
            Completion(tool_calls=[call("nope", "{}", "x1")]),
            Completion(tool_calls=[call("submit", '{"summary": "ok"}', "x2")]),
        ]
    )
    result = make_agent(model, ws).run()
    assert result.exit_status == SUBMIT
    assert "unknown tool" in result.messages[3]["content"]
