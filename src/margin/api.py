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
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from . import runs
from .db import get_engine
from .runs import IdempotencyConflict, RunBusy, RunNotFound
from .settings import get_settings

DEV_USER_ID = 1  # 迁移预置的开发用户；M3 改成从登录 cookie 解析
SSE_POLL_SECONDS = 1.0  # SSE 每秒查一次新事件（M2 改成 Redis 通知，有新事件才查）
SSE_PING_EVERY = 15  # 连续 15 次没有新事件就发一行注释，防止中间的代理把“安静”的连接断掉
FINISHED = {"completed", "failed"}

app = FastAPI(title="Margin")


# ---- 依赖：路由函数通过参数声明“我需要什么”，FastAPI 负责提供；测试里可以整体替换 ----

def get_db() -> Engine:
    return get_engine()


def current_user() -> int:
    return DEV_USER_ID


DB = Annotated[Engine, Depends(get_db)]
User = Annotated[int, Depends(current_user)]


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
    run_id: int, db: DB, user: User,
    last_event_id: Annotated[int, Header()] = 0,
) -> StreamingResponse:
    """SSE（Server-Sent Events）：一个不结束的 HTTP 响应，服务端有新事件就往里写一段。

    每段格式：id: 序号 / event: 类型 / data: JSON，空行结束。浏览器的 EventSource 断线后会
    自动重连，并在请求头 Last-Event-ID 里带上最后收到的 id，这里从它之后补发——这就是事件要
    连续编号、并且存进数据库的原因。执行结束（completed / failed）后关闭流。

    这里是 async 路由：等待新事件时用 asyncio.sleep 让出，不占线程；查库仍是同步代码，
    用 run_in_threadpool 放到线程池执行，不阻塞事件循环。
    """
    # 先查一次：run 不存在或不属于这个用户时，在开始推流之前就返回 404
    first = await run_in_threadpool(runs.events_after, db, user, run_id, last_event_id)

    async def generate() -> AsyncIterator[str]:
        events, status = first
        last_seq = last_event_id
        idle = 0
        while True:
            for event in events:
                data = json.dumps(event.payload, ensure_ascii=False)
                yield f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"
                last_seq = event.seq
            if status in FINISHED:
                return
            idle = 0 if events else idle + 1
            if idle >= SSE_PING_EVERY:
                yield ": ping\n\n"  # 冒号开头是 SSE 注释，浏览器忽略，只为让连接保持有数据
                idle = 0
            await asyncio.sleep(SSE_POLL_SECONDS)
            events, status = await run_in_threadpool(runs.events_after, db, user, run_id,
                                                     last_seq)

    # X-Accel-Buffering: no 让 Nginx（M6）不要攒满缓冲区才转发，否则事件会一批批延迟到达
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
