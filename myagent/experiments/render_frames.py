"""在真实浏览器里定格动画帧，并验证"它真的在动"。

为什么不用转码工具直接出图：SMIL 的时间轴只有浏览器会真正驱动。这里用
Chromium 打开文件，``pauseAnimations()`` + ``setCurrentTime(t)`` 定格再截图，
所以报告里那几张图的时间点可复现。

"是否在动"用像素差判定，不看 DOM：只要两帧之间有足够多的像素不同，
就一定有时间轴在推动画面。这比找某个特定分组的变换矩阵稳，因为模型写
动画的方式很多（SMIL / CSS / 不同层级的分组）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

SVG = Path(sys.argv[1] if len(sys.argv) > 1 else "quests/02_pelican_bicycle/pelican.svg")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/evidence")
TIMES = [0.0, 0.55, 1.1]


def freeze(t: float) -> str:
    return (
        "() => { const s = document.documentElement;"
        f" s.pauseAnimations(); s.setCurrentTime({t});"
        " return s.getCurrentTime(); }"
    )


def diff_ratio(a: Path, b: Path) -> float:
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    d = ImageChops.difference(ia, ib)
    changed = sum(1 for px in d.get_flattened_data() if px != (0, 0, 0))
    return changed / (d.width * d.height)


def main() -> int:
    if not SVG.is_file():
        print(f"FAIL: {SVG} not found")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 960, "height": 600}, device_scale_factor=2)
        page.goto(SVG.resolve().as_uri())
        page.wait_for_timeout(400)
        for t in TIMES:
            clock = page.evaluate(freeze(t))
            page.wait_for_timeout(120)
            out = OUT / f"quest02_t{str(t).replace('.', '_')}.png"
            page.screenshot(path=str(out))
            frames.append(out)
            print(f"t={t:<5} svg clock={clock:<6} -> {out.name}")
        browser.close()

    ratio = diff_ratio(frames[0], frames[1])
    print(f"pixel diff between t=0 and t=0.55: {ratio * 100:.2f}%")
    if ratio < 0.002:
        print("FAIL animation: frames are identical, the SVG is static")
        return 1
    print("PASS animation: the rendered frames differ over time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
