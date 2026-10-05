# 架构 review

**⭐简化流程**

```
Browser
   │ Ngnix
   ▼
FastAPI
   │
   ▼
PostgreSQL (INSERT Run + Attempt + Outbox)
   │
   ▼
Dispatcher (Scan Outbox Table)
   │ 投递
   ▼
Celery 发布 ---> Redis (Task Queue)
                       │
                       │Consume
                       │
                       ▼
                     Worker x N <--- PostgreSQL (Lease + epoch)
                       │
                       │
                       │
                       ▼
                    Harness
                       │
                       ├── 完整结果 ──> PostgreSQL
                       │
                       └── 实时 token ──> Redis ──> SSE ──> Browser (流式展示)
```

**详细讲述**

1. 用户提交 Prompt 会最先发生什么？

**会首先在 PostgreSQL 的三个表中 进行 INSERT 插入（这是个原子操作，要么三个表一起插入，要么都失败），相当于创建任务**

这一步很重要，之后的所有消费和生成都和这里挂钩

```
┌──────────┐
│ Browser  │
└────┬─────┘
     │ POST /api/runs
     ▼
┌──────────┐
│  Nginx   │
└────┬─────┘
     │ /api/* → FastAPI
     ▼
┌──────────┐
│ FastAPI  │
└────┬─────┘
     │ BEGIN
     ▼
┌─────────────────────┐
│     PostgreSQL      │
│  INSERT run         │
│  INSERT attempt     │
│  INSERT outbox      │
│       COMMIT        │
└──────────┬──────────┘
           │
           ▼
      202 Accepted
      run_id = 100
      
--------------------------------------------------------------------------------------------------------------

                PostgreSQL
               【可靠事实】
              ↗          ↖
          FastAPI       Worker
             ↓             ↓
          Outbox        Harness
             ↓
        Dispatcher
             ↓
      Celery + Redis
        【任务队列】
             ↓
           Worker
             │
     ┌───────┴───────┐
     ↓               ↓
PostgreSQL          Redis
完整可靠结果      实时临时 Token
     ↓               ↓
     └───────┬───────┘
             ↓
          FastAPI
             ↓ SSE
           React
```

---

2. **⭐之后是怎么触发运行的？**

Dispatcher 会不断轮询 outbox 的新增信息的状态，如果为 Pending 则会尝试 投递给 Celery

Celery 接受成功后 会发布并保存在 Redis 中（形成消息队列），等待 Worker 进行消费

Worker 的个数是自己设定的，他会按照队列顺序 从前到后取出 Redis 的任务，然后向 PostgreSQL 的 Attempts 表申请 Lease

Attempts 表会先查询该任务有没有被另一个 Worker 消费，此 Worker 有没有超时，
如果没被消费或者上一个 Worker 超时，则 Attempts 表会更新表中的 Lease 相关信息，之后 Worker 取得权限后就开始运行

Worker 开始运行时 每完成一个 step 都会保存到 PostgreSQL 中，为了防止突然的中断导致工作流消失
但是运行每个 step 时，也就是每个 token 产生，会直接缓存到 Redis 中，由 Redis 发布到 SSE 通道回传给 浏览器显示
这样保证了不要频繁访问 PostgreSQL 数据库，完成后就释放了
**Worker 完成后同样会将结果回写至 PostgreSQL（而不是什么 Redis 来回写，Redis 在这里纯桥接前端）**

```
┌───────────────────┐
│ PostgreSQL Outbox │
│                   │
│ attempt_id = 200  │
│ status = pending  │
└─────────┬─────────┘
          │ 扫描
          ▼
┌───────────────────┐
│    Dispatcher     │
└─────────┬─────────┘
          │
          │ Celery 发布
          │ execute(200)
          ▼
┌───────────────────┐
│       Redis       │
│                   │
│   Task Queue      │
│                   │
│  [198]            │
│  [199]            │
│  [200] ← 等待     │
│  [201]            │
└─────────┬─────────┘
          │
          │ 消费
          ▼
┌───────────────────┐
│   Celery Worker   │
│                   │
│ “执行 Attempt 200”│
└───────────────────┘
```

