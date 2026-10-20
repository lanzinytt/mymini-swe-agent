# Quest 01 · 生成环境报告

目标：让 agent 在本目录生成 `env.json`，四个字段必须来自**真实探测**，
而不是把 `"python"` 这种字符串硬写进去。

约定字段：

| 字段 | 含义 |
|---|---|
| `python_executable` | 必须是当前解释器的绝对路径（`sys.executable`） |
| `python_version` | `X.Y.Z` |
| `platform` | `system` + `release`，例如 `Windows 10` |
| `shell` | shell 可执行文件路径 |

验收：

```powershell
D:\conda\envs\main\python.exe check.py
```
