# 第 8 章证据：安全边界

## 位置：拦在入口，不拦在提示词

`ToolRegistry.execute` 在调用任何工具之前先过 `SafetyGate.check()`：

```python
if self.gate is not None:
    blocked = self.gate.check(name, args, ctx.workspace)
    if blocked:
        return ToolResult(
            ok=False,
            error=f"blocked by safety gate: {blocked}",
            metadata={"blocked": blocked},
        )
```

返回的是普通的 `ToolResult`，所以模型会看到一条正常的 observation，
可以自己换方案——而不是把整个 run 崩掉。

## 拦截率与误杀率

`experiments/safety_metrics.py`：

```
dangerous blocked     20/20
handwritten benign FP 0/6
trajectories           3
real tool calls        48
false positives        0
```

| 指标 | 值 |
|---|---|
| 危险命令拦截率 | **20/20 = 100%** |
| 手写正常样本误杀 | 0/6 |
| 真实轨迹调用误杀 | **0/48 = 0%** |

真实轨迹取自本仓库所有 `.myagent/sessions/*.jsonl`，包含 agent 跑 Quest、
修 mini-swe-agent bug 时实际发出的全部工具调用。

## 覆盖的破坏面

| 类别 | 例子 |
|---|---|
| 递归删除 | `rm -rf`、`rd /s`、`Remove-Item -Recurse` |
| 磁盘/分区 | `format C:`、`diskpart`、`mkfs.*`、`dd of=/dev/*` |
| 关机 | `shutdown`、`reboot`、`halt`、`poweroff` |
| git 破坏性 | `push --force`、`reset --hard`、`clean -f` |
| 远程脚本管道 | `curl ... \| bash`、`wget ... \| sh`、`iwr ... \| iex` |
| 路径越界 | workspace 之外的绝对路径 |

## 关键设计：区分"提到"和"执行"

最容易犯的错误是纯正则匹配全文。那样 `print("rm -rf is a dangerous pattern")`
也会被拦——一条完全无害的文档字符串被当成删除命令。

做法是两层：

```python
def _check_command(self, args):
    raw = " ".join(str(args.get(k) or "") for k in ("command", "code", "path"))
    skeleton = _strip_string_literals(raw)      # 去掉所有字符串字面量
    for pattern, reason in COMMAND_RULES:
        if pattern.search(skeleton):
            return reason
    # 字面量落在 os.system / subprocess / eval 这类执行点里时，它就会被执行
    if EXEC_SINKS.search(skeleton):
        for pattern, reason in COMMAND_RULES:
            if pattern.search(raw):
                return f"{reason} inside an exec sink"
    return ""
```

这样两条都能成立：

- `print("rm -rf is dangerous")` → 放行（骨架里没有 `rm -rf`）；
- `os.system("rm -rf /")` → 拦截（骨架里有 `os.system()`，原文里有 `rm -rf`）。

## 第一条被误杀的样本

`test_normal_calls_are_not_blocked[run_python-args8]` 在加"骨架检查"之前是**红**的：

```
AssertionError: false positive: recursive delete (rm -r)
```

这条测试是故意写在"正常样本"里的——如果它不红，就说明误杀检测没起作用。
现在它是绿的，同时 `test_dangerous_commands_are_blocked` 的 19 条全部保持拦截。

## 局限（写进报告第八节）

1. **规则是枚举**，不是语义分析。`python -c "import shutil; shutil.rmtree('/')"`
   目前拦不住，需要 AST 级别的检查。
2. **只覆盖本实验的工具面**。如果给 agent 加上 `kubectl delete`、`docker rm`，
   规则要跟着扩。
3. **误杀率为 0 的样本只有 48 条**，样本量小；真实收益要更长周期才能确认。
