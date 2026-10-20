"""环境探测：解释器、shell、平台。

这一层的存在理由很实际：Windows 上 ``python`` 常常是应用商店的 0 字节别名，
``sys.executable`` 才是真的解释器。Quest 01 的检查器就是拿这里的值做比对，
所以它必须是"实测"而不是"手写常量"。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Environment:
    platform: str
    platform_release: str
    machine: str
    python_executable: str
    python_version: str
    python_impl: str
    shell: str
    shell_version: str
    cwd: str
    path_sep: str

    def as_dict(self) -> dict:
        return asdict(self)


def _shell() -> tuple[str, str]:
    if os.name == "nt":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        try:
            out = subprocess.run(
                [exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = "unknown"
        return exe, out or "unknown"
    shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    try:
        out = subprocess.run(
            [shell, "-c", "echo $BASH_VERSION"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = "unknown"
    return shell, out or "unknown"


def detect(cwd: Path | None = None) -> Environment:
    shell, shell_version = _shell()
    return Environment(
        platform=platform.system(),
        platform_release=platform.release(),
        machine=platform.machine(),
        python_executable=sys.executable,
        python_version=platform.python_version(),
        python_impl=platform.python_implementation(),
        shell=shell,
        shell_version=shell_version,
        cwd=str(Path.cwd() if cwd is None else cwd),
        path_sep=os.sep,
    )


def which_python_alias() -> str:
    """诊断"为什么 doctor 里 python 没输出"：返回 PATH 上 python 的真实路径。"""
    found = shutil.which("python") or ""
    return found
