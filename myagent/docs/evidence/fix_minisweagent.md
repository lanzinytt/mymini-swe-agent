# 第 5 章证据：让 agent 修 mini-swe-agent 的限额 bug

## 缺陷

`InteractiveAgent` 的 human 模式在 `query()` 里直接读用户命令并返回，绕过了父类
`DefaultAgent.query()` 的全部预算记账。后果有两个，第二个更隐蔽：

1. `step_limit` / `cost_limit` / `wall_time_limit_seconds` 在 human 模式下完全失效；
2. `n_calls` 不增长，轨迹里报告的模型调用次数永远是 0，指标失真。

还有一层前置缺陷：`agents/utils/prompt_user.py` 在**模块导入时**就构造
`PromptSession`，在管道/CI/沙箱里直接抛 `NoConsoleScreenBufferError`，
连 `import minisweagent.agents.interactive` 都过不去。

## 验收脚本

`exp2-tasks/interactive_limits/repro.py` 分两步子进程检查：

| 步骤 | 检查 | 修复前 | 修复后 |
|---|---|---|---|
| step1 | 能否 import interactive | `FAIL`（NoConsoleScreenBufferError） | `PASS` |
| step2 | `step_limit=1` 时是否停下、`n_calls` 是否递增 | `FAIL` `{"stopped": false, "n_calls": 0, "fed": 6}` | `PASS` `{"stopped": true, "n_calls": 1, "fed": 1}` |

第二步必须有上界：缺陷的表现就是"永远不停"，没有上界会把检查脚本拖成超时
（第一次跑就吃了 120 s 超时）。所以脚本最多喂 6 条命令，超过即判定为没有停下。

## 交给 agent 的过程

指令只给"现象 + 验收标准"，不给修改方法：

```
先用 run_python 跑 python exp2-tasks/interactive_limits/repro.py，看到 step2 的 JSON
（现在是 stopped=false, n_calls=0, fed=6）。然后直接改 InteractiveAgent.query()：
让 human 分支在拿到用户命令后也计入调用次数并受 step_limit/cost_limit 约束。
不要改 repro.py，不要改测试。
```

轨迹里可以看到它先跑了 3 次复现（`.myagent/sessions/*.jsonl` 第 20-21 行等），
再读 `interactive.py`，最后写补丁。

## 修复内容

### 1. 导入期不再依赖终端

```python
class _LazyPromptSession:
    """Build the real ``PromptSession`` on first use."""
    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._session: PromptSession | None = None

    def prompt(self, *args, **kwargs):
        if self._session is None:
            self._session = PromptSession(**self._kwargs)
        return self._session.prompt(*args, **kwargs)
```

### 2. human 分支也走预算检查

```python
if self.config.mode == "human":
    # A user command is a real action, so it consumes budget like an LM call.
    self._check_limits()
    match command := self._prompt_and_handle_slash_commands(...):
        case "/y" | "/c":
            pass
        case _:
            self.n_calls += 1
            ...
```

```python
def _check_limits(self) -> None:
    if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
        raise LimitsExceeded(...)
    if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
        raise TimeExceeded(...)
```

## 一次回退：`return self.query()` 是错的

agent 的第一版把"用户提高上限后继续"改成了 `return self.query()`。这会让控制流
重新进入 human 分支、再消耗一个用户回合，并且把确定性测试模型直接喂穿：

```
src/minisweagent/models/test_models.py:112: IndexError: list index out of range
```

教训是：**递归调用自己的公开入口，等于重新执行一遍前置副作用**。恢复
`super().query()` 之后消失。

## 回归基线

`exp2-tasks/interactive_limits/baseline.py` 在"HEAD 无补丁"和"工作区有补丁"两个
状态下各跑一次同一组测试，只报**差异集**：

```
[interactive] 2 warnings, 1 error in 3.02s  ->  96 failed, 12 passed
  baseline could not even be collected (import-time crash);
  treat 'patched' as recovery, not as a regression set
[core] 31 failed, 61 passed  ->  31 failed, 61 passed
  no new failures
RESULT PASS
```

关键观察：`test_default.py` + `config` + `test_init.py` 这一组在补丁前后都是
**31 failed / 61 passed**，说明这 31 个失败是 mini-swe-agent 在 Windows 上的既有
环境性问题（`DeterministicModel` 的交互假设），与本次修复无关。如果不做这个对比，
就会把环境噪声误判成自己改坏的东西。
