"""验收脚本：mini-swe-agent human 模式的限额与调用计数。

为什么不用 pytest 直接写：第一个 bug 会让 ``import`` 本身失败，pytest 连收集
阶段都过不去。所以这里用子进程分步检查，哪一步红就报哪一步。

第二个 bug 是"停不下来"，它会真的把检查脚本拖到超时。因此限额检查里加了一个
上限：最多喂 6 条用户命令，超过就判定为"没有停下"。

用法：
    python repro.py                 # 两步都跑
    python repro.py --only import   # 只跑第一步
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent

IMPORT_PROBE = """
import sys
sys.path.insert(0, {root!r})
import minisweagent.agents.interactive as m
print("IMPORT_OK", m.__name__)
"""

LIMIT_PROBE = """
import sys, json
sys.path.insert(0, {root!r})
from unittest.mock import patch

from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import LimitsExceeded
from minisweagent.models.test_models import DeterministicModel

model = DeterministicModel(outputs=[])
env = LocalEnvironment(cwd={cwd!r}, timeout=10)

fed = {{"n": 0}}
result = {{"stopped": False, "n_calls": 0, "fed": 0}}

def fake_prompt(*a, **k):
    # 这里必须有上界：bug 的表现就是"永远不停"，没有上界会把检查拖成超时
    if fed["n"] >= 6:
        result["exhausted"] = True
        raise SystemExit(0)
    fed["n"] += 1
    return "echo step{{0}}".format(fed["n"])

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
        info = agent.run("t")
        result["exit_status"] = (info or {{}}).get("exit_status", "<none>")
        if result["exit_status"] == "LimitsExceeded":
            result["stopped"] = True
    except LimitsExceeded:
        result["stopped"] = True
        result["exit_status"] = "LimitsExceeded"
    except SystemExit:
        pass
    except Exception as e:
        result["error"] = f"{{type(e).__name__}}: {{e}}"

result["n_calls"] = agent.n_calls
result["fed"] = fed["n"]
print("LIMIT_RESULT " + json.dumps(result))
"""


def run_import_probe(root: Path) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-c", IMPORT_PROBE.format(root=str(root))],
                       capture_output=True, text=True, errors="replace", timeout=120)
    ok = p.returncode == 0 and "IMPORT_OK" in p.stdout
    tail = (p.stdout + p.stderr).strip().splitlines()[-2:]
    return ok, " | ".join(tail)


def run_limit_probe(root: Path) -> tuple[bool, dict]:
    p = subprocess.run([sys.executable, "-c", LIMIT_PROBE.format(root=str(root), cwd=str(HERE))],
                       capture_output=True, text=True, errors="replace", timeout=180)
    # rich 会在前面加边框与提示符，所以按子串找而不是按行首找
    m = re.search(r"LIMIT_RESULT (\{.*\})", p.stdout + p.stderr)
    if not m:
        return False, {"error": " | ".join((p.stdout + p.stderr).strip().splitlines()[-3:])}
    data = json.loads(m.group(1))
    # 期望：第一步之后就停（step_limit=1）。两种合法表现都算通过：
    #   a) 命令本身触发限额，run() 抛 LimitsExceeded（stopped=true）
    #   b) 命令执行完、下一轮请求前被拦下（fed=1 且 n_calls=1，不再索取第二条）
    stopped = data.get("stopped") is True or (
        data.get("fed") == 1 and data.get("n_calls", 0) == 1 and not data.get("exhausted")
    )
    ok = stopped and data.get("n_calls", 0) >= 1 and data.get("fed", 0) <= 2
    return ok, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["import", "limit"])
    ap.add_argument("--root", default=None, help="mini-swe-agent 源码根目录（含 src/）")
    args = ap.parse_args()

    root = Path(args.root) if args.root else HERE.parents[1] / "src"
    failures = 0

    if args.only in (None, "import"):
        ok, detail = run_import_probe(root)
        print(f"{'PASS' if ok else 'FAIL'} step1 import    {detail}")
        failures += 0 if ok else 1
        if not ok:
            print("     -> 无法 import，限额检查跳过（先修这个问题）")
            print("RESULT FAIL (1)")
            return 1

    if args.only in (None, "limit"):
        ok, data = run_limit_probe(root)
        print(f"{'PASS' if ok else 'FAIL'} step2 limits    {json.dumps(data, ensure_ascii=False)}")
        if not ok:
            failures += 1
            print(textwrap.indent(
                "期望：step_limit=1 时，human 模式在第一次用户命令之后停下（抛 LimitsExceeded），\n"
                "      n_calls 随每次实际动作递增，且不会继续索取第二条命令。",
                "     ",
            ))

    print("RESULT", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
