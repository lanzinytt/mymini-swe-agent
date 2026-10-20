# 第 9 章证据：会话恢复

## 正确的定义

`resume` 是否"对"，只看一件事：**恢复出来的 messages 与中断前最后一次请求
发出的 messages 完全一致**。所以实现里不做任何"重放"：

- 不重跑模型；
- 不重放工具（重放会重复产生副作用）；
- 只逐行解析事件，重建 messages。

## 两条容易做错的细节

### 1. `run_start` 必须保存提示词

第一版 `run_start` 只记了 workspace 和 model，恢复时 `system` / `user`
两条消息是空的。现象是：能"接着跑"，但模型完全不知道自己原本在做什么。

```python
self._log(
    "run_start",
    workspace=str(self.workspace),
    model=getattr(self.model, "name", "?"),
    tools=self.tools.names(),
    max_steps=self.max_steps,
    # 保存提示词，resume 才能重建出一模一样的历史。
    # 不存的话恢复出来的 system 消息是空的，模型会立刻跑偏。
    system=self.system_prompt,
    instance=self.instance_prompt,
)
```

### 2. 必须追加到原文件

新建一个 session 文件看起来"能恢复"，但原始证据被切成两段，报告里
"第 42 行"这样的引用全部失效。所以 `EventLogger(session, append=True)`
复用同一个文件，并追加一条 `resume` 标记：

```python
with EventLogger(session, append=True) as logger:
    logger.log("resume", from_events=state.event_count, from_steps=state.steps)
```

## 真实运行

步数上限设为 2，任务要求建 3 个文件：

```
> python -m myagent run "创建 note1/2/3.txt ..." -w F:\agent-tmp\resume-demo --max-steps 2
exit_status  MaxSteps     steps 2
session      ...\20261020-194524-723094.jsonl

> python -m myagent resume -w F:\agent-tmp\resume-demo --max-steps 8
session      ...\20261020-194524-723094.jsonl      <- 同一个文件
events       12
steps        2
messages     8
exit_status  Submitted   steps 2
```

恢复后的事件流（21 行，同一个文件）：

```
   1 run_start      model=deepseek-v4.1-flash max_steps=2
   2 model_response step 1  calls=[list_dir]
   ...
  11 tool_result    write_file   ok    wrote 2 chars to note3.txt
  12 run_end        MaxSteps  steps=2
  13 resume         {"from_events": 12, "from_steps": 2}     <- 追加的标记
  14 run_start      model=deepseek-v4.1-flash max_steps=8
  15 model_response step 1  calls=[run_python]              <- 直接验证，不重复写
  17 tool_result    run_python   ok  '.myagent' 'note1.txt' '1\n' ...
  18 model_response step 2  calls=[submit]
  21 run_end        Submitted  steps=2
```

## 核对清单

| 检查项 | 结果 |
|---|---|
| session 文件数 | **1**（没有新建） |
| 恢复后第一步做什么 | `run_python` 校验已有文件，**没有重写 note1/2/3** |
| 工作区文件数 | 3（没有多余临时文件） |
| 历史是否连续 | 第 15 行的 step 从 1 重新计数，但 messages 接续自第 12 行 |
| 计费是否累计 | `agent.cost = state.cost`，不会因恢复而"重置预算" |

## 中断在"半条命令"之间

真实崩溃可能发生在 `assistant` 已落盘、`tool` 结果还没写的瞬间。此时直接
恢复会得到一条**孤立的 tool_call**，下一次请求会被 API 拒绝。

处理办法是补占位消息而不是删除（删除会破坏 append-only）：

```python
for call_id in pending:
    out.append({
        "role": "tool",
        "tool_call_id": call_id,
        "name": "",
        "content": "(interrupted before this call produced output)",
    })
```

测试 `test_restore_repairs_a_call_interrupted_before_its_result` 覆盖这个场景，
并断言 `trailing_note` 里出现 `1 unpaired`，让报告能如实说明"这次恢复补过一条"。
