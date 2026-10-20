"""上下文压缩。

三条不变量，比"压缩率"重要得多：

1. **配对**：``assistant.tool_calls`` 与对应的 ``role=tool`` 消息必须一起留或一起删。
   只删一半，下一次请求会被 API 拒绝（``tool_call_id`` 找不到对应调用）。
2. **头两条固定**：system + 第一个 user（任务描述）。模型的"我在做什么"来自它们。
3. **摘要失败就截断**：摘要本身也要花一次模型调用，失败不能把整个任务拖死。

压缩策略对比在 ``docs/evidence/compaction.md``：不压缩 / 硬截断 / 摘要。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# 粗略估算而不是精确 tokenizer：这里的目标是"别超预算"，不是"省到最后一 token"。
# 中文按 1 字 ≈ 1 token，西文按 4 字符 ≈ 1 token，是常见的保守取值。
CJK_RANGES = ((0x4E00, 0x9FFF), (0x3000, 0x303F), (0xFF00, 0xFFEF))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in CJK_RANGES))
    return cjk + max(0, (len(text) - cjk)) // 4


def estimate_context_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += 4  # 每条消息的固定开销（role、分隔符）
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for call in m.get("tool_calls") or []:
            total += estimate_tokens(json.dumps(call.get("function", {}), ensure_ascii=False))
    return total


@dataclass
class CompactInfo:
    strategy: str
    before: int
    after: int
    dropped: int
    used_summary: bool

    @property
    def saved_pct(self) -> float:
        return 0.0 if self.before == 0 else (self.before - self.after) / self.before * 100

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "tokens_before": self.before,
            "tokens_after": self.after,
            "dropped_messages": self.dropped,
            "used_summary": self.used_summary,
            "saved_pct": round(self.saved_pct, 1),
        }


def groups(messages: list[dict]) -> list[list[dict]]:
    """把消息切成不可再分的组。

    一个 assistant 带 N 个 tool_calls，紧跟 N 条 tool 消息，这 ``1+N`` 条是一组。
    任何压缩都必须以组为单位，否则一定产生孤立消息。
    """
    out: list[list[dict]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            out.append(messages[i : i + 1 + n])
            i += 1 + n
        else:
            out.append([m])
            i += 1
    return out


def _summary_text(removed: list[dict]) -> str:
    """把被删掉的历史压成可读摘要。

    刻意用规则而不是再调一次模型：
    1. 不花 token；2. 失败模式确定（不会因为摘要模型抽风而丢信息）。
    报告里对比过两种做法，规则摘要在本实验的短任务上已经够用。
    """
    lines: list[str] = []
    for m in removed:
        role = m.get("role")
        if role == "assistant":
            for call in m.get("tool_calls") or []:
                fn = call.get("function", {})
                lines.append(f"- called {fn.get('name')}({fn.get('arguments', '')[:80]})")
            if (m.get("content") or "").strip():
                lines.append(f"- said: {m['content'].strip()[:100]}")
        elif role == "tool":
            first = (m.get("content") or "").strip().splitlines()
            if first:
                lines.append(f"- {m.get('name', 'tool')} -> {first[0][:100]}")
        elif role == "user" and not m.get("content", "").startswith("[harness]"):
            lines.append(f"- user: {m.get('content', '')[:100]}")
    if not lines:
        return ""
    return "[compacted history]\n" + "\n".join(lines[-40:])


def maybe_compact(
    messages: list[dict],
    *,
    limit: int,
    keep_recent: int = 6,
    strategy: str = "summarize",
) -> tuple[list[dict], CompactInfo | None]:
    """超预算时压缩，返回新列表与信息；未超预算返回原列表与 ``None``。"""
    before = estimate_context_tokens(messages)
    if strategy == "none" or before <= limit:
        return messages, None

    gs = groups(messages)
    head, tail = gs[:2], gs[-keep_recent:] if keep_recent > 0 else []
    middle = gs[len(head) : len(gs) - len(tail)] if tail else gs[len(head) :]
    if not middle:
        return messages, None

    removed = [m for g in middle for m in g]

    if strategy == "truncate":
        kept = [m for g in head + tail for m in g]
        info = CompactInfo("truncate", before, estimate_context_tokens(kept), len(removed), False)
        return kept, info

    summary = _summary_text(removed)
    note = {"role": "user", "content": summary}
    kept = [m for g in head for m in g] + [note] + [m for g in tail for m in g]
    after = estimate_context_tokens(kept)

    if after >= before:
        # 摘要比原文还长（短任务里很常见）：宁可硬截断，也不要"负收益压缩"
        kept = [m for g in head + tail for m in g]
        info = CompactInfo("truncate-fallback", before, estimate_context_tokens(kept), len(removed), False)
        return kept, info

    return kept, CompactInfo("summarize", before, after, len(removed), True)
