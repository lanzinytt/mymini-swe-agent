"""文件工具：read_file / write_file / list_dir。

全部走 ``ctx.resolve``，所以"工作区外绝对路径"在这里就被挡掉了——这是安全
检查的第一道闸，第二道在 ``safety.py`` 里对 shell 命令做模式匹配。
"""

from __future__ import annotations

from myagent.tools.base import BaseTool, ToolContext, ToolResult, truncate


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file inside the workspace. Supports line ranges."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start": {"type": "integer", "description": "1-based first line (default 1)."},
            "end": {"type": "integer", "description": "1-based last line, inclusive."},
        },
        "required": ["path"],
    }

    def __init__(self, limit: int = 20_000):
        self.limit = limit

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            target = ctx.resolve(str(args.get("path", "")))
        except ValueError as e:
            return ToolResult(ok=False, error=str(e))
        if not target.is_file():
            return ToolResult(ok=False, error=f"no such file: {args.get('path')}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(args.get("start") or 1))
        end = int(args.get("end") or len(lines))
        chunk = lines[start - 1 : end]
        body = "\n".join(f"{n:>5}| {text}" for n, text in enumerate(chunk, start))
        text, truncated = truncate(body, self.limit)
        return ToolResult(
            ok=True,
            output=text,
            truncated=truncated,
            metadata={"limit": self.limit, "lines": len(lines), "shown": len(chunk)},
        )


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Create or overwrite a text file inside the workspace. "
        "Parent directories are created automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw = args.get("path")
        content = args.get("content")
        if not isinstance(raw, str) or not raw.strip():
            return ToolResult(ok=False, error="`path` must be a non-empty string")
        if not isinstance(content, str):
            return ToolResult(ok=False, error="`content` must be a string")
        try:
            target = ctx.resolve(raw)
        except ValueError as e:
            return ToolResult(ok=False, error=str(e))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return ToolResult(
            ok=True,
            output=f"wrote {len(content)} chars to {ctx.relative(target)}",
            metadata={"bytes": len(content.encode('utf-8')), "path": ctx.relative(target)},
        )


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List files and directories inside the workspace (non-recursive by default)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory, defaults to the workspace root."},
            "recursive": {"type": "boolean"},
        },
        "required": [],
    }

    def __init__(self, limit: int = 300):
        self.limit = limit

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            target = ctx.resolve(str(args.get("path") or "."))
        except ValueError as e:
            return ToolResult(ok=False, error=str(e))
        if not target.is_dir():
            return ToolResult(ok=False, error=f"not a directory: {args.get('path')}")
        entries = sorted(
            target.rglob("*") if args.get("recursive") else target.iterdir(),
            key=lambda p: str(p).lower(),
        )
        rows = []
        for p in entries:
            if any(part in {".myagent", "__pycache__", ".git"} for part in p.parts):
                continue
            size = p.stat().st_size if p.is_file() else 0
            rows.append(f"{'d' if p.is_dir() else 'f'} {size:>9} {ctx.relative(p)}")
        body = "\n".join(rows[: self.limit])
        return ToolResult(
            ok=True,
            output=body or "(empty)",
            truncated=len(rows) > self.limit,
            metadata={"count": len(rows), "limit": self.limit},
        )
