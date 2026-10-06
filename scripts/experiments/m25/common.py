"""M2.5 实验（PLAN §5.8）的公共部分。

读题、组装检索器和模型、跑一道题并存盘、不带工具的模型调用、并发。
这些是手动运行的实验脚本（和 scripts/run_episode.py 同类），不是产品代码，也不进测试。
结果存在 cache_dir/m25/（默认 .cache/m25，不进仓库）；
实验结论在对话里汇报、作者确认后再记进 STATUS。
只用学校网关的 DeepSeek（D23）；自训模型等实验室服务器空出来后，用同样的脚本加 --model 复测。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from margin.assembly import build_llm, build_retriever
from margin.harness import run_episode
from margin.harness.state import EpisodeState
from margin.harness.tools import TOOLS, ToolRegistry
from margin.harness.tools.base import ToolContext
from margin.llm import OpenAICompatibleClient
from margin.settings import get_settings

HERE = Path(__file__).parent
settings = get_settings()
RESULTS = settings.cache_dir / "m25"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_e80(e1_path: Path, ids_path: Path) -> list[dict[str, Any]]:
    """E80 的 80 道题：题目快照来自训练仓库的 E1 题集，题号和参考答案来自 E80 的评分参考文件。"""
    references = {row["task_id"]: row["reference_answers"] for row in load_jsonl(ids_path)}
    tasks = []
    for row in load_jsonl(e1_path):
        snapshot = row["task_snapshot"]
        if snapshot["task_id"] in references:
            tasks.append({**snapshot, "reference": references[snapshot["task_id"]]})
    return tasks


BACKOFF = [5, 10, 20, 40, 60]  # 网关限流（429）时依次等待的秒数


def with_retry(call: Callable[[], Any]) -> Any:
    """学校网关并发一高就返回 429，等一会儿再试。

    2026-10-06 实测：4 并发时全部 429，单个请求正常。
    """
    for wait in [*BACKOFF, None]:
        try:
            return call()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or wait is None:
                raise
            time.sleep(wait)


class RetryingLLM:
    """包一层：Harness 调用 chat() 时遇到 429 自动重试。

    产品里的重试由 worker 负责（M4 分层重试），这里只给实验用。
    """

    def __init__(self, llm: OpenAICompatibleClient) -> None:
        self.llm = llm
        self.model = llm.model
        self.http = llm.http
        self.extra_body = llm.extra_body

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return with_retry(lambda: self.llm.chat(*args, **kwargs))


def describe_error(exc: Exception) -> str:
    """错误只记类型和状态码：httpx 的错误信息里带着网关地址，不写进日志。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class Env:
    """一次实验共用的语料、检索器和模型客户端（httpx 客户端可以多线程共用）。"""

    def __init__(self, model: str) -> None:
        started = time.monotonic()
        self.corpus, self.retriever = build_retriever(settings)
        self.model = model
        self.llm = RetryingLLM(build_llm(settings, model))
        print(f"[就绪] 模型 {model}，检索器 {time.monotonic() - started:.1f}s", flush=True)

    def registry(self, task: dict[str, Any], tools: dict | None = None,
                 schemas: list[dict] | None = None) -> ToolRegistry:
        """同 harness.build_registry，但可以换工具实现或工具说明。

        E3 换 finalize 的实现（引用提醒），E5 换工具说明（新版 finalize）。
        """
        ctx = ToolContext(self.corpus, self.retriever, EpisodeState(task))
        registry = ToolRegistry(ctx, tools or TOOLS)
        if schemas is not None:
            registry.schemas = schemas
            registry.names = [s["function"]["name"] for s in schemas]
        return registry


def run_task(env: Env, task: dict[str, Any], out: Path,
             registry: ToolRegistry | None = None) -> dict:
    """跑一道题，存成 JSON（同 run_episode.py --json-out，另加每步的思考）。跑过的直接读回。"""
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    started = time.monotonic()
    try:
        trace = run_episode(task, env.llm, registry or env.registry(task))
    except Exception as exc:  # 网关报错、超时：记下来，这道题算没跑成，不影响其他题
        print(f"[失败] {out.stem}：{describe_error(exc)}", flush=True)
        return {"task": task, "error": describe_error(exc)}
    steps = [{"turn": s.turn, "tool": s.tool_name, "arguments": s.arguments, "result": s.result,
              "reasoning": s.reasoning, "llm_seconds": round(s.llm_seconds, 1)}
             for s in trace.steps]
    result = {
        "task": task,
        "model": env.model,
        "final": trace.final,
        "violation": trace.violation,
        "total_seconds": round(time.monotonic() - started, 1),
        "steps": steps,
        "usage": trace.usage,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    final = trace.final or {}
    answer = final.get("submitted") or final.get("reason_code") or trace.violation
    print(f"[完成] {out.stem}：{answer}，{len(steps)} 步，{result['total_seconds']}s", flush=True)
    return result


def plain_chat(llm: OpenAICompatibleClient | RetryingLLM, messages: list[dict[str, Any]],
               max_tokens: int = 4000) -> dict[str, Any]:
    """不带工具的一次模型调用（理解题目、撰写回答用）。

    产品代码里的 chat() 每次都带工具列表；实验先直接发请求，
    正式实现时再给客户端加一个方法（M2.5-2）。
    """
    started = time.monotonic()

    def post() -> httpx.Response:
        response = llm.http.post("/chat/completions", json={
            "model": llm.model, "messages": messages, "max_tokens": max_tokens,
            **llm.extra_body,
        })
        response.raise_for_status()
        return response

    data = with_retry(post).json()
    message = data["choices"][0]["message"]
    return {
        "content": message.get("content") or "",
        "reasoning": message.get("reasoning_content") or message.get("reasoning") or "",
        "seconds": round(time.monotonic() - started, 1),
        "usage": data.get("usage") or {},
    }


def parallel(fn: Callable[[Any], Any], items: Iterable[Any], workers: int = 2) -> list[Any]:
    """并发跑。E80（10-04）4 并发 × 2 个模型没出错；10-06 再用 4 并发全部 429，改 2 并发 + 重试。"""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def correct(task: dict[str, Any], result: dict[str, Any]) -> bool | None:
    """和参考答案逐项比较（规范化后的字符串相等）。没有参考答案的题（开放问题）返回 None。

    这是实验用的简化判分；E80 官方评分器还会回放“第一次有效的 finalize”等细节，
    正式数字以 M5 评测回放为准。
    """
    if not task.get("reference"):
        return None
    final = result.get("final") or {}
    return final.get("name") == "finalize" and final.get("submitted") == task["reference"]
