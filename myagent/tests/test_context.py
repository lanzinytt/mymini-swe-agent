"""上下文压缩的不变量。

最重要的两条：配对不能拆、头两条不能动。压缩率再好看，破了这两条就是 bug。
"""

from __future__ import annotations

import json

import pytest

from myagent.agent import Agent
from myagent.context import (
    CompactInfo,
    estimate_context_tokens,
    groups,
    maybe_compact,
)
from myagent.model import Completion, ToolCall
from myagent.tools.files import WriteFileTool
from myagent.tools.registry import ToolRegistry
from myagent.tools.submit import SubmitTool


def history(n: int, big: int = 400) -> list[dict]:
    """构造 n 步真实形状的历史：system + task + (assistant + tool) * n。"""
    msgs: list[dict] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "the task"},
    ]
    for i in range(n):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": f'{{"i": {i}}}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "name": "write_file",
                     "content": "x" * big})
    return msgs


def assert_paired(msgs: list[dict]) -> None:
    pending = set()
    for m in msgs:
        if m["role"] == "assistant":
            pending = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in pending, f"orphan tool message {m['tool_call_id']}"
            pending.discard(m["tool_call_id"])
    assert not pending, f"unpaired tool calls: {pending}"


def test_groups_keeps_call_and_result_together():
    gs = groups(history(3))
    assert [len(g) for g in gs] == [1, 1, 2, 2, 2]


def test_no_compaction_under_budget():
    msgs = history(2, big=10)
    out, info = maybe_compact(msgs, limit=10**6)
    assert (out is msgs, info) == (True, None)


def test_compaction_preserves_pairing_and_head():
    msgs = history(20)
    out, info = maybe_compact(msgs, limit=2000, keep_recent=4)
    assert info is not None
    assert out[:2] == msgs[:2]  # system + task 不动
    assert_paired(out)
    assert estimate_context_tokens(out) < estimate_context_tokens(msgs)


def test_truncate_strategy_drops_whole_groups():
    msgs = history(20)
    out, info = maybe_compact(msgs, limit=2000, keep_recent=4, strategy="truncate")
    assert info.strategy == "truncate"
    assert_paired(out)
    assert all("compacted history" not in (m.get("content") or "") for m in out)


def test_summary_is_inserted_between_head_and_tail():
    msgs = history(20)
    out, info = maybe_compact(msgs, limit=2000, keep_recent=4)
    assert info.used_summary
    slot = out[2]
    assert "compacted history" in slot["content"]
    assert "write_file" in slot["content"]


def test_summary_falls_back_when_not_shorter():
    """摘要比原文长时必须退化成截断，不能"压缩完反而更大"。"""
    msgs = history(3, big=4)
    out, info = maybe_compact(msgs, limit=1, keep_recent=2)
    assert info is not None
    assert (info.strategy, info.after <= info.before) == ("truncate-fallback", True)


def test_agent_compacts_before_request_and_stays_paired(tmp_path):
    class M:
        name = "m"

        def __init__(self):
            self.seen = []

        def chat(self, messages, tools):
            self.seen.append(json.loads(json.dumps(messages)))
            if len(self.seen) < 12:
                return Completion(tool_calls=[ToolCall(
                    id=f"c{len(self.seen)}", name="write_file", args=None,
                    raw_args=f'{{"path": "f{len(self.seen)}.txt", "content": "{"y" * 2000}"}}',
                )])
            return Completion(tool_calls=[ToolCall(
                id="done", name="submit", args=None, raw_args='{"summary": "ok"}')])

    model = M()
    agent = Agent(
        model,
        ToolRegistry([WriteFileTool(), SubmitTool()]),
        system_prompt="sys",
        instance_prompt="task",
        workspace=tmp_path,
        max_steps=30,
        context_limit=3000,
        compact_keep_recent=2,
    )
    result = agent.run()
    assert result.exit_status == "Submitted"
    assert result.compactions, "expected at least one compaction"
    for snapshot in model.seen:
        assert_paired(snapshot)
    assert all(s[0]["role"] == "system" for s in model.seen)
