"""把任务跑一遍，收集每次运行的指标，供报告第五节与 A/B 使用。

这个脚本本身也是证据：报告里的每个数字都能由它重建。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from myagent.agent import Agent  # noqa: E402
from myagent.config import Config  # noqa: E402
from myagent.environment import detect  # noqa: E402
from myagent.events import EventLogger, new_session_path  # noqa: E402
from myagent.model import OpenAICompatibleModel  # noqa: E402
from myagent.prompts import build_instance_prompt, build_system_prompt  # noqa: E402
from myagent.tools.files import ListDirTool, ReadFileTool, WriteFileTool  # noqa: E402
from myagent.tools.registry import ToolRegistry  # noqa: E402
from myagent.tools.shell import PythonTool, ShellTool  # noqa: E402
from myagent.tools.submit import SubmitTool  # noqa: E402


def build_agent(ws: Path, task: str, *, max_steps: int = 30, **kw) -> tuple[Agent, EventLogger]:
    cfg = Config.from_env(ROOT / ".env")
    tools = ToolRegistry(
        [PythonTool(), ShellTool(), ReadFileTool(), WriteFileTool(), ListDirTool(), SubmitTool()]
    )
    model = OpenAICompatibleModel(
        model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key, price=cfg.price
    )
    logger = EventLogger(new_session_path(ws))
    agent = Agent(
        model,
        tools,
        system_prompt=build_system_prompt(detect(ws), tools.names()),
        instance_prompt=build_instance_prompt(task, "Satisfy the task. Verify before you submit."),
        workspace=ws,
        max_steps=max_steps,
        logger=logger,
        **kw,
    )
    return agent, logger


def run_one(ws: Path, task: str, *, max_steps: int = 30, **kw) -> dict:
    agent, logger = build_agent(ws, task, max_steps=max_steps, **kw)
    result = agent.run()
    logger.close()
    return {
        "workspace": str(ws),
        "session": str(logger.path),
        **result.as_dict(),
        "peak_tokens": max(
            (e.get("usage", {}).get("total_tokens", 0) for e in logger.events
             if e.get("kind") == "model_response"),
            default=0,
        ),
    }


if __name__ == "__main__":
    quest = sys.argv[1] if len(sys.argv) > 1 else "01"
    ws = ROOT / "quests" / ("01_env_report" if quest == "01" else "02_pelican_bicycle")
    task = {
        "01": "读取 README.md，生成 env.json，四个字段必须来自真实探测，"
              "然后用 run_python 运行 check.py 确认 PASS quest01，最后 submit。",
        "02": "创建一个SVG，内容绘制一个鹈鹕骑自行车的2D动画，不参考其他任何内容。"
              "写完后运行 check.py 确认 PASS quest02，然后 submit。",
    }[quest]
    print(json.dumps(run_one(ws, task, max_steps=30), ensure_ascii=False, indent=2))
