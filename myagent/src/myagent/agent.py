"""Agent 循环（H1）。

四条不变量，``tests/test_loop.py`` 逐条断言：

1. 一个 step 只发一次模型请求；
2. 历史 append-only，旧消息永不被改写；
3. 每个 ``assistant.tool_calls`` 条目都对应恰好一条 ``role=tool`` 消息，
   且 ``tool_call_id`` 相同——被跳过、被拦截的调用也要有；
4. ``content`` 不是结束信号。只有 ``submit``、超限、或模型不再调用工具时
   才停。

停止优先级：``submit > max_steps > max_cost/max_seconds > 无工具调用``。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from myagent.model import Model, ToolCall
from myagent.tools.base import ToolContext
from myagent.tools.registry import ToolRegistry

SUBMIT = "Submitted"
MAX_STEPS = "MaxSteps"
MAX_COST = "MaxCost"
MAX_SECONDS = "MaxSeconds"
FINAL_ANSWER = "FinalAnswer"
MODEL_ERROR = "ModelError"


@dataclass
class RunResult:
    exit_status: str
    submission: str = ""
    steps: int = 0
    cost: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)
    compactions: list[dict] = field(default_factory=list)
    stalls: int = 0
    session_path: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_status": self.exit_status,
            "submission": self.submission,
            "steps": self.steps,
            "cost": round(self.cost, 6),
            "usage": self.usage,
            "n_compactions": len(self.compactions),
            "n_stall_nudges": self.stalls,
            "error": self.error,
        }


class Agent:
    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        *,
        system_prompt: str,
        instance_prompt: str,
        workspace: Path,
        max_steps: int = 30,
        max_cost: float = 0.5,
        max_seconds: int = 600,
        timeout: int = 60,
        logger=None,
        context_limit: int = 120_000,
        compact_keep_recent: int = 6,
        compact_strategy: str = "summarize",
        stall_reminder_after: int = 6,
        python_exe: str | None = None,
        safety: bool = True,
        messages: list[dict] | None = None,
    ):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.instance_prompt = instance_prompt
        self.workspace = Path(workspace).resolve()
        self.max_steps = max_steps
        self.max_cost = max_cost
        self.max_seconds = max_seconds
        self.context_limit = context_limit
        self.compact_keep_recent = compact_keep_recent
        self.compact_strategy = compact_strategy
        self.stall_reminder_after = stall_reminder_after
        self.safety = safety
        self.logger = logger

        self.ctx = ToolContext(
            workspace=self.workspace,
            timeout=timeout,
            max_output_chars=8_000,
            safety=safety,
            **({"python_exe": python_exe} if python_exe else {}),
        )
        self.messages: list[dict] = list(messages or [])
        self.steps = 0
        self.cost = 0.0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.compactions: list[dict] = []
        self.stalls = 0
        self.stop_reason = ""
        self.submission = ""
        self.error = ""
        self._final_answer = False
        self._start = time.time()
        # 连续"只读"步数：模型在 checker 已通过后还反复渲染预览图时，这一计数
        # 会在第 N 次时触发一次提醒（审阅稿第 4 节）。
        self._idle_steps = 0
        self._reminded = False

    # ------------------------------------------------------------------ 入口
    @property
    def elapsed(self) -> float:
        return time.time() - self._start

    def run(self) -> RunResult:
        self._prepare_history()
        self._log("run_start", workspace=str(self.workspace), model=getattr(self.model, "name", "?"),
                  tools=self.tools.names(), max_steps=self.max_steps)
        status = ""
        try:
            while True:
                status = self.should_stop()
                if status:
                    break
                self.step()
        except Exception as e:  # noqa: BLE001 - 记录后如实返回，便于 trace
            self.error = f"{type(e).__name__}: {e}"
            status = MODEL_ERROR
        self.stop_reason = status
        self._log(
            "run_end",
            exit_status=status,
            steps=self.steps,
            cost=round(self.cost, 6),
            usage=self.usage,
            elapsed=round(self.elapsed, 2),
            error=self.error,
        )
        return RunResult(
            exit_status=status,
            submission=self.submission,
            steps=self.steps,
            cost=self.cost,
            usage=self.usage,
            messages=self.messages,
            compactions=self.compactions,
            stalls=self.stalls,
            error=self.error,
        )

    def _prepare_history(self) -> None:
        if self.messages:
            return
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.instance_prompt},
        ]

    # ------------------------------------------------------------------ 停止
    def should_stop(self) -> str:
        """按优先级返回停止原因；空字符串表示继续。顺序本身就是规格。"""
        if self.ctx.submitted:
            self.submission = self.ctx.submission
            return SUBMIT
        if self.max_steps > 0 and self.steps >= self.max_steps:
            return MAX_STEPS
        if self.max_cost > 0 and self.cost >= self.max_cost:
            return MAX_COST
        if self.max_seconds > 0 and self.elapsed >= self.max_seconds:
            return MAX_SECONDS
        # 最低优先级：模型不再调工具。放在最后，保证"超限"优先于"自然结束"。
        if self._final_answer:
            return FINAL_ANSWER
        return ""

    # ------------------------------------------------------------------ 一步
    def step(self) -> None:
        self.steps += 1
        completion = self.model.chat(self.messages, self.tools.schemas())
        self.cost += completion.cost
        for k, v in completion.usage.items():
            self.usage[k] = self.usage.get(k, 0) + v
        self.messages.append(completion.as_message())
        self._log(
            "model_response",
            step=self.steps,
            content=completion.content,
            tool_calls=[{"id": c.id, "name": c.name, "raw_args": c.raw_args[:400]}
                        for c in completion.tool_calls],
            usage=completion.usage,
            cost=round(completion.cost, 6),
            finish_reason=completion.finish_reason,
        )

        if not completion.tool_calls:
            # 没有工具调用 = 模型认为无事可做。这不是成功，只是"结束"。
            # 用标志位而不是 return，是为了让 should_stop 仍是唯一出口。
            self._final_answer = True
            return

        self._execute_all(completion.tool_calls)

    def _execute_all(self, calls: list[ToolCall]) -> None:
        for call in calls:
            self._log("tool_call", step=self.steps, id=call.id, name=call.name,
                      raw_args=call.raw_args[:2000])
            if self.ctx.submitted:
                # submit 之后的调用不再执行，但**必须**补一条 tool 消息，
                # 否则下一轮请求会因为缺少 tool_call_id 而被 API 拒绝。
                self.messages.append(self._tool_message(call, "skipped: run already submitted"))
                self._log("tool_result", id=call.id, name=call.name, ok=False,
                          returncode=0, output="", error="skipped after submit",
                          skipped=True, truncated=False)
                continue

            result = self.tools.execute(call.name, call.raw_args, self.ctx)
            text = result.as_observation()
            self.messages.append(self._tool_message(call, text))
            self._log(
                "tool_result",
                id=call.id,
                name=call.name,
                ok=result.ok,
                returncode=result.returncode,
                output=result.output[:4000],
                error=result.error,
                truncated=result.truncated,
                skipped=False,
            )

    @staticmethod
    def _tool_message(call: ToolCall, text: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": text}

    # ------------------------------------------------------------------ 日志
    def _log(self, kind: str, **payload) -> None:
        if self.logger is None:
            return
        self.logger.log(kind, **payload)


def dump_messages(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False, indent=2)
