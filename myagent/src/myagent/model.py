"""把一个 OpenAI 兼容端点包装成 ``Model`` 协议。

设计取舍：这里只保留 ``chat()`` 一个方法，返回带 ``tool_calls`` 的响应对象。
把"怎么发 HTTP"和"怎么跑循环"彻底分开，是后面 H1 能被单测覆盖的前提。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# 每 1M token 的价格（USD）。网关地址各家的实际计费不同，所以允许环境变量覆盖，
# 也允许在 .env 里写 MYAGENT_PRICE_* 直接指定；对不确定的端点就记 0，报告里
# 只用 token 数做横向比较。
PRICES = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}
DEFAULT_PRICE = (0.0, 0.0)


@dataclass
class ToolCall:
    id: str
    name: str
    args: Any
    raw_args: str = ""


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    finish_reason: str = ""
    raw: dict = field(default_factory=dict)

    def as_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_args},
                }
                for c in self.tool_calls
            ]
        return msg


class Model(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> Completion: ...


class OpenAICompatibleModel:
    """``POST {base_url}/chat/completions``，带重试与超时。"""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        retries: int = 2,
        price: tuple[float, float] | None = None,
        temperature: float = 0.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.price = price or PRICES.get(model, DEFAULT_PRICE)
        self.temperature = temperature
        self.name = model

    # ---------------------------------------------------------------- 对外接口
    def chat(self, messages: list[dict], tools: list[dict]) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = self._post(payload)
        return self._parse(data)

    def probe(self) -> dict:
        """doctor 用：发一次最小请求，确认端点、鉴权和 tool calling。"""
        started = time.time()
        try:
            first = self._post(
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ok"}],
                    "max_tokens": 4,
                }
            )
        except Exception as e:  # noqa: BLE001 - doctor 只负责如实报告
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "model": self.model}
        elapsed = time.time() - started
        tool_ok = self._probe_tools()
        return {
            "ok": True,
            "model": first.get("model", self.model),
            "latency_ms": int(elapsed * 1000),
            "tools": tool_ok,
            "served_by": self.base_url,
        }

    def _probe_tools(self) -> bool:
        try:
            data = self._post(
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": "call the ping tool"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "ping",
                                "description": "ping",
                                "parameters": {"type": "object", "properties": {}},
                            },
                        }
                    ],
                    "tool_choice": "auto",
                    "max_tokens": 64,
                }
            )
        except Exception:  # noqa: BLE001
            return False
        msg = data["choices"][0]["message"]
        return bool(msg.get("tool_calls"))

    # ---------------------------------------------------------------- 内部实现
    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last = e
                if attempt == self.retries:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise last  # pragma: no cover - 循环里已 return 或 raise

    def _parse(self, data: dict) -> Completion:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            raw = fn.get("arguments") or "{}"
            # 参数原样保留：解析留给工具层，因为"坏 JSON"本身是要报告给模型的信息
            calls.append(
                ToolCall(
                    id=c.get("id") or f"call_{uuid.uuid4().hex[:16]}_{i}",
                    name=fn.get("name", ""),
                    args=None,
                    raw_args=raw,
                )
            )
        usage = data.get("usage") or {}
        return Completion(
            content=msg.get("content") or "",
            tool_calls=calls,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            cost=self._cost(usage),
            finish_reason=choice.get("finish_reason") or "",
            raw=data,
        )

    def _cost(self, usage: dict) -> float:
        pin, pout = self.price
        return (
            usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout
        ) / 1_000_000


class FakeModel:
    """零成本冒烟测试：按脚本吐工具调用，不发网络请求。

    轨迹里也会留下 ``model.fake`` 事件，报告据此声明"这次没有花 token"。
    """

    name = "fake"

    def __init__(self, script: list[Completion] | None = None):
        self.script = list(script or [])
        self.calls = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.calls += 1
        self.seen.append(json.loads(json.dumps(messages)))
        if self.script:
            return self.script.pop(0)
        return Completion(content="done", finish_reason="stop")
