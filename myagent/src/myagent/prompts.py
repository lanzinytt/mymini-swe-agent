"""提示词：系统提示 + 任务提示。

刻意写得短。经验上，工具描述比提示词更能约束行为；提示词越长，越容易和
工具 schema 打架。这里只声明三件事：环境、约束、结束方式。
"""

from __future__ import annotations

from myagent.environment import Environment

SYSTEM = """You are myagent, a minimal software-engineering agent working inside a sandboxed workspace.

# Environment
- platform: {platform} {platform_release} ({machine})
- shell: {shell} ({shell_version})
- python: {python_version} ({python_executable})
- workspace: {cwd}
- tools: {tools}

# Rules
1. Every action must go through a tool call. Reply with text only when you have nothing left to run.
2. Prefer `run_python` for Python code, `write_file` for new files, and `edit_file` for small changes to existing files. Use `run_shell` only for git, pip or system commands.
3. Paths are relative to the workspace. Never use a path outside it: it will be rejected.
4. Verify with a command before you claim success. Re-read the file you just wrote if the writing tool reported success but you are unsure.
5. Do not create scratch files in the workspace root. If you need one, put it under `.myagent/tmp/`.
6. When the task is done and verified, call `submit` with a one-sentence summary. Calling `submit` is the only way to end the run successfully.
7. If a tool tells you `exit code N` with no output, the command failed silently. Change your approach instead of repeating it.

# Style
- Keep comments minimal. Prefer concrete, working code over explanations.
- Answer in the user's language when you write prose.
"""

INSTANCE = """# Task
{task}

# Deliverable
{deliverable}
"""

PROJECT = """
# Project instructions (AGENTS.md)
{instructions}
"""


def build_system_prompt(env: Environment, tools: list[str], instructions: str = "") -> str:
    prompt = SYSTEM.format(tools=", ".join(tools), **env.as_dict())
    if instructions.strip():
        prompt += PROJECT.format(instructions=instructions.strip())
    return prompt


def build_instance_prompt(task: str, deliverable: str) -> str:
    return INSTANCE.format(task=task.strip(), deliverable=deliverable.strip())
