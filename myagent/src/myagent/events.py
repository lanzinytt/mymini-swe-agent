"""事件流：一行一个 JSON 事件，写后立即 flush。

轨迹文件是实验证据的唯一来源——报告里的"第 N 行"指的就是这个文件的行号。
所以它必须满足三点：append-only、崩溃时已有的行仍然有效、每条事件带
``id`` 与 ``parent_id`` 以便后面做 resume。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

SESSION_DIR = ".myagent/sessions"


class EventLogger:
    def __init__(self, path: Path, *, append: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a" if append else "w", encoding="utf-8", newline="\n")
        self.last_id: str | None = None
        self.events: list[dict] = []
        if append:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    evt = json.loads(line)
                    self.events.append(evt)
                    self.last_id = evt.get("id")

    # ------------------------------------------------------------------ 写事件
    def log(self, kind: str, **payload: Any) -> dict:
        evt = {
            "id": uuid.uuid4().hex[:12],
            "parent_id": self.last_id,
            "kind": kind,
            "ts": round(time.time(), 3),
            "at": datetime.now().strftime("%H:%M:%S"),
            **payload,
        }
        self._fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
        self._fh.flush()  # 崩溃也要留住已发生的调用
        self.last_id = evt["id"]
        self.events.append(evt)
        return evt

    # 让 agent 可以 `logger.step(...)` 这样调用，读起来更自然
    def __getattr__(self, kind: str):
        if kind.startswith("_"):
            raise AttributeError(kind)
        return lambda **payload: self.log(kind, **payload)

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def new_session_path(workspace: Path, stamp: str | None = None) -> Path:
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return Path(workspace) / SESSION_DIR / f"{stamp}.jsonl"


def list_sessions(workspace: Path) -> list[Path]:
    d = Path(workspace) / SESSION_DIR
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def read_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class NullLogger:
    """FakeModel 冒烟与单测用：不落盘，接口与 EventLogger 一致。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.last_id = None

    def log(self, kind: str, **payload: Any) -> dict:
        evt = {"kind": kind, **payload}
        self.events.append(evt)
        return evt

    def __getattr__(self, kind: str):
        if kind.startswith("_"):
            raise AttributeError(kind)
        return lambda **payload: self.log(kind, **payload)

    def close(self) -> None:
        pass
