"""压缩策略对比：同一份历史，三种策略 × 两个预算。

产出 ``docs/evidence/compaction_ab.json``，报告里的表格直接读它。
关键不是"省了多少 token"，而是**压缩之后还能不能跑完任务**——所以第三列
是"任务是否仍然完成"，不是压缩率。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from myagent.agent import Agent  # noqa: E402
from myagent.context import estimate_context_tokens, maybe_compact  # noqa: E402
from myagent.model import Completion, ToolCall  # noqa: E402
from myagent.tools.files import ListDirTool, ReadFileTool, WriteFileTool  # noqa: E402
from myagent.tools.registry import ToolRegistry  # noqa: E402
from myagent.tools.submit import SubmitTool  # noqa: E402

STEPS = 14        # 总模型调用次数
WRITES = 6        # 其中前 6 次写文件
PAYLOAD = 12000   # 每次写入的字符数，让上下文真的会爆（6000 token 预算 ≈ 24000 西文字符）

# 关键：工具的 observation 只回显"wrote N chars"，所以写小文件**不会**撑爆上下文。
# 真正撑爆上下文的是反复读取大文件。所以第 8 步之后改为 read_file 把大段
# 工具输出塞进历史。
READ_BACK = STEPS - WRITES


class ScriptedModel:
    """第 n 次调用写第 n 个文件；写满之后 submit。

    这个脚本刻意不依赖历史内容——否则压缩把历史改掉就会改变行为，
    测出来的就不是"压缩的影响"而是"压缩改变了任务"。
    """

    name = "scripted"

    def __init__(self, steps: int = STEPS):
        self.steps = steps
        self.calls = 0
        self.max_prompt_chars = 0

    def chat(self, messages, tools):
        self.calls += 1
        self.max_prompt_chars = max(
            self.max_prompt_chars, sum(len(str(m.get("content") or "")) for m in messages)
        )
        i = self.calls
        if i <= WRITES:
            return Completion(
                tool_calls=[
                    ToolCall(
                        id=f"c{i}",
                        name="write_file",
                        args=None,
                        raw_args=json.dumps(
                            {"path": f"f{i}.txt", "content": f"{i}:" + "z" * PAYLOAD}
                        ),
                    )
                ]
            )
        if i < STEPS:
            # 读回刚写的大文件：每一步都把 ~PAYLOAD 字符塞进历史
            target = i - WRITES
            return Completion(
                tool_calls=[
                    ToolCall(id=f"r{i}", name="read_file", args=None,
                             raw_args=json.dumps({"path": f"f{target}.txt"}))
                ]
            )
        return Completion(
            tool_calls=[ToolCall(id="done", name="submit", args=None,
                                 raw_args='{"summary": "wrote all files"}')]
        )


def run_case(limit: int, strategy: str, ws: Path) -> dict:
    model = ScriptedModel()
    agent = Agent(
        model,
        ToolRegistry([WriteFileTool(), ReadFileTool(), ListDirTool(), SubmitTool()]),
        system_prompt="sys",
        instance_prompt="write files",
        workspace=ws,
        max_steps=40,
        context_limit=limit,
        compact_keep_recent=4,
        compact_strategy=strategy,
    )
    result = agent.run()
    files = len(list(ws.glob("f*.txt")))
    return {
        "limit": limit,
        "strategy": strategy,
        "exit_status": result.exit_status,
        "steps": result.steps,
        "files_written": files,
        "task_complete": result.exit_status == "Submitted" and files == WRITES,
        "n_compactions": len(result.compactions),
        # 策略为 none 时没有压缩记录，报 0 而不是拿别的值占位
        "tokens_saved_pct": round(
            sum(c["saved_pct"] for c in result.compactions) / len(result.compactions), 1
        ) if result.compactions else 0.0,
        "peak_prompt_chars": model.max_prompt_chars,
        "compactions": result.compactions,
    }

def static_comparison() -> list[dict]:
    """同一份历史、三种策略的纯函数级对比（不含模型）。"""
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "the task"},
    ]
    for i in range(20):
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "write_file", "arguments": f'{{"i": {i}}}'}}],
        })
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "name": "write_file",
                     "content": "x" * 400})
    rows = [{"strategy": "none", "tokens": estimate_context_tokens(msgs),
             "kept_messages": len(msgs), "saved_pct": 0.0}]
    for strategy in ("truncate", "summarize"):
        out, info = maybe_compact(msgs, limit=2000, keep_recent=4, strategy=strategy)
        rows.append({
            "strategy": strategy,
            "tokens": estimate_context_tokens(out),
            "kept_messages": len(out),
            "saved_pct": round(info.saved_pct, 1) if info else 0.0,
        })
    return rows


def main() -> int:
    import tempfile

    rows = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for limit in (120_000, 6_000):
            for strategy in ("none", "truncate", "summarize"):
                ws = root / f"{limit}_{strategy}"
                ws.mkdir()
                rows.append(run_case(limit, strategy, ws))

    out = {
        "static": static_comparison(),
        "runs": rows,
    }
    path = ROOT / "docs" / "evidence" / "compaction_ab.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'limit':>8} {'strategy':<12} {'exit':<10} {'steps':>5} {'files':>5} "
          f"{'compactions':>12} {'complete'}")
    for r in rows:
        print(f"{r['limit']:>8} {r['strategy']:<12} {r['exit_status']:<10} {r['steps']:>5} "
              f"{r['files_written']:>5} {r['n_compactions']:>12} {r['task_complete']}")
    print(f"wrote {path}")
    return 0 if all(r["task_complete"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
