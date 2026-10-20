"""验收脚本：mini-swe-agent human 模式的限额与调用计数。

为什么不用 pytest 直接写：第一步的 bug 会让 ``import`` 本身失败，
pytest 连收集阶段都过不去。所以这里用子进程分两步检查，哪一步红就报哪一步。

用法：
    python repro.py                     # 跑全部两步
    python repro.py --only import       # 只跑第一步
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------- step 1
IMPORT_PROBE = """
import sys
sys.path.insert(0, {root!r})
import minisweagent.agents.interactive as m
print("IMPORT_OK", m.__name__)
"""

# --------------------------------------------------------------------- step 2
LIMIT_PROBE = """
import sys, json
sys.path.insert(0, {root!r})
from unittest.mock import patch

from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import LimitsExceeded
from minisweagent.models.test_models import DeterministicModel

# human 模式不使用模型；给它一个空的确定性模型以复用默认模板
model = DeterministicModel(outputs=[])
env = LocalEnvironment(cwd={cwd!r}, timeout=10)

script = iter(["echo one", "echo two", "echo three", "echo four"])
result = {{"calls": 0, "stopped": False, "n_calls": 0, "submission": ""}}

def fake_prompt(*a, **k):
    try:
        return next(script)
    except StopIteration:
        result["ran_out"] = True
        return "echo done"

with patch("minisweagent.agents.utils.prompt_user.prompt_session.prompt", side_effect=fake_prompt), \\
     patch("minisweagent.agents.utils.prompt_user._multiline_prompt_session.prompt", side_effect=fake_prompt):
    agent = InteractiveAgent(
        model, env,
        system_template="{{task}}",
        instance_template="{{task}}",
        mode="human",
        step_limit=1,
        cost_limit=100.0,
    )
    try:
        agent.run("t")
    except LimitsExceeded:
        result["stopped"] = True
    except Exception as e:
        result["error"] = f"{{type(e).__name__}}: {{e}}"

result["n_calls"] = agent.n_calls
result["n_messages"] = len(agent.messages)
print("LIMIT_RESULT " + json.dumps(result))
"""


def run_import_probe(root: Path) -> tuple[bool, str]:
    code = IMPORT_PROBE.format(root=str(root))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       errors="replace", timeout=120)
    ok = p.returncode == 0 and "IMPORT_OK" in p.stdout
    tail = (p.stdout + p.stderr).strip().splitlines()[-3:]
    return ok, " | ".join(tail)


def run_limit_probe(root: Path) -> tuple[bool, dict]:
    code = LIMIT_PROBE.format(root=str(root), cwd=str(HERE))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       errors="replace", timeout=120)
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("LIMIT_RESULT ")), "")
    if not line:
        return False, {"error": "|".join((p.stdout + p.stderr).strip().splitlines()[-3:])}
    data = json.loads(line[len("LIMIT_RESULT "):])
    # 期望：第 1 步就触发限额；n_calls 至少为 1（用户命令也算一步）
    ok = data.get("stopped") is True and data.get("n_calls", 0) >= 1
    return ok, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["import", "limit"], default=None)
    ap.add_argument("--root", default=None, help="mini-swe-agent 源码根目录（含 src/）")
    args = ap.parse_args()

    root = Path(args.root) if args.root else HERE.parents[1] / "src"
    failures = 0

    if args.only in (None, "import"):
        ok, detail = run_import_probe(root)
        print(f"{'PASS' if ok else 'FAIL'} step1 import    {detail}")
        failures += 0 if ok else 1
        if not ok:
            print("     -> 无法 import，后续限额检查跳过（先修这个问题）")
            return 1

    if args.only in (None, "limit"):
        ok, data = run_limit_probe(root)
        print(f"{'PASS' if ok else 'FAIL'} step2 limits    {json.dumps(data, ensure_ascii=False)}")
        if not ok:
            failures += 1
            detail = textwrap.dedent(
                """
                期望：step_limit=1 时，human 模式的第一次用户命令之后就应停下
                      （抛 LimitsExceeded），且 n_calls 随每次实际动作递增。
                实际：见上面的 JSON。
                """
            ).strip()
            print(textwrap.indent(detail, "     "))

    print("RESULT", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
