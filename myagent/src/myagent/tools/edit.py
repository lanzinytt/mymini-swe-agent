"""精确编辑：改一段，而不是重写整个文件。

设计要点全部围绕"模型最容易犯的错"来定：

1. **文本重复出现**：要求补充上下文或显式 ``all=True``，绝不猜第一个还是最后一个。
2. **只改目标**：用字符串切片替换，不经过"读全文 → 改 → 整体重写"，
   所以换行、缩进、行尾空白、BOM 都不会被动到。
3. **失败要说清楚**：找不到就报"出现 0 次"，出现多次就报"出现 N 次，
   给出更多上下文"——模型据此能自己修好。
"""

from __future__ import annotations

from myagent.tools.base import BaseTool, ToolContext, ToolResult


def _count(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "Replace an exact text span in a file. `old` must appear exactly once unless "
        "`all=true`. Use this instead of rewriting the whole file with write_file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Exact text to replace, copied verbatim."},
            "new": {"type": "string", "description": "Replacement text."},
            "all": {
                "type": "boolean",
                "description": "Replace every occurrence. Required when `old` appears more than once.",
            },
        },
        "required": ["path", "old", "new"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw_path = args.get("path")
        old = args.get("old")
        new = args.get("new")
        replace_all = bool(args.get("all"))
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ToolResult(ok=False, error="`path` must be a non-empty string")
        if not isinstance(old, str) or old == "":
            return ToolResult(ok=False, error="`old` must be a non-empty string")
        if not isinstance(new, str):
            return ToolResult(ok=False, error="`new` must be a string")
        if old == new:
            return ToolResult(ok=False, error="`old` and `new` are identical; nothing to do")

        try:
            target = ctx.resolve(raw_path)
        except ValueError as e:
            return ToolResult(ok=False, error=str(e))
        if not target.is_file():
            return ToolResult(ok=False, error=f"no such file: {raw_path}")

        # 用字节读再解码：Path.read_text 不支持 newline=，而文本模式读会把 CRLF
        # 折成 LF，一次编辑就把整个文件的换行改掉
        raw = target.read_bytes()
        crlf = b"\r\n" in raw
        original = raw.decode("utf-8")
        # 模型给的 old/new 基本总是 LF，而 Windows 上的文件常是 CRLF。
        # 直接匹配会 100% 失败，所以按文件的换行风格归一化后再匹配，写回时还原。
        haystack = original.replace("\r\n", "\n") if crlf else original
        needle = old.replace("\r\n", "\n")
        replacement = new.replace("\r\n", "\n")
        occurrences = _count(haystack, needle)

        if occurrences == 0:
            return ToolResult(
                ok=False,
                error=(
                    f"`old` text not found in {raw_path}. "
                    "Copy the exact text including indentation; do not retype it."
                ),
            )
        if occurrences > 1 and not replace_all:
            lines = [n for n, ln in enumerate(haystack.splitlines(), 1) if needle in ln]
            return ToolResult(
                ok=False,
                error=(
                    f"`old` appears {occurrences} times in {raw_path} "
                    f"(lines {lines[:5]}). Add surrounding context to make it unique, "
                    "or pass all=true to replace every occurrence."
                ),
            )

        updated = haystack.replace(needle, replacement, -1 if replace_all else 1)
        if crlf:
            updated = updated.replace("\n", "\r\n")
        # 原样写回：不经过 text 模式的换行转换，BOM / 结尾无换行都保持
        target.write_bytes(updated.encode("utf-8"))

        # 只统计"新旧之间"的行差，用来证明没有重排全文
        changed = sum(
            1
            for a, b in zip(original.splitlines(), updated.splitlines())
            if a != b
        ) + abs(len(updated.splitlines()) - len(original.splitlines()))
        return ToolResult(
            ok=True,
            output=(
                f"replaced {occurrences if replace_all else 1} occurrence(s) in {raw_path}; "
                f"{len(original)} -> {len(updated)} chars"
            ),
            metadata={
                "occurrences": occurrences,
                "replace_all": replace_all,
                "lines_changed": changed,
                "bytes_before": len(original.encode("utf-8")),
                "bytes_after": len(updated.encode("utf-8")),
            },
        )
