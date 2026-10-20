"""精确编辑的边界。

每个断言对应一种"模型真实会犯的错"：文本重复、只给片段、想改 CRLF 文件、
以及"我以为改了其实没改"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myagent.tools.base import ToolContext
from myagent.tools.edit import EditFileTool
from myagent.tools.registry import ToolRegistry


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, timeout=30, python_exe=sys.executable)


def run(ctx: ToolContext, **args):
    return ToolRegistry([EditFileTool()]).execute("edit_file", args, ctx)


def test_replaces_single_unique_occurrence(ctx: ToolContext):
    f = ctx.workspace / "a.py"
    f.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="return 1", new="return 42")
    assert r.ok and "return 42" in f.read_text(encoding="utf-8")


def test_ambiguous_text_is_rejected_with_line_numbers(ctx: ToolContext):
    f = ctx.workspace / "a.py"
    f.write_text("x = 1\ny = 1\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="= 1", new="= 2")
    assert (r.ok, "appears 2 times" in r.error) == (False, True)
    assert "lines" in r.error  # 必须告诉模型去哪儿看
    assert f.read_text(encoding="utf-8") == "x = 1\ny = 1\n"  # 原文件未变


def test_replace_all_needs_explicit_flag(ctx: ToolContext):
    f = ctx.workspace / "a.py"
    f.write_text("a = 1\nb = 1\nc = 1\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="= 1", new="= 9", all=True)
    assert r.ok and f.read_text(encoding="utf-8") == "a = 9\nb = 9\nc = 9\n"


def test_missing_text_reports_zero_occurrences(ctx: ToolContext):
    (ctx.workspace / "a.py").write_text("hello\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="goodbye", new="hi")
    assert (r.ok, "not found" in r.error) == (False, True)


def test_identical_old_and_new_is_refused(ctx: ToolContext):
    (ctx.workspace / "a.py").write_text("hello\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="hello", new="hello")
    assert (r.ok, "identical" in r.error) == (False, True)


def test_crlf_line_endings_are_preserved(ctx: ToolContext):
    f = ctx.workspace / "crlf.txt"
    f.write_bytes(b"line1\r\nline2\r\nline3\r\n")
    r = run(ctx, path="crlf.txt", old="line2", new="LINE2")
    assert r.ok
    assert f.read_bytes() == b"line1\r\nLINE2\r\nline3\r\n"


def test_lf_needle_matches_a_crlf_file(ctx: ToolContext):
    """模型几乎总是给 LF 的 old/new，而 Windows 上的文件常是 CRLF。

    不做归一化的话这条会 100% 失败，而现象是"文本找不到"，非常难排查。
    """
    f = ctx.workspace / "crlf.py"
    f.write_bytes(b"def f():\r\n    return 1\r\n\r\n\r\ndef g():\r\n    return 2\r\n")
    r = run(ctx, path="crlf.py", old="    return 1\n", new="    return 100\n")
    assert r.ok
    assert f.read_bytes() == b"def f():\r\n    return 100\r\n\r\n\r\ndef g():\r\n    return 2\r\n"


def test_no_trailing_newline_is_preserved(ctx: ToolContext):
    f = ctx.workspace / "x.txt"
    f.write_bytes(b"alpha\nbeta")
    assert run(ctx, path="x.txt", old="beta", new="BETA").ok
    assert f.read_bytes() == b"alpha\nBETA"


def test_reports_lines_changed_not_file_size(ctx: ToolContext):
    f = ctx.workspace / "a.py"
    f.write_text("\n".join(f"row{i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    r = run(ctx, path="a.py", old="row7", new="row7_edited")
    assert r.ok
    assert r.metadata["lines_changed"] == 1
    assert len(f.read_text(encoding="utf-8").splitlines()) == 20


def test_untouched_regions_keep_formatting(ctx: ToolContext):
    body = "  indented with spaces  \n\ttab line\n\n\nthree blank lines above\n"
    f = ctx.workspace / "fmt.txt"
    f.write_text(body, encoding="utf-8")
    r = run(ctx, path="fmt.txt", old="indented", new="INDENTED")
    assert r.ok
    assert f.read_text(encoding="utf-8") == body.replace("indented", "INDENTED")


def test_edit_outside_workspace_is_rejected(ctx: ToolContext):
    r = run(ctx, path="../x.txt", old="a", new="b")
    assert (r.ok, "escapes workspace" in r.error) == (False, True)


def test_edit_does_not_require_rewriting_whole_file(ctx: ToolContext):
    """大文件里的小改动：写入量应与改动量同阶，而不是整篇重写。"""
    f = ctx.workspace / "big.txt"
    original = ("filler line\n" * 5000) + "TARGET\n" + ("filler line\n" * 5000)
    f.write_text(original, encoding="utf-8")
    r = run(ctx, path="big.txt", old="TARGET", new="REPLACED")
    assert r.ok
    new = f.read_text(encoding="utf-8")
    assert len(new) == len(original) - len("TARGET") + len("REPLACED")
    assert new.count("filler line") == original.count("filler line")
