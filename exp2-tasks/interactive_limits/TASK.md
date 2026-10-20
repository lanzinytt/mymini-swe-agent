# 任务：修复 mini-swe-agent human 模式的限额绕过

工作区是 mini-swe-agent 仓库根。源码在 `src/minisweagent/`。

## 做法

1. 先用 `run_python` 运行 `python exp2-tasks/interactive_limits/repro.py`，确认当前是 FAIL。
2. 读 `src/minisweagent/agents/interactive.py`，找出为什么 human 模式不受 `step_limit`
   约束、`n_calls` 也不增长。
3. 修 `src/minisweagent/agents/interactive.py`（必要时可动 `agents/utils/prompt_user.py`）。
   不许修改 `exp2-tasks/interactive_limits/repro.py`：它是验收标准。
4. 重新运行 `python exp2-tasks/interactive_limits/repro.py`，必须输出 `RESULT PASS`。
5. 再运行 `python -m pytest tests/agents/test_interactive.py -q`，确认没有把原有测试改坏。
6. 最后 `submit`，在 summary 里写清楚改了什么。

## 约束

- 不改变 confirm / yolo 两种模式的行为。
- 不引入新的第三方依赖。
- 注释只写"为什么"，不写"是什么"。
