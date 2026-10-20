"""Quest 01 检查器。

只看"这四个值是不是真的来自这台机器"。不评分，不做模糊匹配：
`python_executable` 必须能被执行且版本对得上，否则就是手写的字符串。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    path = HERE / "env.json"
    if not path.is_file():
        print(f"FAIL quest01: {path.name} not found")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL quest01: env.json is not valid JSON ({e})")
        return 1

    problems: list[str] = []
    for key in ("python_executable", "python_version", "platform", "shell"):
        if not str(data.get(key, "")).strip():
            problems.append(f"missing field `{key}`")

    exe = str(data.get("python_executable", ""))
    if exe:
        if not Path(exe).is_file():
            problems.append(f"python_executable is not a file: {exe}")
        else:
            # 唯一能区分"真的探测"和"抄提示词"的办法：现场跑一次
            out = subprocess.run(
                [exe, "-c", "import platform;print(platform.python_version())"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=60,
            )
            if out.returncode != 0:
                problems.append(f"python_executable failed to run: {out.stderr[:200]}")
            elif out.stdout.strip() != str(data.get("python_version", "")).strip():
                problems.append(
                    f"version mismatch: env.json says {data.get('python_version')}, "
                    f"executable reports {out.stdout.strip()}"
                )
            if Path(exe).stat().st_size < 1024:
                problems.append(f"python_executable is a 0-byte alias: {exe}")

    shell = str(data.get("shell", ""))
    if shell and exe and shell.strip() == exe.strip():
        problems.append("`shell` must not be the python executable")

    if problems:
        print("FAIL quest01")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        "PASS quest01  "
        f"python={data['python_executable']}  version={data['python_version']}  "
        f"platform={data['platform']}  shell={data['shell']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
