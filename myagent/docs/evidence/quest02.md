# Quest 02 证据：收敛提醒的 A/B

同一条原始指令、同样 30 步预算、同一个模型，唯一变量是收敛提醒开关。

指令（原文，不加任何补充提示）：

> 创建一个SVG，内容绘制一个鹈鹕骑自行车的2D动画，不参考其他任何内容

## 指标

| 运行 | 提醒 | exit_status | steps | prompt tokens | total tokens | peak tokens | 检查器 |
|---|---|---|---|---|---|---|---|
| A | 关 | `MaxSteps` | 30 | 366,031 | 411,982 | 22,782 | PASS（30 步后仍未提交） |
| B | 开 | `Submitted` | 7 | 33,058 | 33,847 | 10,681 | PASS |

B 的轨迹里第 7 步触发 1 次提醒（`n_stall_nudges=1`），随后一步提交。

**结论**：收敛提醒把"跑满预算"变成"主动收工"。token 消耗降到 8.2%，
峰值上下文降到 46.9%。产物检查器在两次运行里都通过，所以省下的
是完全重复的打磨成本，而不是任务质量。

## 产物结构

```
PASS quest02  elements=126  colours=53  circles=21  animations=22
```

## 动画验证

`experiments/render_frames.py` 在 Chromium 里暂停时间轴并定格：

```
t=0.0   svg clock=0      -> quest02_t0_0.png
t=0.55  svg clock=0.550000011920929 -> quest02_t0_55.png
t=1.1   svg clock=1.100000023841858 -> quest02_t1_1.png
pixel diff between t=0 and t=0.55: 9.60%
PASS animation: the rendered frames differ over time
```

9.60% 的像素在 0.55 s 内发生变化，说明 SMIL 时间轴真的在驱动画面；
若只是写了 `<animate>` 标签而时间轴没生效，这个数字会是 0。
