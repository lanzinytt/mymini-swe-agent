"""配置：.env 解析 + provider 预设 + 运行时上限。

刻意不引 python-dotenv：一个 30 行的解析器足以覆盖本实验用到的全部语法
（``KEY=VALUE``、``#`` 注释、可选引号），少一个依赖就少一个环境坑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    # 自建网关 / 中转站：model 与 base_url 必须自己写
    "gateway": {"base_url": "", "model": ""},
    "custom": {"base_url": "", "model": ""},
}


def load_env_file(path: Path) -> dict[str, str]:
    """读 .env；文件不存在时返回空字典，方便纯 FakeModel 冒烟。"""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@dataclass
class Config:
    provider: str = "custom"
    model: str = ""
    base_url: str = ""
    api_key: str = ""

    max_steps: int = 30
    max_cost: float = 0.5
    max_seconds: int = 600
    context_limit: int = 120_000
    compact_keep_recent: int = 6
    compact_strategy: str = "summarize"
    tool_timeout: int = 60
    model_timeout: float = 120.0
    max_output_chars: int = 8_000
    stall_reminder_after: int = 6
    safety: bool = True
    price: tuple[float, float] | None = None
    extras: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env_file: Path | None = None, environ: dict | None = None) -> "Config":
        e = dict(os.environ if environ is None else environ)
        if env_file is not None:
            # 文件优先于真实环境变量，避免上一节课残留的全局变量干扰本实验
            e.update(load_env_file(env_file))
        provider = e.get("MYAGENT_PROVIDER", "custom").strip().lower()
        preset = PRESETS.get(provider, {})
        price = None
        if e.get("MYAGENT_PRICE_INPUT") and e.get("MYAGENT_PRICE_OUTPUT"):
            price = (float(e["MYAGENT_PRICE_INPUT"]), float(e["MYAGENT_PRICE_OUTPUT"]))
        return cls(
            provider=provider,
            model=e.get("MYAGENT_MODEL") or preset.get("model", ""),
            base_url=e.get("MYAGENT_BASE_URL") or preset.get("base_url", ""),
            api_key=e.get("MYAGENT_API_KEY", ""),
            max_steps=int(e.get("MYAGENT_MAX_STEPS", 30)),
            max_cost=float(e.get("MYAGENT_MAX_COST", 0.5)),
            max_seconds=int(e.get("MYAGENT_MAX_SECONDS", 600)),
            context_limit=int(e.get("MYAGENT_CONTEXT_LIMIT", 120_000)),
            stall_reminder_after=int(e.get("MYAGENT_STALL_AFTER", 6)),
            safety=e.get("MYAGENT_SAFETY", "1") not in ("0", "false", "False"),
            price=price,
        )

    def problems(self) -> list[str]:
        """doctor 用：列出阻塞项，而不是直接抛异常。"""
        missing = []
        if not self.model:
            missing.append("MYAGENT_MODEL")
        if not self.base_url:
            missing.append("MYAGENT_BASE_URL")
        if not self.api_key:
            missing.append("MYAGENT_API_KEY")
        return missing

    def redacted(self) -> dict:
        key = self.api_key
        shown = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("<empty>" if not key else "<short>")
        return {
            "provider": self.provider,
            "model": self.model or "<empty>",
            "base_url": self.base_url or "<empty>",
            "api_key": shown,
            "max_steps": self.max_steps,
            "max_cost": self.max_cost,
            "max_seconds": self.max_seconds,
            "context_limit": self.context_limit,
            "safety": self.safety,
        }
