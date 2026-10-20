"""Quest 02 检查器：只看结构，不评分。

刻意不做"画得好不好"的判断——那不可自动化，而且会诱导模型去猜评分口径。
这里只回答一个问题：这个文件在浏览器里打开时，会不会真的动起来。
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVG_NS = "{http://www.w3.org/2000/svg}"
SHAPES = {"rect", "circle", "ellipse", "path", "polygon", "polyline", "line", "text"}


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def main() -> int:
    path = HERE / "pelican.svg"
    if not path.is_file():
        print(f"FAIL quest02: {path.name} not found")
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"FAIL quest02: not valid XML ({e})")
        return 1

    problems: list[str] = []
    if _local(root.tag) != "svg":
        problems.append(f"root element is <{_local(root.tag)}>, expected <svg>")

    tags = [_local(e.tag) for e in root.iter()]
    shapes = [t for t in tags if t in SHAPES]
    circles = tags.count("circle")
    colours = {
        c.lower()
        for c in re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", text)
        + re.findall(r'fill="([a-z]+)"', text)
    }
    animations = tags.count("animate") + tags.count("animateTransform") + tags.count("animateMotion")
    keyframes = "@keyframes" in text

    if circles < 2:
        problems.append(f"expected at least 2 circles (wheels), found {circles}")
    if len(shapes) < 8:
        problems.append(f"expected at least 8 drawing elements, found {len(shapes)}")
    if len(colours) < 3:
        problems.append(f"expected at least 3 colours, found {len(colours)}: {sorted(colours)}")
    if not (tags.count("title") or tags.count("desc")):
        problems.append("no <title> or <desc>")
    if re.search(r"<image[^>]+href=\"https?:", text):
        problems.append("external image reference found")
    if animations == 0 and not keyframes:
        problems.append("no animation (animate / animateTransform / @keyframes)")

    if problems:
        print("FAIL quest02")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        f"PASS quest02  elements={len(shapes)}  colours={len(colours)}  "
        f"circles={circles}  animations={animations}{' + css' if keyframes else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