---

3. 部分重要表的数据模型

**`outbox` —— 可靠的“待投递任务”**（由 Dispatcher 轮询）

```
┌────────────────────────────────────────────────────┐
│ outbox                  投递记录                    │
├────────────────────────────────────────────────────┤
│ id               Outbox 记录唯一 ID                 │
│ attempt_id       要投递哪个 Attempt → attempts.id   │
│ status           当前是否已经成功投递                │
│ next_attempt_at  下次允许尝试投递的时间              │
└────────────────────────────────────────────────────┘
```

```
attempt_id = 200
status = pending 表示: Attempt 200 还需要发送给 Celery
next_attempt_at = 18:00 表示: 18:00 可以再次尝试 
(⭐next_attempt_at 可以防止因为 Celery 或者后面的 Redis 挂掉了，导致 Dispatcher 疯狂 Send)

如果投递失败
   │
   ▼
Outbox 仍然存在
   │
   ▼
next_attempt_at
   │
   ▼
到时间重新投递
```

**注意⚠：outbox 只是一种 "设计模式"，不是中间件**

>  **通常叫：Transactional Outbox Pattern，事务发件箱模式 / 事务 Outbox 模式**

核心思想特别简单：

> “我要通知外部系统做某件事”这件事情，也先可靠地记进自己的数据库。

 **`attempts` —— Run 的一次执行尝试**（Lease 租约在这里面管理）

```
┌────────────────────────────────────────────────────┐
│ attempts                执行尝试                    │
├────────────────────────────────────────────────────┤
│ id           Attempt 唯一 ID                        │
│ run_id       属于哪个 Run → runs.id                 │
│ attempt_no   第几次尝试：1、2、3...                  │
│ status       本次尝试的执行状态                      │
│ lease_owner  当前哪个 Worker 拥有执行权              │
│ lease_epoch  当前是第几代执行权，防旧 Worker 写回     │
│ lease_until  当前租约什么时候过期                    │
└────────────────────────────────────────────────────┘

lease_epoch 防止的是这种情况：Worker A 卡死过期了，由 Worker B 接手，但是后面 Worker A 又复活了继续来完成任务
此时 如果 Worker A 完成了某一 step 需要像 PostgreSQL 写入时
PostgreSQL 会查询 该任务的 lease_epoch 会发现 Worker A 接手任务时的 epoch 不等于 当前的 lease_epoch
是因为一旦某个任务，被新 Worker 接手时，PostgreSQL 的 lease_epoch 会原子操作自增 并赋值给 新 Worker
----------------------------------------------------
          Worker A
          epoch = 5
              │
              │ 执行中
              ▼
             💥
          心跳停止
              │
              ▼
        Lease 最终过期
              │
              ▼
          Worker B
          重新领取
              │
              ▼
   ┌────────────────────────┐
   │       PostgreSQL       │
   │                        │
   │ lease_owner = B        │
   │ lease_epoch = 6        │
   └────────────────────────┘
----------------------------------------------------
这时候如果 A 又恢复：

Worker A                     Worker B
epoch = 5                    epoch = 6
   │                            │
   │ 写数据库                   │ 写数据库
   ▼                            ▼
┌────────────┐              ┌────────────┐
│    拒绝    │              │    接受     │
│     ✗     │              │     ✓      │
└────────────┘              └────────────┘

       PostgreSQL 当前 epoch = 6
----------------------------------------------------
所以：
Lease → 决定“现在谁有执行权”
epoch → 防止“上一任 Worker 复活后乱写”
```

```
Run_ID 100
   │
   ├── Attempt_ID 200  attempt_no=1 → failed
   │
   └── Attempt_ID 245  attempt_no=2 → running
```

Run 是一道题，Attempt 是这道题的一次执行

**表之间的关系**（很多独立的表，通过 ID 关联）

