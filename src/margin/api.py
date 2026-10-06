"""HTTP 接口（FastAPI）。只负责收请求、校验、读写数据库，不执行 Agent——执行在后台 worker 里。

    POST /runs                      提交一道题（需要 Idempotency-Key 请求头），立即返回 202
    GET  /runs                      最近提交的题目（前端左侧历史列表）
    GET  /runs/{run_id}             题目、状态、每次执行及其每一步
    POST /runs/{run_id}/regenerate  重新生成：新建一次执行
    GET  /runs/{run_id}/events      SSE 事件流，断线重连带 Last-Event-ID（或 ?after=）从断点补发

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

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from . import live, runs
from .db import get_engine
from .event_hub import NEW_EVENTS, EventHub
from .runs import IdempotencyConflict, RunBusy, RunNotFound
from .settings import get_settings

DEV_USER_ID = 1  # 迁移预置的开发用户；M3 改成从登录 cookie 解析
# SSE 平时等“有新事件”的通知（event_hub.py）；最多等 10 秒也查一次库（兜底：通知不持久，
# LISTEN 连接重连期间会丢），同时发一个心跳事件（见 stream_events 里 event: ping 的说明）
SSE_FALLBACK_SECONDS = 10.0
FINISHED = {"completed", "failed"}
RECENT_RUNS = 50  # 历史列表最多返回几道题


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


def get_redis_url() -> str:
    """实时片段从哪个 Redis 订阅。每个 SSE 连接用它建自己的订阅连接（live.forward_deltas）。"""
    return get_settings().redis_url


DB = Annotated[Engine, Depends(get_db)]
User = Annotated[int, Depends(current_user)]
Hub = Annotated[EventHub, Depends(get_hub)]
RedisURL = Annotated[str, Depends(get_redis_url)]


# ---- 请求 / 响应的数据格式（Pydantic 校验用户输入，不合格自动返回 422）----

class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 多传了未知字段也算错，及早暴露前端 bug

    question: str = Field(min_length=1, max_length=2000)
    options: dict[Literal["A", "B", "C", "D"], str] | None = None  # 选择题的选项
    # 产品里不传，由 worker 理解题目后判断（PLAN §5.8 ①）；评测回放按题集给定的格式传
    answer_format: Literal["num", "pct", "tf", "mcq", "multi", "date", "rank", "text"] | None = None
    # 没有引用就交答案时提醒一次（PLAN §5.8 ②）；评测回放、自训模型对照实验传 false，规则和以前一样
    require_citation: bool = True


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


class RunSummary(BaseModel):
    id: int
    question: str
    title: str | None  # 理解题目后得到的标题；还没理解完时为空，前端显示问题原句
    status: str
    created_at: datetime


class RunDetail(BaseModel):
    id: int
    question: str
    title: str | None
    answer_label: str | None  # 结论旁边的一行说明
    options: dict[str, str] | None
    answer_format: str | None  # 调用方给定的格式；产品里的题为空
    guessed_format: str | None  # 理解题目猜的格式
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
                                body.answer_format, get_settings().llm_model,
                                require_citation=body.require_citation)
    return RunAccepted(run_id=run_id)


@app.get("/runs")
def list_runs(db: DB, user: User) -> list[RunSummary]:
    """最近 50 道题，前端左侧的历史列表。只返回自己的（M3 加登录后按真实用户过滤）。"""
    return [RunSummary.model_validate(r) for r in runs.list_runs(db, user, RECENT_RUNS)]


@app.get("/runs/{run_id}")
def get_run(run_id: int, db: DB, user: User) -> RunDetail:
    return RunDetail.model_validate(runs.get_run(db, user, run_id))


@app.post("/runs/{run_id}/regenerate", status_code=202)
def regenerate(run_id: int, db: DB, user: User) -> RegenerateAccepted:
    return RegenerateAccepted(attempt_no=runs.regenerate(db, user, run_id))


@app.get("/runs/{run_id}/events")
async def stream_events(
    run_id: int, db: DB, user: User, hub: Hub, redis_url: RedisURL,
    last_event_id: Annotated[int, Header()] = 0,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """SSE（Server-Sent Events）：一个不结束的 HTTP 响应，服务端有新事件就往里写一段。

    每段格式：id: 序号 / event: 类型 / data: JSON，空行结束。浏览器的 EventSource 断线后会
    自动重连，并在请求头 Last-Event-ID 里带上最后收到的 id，这里从它之后补发——这就是事件要
    连续编号、并且存进数据库的原因。执行结束（completed / failed）后关闭流。

    什么时候去查新事件：worker 每提交一个事件，PG 发一个“run N 有新事件”的通知（runs.add_event），
    event_hub 收到后往这个连接的收件箱里放一个 NEW_EVENTS；没有通知时最多等 10 秒也查一次（兜底）。
    M1 是每个连接每秒查一次库，看的人越多查询越多；现在只有真的有新事件时才查。

    两层事件（PLAN §5.4）：除了上面的持久事件，还转发模型逐字输出的思考片段（event: delta），
    它们由 worker 发到 Redis（live.py），不落库、不补发，这一步结束后由持久事件里的完整思考替换。

    这里是 async 路由：等待时 await 让出，不占线程；查库仍是同步代码，
    用 run_in_threadpool 放到线程池执行，不阻塞事件循环。

    event: caught_up：执行中打开页面，先一次性补发历史事件，补发完发这个标记（不带 id），
    之后才是新发生的。前端靠它区分“回放”和“实时”：例如来源卡片出现时的连线动画，只给实时出现的卡片画。

    ?after=N：和 Last-Event-ID 作用相同。前端新开一个 EventSource（例如点“重新生成”后，原来的流
    已在执行结束时关闭）没法自己设置 Last-Event-ID 请求头，就用这个参数。两者都有时取大的：
    浏览器自动重连时会带上更新的 Last-Event-ID，而地址里的 after 还是最初的值。
    """
    start = max(last_event_id, after)
    # 先查一次：run 不存在或不属于这个用户时，在开始推流之前就返回 404
    first = await run_in_threadpool(runs.events_after, db, user, run_id, start)

    async def generate() -> AsyncIterator[str]:
        events, status = first
        last_seq = start
        caught_up = False
        # 收件箱里有两种东西：NEW_EVENTS（库里有新的持久事件）和实时片段（JSON 字符串）
        with hub.watch(run_id) as inbox:
            async with live.forward_deltas(redis_url, run_id, inbox):
                # first 是登记之前查的：查完到登记之间提交的事件不会叫醒我们，所以先放一个标记，
                # 第一轮直接补查
                inbox.put_nowait(NEW_EVENTS)
                while True:
                    for event in events:
                        data = json.dumps(event.payload, ensure_ascii=False)
                        yield f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"
                        last_seq = event.seq
                    if status in FINISHED:
                        return
                    if not caught_up:
                        caught_up = True
                        yield "event: caught_up\ndata: {}\n\n"
                    events = []
                    try:
                        item = await asyncio.wait_for(inbox.get(), SSE_FALLBACK_SECONDS)
                    except TimeoutError:
                        # 心跳：10 秒没有任何数据就发一个。两个作用：
                        # 中间的代理不会把“安静”的连接当成空闲断掉；
                        # 前端超过 25 秒什么都没收到，就知道连接已经“半死”（例如 API 进程被杀、
                        # 中间的代理却没把浏览器那一侧关掉），主动重连。
                        # 所以要用具名事件而不是 SSE 注释（": ping"）：注释浏览器不交给 JS。
                        # 不带 id，不影响 Last-Event-ID
                        yield "event: ping\ndata: {}\n\n"
                        item = NEW_EVENTS  # 兜底：10 秒没动静也查一次库
                    if item == NEW_EVENTS:
                        # 查库期间到达的通知会再放一个标记进收件箱，下一轮立刻再查，不会漏
                        events, status = await run_in_threadpool(runs.events_after, db, user,
                                                                 run_id, last_seq)
                    else:
                        # 实时片段不带 id：浏览器重连时的 Last-Event-ID 只跟着持久事件走
                        yield f"event: delta\ndata: {item}\n\n"

    # X-Accel-Buffering: no 让 Nginx（M6）不要攒满缓冲区才转发，否则事件会一批批延迟到达
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
