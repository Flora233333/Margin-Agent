"""Agent Harness（移植自 RC6-C）：提示词、十个工具、主循环。

用法：
    registry = build_registry(task, corpus, retriever)
    trace = run_episode(task, llm, registry)
    trace.final      # 提交的答案
    trace.steps      # 每一轮的思考、工具调用和结果
"""

from .loop import Step, Trace, run_episode
from .tools import build_registry

__all__ = ["Step", "Trace", "build_registry", "run_episode"]
