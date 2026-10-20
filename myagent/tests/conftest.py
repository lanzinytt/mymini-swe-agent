from __future__ import annotations

import json
from pathlib import Path

import pytest

from myagent.model import Completion, ToolCall


class ScriptedModel:
    """把一串预置响应喂给 Agent；每次调用记录下当时的 messages 快照。"""

    name = "scripted"

    def __init__(self, script: list[Completion]):
        self.script = list(script)
        self.snapshots: list[list[dict]] = []

    def chat(self, messages, tools):
        self.snapshots.append(json.loads(json.dumps(messages)))
        if not self.script:
            return Completion(content="nothing left", finish_reason="stop")
        return self.script.pop(0)


def call(name: str, args: str, call_id: str = "") -> ToolCall:
    return ToolCall(id=call_id or f"c_{name}", name=name, args=None, raw_args=args)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    return tmp_path
