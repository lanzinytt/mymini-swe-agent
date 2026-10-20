"""工具层的基本约定。

核心约定只有一条：**任何工具都不许抛异常给 Agent 循环**。
所有失败都变成 ``ToolResult(ok=False, ...)``，由 agent 原样回填给模型。
理由是：异常会绕过 ``should_stop()`` 的记账，让"这一步花了多少"丢失。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ToolContext:
    workspace: Path
    timeout: int = 60
    submitted: bool = False
    submission: str = ""
    max_output_chars: int = 8_000
    safety: bool = True
    # 注入而非硬编码：doctor 报告的解释器与实际执行必须是同一个
    python_exe: str = sys.executable

    def resolve(self, raw: str | Path) -> Path:
        """把用户/模型给的路径解析到 workspace 内；越界直接抛 ValueError。

        抛异常是安全的：调用它的是工具，不是 agent 循环。
        """
        p = Path(raw)
        target = (self.workspace / p).resolve() if not p.is_absolute() else p.resolve()
        root = self.workspace.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes workspace: {raw}")
        return target

    def relative(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.workspace.resolve()))
        except ValueError:
            return str(p)


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    returncode: int = 0
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_observation(self) -> str:
        """回填给模型的文本。失败时显式给 returncode，避免模型靠猜。"""
        parts: list[str] = []
        if self.error and not self.ok:
            parts.append(f"ERROR: {self.error}")
        if self.output:
            parts.append(self.output)
        if self.truncated:
            parts.append(f"[output truncated at {self.metadata.get('limit', '?')} chars]")
        if not self.ok and not self.output and self.error:
            parts.append(f"(exit code {self.returncode})")
        if not self.ok and not self.output and not self.error:
            parts.append(f"command failed with exit code {self.returncode} and printed nothing; "
                         "check the path, the interpreter name and whether the file exists")
        return "\n".join(parts) or "(no output)"


class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    def run(self, args: dict, ctx: ToolContext) -> ToolResult: ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """保留头尾：头是"它开始做什么"，尾是报错与退出码，中间可以不要。"""
    if len(text) <= limit:
        return text, False
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n... [{omitted} chars omitted] ...\n{tail}", True


class BaseTool:
    """给具体工具一个默认实现，子类只需写 name/description/parameters/run。"""

    name = ""
    description = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError
