"""submit：唯一的正常结束信号。

为什么单独做一个工具而不是"模型说完成就算完成"：``content`` 里的自然语言
是给用户看的，不可判定；``submit`` 是结构化的，能被 ``should_stop()`` 精确
识别，也能在轨迹里留下一个可 grep 的事件。
"""

from __future__ import annotations

from myagent.tools.base import BaseTool, ToolContext, ToolResult


class SubmitTool(BaseTool):
    name = "submit"
    description = (
        "Declare the task finished. Call this exactly once, at the end, "
        "after you have verified the result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One or two sentences describing what was produced and how it was verified.",
            }
        },
        "required": ["summary"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        summary = args.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return ToolResult(ok=False, error="`summary` must be a non-empty string")
        ctx.submitted = True
        ctx.submission = summary.strip()
        return ToolResult(ok=True, output="submitted", metadata={"summary": ctx.submission})
