"""myagent —— 一个最小的 agent harness。

对外只暴露 ``Agent``、``ToolRegistry`` 和 ``Config``：其余都是内部管道。
"""

from myagent.agent import Agent, RunResult
from myagent.config import Config
from myagent.tools.base import ToolContext, ToolResult
from myagent.tools.registry import ToolRegistry

__all__ = ["Agent", "RunResult", "Config", "ToolContext", "ToolResult", "ToolRegistry"]
__version__ = "0.1.0"