```
                    ┌─────────┐
                    │  users  │
                    └────┬────┘
                         │ user_id / owner_id
                 ┌───────┴────────┐
                 ▼                ▼
           ┌──────────┐      ┌──────────┐
           │ sessions │      │   runs   │
           └──────────┘      └────┬─────┘
                                  │ run_id
                    ┌─────────────┼─────────────┐
                    │             │             │
                    ▼             │             ▼
              ┌──────────┐        │       ┌──────────┐
              │ attempts │        │       │  events  │
              └────┬─────┘        │       └──────────┘
                   │              │
          ┌────────┼────────┐     │
          │        │        │     │
          ▼        ▼        ▼     │
      ┌───────┐ ┌──────┐ ┌───────────┐
      │ steps │ │outbox│ │ llm_calls │
      └───────┘ └──────┘ └───────────┘
```

```
User
  ↓
Run
  ↓
Attempt
  ├── Steps       → “执行了什么”
  ├── LLM Calls   → “模型调用情况”
  └── Outbox      → “需要把执行任务投递出去”

Run
  └── Events      → “这个任务发生过什么事件”
```

---

4. Worker 是如何维持 租期时间的？

```
        Redis Queue
             │
             │ attempt 200
             ▼
        ┌──────────┐
        │ Worker A │
        └────┬─────┘
             │ 申请 Lease
             ▼
┌────────────────────────┐
│       PostgreSQL       │
│                        │
│ attempt_id  = 200      │
│ lease_owner = A        │
│ lease_epoch = 5        │
│ lease_until = 12:01:30 │
└────────────┬───────────┘
             │
             │ 领取成功
             ▼
        ┌──────────┐
        │ Worker A │
        │ 开始执行  │
        └──────────┘
```

**⭐Worker 有独立的心跳线程在执行过程中不断续租：**

```
Worker A
   │
   ├── 12:00:30 ──> 续租
   │
   ├── 12:01:00 ──> 续租
   │
   ├── 12:01:30 ──> 续租
   │
   └──    ...   ──> 续租
          ↓
lease_until 不断向后移动
```

所以 **90 秒不是任务只能跑 90 秒**，而是“超过一段时间没成功续租，就认为这个执行权失效”

---

4. Celery 和 Redis 的关系

Celery 不是一个独立的服务器，它是 Python 库：Dispatcher 用它往 Redis 发消息，Worker 用它从 Redis 收消息

```
Celery = 后台任务框架
Redis  = Celery 使用的消息 Broker
```

可以想象成外卖：

```
Dispatcher
“有个 attempt 102 要执行”
       ↓
    Celery
  按任务规则发消息
       ↓
     Redis
  暂存这条任务消息
       ↓
 Celery Worker ( Worker 的数量可由自己设定 )
 从 Redis 拿任务
       ↓
执行 attempt 102
```

所以物理上消息存在：**Redis**

但：**Celery 帮你管理“怎么发任务、Worker 怎么消费任务”**

项目计划中 Redis 的第一个职责就是 **Celery 的消息队列**

---

5. 原子操作问题

其实项目目前很多需要原子操作的地方，都被数据库本身自带的原子操作给代劳了

---

6. Redis 在该项目的职责

共有四种职责：

```
Redis
│
├─ Celery Broker
│  → “哪些任务等 Worker 执行？”
│
├─ Pub/Sub (这一步纯转发，不会存储)
│  → “Agent 刚刚吐了什么 token？”
│
├─ 限流 / Lua 信号量 (使用方法类似 ROS 的参数服务器，但主要价值是在"原子性" (检查和加一一次完成，不会被别的请求插进来))
│  → “这个用户还能请求吗？”
│  → “现在还能再调用一个 LLM 吗？”
│
└─ Cache
   → “这段经常查询的证据原文我有没有？” (只有这里可以准备面试问的: redis 缓存穿透 / 击穿 / 雪崩 问题)
```

**都是 Redis，但四件事互相不是一套机制**

