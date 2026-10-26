"""项目指令注入：AGENTS.md 只在存在时追加，不影响无文件的仓库。"""

from __future__ import annotations

from pathlib import Path

from myagent.environment import Environment
from myagent.prompts import build_system_prompt

ENV = Environment(
    platform="Windows",
    platform_release="10",
    machine="AMD64",
    python_executable=r"D:\conda\envs\main\python.exe",
    python_version="3.11.14",
    python_impl="CPython",
    shell="powershell",
    shell_version="5.1",
    cwd=".",
    path_sep="\\",
)


def test_without_instructions_prompt_is_unchanged():
    prompt = build_system_prompt(ENV, ["run_python"])
    assert "Project instructions" not in prompt
    assert "run_python" in prompt


def test_instructions_are_appended():
    prompt = build_system_prompt(ENV, ["run_python"], "  use D:\\conda\\envs\\main\\python.exe  ")
    assert "# Project instructions (AGENTS.md)" in prompt
    assert prompt.rstrip().endswith("use D:\\conda\\envs\\main\\python.exe")


def test_blank_instructions_are_ignored():
    assert "Project instructions" not in build_system_prompt(ENV, [], "   \n  ")


def test_cli_reads_agents_md(tmp_path: Path):
    from myagent.cli import _read_project_instructions

    assert _read_project_instructions(tmp_path) == ""
    (tmp_path / "AGENTS.md").write_text("# rules\nno python alias", encoding="utf-8")
    assert "no python alias" in _read_project_instructions(tmp_path)
