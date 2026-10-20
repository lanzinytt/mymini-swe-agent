"""会话恢复：从轨迹接着跑。

`resume` 的正确性只取决于一件事：**恢复到的那份 messages，与中断前最后
一次请求时发出的 messages 完全一致**。所以这里不重放工具、不重跑模型，
只做两件事：

1. 逐行解析轨迹，重建 messages；
2. 用**同一个文件追加**写新事件，不新建轨迹。

第二条尤其容易被做错：新建一个 session 文件，看起来"能恢复"，但原始
证据被切断，报告里的行号引用全部失效。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from myagent.events import read_events


@dataclass
class Restored:
    messages: list[dict]
    steps: int
    cost: float
    usage: dict[str, int]
    last_id: str | None
    event_count: int
    trailing_note: str = ""

    def summary(self) -> dict:
        return {
            "events": self.event_count,
            "steps": self.steps,
            "cost": round(self.cost, 6),
            "usage": self.usage,
            "messages": len(self.messages),
        }


def _assistant_message(event: dict) -> dict:
    msg: dict = {"role": "assistant", "content": event.get("content") or ""}
    calls = event.get("tool_calls") or []
    if calls:
        msg["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c.get("raw_args", "{}")},
            }
            for c in calls
        ]
    return msg


def restore(session: Path) -> Restored:
    """把轨迹重建成 messages。

    重建规则直接对应 ``Agent`` 写事件的顺序：
    ``model_response`` → assistant 消息；``tool_result`` → tool 消息。
    """
    events = read_events(session)
    messages: list[dict] = []
    steps = 0
    cost = 0.0
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    started = False

    for e in events:
        kind = e.get("kind")
        if kind == "run_start":
            started = True
            messages.append({"role": "system", "content": e.get("system") or ""})
            messages.append({"role": "user", "content": e.get("instance") or ""})
        elif kind == "model_response":
            messages.append(_assistant_message(e))
            steps = max(steps, int(e.get("step") or 0))
            cost += float(e.get("cost") or 0.0)
            for k, v in (e.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + int(v)
        elif kind == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": e.get("id", ""),
                    "name": e.get("name", ""),
                    "content": e.get("output") or e.get("error") or "",
                }
            )
        elif kind == "stall_nudge":
            messages.append({"role": "user", "content": e.get("content") or "[harness] nudge"})

    note = ""
    if not started:
        note = "session has no run_start; history cannot be rebuilt"
    leftovers = _unpaired(messages)
    if leftovers:
        note = (note + " " if note else "") + f"dropped {len(leftovers)} unpaired tool call(s)"
        messages = _drop_unpaired(messages)

    return Restored(
        messages=messages,
        steps=steps,
        cost=cost,
        usage=usage,
        last_id=events[-1].get("id") if events else None,
        event_count=len(events),
        trailing_note=note,
    )


def _unpaired(messages: list[dict]) -> set[str]:
    pending: set[str] = set()
    for m in messages:
        if m["role"] == "assistant":
            pending = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            pending.discard(m.get("tool_call_id", ""))
    return pending


def _drop_unpaired(messages: list[dict]) -> list[dict]:
    """中断发生在"assistant 已写、tool 还没写"之间时，补一条占位消息。

    直接删除 assistant 会破坏 append-only；补占位则保持了"每个 tool_call
    都有对应 tool 消息"的不变量，下一次请求一定合法。
    """
    out = list(messages)
    pending: set[str] = set()
    for m in out:
        if m["role"] == "assistant":
            pending = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            pending.discard(m.get("tool_call_id", ""))
    for call_id in pending:
        out.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "",
                "content": "(interrupted before this call produced output)",
            }
        )
    return out


def write_resume_marker(session: Path, *, extra: dict | None = None) -> None:
    """在原文件末尾追加一条标记；不新建文件。"""
    with session.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"kind": "resume", **(extra or {})}, ensure_ascii=False) + "\n")
