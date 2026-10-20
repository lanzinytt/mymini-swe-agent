"""ToolRegistry：schema 广播 + 单一分发路径。

``execute`` 要处理五件事，且**全部**变成 observation 而不是异常：

1. 未知工具；
2. ``arguments`` 是 JSON 字符串 → 解析；坏 JSON；
3. 解析出来不是 dict；
4. 工具实现自己抛异常（含 bug）；
5. 输出过长 → 截断并置 ``truncated``。

只有一条路径意味着只有一处需要测试，也意味着轨迹里"模型看到的东西"和
测试里断言的东西是同一个。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from myagent.tools.base import BaseTool, ToolContext, ToolResult, truncate


@dataclass
class CallOutcome:
    call_id: str
    name: str
    role: str  # "tool" | "assistant"
    text: str
    result: ToolResult | None


class ToolRegistry:
    def __init__(self, tools: list[BaseTool] | None = None, *, gate=None):
        self._tools: dict[str, BaseTool] = {}
        self.gate = gate  # SafetyGate 在第 8 章接入，此前为 None
        for t in tools or []:
            self.register(t)

    # ------------------------------------------------------------------ 注册
    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [self._tools[n].schema() for n in self.names()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------------ 分发
    def execute(self, name: str, raw_args: str | dict | None, ctx: ToolContext) -> ToolResult:
        if name not in self._tools:
            return ToolResult(
                ok=False,
                error=f"unknown tool `{name}`; available: {', '.join(self.names())}",
            )
        args, problem = _parse_args(raw_args)
        if problem:
            return ToolResult(ok=False, error=problem)

        if self.gate is not None:
            blocked = self.gate.check(name, args, ctx.workspace)
            if blocked:
                return ToolResult(
                    ok=False,
                    error=f"blocked by safety gate: {blocked}",
                    metadata={"blocked": blocked},
                )

        try:
            result = self._tools[name].run(args, ctx)
        except Exception as e:  # noqa: BLE001 - 工具的 bug 不能杀死 agent
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")

        if result.output and len(result.output) > ctx.max_output_chars:
            text, truncated = truncate(result.output, ctx.max_output_chars)
            result.output = text
            result.truncated = True
            result.metadata.setdefault("limit", ctx.max_output_chars)
        return result


def _parse_args(raw_args: str | dict | None) -> tuple[dict, str]:
    if raw_args is None or raw_args == "":
        return {}, ""
    if isinstance(raw_args, dict):
        return raw_args, ""
    if not isinstance(raw_args, str):
        return {}, f"`arguments` must be a JSON object string, got {type(raw_args).__name__}"
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError as e:
        return {}, f"invalid JSON arguments ({e.msg} at char {e.pos}): {raw_args[:200]}"
    if not isinstance(parsed, dict):
        return {}, f"`arguments` must decode to an object, got {type(parsed).__name__}"
    return parsed, ""
