# Quest 02 · 鹈鹕骑自行车（2D 动画）

原始指令（不补充任何提示）：

> 创建一个SVG，内容绘制一个鹈鹕骑自行车的2D动画，不参考其他任何内容

产物：`pelican.svg`，浏览器能直接打开并看到运动。

验收（只看结构，不评分）：

```powershell
D:\conda\envs\main\python.exe check.py
```

检查项：

1. 合法的 XML + SVG 根元素；
2. 至少两个 `<circle>` 轮子；
3. 至少 8 个绘图元素（rect/circle/path/polygon/ellipse/line）；
4. 至少 3 种颜色；
5. 有 `<title>` 或 `<desc>`；
6. 没有任何外链图片（`<image href="http...">`）；
7. 有随时间变化的动画（`<animate>` / `<animateTransform>` / `@keyframes`）。
