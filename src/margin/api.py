"""HTTP 接口（FastAPI）。只负责收请求、校验、读写数据库，不执行 Agent——执行在后台 worker 里。

    POST /runs                      提交一道题（需要 Idempotency-Key 请求头），立即返回 202
    GET  /runs/{run_id}             题目、状态、每次执行及其每一步
    POST /runs/{run_id}/regenerate  重新生成：新建一次执行
    GET  /runs/{run_id}/events      SSE 事件流，断线重连带 Last-Event-ID 从断点补发

运行（开发）：conda run -n margin --no-capture-output uvicorn margin.api:app --reload
接口文档：启动后打开 http://127.0.0.1:8000/docs（FastAPI 按下面的类型自动生成）
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from . import runs
from .db import get_engine
from .event_hub import EventHub
from .runs import IdempotencyConflict, RunBusy, RunNotFound
from .settings import get_settings

DEV_USER_ID = 1  # 迁移预置的开发用户；M3 改成从登录 cookie 解析
# SSE 平时等“有新事件”的通知（event_hub.py）；最多等 10 秒也查一次库（兜底：通知不持久，
# LISTEN 连接重连期间会丢），同时发一行注释保活，防止中间的代理把“安静”的连接断掉
SSE_FALLBACK_SECONDS = 10.0
FINISHED = {"completed", "failed"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """进程启动时开始监听事件通知，退出时停止。yield 之前是启动，之后是关闭。"""
    hub = EventHub(get_settings().database_url)
    hub.start()
    app.state.hub = hub
    yield
    hub.stop()


app = FastAPI(title="Margin", lifespan=lifespan)


# ---- 依赖：路由函数通过参数声明“我需要什么”，FastAPI 负责提供；测试里可以整体替换 ----

def get_db() -> Engine:
    return get_engine()


def current_user() -> int:
    return DEV_USER_ID


def get_hub(request: Request) -> EventHub:
    return request.app.state.hub


DB = Annotated[Engine, Depends(get_db)]
User = Annotated[int, Depends(current_user)]
Hub = Annotated[EventHub, Depends(get_hub)]


# ---- 请求 / 响应的数据格式（Pydantic 校验用户输入，不合格自动返回 422）----

class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 多传了未知字段也算错，及早暴露前端 bug

    question: str = Field(min_length=1, max_length=2000)
    options: dict[Literal["A", "B", "C", "D"], str] | None = None  # 选择题的选项
    answer_format: Literal["num", "pct", "tf", "mcq", "multi", "date", "rank", "text"]


class RunAccepted(BaseModel):
    run_id: int


class RegenerateAccepted(BaseModel):
    attempt_no: int


class StepOut(BaseModel):
    step_no: int
    tool_name: str
    arguments: str
    result: dict[str, Any]
    reasoning: str | None
    llm_ms: int


class AttemptOut(BaseModel):
    attempt_no: int
    trigger: str
    status: str
    final: dict[str, Any] | None
    violation: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[StepOut]


class RunDetail(BaseModel):
    id: int
    question: str
    options: dict[str, str] | None
    answer_format: str
    model: str
    status: str
    created_at: datetime
    attempts: list[AttemptOut]


# ---- 业务异常 -> HTTP 状态码 ----

@app.exception_handler(RunNotFound)
def not_found(request: Request, exc: RunNotFound) -> JSONResponse:
    return JSONResponse({"detail": "run 不存在"}, status_code=404)


@app.exception_handler(IdempotencyConflict)
def idempotency_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
    return JSONResponse({"detail": "同一个 Idempotency-Key 已用于提交另一道题"}, status_code=409)


@app.exception_handler(RunBusy)
def run_busy(request: Request, exc: RunBusy) -> JSONResponse:
    return JSONResponse({"detail": "这道题还在执行中"}, status_code=409)


# ---- 路由 ----
# 普通 def 路由：FastAPI 放到线程池里执行，里面可以直接调用同步的数据库代码。

@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/runs", status_code=202)
def create_run(
    body: RunCreate, db: DB, user: User,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
) -> RunAccepted:
    """202 Accepted：已收下、在后台执行。同一个幂等键再次提交，返回同一个 run_id。

    请求头写作 Idempotency-Key，FastAPI 会把参数名 idempotency_key 自动对应过去。
    """
    run_id, _ = runs.create_run(db, user, idempotency_key, body.question, body.options,
                                body.answer_format, get_settings().llm_model)
    return RunAccepted(run_id=run_id)


@app.get("/runs/{run_id}")
def get_run(run_id: int, db: DB, user: User) -> RunDetail:
    return RunDetail.model_validate(runs.get_run(db, user, run_id))


@app.post("/runs/{run_id}/regenerate", status_code=202)
def regenerate(run_id: int, db: DB, user: User) -> RegenerateAccepted:
    return RegenerateAccepted(attempt_no=runs.regenerate(db, user, run_id))


@app.get("/runs/{run_id}/events")
async def stream_events(
    run_id: int, db: DB, user: User, hub: Hub,
    last_event_id: Annotated[int, Header()] = 0,
) -> StreamingResponse:
    """SSE（Server-Sent Events）：一个不结束的 HTTP 响应，服务端有新事件就往里写一段。

    每段格式：id: 序号 / event: 类型 / data: JSON，空行结束。浏览器的 EventSource 断线后会
    自动重连，并在请求头 Last-Event-ID 里带上最后收到的 id，这里从它之后补发——这就是事件要
    连续编号、并且存进数据库的原因。执行结束（completed / failed）后关闭流。

    什么时候去查新事件：worker 每提交一个事件，PG 发一个“run N 有新事件”的通知（runs.add_event），
    event_hub 收到后 set 这里的 wake；没有通知时最多等 10 秒也查一次（兜底）。
    M1 是每个连接每秒查一次库，看的人越多查询越多；现在只有真的有新事件时才查。

    这里是 async 路由：等待时 await 让出，不占线程；查库仍是同步代码，
    用 run_in_threadpool 放到线程池执行，不阻塞事件循环。
    """
    # 先查一次：run 不存在或不属于这个用户时，在开始推流之前就返回 404
    first = await run_in_threadpool(runs.events_after, db, user, run_id, last_event_id)

    async def generate() -> AsyncIterator[str]:
        events, status = first
        last_seq = last_event_id
        with hub.watch(run_id) as wake:
            # first 是登记之前查的：查完到登记之间提交的事件不会叫醒我们，所以第一轮不等待、直接补查
            wake.set()
            while True:
                for event in events:
                    data = json.dumps(event.payload, ensure_ascii=False)
                    yield f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"
                    last_seq = event.seq
                if status in FINISHED:
                    return
                try:
                    await asyncio.wait_for(wake.wait(), SSE_FALLBACK_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"  # 冒号开头是 SSE 注释，浏览器忽略，只为让连接保持有数据
                # 先清除再查库：查库期间到达的通知会重新 set，下一轮立刻再查，不会漏
                wake.clear()
                events, status = await run_in_threadpool(runs.events_after, db, user, run_id,
                                                         last_seq)

    # X-Accel-Buffering: no 让 Nginx（M6）不要攒满缓冲区才转发，否则事件会一批批延迟到达
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
