"""精确编辑 vs 整体重写：改动量、写入量、格式保留。

回答一个具体问题：「改一行」到底应该写多少字节？
用同一份文件、同一处改动，分别走 edit_file 与 write_file 全量重写，
对比真正落盘的字节数与受影响行数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from myagent.tools.base import ToolContext  # noqa: E402
from myagent.tools.edit import EditFileTool  # noqa: E402
from myagent.tools.files import WriteFileTool  # noqa: E402
from myagent.tools.registry import ToolRegistry  # noqa: E402

LINES = 800
MARK = "    return self.value  # changed\n"


def sample() -> str:
    parts = ["class Widget:\n", "    def __init__(self):\n", "        self.value = 0\n\n"]
    for i in range(LINES):
        parts.append(f"    def method_{i}(self, x):\n")
        parts.append(f"        return x * {i}\n\n")
    parts.append("    def target(self):\n")
    parts.append("    return self.value\n")
    return "".join(parts)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        ctx = ToolContext(workspace=ws, timeout=30, python_exe=sys.executable)
        reg = ToolRegistry([WriteFileTool(), EditFileTool()])

        src = sample()

        # A. 精确编辑
        a = ws / "edit.py"
        a.write_text(src, encoding="utf-8")
        before_bytes = a.stat().st_size
        r = reg.execute(
            "edit_file",
            json.dumps({"path": "edit.py", "old": "    return self.value\n", "new": MARK}),
            ctx,
        )
        edit_bytes = a.stat().st_size - before_bytes
        edit_meta = r.metadata

        # B. 整体重写（模型把全文读出来、改一行、再整篇写回）
        b = ws / "rewrite.py"
        b.write_text(src, encoding="utf-8")
        rewritten = src.replace("    return self.value\n", MARK)
        r2 = reg.execute(
            "write_file", json.dumps({"path": "rewrite.py", "content": rewritten}), ctx
        )
        rewrite_bytes = r2.metadata["bytes"]

        result = {
            "file_lines": len(src.splitlines()),
            "file_bytes": len(src.encode("utf-8")),
            "edit_file": {
                "chars_written": int(edit_bytes),
                "lines_changed": edit_meta["lines_changed"],
                "occurrences": edit_meta["occurrences"],
            },
            "write_file": {
                "chars_written": rewrite_bytes,
                "lines_changed": len(src.splitlines()),
            },
            "ratio": round(rewrite_bytes / max(edit_bytes, 1), 1),
            "content_equal": a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8"),
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    # 精确编辑必须写得更少、且结果与整体重写完全一致
    ok = (
        result["content_equal"]
        and result["edit_file"]["chars_written"] < result["write_file"]["chars_written"] // 10
        and result["edit_file"]["lines_changed"] == 1
    )
    print("RESULT", "PASS" if ok else "FAIL")

    out = ROOT / "docs" / "evidence" / "edit_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
