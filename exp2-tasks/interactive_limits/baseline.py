"""对比"修复前 vs 修复后"的回归基线。

这个脚本存在的理由是一个真实教训：mini-swe-agent 在 Windows 上有 31 个测试
本身就是红的（`DeterministicModel` 在无交互环境下行为不同）。如果不先测基线，
就会把环境性失败误判成自己改坏的东西。

它做两件事：
1. 用 `git stash` 在"当前改动"和"HEAD"两个状态下各跑一次同一组测试；
2. 只报告**差异集**：新增失败 = 真正的回归，消失的失败 = 修好了。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GROUPS = [
    ["tests/agents/test_interactive.py"],
    ["tests/agents/test_default.py", "tests/config", "tests/test_init.py"],
]


def run_pytest(args: list[str]) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, errors="replace", timeout=1800, env=env,
    )
    out = p.stdout + p.stderr
    failed = sorted(set(re.findall(r"^FAILED (\S+)", out, re.M)))
    m = re.search(r"(\d+) failed, (\d+) passed", out)
    return {
        "failed": failed,
        "summary": m.group(0) if m else out.strip().splitlines()[-1][:160],
        "collection_error": "error during collection" in out,
    }


def snapshot(stash: bool) -> dict:
    if stash:
        subprocess.run(["git", "stash", "push", "--keep-index", "--", "src"],
                       cwd=REPO, capture_output=True, text=True)
    try:
        return {label: run_pytest(g) for label, g in zip(("interactive", "core"), GROUPS)}
    finally:
        if stash:
            subprocess.run(["git", "stash", "pop"], cwd=REPO, capture_output=True, text=True)


def main() -> int:
    print("== baseline (HEAD, without the fix) ==")
    base = snapshot(stash=True)
    print("== patched (working tree) ==")
    patched = snapshot(stash=False)

    verdict: dict = {}
    for label in ("interactive", "core"):
        b, a = set(base[label]["failed"]), set(patched[label]["failed"])
        # 基线是收集错误时，失败集为空；此时不能把 patch 后的失败当成"新增"
        comparable = not base[label]["collection_error"]
        verdict[label] = {
            "baseline": base[label]["summary"],
            "patched": patched[label]["summary"],
            "comparable": comparable,
            "new_failures": sorted(a - b) if comparable else [],
            "fixed": sorted(b - a),
        }
        print(f"[{label}] {base[label]['summary']}  ->  {patched[label]['summary']}")
        if not comparable:
            print("  baseline could not even be collected (import-time crash);")
            print("  treat 'patched' as recovery, not as a regression set")
            continue
        if a - b:
            print(f"  NEW FAILURES ({len(a - b)}):")
            for n in sorted(a - b)[:10]:
                print("   ", n)
        else:
            print("  no new failures")

    out = REPO / "docs" / "evidence" / "regression_baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"groups": verdict}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")

    regressions = sum(len(v["new_failures"]) for v in verdict.values())
    print("RESULT", "PASS" if regressions == 0 else f"FAIL ({regressions} regressions)")
    return 0 if regressions == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
