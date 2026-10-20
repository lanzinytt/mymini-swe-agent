# 第 6 章证据：上下文压缩

## 不变量

压缩最容易出错的地方不是"省了多少"，而是**会不会把历史弄成非法状态**。三条：

1. **配对**：`assistant.tool_calls` 与对应 `role=tool` 消息成组处理。只删一半，
   下一次请求会因为 `tool_call_id` 找不到对应调用而被 API 拒绝。
2. **头两条固定**：`system` + 第一个 `user`（任务描述）。模型"我在做什么"来自它们。
3. **摘要不划算就截断**：短任务里摘要常常比原文还长，此时必须退化成截断，
   否则就是"压缩完反而更大"。

分组函数是整个设计的地基：

```python
def groups(messages):
    """一个 assistant 带 N 个 tool_calls，紧跟 N 条 tool 消息，这 1+N 条是一组。"""
    out, i = [], 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            out.append(messages[i : i + 1 + n])
            i += 1 + n
        else:
            out.append([m])
            i += 1
    return out
```

## 静态对比（同一份 42 条消息的历史）

```json
[
  {"strategy": "none",      "tokens": 2413, "kept_messages": 42, "saved_pct": 0.0},
  {"strategy": "truncate",  "tokens":  493, "kept_messages": 10, "saved_pct": 79.6},
  {"strategy": "summarize", "tokens": 1091, "kept_messages": 11, "saved_pct": 54.8}
]
```

截断省得最多，但它把中间过程**整段丢掉**；摘要保留一条规则生成的
`[compacted history]`，用 111 条消息换 54.8% 的 token，换来"还知道之前做过什么"。
这正是不选最大压缩率的原因。

## 运行级 A/B（同一脚本、两个预算、三种策略）

`experiments/compaction_ab.py`，脚本先写 6 个大文件，再反复读回，
让工具输出把上下文顶过预算。

| 预算 | 策略 | exit_status | 压缩次数 | 峰值 prompt 字符 | 任务完成 |
|---|---|---|---|---|---|
| 120000 | none | Submitted | 0 | 48,827 | 是 |
| 120000 | truncate | Submitted | 0 | 48,827 | 是 |
| 120000 | summarize | Submitted | 0 | 48,827 | 是 |
| 6000 | none | Submitted | 0 | 48,827 | 是 |
| 6000 | truncate | Submitted | **9** | **32,270** | 是 |
| 6000 | summarize | Submitted | **9** | **32,553** | 是 |

读数要点：

- 120000 预算下一共只有 ~4.9 万字符 ≈ 1.2 万 token，从未触线。这说明
  **预算不能拍脑袋设**：设太大等于没有压缩，报告里必须给出"实际峰值"。
- 6000 预算下触发 9 次压缩，峰值从 48,827 降到 32,270 字符（-33.9%）。
- 六次运行的 `task_complete` 全为真：压缩没有破坏任务，只削减了历史。

## 为什么"削峰"不等于"削平"

每次压缩只处理**触发那一刻**的历史，之后工具还会继续产生输出，所以峰值
仍然会爬回去。要真正削平需要"预算内持续淘汰"（滑动窗口），本实验没做，
原因写在报告第八节的改进建议里。

## 学到的教训

第一次跑这个 A/B 时，`none` 策略也被压缩了——因为 `maybe_compact` 只检查
`before <= limit`，`strategy` 参数被忽略。这种"开关无效"的 bug 不会报错，
只会让对比结论完全失真。修法是让 `strategy == "none"` 在最前面短路：

```python
if strategy == "none" or before <= limit:
    return messages, None
```
