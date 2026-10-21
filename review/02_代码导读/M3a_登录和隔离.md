# 代码导读 · M3a：登录和隔离

方案见 docs/PLAN.md §5.10（M3a 部分）、§5.5，DECISIONS D30。运行方式见 `../01_运行链路.md`。

一句话：以前所有请求都算作开发用户 `dev`（`api.py` 里写死的 `DEV_USER_ID = 1`）。M3a 加上账号密码登录：
登录成功后服务端发一张随机“通行证”（session 令牌）放进 HttpOnly cookie，之后每个请求从 cookie 查出“你是谁”，
别人的题照样 404；再加两道防线——改数据的请求检查 `Origin`（防跨站伪造请求）、登录按“用户名 + IP”限流（防暴力猜密码）。

前半部分是总览（M3a-8 收尾时补），后半部分“逐步记录”是每个提交的说明（改了什么、为什么、例子、验证、review 重点）。
链接都是相对本文件的路径，带行号（行号对应写那一节时的那个提交）。

## 总览

**对应提交**：`83d5349`（M3a-0）… M3a-8，按 §5.10 的拆分一个提交一件事，共 10 个：实验 A1～A6 没有发现代码问题；
收尾前重跑测试发现限流的一个测试时间余量太小，单独修了（`5bd7f0b` M3a-4a，见 M3a-8 一节）。
阶段报告（为什么做、一次登录怎么走、实验截图、待确认的取舍）：[../M3a_阶段报告.html](../M3a_阶段报告.html)。

**改动的文件**（不含文档）：

| 位置 | 文件 | 做什么 |
|---|---|---|
| 数据库 | [migrations/versions/0008_users_password_sessions.py](../../migrations/versions/0008_users_password_sessions.py)、[models.py](../../src/margin/models.py) | `users.password_hash`；`sessions` 表（只存令牌的 SHA-256） |
| 后端 | [auth.py](../../src/margin/auth.py)（新） | 密码哈希、建 / 改账号、验密码、建 / 查 / 删 session |
| 后端 | [ratelimit.py](../../src/margin/ratelimit.py)（新） | Redis 滑动窗口限流（一段 Lua） |
| 后端 | [api.py](../../src/margin/api.py) | `/auth/login`、`/auth/logout`、`/auth/me`；`current_user` 依赖代替 `DEV_USER_ID`；全局的 Origin 检查 |
| 后端 | [settings.py](../../src/margin/settings.py)、[.env.example](../../.env.example) | 允许的 Origin、cookie 是否 Secure、限流次数和窗口 |
| 脚本 | [scripts/create_user.py](../../scripts/create_user.py)（新） | 建账号 / 改密码（交互输入，不进命令行历史） |
| 前端 | [pages/LoginPage.tsx](../../web/app/src/pages/LoginPage.tsx)、[styles/login.css](../../web/app/src/styles/login.css)（新） | 登录页（设计稿方案 A） |
| 前端 | [auth.ts](../../web/app/src/auth.ts)（新）、[api.ts](../../web/app/src/api.ts)、[App.tsx](../../web/app/src/App.tsx)、[router.ts](../../web/app/src/router.ts) | 统一处理 401、登录后回原页面、启动时问当前用户 |
| 前端 | [Sidebar.tsx](../../web/app/src/components/Sidebar.tsx)、[useRunStream.ts](../../web/app/src/useRunStream.ts) | 侧栏账号和退出；事件流断开时查一次是否过期 |
| 设计稿 | [web/design/login.html](../../web/design/login.html)（新） | 三个方案 × 五种状态 × 深浅色 × 窄屏 |
| 测试 | `tests/` 5 个新文件 + `conftest.py`、`test_api_pg.py`；`web/app/e2e/` 假 API、`support.ts`、`auth.spec.ts` | 见下方测试地图 |

**阅读顺序**（从里到外）：

1. [auth.py](../../src/margin/auth.py) 开头的 docstring（为什么用服务端 session、为什么只存哈希），再看 `authenticate` → `create_session` → `resolve_session`。
2. [api.py](../../src/margin/api.py#L62-L147)：`check_origin`、`set_session_cookie`、`current_user`、`get_login_limiter`；再看 [login](../../src/margin/api.py#L254) 一个函数里的先后顺序。
3. [ratelimit.py](../../src/margin/ratelimit.py)：Lua 脚本。
4. 前端 [api.ts](../../web/app/src/api.ts) 的 401 处理 → [App.tsx](../../web/app/src/App.tsx) 的 `who` → [LoginPage.tsx](../../web/app/src/pages/LoginPage.tsx)。
5. 测试：先看 [test_auth_api_pg.py](../../tests/test_auth_api_pg.py) 和 [auth.spec.ts](../../web/app/e2e/auth.spec.ts)，它们把行为从外面描述了一遍。

**一次登录的数据流**（compose + Vite，浏览器打开 `http://localhost:5173/runs/196`）：

```text
浏览器                         Vite(5173)            API(8000)                                   PG / Redis
GET /api/auth/me（无 cookie） ──转发──▶ current_user：没有 cookie → 401
  ◀── 401：api.ts 统一处理 → 之前不知道是谁 → 不算过期 → 替换成 /login?next=%2Fruns%2F196
POST /api/auth/login {username, password}，Origin: http://localhost:5173
                               ──转发──▶ check_origin：Origin 在允许列表里 → 放行
                                         login：hit("login:test_a:172.18.0.1")  ──Lua──▶ Redis：删窗口外的、数一数、记一次
                                                authenticate：查 users、argon2 验密码（约 30 ms）──▶ PG
                                                create_session：随机令牌；存 SHA-256、删这个人过期的 ──▶ PG sessions
                                                reset(限流键) ──▶ Redis DEL
  ◀── 200 {username}，Set-Cookie: margin_session=<令牌>; HttpOnly; Max-Age=604800; Path=/; SameSite=lax
前端：替换成 next=/runs/196
GET /api/runs/196（浏览器自动带 cookie）──▶ current_user：resolve_session(SHA-256(令牌)) ──▶ PG：session 有效 → user_id=3
                                         get_run(196, owner_id=3)：是自己的 → 200；别人的 → 404
GET /api/runs/196/events（EventSource 也自动带 cookie）──▶ 同上，之后照常推事件
```

**测试地图**（一个测试对应一个会出事故的场景）：

| 场景 | 测试 |
|---|---|
| 密码只存加盐哈希，只有对的密码能验过 | `test_auth.py` · `test_password_hash_is_salted_and_only_the_right_password_verifies` |
| 不合规的用户名 / 太短的密码建不了 | `test_auth.py` · `test_bad_new_account_is_rejected_with_a_reason` |
| 给 dev 设密码时 id 不变、旧题仍归 dev | `test_auth_pg.py` · `test_setting_dev_password_keeps_its_id_and_old_runs` |
| 改密码后已登录的地方全部下线 | `test_auth_pg.py` · `test_changing_password_logs_out_existing_sessions` |
| cookie 是 HttpOnly、库里只有令牌的哈希 | `test_auth_api_pg.py` · `test_login_sets_httponly_cookie_and_stores_only_its_hash` |
| 用户不存在 / 密码不对 / 没设密码，返回一模一样 | `test_auth_api_pg.py` · `test_failed_login_gets_the_same_401_and_no_cookie` |
| 没 cookie、乱写的 cookie 访问各接口都是 401 | `test_auth_api_pg.py` · `test_requests_without_a_valid_session_get_401` |
| 退出后旧 cookie 立刻失效（A2 的自动化版） | `test_auth_api_pg.py` · `test_logout_makes_the_old_cookie_useless` |
| 过期的 session 401，下次登录顺手清掉 | `test_auth_api_pg.py` · `test_expired_session_gets_401_and_next_login_cleans_it_up` |
| 顺延一天最多一次（不是每个请求都写库） | `test_auth_api_pg.py` · `test_session_is_renewed_at_most_once_a_day` |
| 别人的题 404、列表只有自己的（A1 的自动化版） | `test_api_pg.py` · `test_other_users_run_returns_404`、`test_run_list_shows_only_my_runs_newest_first` |
| 别的网站 / 没有 Origin 的提交被拒、不建题（A4 的自动化版） | `test_csrf_pg.py` · `test_post_from_another_site_is_rejected_and_creates_nothing`、`test_every_kind_of_post_is_checked` |
| cookie 是 SameSite=Lax，配置了才 Secure | `test_csrf_pg.py` · `test_session_cookie_is_samesite_lax_and_secure_only_when_configured` |
| 并发时限流不被冲破；窗口是滑动的 | `test_ratelimit_pg.py` · `test_concurrent_attempts_never_get_past_the_limit`、`test_window_slides_instead_of_resetting_all_at_once` |
| 第 11 次 429，密码对也不放（A5 的自动化版） | `test_ratelimit_pg.py` · `test_eleventh_login_attempt_gets_429_even_with_the_right_password` |
| Redis 挂了登录 503，已登录的人不受影响 | `test_ratelimit_pg.py` · `test_login_is_refused_when_redis_is_down` |
| 前端：登录后只回本站页面（防开放重定向） | `auth.test.ts` · `登录后只回本站的页面…` |
| 前端：没登录跳登录页、登录后回原页面 | `e2e/auth.spec.ts:16` |
| 前端：A 的题 B 看不到 | `e2e/auth.spec.ts:29` |
| 前端：密码不对 / 被锁住、到点恢复 | `e2e/auth.spec.ts:47` |
| 前端：执行中登录过期，跳登录页并回来接着看（A3 的自动化版） | `e2e/auth.spec.ts:69` |
| 前端：退出 | `e2e/auth.spec.ts:83` |

合计：后端单元 71、集成 89；前端单元 23、端到端 33；ruff、lint 无报错。

**实验结果**（M3a-7，compose 全套服务 + 学校网关 DeepSeek，详细记录见“逐步记录”的 M3a-7 一节）：

| # | 实验 | 结果 |
|---|---|---|
| A1 | B 打开 A 的题 | 列表 `[]`；详情、事件流、重新生成都 404，和不存在的题一模一样；浏览器显示“不属于当前账号” |
| A2 | 退出后用旧 cookie | 退出 204、库里那一行删掉；旧令牌 `/auth/me`、`GET /runs`、`POST /runs` 全部 401 |
| A3 | session 改成过期再操作页面 | 点“重新生成”401 → `/login?next=%2Fruns%2F196&expired=1` 带“登录已过期”提示 → 登录后回到 run 196 |
| A4 | 别的网站的 Origin 提交 | 4 种错误 Origin（含不带、换端口、换写法）都 403、不建题；真浏览器跨站表单 403；跨站 POST 浏览器根本不带 cookie |
| A5 | 连续输错密码 | 第 11 次（密码是对的）429、Retry-After 587；页面倒计时、按钮不可点；窗口过去后能登录，限流记录清零 |
| A6 | 登录后提交看执行过程 | 23 秒完成，事件序列和登录前的同一道题完全一样，实时片段照常推 |

## 逐步记录

### M3a-0：登录页设计稿（3 个方案）

**改了什么**

- 新增 [web/design/login.html](../../web/design/login.html)：一个文件里画了三个方向不同的登录页，浏览器打开即可看
  （file:// 打不开时在 `web/` 下起一个本地 http 服务，再打开 `/design/login.html`）。
  设计变量直接引用前端正式的 [tokens.css](../../web/app/src/styles/tokens.css)，所以颜色、字体、圆角和网页完全一致。
- 右下角“设计评审”面板切换：方案（A / B / C）、状态（正常 / 登录中 / 密码不对 / 尝试太多 / 登录过期）、
  深浅色、宽度（整个窗口 / 手机 390px）。也能用地址参数直接打开，如 `login.html?variant=c&state=wrong&theme=dark&width=phone`。

三个方案：

| 方案 | 样子 | 优点 | 缺点 |
|---|---|---|---|
| A 居中卡片 | 页面中间一张卡片，材质和网页的输入框一样（表面色 + 细边框 + 大圆角），提示固定在密码框下面 | 最常见，用户一看就懂；所有状态的提示在同一个位置；窄屏就是一张满宽卡片 | 最普通，没有产品特色 |
| B 左右两栏 | 左边示意产品“带引用的回答”（结论大字 + 带 [n] 的正文 + 页边旁注），右边表单 | 登录前就能看出这个产品做什么 | 示例是写死的假数据，要和产品一起维护；窄屏放不下，只能整块隐藏 |
| C 页边批注 | 没有卡片，表单像研究稿正文（输入框只有下划线），提示写在右侧页边，像朱批 | 和网页“页边批注”的版式最呼应，最有特色 | 输入框和网页里的输入框（圆角框）长得不一样；宽屏时提示离输入框远，视线要左右跳 |

五种状态在三个方案里的画法一样：

| 状态 | 画法 |
|---|---|
| 正常 | 用户名、密码、登录按钮；页脚“账号由管理员创建”（没有注册入口，D30） |
| 登录中 | 按钮变成转圈 +“登录中…”、不能再点；输入框只读（防止连点两次、或者提交后又改了内容） |
| 用户名或密码不对 | 密码框标红并清空；**不说是用户名错还是密码错**（否则等于告诉别人“这个用户名存在”） |
| 尝试太多 | “尝试次数太多，请 n 分钟后再试”，按钮不能点（网页里倒计时到 0 恢复） |
| 登录已过期 | 表单上方一条主色浅底的通知（不是红色错误）：“登录已过期，请重新登录。登录后会回到刚才打开的页面。” |

**为什么**

- 交接要求先出 2～3 个方向明显不同的方案，作者不在时由执行会话自己选一个做进网页（M3a-5），其余留在设计稿里。
- 窄屏用容器查询（`@container`）而不是媒体查询：面板的“手机”按钮只把舞台缩到 390px，浏览器窗口还是宽的，
  媒体查询看的是窗口宽度、不会切换；容器查询看的是舞台宽度，所以“缩舞台”和“真的用手机打开”效果相同。

**选了哪个做进网页：方案 A（居中卡片）**，理由记在 STATUS“执行中的取舍”第 28 条：

1. 和现有网页最一致：网页里唯一的“框”就是输入框（`.composer`），A 的卡片和输入框是同一种材质，聚焦样式也一样
   （主色边框 + 一圈主色浅底）；C 的下划线输入框是网页里没有的新样式。
2. 状态最清楚：所有提示在同一个位置（密码框下、按钮上），登录过期的通知在表单最上面，一眼能看到；
   C 的提示分散在右侧页边，宽屏时离输入框约 400px。
3. 窄屏最稳：A 在 390px 宽时就是一张满宽卡片，什么都不用藏；B 要整块隐藏左边的示意，C 要把页边批注挪到行下面。
4. 改起来最小：作者不满意时只换登录页组件和它的样式（`LoginPage.tsx`、`login.css`），后端和路由都不用动。

**一个具体例子**

用户在 `/runs/12` 页面开着，过了 7 天 session 过期，点“重新生成”收到 401 → 跳到 `/login?next=/runs/12&expired=1` →
看到方案 A 的卡片，最上面是“登录已过期”的通知 → 输错密码，密码框标红清空、下面出现“用户名或密码不对。” →
输对后按钮转圈“登录中…” → 回到 `/runs/12`。（跳转和回到原页面在 M3a-5 实现。）

**怎么验证的**

用 Playwright CLI 在本地 http 服务上截图自查：3 个方案 × 5 种状态（浅色）、3 个方案的深色、3 个方案在 390px 宽下的两种状态，
共 24 张。390px 宽时 `document.documentElement.scrollWidth` 都是 390（没有横向滚动）。
截图过程中发现一处问题已改：提示元素自己设了 `display: flex`，压过了 `hidden` 属性，导致所有提示同时出现；
加了 `[hidden] { display: none !important; }`。

**建议重点看**

- 设计稿底部脚本的 `paint`：每种状态改了哪些东西（提示、按钮文字、是否禁用、输入框只读、密码框标红清空），
  M3a-5 的 React 组件照着它做。
- “密码不对”为什么不区分用户名和密码。

### M3a-1：迁移 0008——密码哈希、sessions 表；建账号脚本

**改了什么**

- [migrations/versions/0008_users_password_sessions.py](../../migrations/versions/0008_users_password_sessions.py)：
  `users` 加一列 `password_hash`（可以为空），新表 `sessions`（`user_id`、`token_hash`、`created_at`、`expires_at`），
  `user_id` 上建普通索引、外键 `ON DELETE CASCADE`。
- [models.py:43-72](../../src/margin/models.py#L43-L72)：`User.password_hash`；新类 `LoginSession`（表名 `sessions`。
  不叫 `Session` 是因为 SQLAlchemy 的“数据库会话”也叫 `Session`，同一个文件里两个 `Session` 会看糊涂）。
- 新文件 [auth.py](../../src/margin/auth.py)：
  - [hash_password / verify_password](../../src/margin/auth.py#L31-L39)：argon2id，库是新加的 `argon2-cffi`（D31）；
  - [check_new_account](../../src/margin/auth.py#L42)：用户名只许字母数字和 `. _ -`、最长 32；密码至少 8 位；
  - [set_password](../../src/margin/auth.py#L51)：用户名存在就换密码（同一事务删掉他的全部 session），不存在就新建。
- 新脚本 [scripts/create_user.py](../../scripts/create_user.py)：`python scripts/create_user.py <用户名>`，密码用 `getpass`
  输两遍（不回显）；标准输入不是终端时（实验脚本用管道传）按行读两行。
- [tests/conftest.py](../../tests/conftest.py#L119)：每个测试前还要清 `sessions`、删掉 dev 以外的用户、把 dev 的密码清空。
- `pyproject.toml` 加 `argon2-cffi`，DECISIONS 新增 D31。

**为什么**

- 密码不能明文存，也不能用 SHA-256 这种快哈希存：库泄露后，攻击者用显卡每秒算几十亿次，拿常见密码表一比就出来了。
  argon2 每次要约 30 毫秒（本机实测 31 毫秒）、占 64MB 内存，还自带随机盐，见 auth.py 开头的注释和 D31。
- `password_hash` 可以为空：迁移时 dev 用户已经存在，迁移脚本不能替作者编一个密码（编了就得写进仓库）。
  空哈希 = 登录不了，作者用脚本设上密码后继续用，旧题都还归 id=1。
- sessions 表只存令牌的 SHA-256：令牌在浏览器的 cookie 里，库被拖走时，攻击者拿到的哈希不能直接塞进 cookie 用。
  令牌本身是 32 字节随机数（M3a-2 生成），没有“常见令牌”可以猜，所以普通的快哈希就够。
- 改密码时删掉全部 session：会改密码通常是怀疑泄露了，只让旧密码作废、却让别人已经登录着的浏览器继续用，等于没改。
- 用户名字符限制：用户名会拼进 M3a-4 限流的 Redis 键（`login:<用户名>:<IP>`）、也会出现在页面和日志里，不要空格和冒号。
- 脚本支持管道输入：`getpass` 只能从终端读，实验（M3a-7）要在脚本里建 `test_a`、`test_b` 两个账号。
  管道输入时密码同样不出现在命令行参数里（`ps` 看不到）。注意要用 `conda run --no-capture-output`，
  不加这个参数 conda 不把标准输入传给 Python。

**一个具体例子**

```text
> conda run -n margin --no-capture-output python scripts/create_user.py dev
密码：（输入不显示）
再输一遍：
已改密码：dev（id=1）

库里：users(1, 'dev', '$argon2id$v=19$m=65536,t=3,p=4$<22 个字符的盐>$<43 个字符的哈希>')
```

两次输得不一样 → “两次输入的密码不一样，没有修改”，退出码 1，库里不变（实测）。

**怎么验证的**

- 单元测试 [test_auth.py](../../tests/test_auth.py)：
  - `test_password_hash_is_salted_and_only_the_right_password_verifies`：同一个密码两次哈希不同、哈希里没有明文、错一个字符验不过；
  - `test_bad_new_account_is_rejected_with_a_reason`（5 种：短密码、中文、空格、空、超长用户名）和 `test_valid_new_account_passes`。
- 集成测试 [test_auth_pg.py](../../tests/test_auth_pg.py)：
  - `test_setting_dev_password_keeps_its_id_and_old_runs`：给 dev 设密码后 users 还是 1 行、旧题的 owner 还是 1；
  - `test_new_username_creates_an_account`；
  - `test_changing_password_logs_out_existing_sessions`：改 test_a 的密码后他的 session 没了，test_b 的还在。
- 迁移：conftest 每次从空库执行到 0008；开发库 `alembic upgrade head` 后 `alembic check` 无差异。
- `pytest`（71）、`pytest -m integration`（57）、ruff 全部通过。

**建议重点看**

- `set_password` 里先 `UPDATE ... RETURNING id`、没更新到再 `INSERT`：一条 UPDATE 就知道用户在不在，不用先查一次。
- 为什么 `password_hash` 可以为空、空的时候意味着什么（M3a-2 的 `authenticate` 会处理）。

### M3a-2：登录、退出和当前用户

**改了什么**

- [auth.py](../../src/margin/auth.py)（开头的 docstring 讲了 session 是什么、和 JWT 的区别）新增：
  - [authenticate](../../src/margin/auth.py#L98)：查用户、验密码；用户不存在或还没设密码时也拿假哈希
    [_dummy_hash](../../src/margin/auth.py#L94) 空算一次（取舍 29）；
  - [create_session](../../src/margin/auth.py#L119)：`secrets.token_urlsafe(32)` 生成令牌，存它的 SHA-256，过期时间 = 数据库 `now()` + 7 天；
    同一事务删掉这个用户已过期的 session（取舍 31）；
  - [resolve_session](../../src/margin/auth.py#L135)：令牌 → 当前用户；剩余有效期不到 6 天才顺延（取舍 30），
    返回值里带“这次顺延了没有”；
  - [delete_session](../../src/margin/auth.py#L161)：退出。
- [api.py](../../src/margin/api.py)：
  - 删掉 `DEV_USER_ID`；新依赖 [current_user](../../src/margin/api.py#L86)：从 cookie `margin_session` 取令牌 → `resolve_session`，
    查不到就 `HTTPException(401, "请先登录")`；顺延了就用 [set_session_cookie](../../src/margin/api.py#L79) 重发 cookie；
  - `User` 类型从 `int` 变成 `CurrentUser`（id + 用户名），各路由改传 `user.id`；
  - 新路由 [POST /auth/login](../../src/margin/api.py#L209)、[POST /auth/logout](../../src/margin/api.py#L220)、
    [GET /auth/me](../../src/margin/api.py#L229)。
- 测试：
  - [conftest.py](../../tests/conftest.py#L137) 新夹具 `make_client(用户名)`：建账号、真的调 `/auth/login`，返回带 cookie 的 TestClient；
    `client` = 已登录的 dev。原来测“别人的题”靠 `dependency_overrides[current_user] = lambda: 999` 假装换人，
    现在换成第二个真登录的客户端 `make_client("test_b")`。
  - 新文件 [test_auth_api_pg.py](../../tests/test_auth_api_pg.py)：7 个测试函数（参数化后 19 个用例），见下面“怎么验证的”。

**为什么**

- **为什么用服务端 session 而不是 JWT**（PLAN §5.5）：浏览器的 EventSource（SSE）不能带自定义请求头，只能靠 cookie；
  JWT 签出去就收不回来，退出、改密码要另做黑名单；session 删一行就失效。
- **为什么 401 而不是跳转**：接口只返回状态码，跳不跳登录页是前端的事（M3a-5）。
- **别人的题仍然 404**：查询本来都带 `owner_id`（`runs.py`），只是 owner 从写死的 1 变成了 cookie 里查出来的人，所以这里不用改查询。
- **顺延为什么要重发 cookie**：cookie 自己有 `Max-Age=604800`（7 天），从下发那一刻算。服务端把 session 续到第 14 天，
  浏览器第 7 天还是会把 cookie 删掉，用户照样被登出。
- **FastAPI 的“临时响应”**：依赖函数声明一个 `response: Response` 参数，在上面 `set_cookie`，FastAPI 会把它的头合并进路由最后的响应。
  但路由自己 `return StreamingResponse(...)` 时不合并（FastAPI 的规则），所以 SSE 那一路顺延了也不会重发 cookie，见取舍 30。

**一个具体例子**

```text
POST /auth/login {"username":"test_a","password":"…"}
  → authenticate：argon2 验证通过（约 30 毫秒）
  → create_session：令牌 Qm9…（43 个字符），库里 INSERT sessions(user_id=2, token_hash=sha256(令牌), expires_at=now()+7天)
  ← 200 {"username":"test_a"}
     Set-Cookie: margin_session=Qm9…; HttpOnly; Max-Age=604800; Path=/; SameSite=lax

GET /runs（浏览器自动带上 Cookie: margin_session=Qm9…）
  → current_user：sha256(Qm9…) 查 sessions → (user_id=2, 剩 6 天 23 小时) → 不顺延
  ← 200 [...只有 test_a 的题]

（5 天后）GET /runs → 剩 2 天 < 6 天 → UPDATE expires_at = now()+7天 → 响应里重发 Set-Cookie

POST /auth/logout → DELETE 这一行 → Set-Cookie: margin_session=""; Max-Age=0
之后拿旧令牌 → 查不到 → 401 {"detail":"请先登录"}
```

**怎么验证的**

[test_auth_api_pg.py](../../tests/test_auth_api_pg.py)：

| 测试 | 防的事故 |
|---|---|
| `test_login_sets_httponly_cookie_and_stores_only_its_hash` | cookie 没加 HttpOnly 被脚本偷走；库里存了令牌原文 |
| `test_failed_login_gets_the_same_401_and_no_cookie`（密码错 / 没这个人 / dev 没设密码） | 提示语不同让人试出用户名；失败也下发了 cookie |
| `test_requests_without_a_valid_session_get_401`（6 个接口 × 不带 / 编造 cookie） | 漏了某个接口没加登录检查 |
| `test_logout_makes_the_old_cookie_useless` | 退出只删了浏览器的 cookie、服务端没删，抄下来的 cookie 还能用（实验 A2 的自动化版） |
| `test_logout_without_session_still_succeeds` | 重复退出报错 |
| `test_expired_session_gets_401_and_next_login_cleans_it_up` | 过期了还能用；过期记录越积越多 |
| `test_session_is_renewed_at_most_once_a_day` | 在用的人 7 天后被登出；每个请求都写库 |

[test_api_pg.py](../../tests/test_api_pg.py) 里原有的两个“别人的题”测试改成两个真实账号。
`pytest`（71）、`pytest -m integration`（77）、ruff 全部通过。

前端这时还没有登录页（M3a-5 才做），compose 上打开网页会因为 401 什么都看不到，这是中间状态。

**建议重点看**

- `resolve_session` 为什么用数据库的 `now()` 而不是 Python 的 `datetime.now()`：API 容器和 PG 容器的时钟可能差几秒，
  “过期了没有”只认一个时钟。
- `current_user` 在 SSE 路由里怎么生效：它是同步函数，FastAPI 放到线程池里跑，不会卡住异步的事件流。
- `authenticate` 里 `_dummy_hash()` 带 `@cache`：进程里第一次用到时算一次（30 毫秒），之后复用。

### M3a-3：检查 Origin 头；cookie 属性

**改了什么**

- [api.py:61-83](../../src/margin/api.py#L61-L83)：`check_origin` 依赖——请求方法不是 GET / HEAD / OPTIONS 时，
  `Origin` 头必须在配置 `allowed_origins` 里，否则 `403 请求来源不在允许列表里`；没有 Origin 头也是 403（取舍 33）。
  挂在 `FastAPI(dependencies=[Depends(check_origin)])` 上，每个路由都先跑它（取舍 34）。
- [set_session_cookie](../../src/margin/api.py#L104)：cookie 属性写全并逐个注释——`HttpOnly`、`SameSite=Lax`、`Path=/`、
  `Max-Age=604800`、`Secure`（由配置 `cookie_secure` 决定）。退出时 [delete_cookie](../../src/margin/api.py#L253) 用同样的属性。
- [settings.py:56-62](../../src/margin/settings.py#L56-L62)：`allowed_origins`（默认 `["http://localhost:5173"]`）、
  `cookie_secure`（默认 false）；`.env.example` 加说明。
- 测试：conftest 的 `make_client` 像浏览器一样给每个请求带 `Origin: http://localhost:5173`；
  新文件 [test_csrf_pg.py](../../tests/test_csrf_pg.py) 4 个测试函数（参数化后 6 个用例）。

**为什么**

跨站伪造请求（CSRF）：你登录着 Margin，又打开了一个坏网站，它的页面里藏一个自动提交的表单，向 Margin 发 POST。
浏览器发请求会自动带上 Margin 的 cookie——服务端只看 cookie 的话，分不出是你点的还是坏网站发的。两道防线：

| 防线 | 原理 | 挡不住的情况 |
|---|---|---|
| `SameSite=Lax` | 请求的发起页面和目标不是同一个“站点”时，浏览器不带这个 cookie（点链接跳转的 GET 除外） | 老浏览器不认这个属性；同一站点的其他子域名（如 `evil.margin.example` 发给 `margin.example`）算“同站” |
| 检查 `Origin` | 浏览器给 POST 加的 `Origin` 头写着发起页面的来源（协议 + 域名 + 端口），页面里的脚本改不了 | 只认浏览器；所以没有 Origin 的一律拒绝（取舍 33） |

为什么 GET 不检查：GET 按约定不改数据；坏网站即使让浏览器发了 GET，也读不到响应（同源策略），Lax 下也不带 cookie。

`Secure` 为什么做成配置：`Secure` 的 cookie 浏览器只在 HTTPS 上发，开发时是 `http://localhost`，打开了就谁都登录不了；
M6 上 HTTPS 后把 `MARGIN_COOKIE_SECURE=true` 打开。

为什么来源列表是配置项：网页从哪个地址打开，Origin 就是什么。开发时是 Vite 的 `http://localhost:5173`
（Vite 转发 `/api` 时原样保留 Origin 头）；M6 换成 Nginx 后的域名。端口不同也算不同来源（`localhost:5174` 会被拒）。

**一个具体例子**

```text
坏网站 https://evil.example 的页面：
  <form action="http://localhost:5173/api/runs" method="post"> … 自动提交
浏览器发出：POST /api/runs   Origin: https://evil.example   （Cookie：SameSite=Lax，跨站 POST 不带）
→ Vite 转发给 API → check_origin：https://evil.example 不在 ["http://localhost:5173"] 里 → 403，路由函数根本不执行，库里不多一道题

自己的网页：POST /api/runs   Origin: http://localhost:5173   Cookie: margin_session=… → 通过 → current_user → 202
curl 不加 Origin：→ 403；加 -H 'Origin: http://localhost:5173' 并带上 cookie → 202
```

**怎么验证的**

| 测试 | 防的事故 |
|---|---|
| `test_post_from_another_site_is_rejected_and_creates_nothing`（evil.example / 端口 5174 / 没有 Origin） | 跨站提交建了题（实验 A4 的自动化版） |
| `test_every_kind_of_post_is_checked` | 只给 `/runs` 加了检查，重新生成、登录、退出漏了 |
| `test_reads_are_not_checked` | 检查太宽，GET 也被拦（页面打不开） |
| `test_session_cookie_is_samesite_lax_and_secure_only_when_configured` | cookie 少了 SameSite；开发环境误开 Secure 导致登录不了 |

去掉 `check_origin` 后前两个测试失败（POST 返回 202）。`pytest`（71）、`pytest -m integration`（83）、ruff 全部通过。

**建议重点看**

- 全局依赖和路由依赖的执行顺序：全局的先执行，所以跨站请求在 `current_user` 查库之前就被拒了，返回 403 而不是 401。
- `check_origin` 的比较是整串相等：`http://localhost:5173` 和 `http://localhost:5173/`、`http://127.0.0.1:5173` 都不相等。
  浏览器发的 Origin 没有结尾的 `/`，所以配置里也不要写。

### M3a-4：登录限流（Redis 滑动窗口 + Lua）

**改了什么**

- 新文件 [ratelimit.py](../../src/margin/ratelimit.py)：开头的 docstring 讲了滑动窗口和为什么要 Lua。
  - [SLIDING_WINDOW](../../src/margin/ratelimit.py#L28)：一段 Lua 脚本——取 Redis 的时间 → 删掉窗口外的记录 → 数一数 →
    没超就记一次并返回 0，超了返回“最早那次还要多久滑出窗口”（毫秒）；
  - [SlidingWindowLimiter](../../src/margin/ratelimit.py#L43)：`hit(key)` 返回还要等几秒（0 = 放行），`reset(key)` 清零。
- [api.py](../../src/margin/api.py)：
  - [get_login_limiter](../../src/margin/api.py#L142)：每个进程一个限流器（`@cache`），参数来自配置；
  - [login](../../src/margin/api.py#L255)：键 `login:<用户名>:<IP>`；先 `hit` 再验密码；超了 429 +
    `Retry-After` 头 + “请 n 分钟后再试”；成功后 `reset`；Redis 出错 503。
- [settings.py](../../src/margin/settings.py#L63-L66)：`login_max_attempts=10`、`login_window_seconds=600`。
- 测试：conftest 的 `make_client` 每个测试前清掉 Redis 里的 `login:*`；新文件 [test_ratelimit_pg.py](../../tests/test_ratelimit_pg.py) 6 个测试。

**为什么**

- **为什么要限流**：没有限流的话，攻击者可以每秒试几十个密码（每次只要 30 毫秒）。10 分钟 10 次，一天最多 1440 次，弱密码也要猜很久。
- **为什么按“用户名 + IP”**：只按 IP——同一个办公室出口 IP 的人互相连累；只按用户名——攻击者故意输错就能把别人锁住 10 分钟
  （拒绝服务）。两个一起，锁住的只是“这个 IP 上的这个用户名”，本人换个网络还能登。
- **为什么滑动窗口**：固定窗口（每整 10 分钟清零）在 9:59 和 10:01 各试 10 次，2 分钟里 20 次也不拦。
- **为什么用有序集合**：成员是每次尝试（随机名），分数是时刻。“删掉 10 分钟前的”就是 `ZREMRANGEBYSCORE`，“数一数”就是 `ZCARD`，
  最早一次的时刻用 `ZRANGE 0 0 WITHSCORES` 取，“还要等多久”= 它 + 10 分钟 − 现在，给前端显示“请 n 分钟后再试”。
- **为什么要 Lua**：见 ratelimit.py 开头。实测对照（见下面“怎么验证的”）：把三步拆成三条 Python 命令，30 个线程同时抢 10 个名额，
  5 次里有 4 次放进来 21～25 个。
- **时间为什么用 Redis 的 `TIME`**：以后 API 多开几个进程 / 机器，各自的时钟可能差几百毫秒，窗口边界会乱；只认 Redis 一个时钟。
- **为什么先计数再验密码**：被拦下的请求不再花 30 毫秒、64MB 内存算 argon2，攻击者没法靠狂发登录请求拖垮服务器。
  代价：输对密码的那一次也计数——所以成功后清零（取舍 35）。
- **Redis 挂了为什么 503**：见取舍 37。

**一个具体例子**

```text
（窗口 10 分钟、上限 10 次；Redis 里键 login:test_a:127.0.0.1）
12:00:00 起连续输错 10 次      → 每次脚本：删过期（无）→ ZCARD = 0…9 < 10 → ZADD → 返回 0 → 验密码失败 → 401
12:00:05 第 11 次（密码是对的） → ZCARD = 10 ≥ 10 → 最早一次 12:00:00.1 + 600 秒 − 12:00:05 = 595.1 秒
                                → 429  Retry-After: 596  {"detail":"尝试次数太多，请 10 分钟后再试"}
12:10:01 再试                  → 12:00:00.1 那次已经滑出窗口被删掉，ZCARD = 9 → 放行 → 密码对 → 200，DEL 这个键
```

**怎么验证的**

| 测试 | 防的事故 |
|---|---|
| `test_concurrent_attempts_never_get_past_the_limit` | 30 个请求同时到，三步不是原子的，放进来超过 10 个（对照实验：拆成三条命令时 5 次里 4 次放进 21～25 个） |
| `test_window_slides_instead_of_resetting_all_at_once` | 写成了固定窗口；或者窗口过了还一直锁着 |
| `test_eleventh_login_attempt_gets_429_even_with_the_right_password` | 被锁后密码对了就放行（等于没锁）；Retry-After 不对，前端显示的等待时间错（实验 A5 的自动化版） |
| `test_limit_counts_each_username_and_ip_separately` | 一个人输错把别人锁住；换个网络也登不上 |
| `test_successful_login_clears_earlier_failures` | 输错几次后登录上的人，之后很快被锁 |
| `test_login_is_refused_when_redis_is_down` | Redis 挂了时限流失效、可以无限猜；或者已登录的人也被连累 |

`pytest`（71）、`pytest -m integration`（89）、ruff 全部通过。

**建议重点看**

- Lua 脚本里 `TIME` 返回的是 `{秒, 微秒}` 两个字符串，拼成毫秒；`PEXPIRE` 让一个不再有人试的键自己消失，不用清理。
- `register_script` 的 EVALSHA：第一次把脚本发给 Redis，之后只发 40 个字符的 SHA1；Redis 重启后丢了缓存，redis-py 自动重发。
- `request.client.host` 在开发环境是什么（Vite / Docker 网关的地址），为什么键里必须带用户名（取舍 36）。

### M3a-5：前端——登录页、401 跳转、侧栏账号和退出

**改了什么**

- 新页面 [pages/LoginPage.tsx](../../web/app/src/pages/LoginPage.tsx) + 样式 [styles/login.css](../../web/app/src/styles/login.css)：
  设计稿方案 A（取舍 28），五种状态照设计稿做：登录中（转圈、只读）、密码不对（标红清空、焦点回密码框）、
  尝试太多（按 `Retry-After` 倒计时，取舍 41）、登录过期（地址里 `expired=1` 时顶上一条通知）、其他错误（连不上、503）。
- 新文件 [auth.ts](../../web/app/src/auth.ts)（纯函数）：`loginPath`（拼 `/login?next=…&expired=1`）、`safeNext`（登录后只回本站路径）、
  `lockedMessage`；单元测试 [auth.test.ts](../../web/app/src/auth.test.ts) 4 个。
- [api.ts](../../web/app/src/api.ts)：
  - [setUnauthorizedHandler](../../web/app/src/api.ts#L129)：App 注册一个处理函数，任何接口 401（登录接口自己的除外）都调用它（[第 146 行](../../web/app/src/api.ts#L146)）；
  - `ApiError` 多一个 `retryAfter`（429 的响应头）；204 没有响应体；
  - 新函数 `login`、`logout`、`getMe`。
- [App.tsx](../../web/app/src/App.tsx)：
  - 当前用户 `who`：`undefined`（正在问）/ `null`（没登录）/ 用户名（空串 = API 连不上、不知道是谁，取舍 38）；
  - 启动时注册 401 处理函数（[第 59 行](../../web/app/src/App.tsx#L59)）：之前知道用户名 → 是“过期”，带 `expired=1`；清空历史列表；跳登录页（替换历史记录，取舍 39）；
  - 启动时 `getMe()`（[第 68 行](../../web/app/src/App.tsx#L68)），问到前什么都不画；
  - `/login` 只画登录页（没有侧栏）；登录成功回 `next`；退出回登录页。
- [router.ts](../../web/app/src/router.ts)：多一个 `login` 页面；`navigate` 可以带查询参数、可以 `replace`。
- [Sidebar.tsx](../../web/app/src/components/Sidebar.tsx#L105)：底部账号——头像（用户名首字母）+ 用户名 +“退出”。
  图标栏收起时只露出头像（沿用设计稿 `preview.html` 的 `.account`）。
- [useRunStream.ts](../../web/app/src/useRunStream.ts#L101)：事件流被浏览器放弃（`readyState` 变成 CLOSED）时问一次 `getMe()`。
- 假 API [e2e/mock-api.mjs](../../web/app/e2e/mock-api.mjs)：先加一个总是返回 `dev` 的 `/auth/me`，现有 e2e 照常通过；真正的登录在 M3a-6。

**为什么**

- **401 统一在 api.ts 处理**：页面上发请求的地方有好几处（历史列表、题目详情、提交、重新生成、事件流），每处都写“401 就跳登录页”会漏。
  放在唯一的 `request()` 里，谁收到 401 都一样处理。
- **怎么分“没登录”和“登录过期”**：后端两种都是 401。前端记着“刚才知道当前用户是谁”——知道，说明之前登录着，现在是过期；
  一打开页面就 401，就是还没登录，不提示“过期”。（第一版用字符串 `'checking'` 表示“正在问”，`typeof === 'string'` 把它也当成了登录过，
  从没登录的人也看到“登录已过期”，浏览器里自查时发现，改成 `undefined`。）
- **`next` 为什么要检查**：地址栏谁都能改。不检查的话，别人发你一个 `…/login?next=https://钓鱼网站`，你登录后就被带过去了（开放重定向）。
- **事件流为什么要问 `/auth/me`**：EventSource 收到 401 只会触发 `error`、拿不到状态码，和 API 重启时的 502 长得一样。
  问一次 `/auth/me`：是 401 就由统一处理跳登录页（RunPage 卸载，重连随之取消），不是就照常 2 秒后重连。
- **退出请求失败也回登录页**：用户点了“退出”，就该看到退出了；cookie 还在的话，下次退出或 7 天后过期时清掉。

**一个具体例子**（compose 全套服务 + Vite，浏览器实测）

```text
打开 http://localhost:5173/runs/3（没登录）
  → GET /api/auth/me 401 → 统一处理：之前不知道是谁 → 不算过期 → 替换成 /login?next=%2Fruns%2F3
输入 test_a + 错密码 → POST /api/auth/login 401 → “用户名或密码不对。”，密码框清空、标红、焦点在密码框
输入正确密码 → 200，Set-Cookie: margin_session=…; HttpOnly → 替换成 /runs/3
  → run 3 是 dev 的题 → GET /api/runs/3 404 →“这道题不存在，或者不属于当前账号。”
页面里执行 document.cookie → ""（HttpOnly，脚本读不到）
鼠标移到侧栏 → 底部“T test_a 退出” → 点退出 → POST /api/auth/logout 204 → /login
```

**怎么验证的**

- 单元测试 `auth.test.ts`（4 个）：登录页地址带上原页面和过期标记、在登录页时不再带 next、外站地址一律回首页、分钟数向上取整。
- 浏览器实测（Playwright CLI，compose + Vite）：上面的例子逐步截图；手机宽度 390px 下登录页 `scrollWidth` = 390（没有横向滚动）。
- `npm run lint`、`npm test`（23）、`npm run build`（类型检查）、`npm run e2e`（28）全部通过。登录相关的端到端测试在 M3a-6。

**建议重点看**

- `App.tsx` 里 `whoRef`：401 处理函数只在启动时注册一次，函数里读的 state 会停在注册那一刻（闭包），所以“当前用户”另存一份在 ref 里。
- `LoginPage` 的 `submit` 里三种失败各改了哪些状态；`finally` 里恢复 `submitting`。
- `safeNext` 为什么连 `//evil.example` 也要拦（浏览器把 `//` 开头当成“同协议的另一个网站”）。

### M3a-6：端到端测试——假 API 加登录；“A 看不到 B”等场景

**改了什么**

- 假 API [e2e/mock-api.mjs](../../web/app/e2e/mock-api.mjs)：
  - 账号写死 [USERS](../../web/app/e2e/mock-api.mjs#L233)（`dev`、`test_b`、`test_c`、`test_lock`，密码都是 `e2e password`）；
  - [login](../../web/app/e2e/mock-api.mjs#L251)：和真 API 一样先计数再验密码，429 带 `Retry-After`，成功下发 HttpOnly cookie；
    `test_lock` 只能试 3 次、锁 2 秒（[limitOf](../../web/app/e2e/mock-api.mjs#L238)），测试不用等 10 分钟；
  - `/auth/logout`、`/auth/me`；没登录访问 `/auth/me`、`/runs…` 都 401（[第 328 行](../../web/app/e2e/mock-api.mjs#L328)）；
  - 每道题有 `owner`：已结束的题都属于 dev，实时的题属于提交它的人；历史列表只列自己的，别人的题 404（[第 356 行](../../web/app/e2e/mock-api.mjs#L356)）；
  - 测试用的控制接口 [POST /__expire?username=X](../../web/app/e2e/mock-api.mjs#L270)：让这个人的登录全部过期，并断开他正开着的事件流。
- [support.ts](../../web/app/e2e/support.ts#L15)：`loginAs(page, 用户名)` 用 `page.request` 直接调登录接口（和页面共用 cookie）；
  导出一个 `test`，每个测试开始前已经以 dev 登录。原有三个 spec 改从 `./support` 导入 `test`，测试内容一行没改。
- 新文件 [auth.spec.ts](../../web/app/e2e/auth.spec.ts) 5 个场景（下表）。

**为什么**

- 假 API 要“像真的一样”拒绝：只返回固定的 `dev`（M3a-5 的临时做法）的话，401 跳转、别人的题 404 这些浏览器里的行为都测不到。
- `loginAs` 走 `/api/auth/login`（Vite 的转发）而不是直接调假 API 的 8100 端口：cookie 记在哪个域名名下，要和页面一致（`localhost:5174`），
  否则页面的请求带不上。
- 已有的 28 个测试关心的是登录之后的页面，所以默认登录好；登录本身的测试用 Playwright 原本的 `test`，自己决定登不登录。
- 假 API 不检查 Origin：跨站请求只有后端能拦，已由 `test_csrf_pg.py` 覆盖；浏览器里的同源请求本来就带对的 Origin。

**场景和防的事故**

| 测试 | 防的事故 |
|---|---|
| 没登录打开一道题：跳到登录页（不提示“过期”），登录后回到这道题 | 没登录也能看到页面框架和历史；登录后被扔回首页，找不到刚才的题；从没登录过也提示“过期”（M3a-5 自查发现过） |
| A 的题 B 看不到 | 换了人，左栏还是上一个人的历史；别人的题能打开（含正在执行的题） |
| 密码不对 / 试太多被锁住、到点自动恢复 | 错误提示不出现、密码不清空；被锁时还能点；锁过了按钮一直灰着 |
| 执行中登录过期 | 事件流断线后对着 401 无限重连（页面一直“正在重连”），不跳登录页 |
| 退出 | 退出后再打开题目页还能看到内容 |

**怎么验证的**

- `npm run e2e`：33 个全部通过（原有 28 + 新增 5）；`npm run lint`、`npm test`（23）、类型检查通过。
- 对照：把 `useRunStream.ts` 里新加的 `getMe().catch(() => {})` 注释掉，“执行中登录过期”失败（15 秒内地址没变成登录页），还原后通过。
- 写的时候 lint 报了一个误报：Playwright 夹具的第二个参数文档里叫 `use`，oxlint 当成了 React 的 Hook，改名为 `provide`。

**建议重点看**

- `auth.spec.ts` 第二个测试用 `browser.newPage()` 开第二个人：不同的页面上下文 cookie 不共用，就像两个人各用各的浏览器。
- `/__expire` 为什么要顺便断开事件流：真 API 只在连接时检查登录，已经连着的流会一直推到这次执行结束；
  要测“过期后重连”这条路径，得让连接断一次（真实环境里是网络抖动、API 重启等）。

### M3a-7：实验验证 A1～A6（compose 全套服务 + Vite + 学校网关 DeepSeek，2026-10-22）

**环境**：M3a-6 之后的代码 `docker compose build` + `up -d`（迁移到 0008）；前端 `npx vite --port 5173`；
浏览器用 Playwright CLI（两个独立会话 = 两个人）；接口用 curl 直连 `127.0.0.1:8000`。
实验账号 `test_a`（id=3）、`test_b`（id=4）用 `scripts/create_user.py` 建，密码随机生成、只放在临时目录，做完删账号、删文件。
原始记录和截图在 `.cache/m3a-experiments/`（不进仓库）。API 看到的客户端 IP 都是 Docker 网关 `172.18.0.1`（Windows 上的 curl 和 Vite 都经它进来）。

按依赖关系实际做的顺序是 A6 → A1 → A2 → A4 → A5（开始）→ A3 → A5（窗口过去后）。

#### A6 登录后提交一道题看执行过程

- **操作**：浏览器打开 `/` → 被带到 `/login` → 以 test_a 登录 → 提交“中国建筑 2024 年新签合同额是多少亿元？”；
  完成后点“重新生成”，执行中每 250 毫秒采样一次思考文字的长度。
- **观察**：页面标题栏、步骤数；浏览器的 Resource Timing（`performance.getEntriesByType('resource')`）；API 日志；
  库里事件序列和登录前 dev 的同一道题（run 195，M1.6-9 时跑的）对比。
- **预期**：事件流正常（cookie 跟着 SSE 带上），和登录前一样。
- **实际**：run 196 第 9 秒“正在思考 · 已完成 3 步”，23 秒完成（5 步）。`/api/runs/196/events?after=0` 返回 200
  （这个接口不带有效 cookie 就是 401，所以 EventSource 确实带上了 cookie）；API 日志同一行 200。
  事件序列 `attempt_queued > attempt_started > run_understood > step ×5 > answer_written > attempt_finished`，和 run 195 完全一样。
  重新生成时思考文字长度 543→607→699→766→…→1062，一小段一小段地长，实时片段（delta）也在推。
- **结论**：通过。

#### A1 建 A、B 两个账号，A 提交一道题，B 打不开

- **操作**：A = test_a（run 196 是他的），B = test_b。curl 分别登录拿 cookie，再以 B、A 各访问一遍；浏览器另开一个会话以 B 登录。
- **预期**：B 在浏览器和 curl 里都打不开：列表里没有，详情、事件流、重新生成都是 404。
- **实际**（`a1_curl.txt`）：

  | 请求 | B（test_b） | A（test_a，对照） |
  |---|---|---|
  | `GET /runs` | `[]` | `[196]` |
  | `GET /runs/196` | 404 | 200 |
  | `GET /runs/196/events` | 404 | 200 |
  | `POST /runs/196/regenerate`（带正确 Origin） | 404 `{"detail":"run 不存在"}` | 没试（会真的再跑一次） |
  | `GET /runs/999999`（不存在的题，对照） | 404 `{"detail":"run 不存在"}`，和别人的题一模一样 | |

  浏览器 B：左栏历史 0 条，底部账号 test_b；直接打开 `/runs/196` 显示“这道题不存在，或者不属于当前账号。”（`a1_b_404.png`）。
  库里 run 196 仍是 2 次执行（B 的重新生成没有建出第 3 次）。
- **结论**：通过。

#### A2 退出后用旧 cookie 请求

- **操作**：test_b 用 curl 登录，从 cookie 文件里抄下令牌；`POST /auth/logout`；再手动带上旧令牌请求。
- **观察**：响应码；库里按令牌的 SHA-256 查这一行 session。
- **预期**：401。
- **实际**（`a2_curl.txt`）：退出前库里有这一行、`/auth/me` 200；退出返回 204 和
  `set-cookie: margin_session=""; …; Max-Age=0`；库里这一行没了；之后带旧令牌 `GET /auth/me`、`GET /runs`、`POST /runs` 全部 401。
- **结论**：通过。退出删的是服务端那一行，别人事先抄下的 cookie 也立刻作废。

#### A4 带别的网站的 Origin 发提交请求

- **操作**：test_a 已登录（cookie 有效），用 curl `POST /runs`，只改 Origin 头；前后数 test_a 有几道题。
- **预期**：被拒绝，不建题。
- **实际**（`a4_curl.txt`）：

  | Origin | 结果 |
  |---|---|
  | `https://evil.example` | 403 `请求来源不在允许列表里` |
  | 不带（curl 默认） | 403 |
  | `http://localhost:5174`（端口不同） | 403 |
  | `http://127.0.0.1:5173`（同一台机器，换个写法） | 403 |
  | `http://localhost:5173`（对照） | 202，建出 run 197 |

  被拒的四次前后 test_a 都是 1 道题，对照之后变 2 道。
- **补充：真浏览器里的跨站提交**（`a4_browser.txt`）：在 `127.0.0.1:8766` 放一个“别的网站”的页面，打开就自动提交表单到
  `http://localhost:5173/api/runs`。浏览器发出的请求 `origin: http://127.0.0.1:8766`，API 返回 403，库里没有这道题。
  再证明 SameSite：本机 `localhost:8767` 起一个只回显“收到了哪些 cookie 名字”的小服务（cookie 按域名、不按端口，localhost 的 cookie 会发给它），
  从“别的网站”提交表单过去 → `cookies=无`；从本站页面提交过去 → `cookies=['margin_session']`。
  也就是说跨站 POST 浏览器根本不带登录 cookie（第一道防线），就算带了，Origin 检查也会拒绝（第二道防线）。
- **结论**：通过。

#### A3 把 session 手动改成过期，再操作页面

- **操作**：浏览器 test_a 打开 `/runs/196`；psql `UPDATE sessions SET expires_at = now() - interval '1 second' WHERE user_id=3`；
  在页面上点“重新生成”；在登录页输入密码。
- **预期**：跳到登录页；登录后回到原来那道题。
- **实际**：`POST /api/runs/196/regenerate` 401 → 地址变成 `/login?next=%2Fruns%2F196&expired=1`，卡片顶上
  “登录已过期，请重新登录。登录后会回到刚才打开的页面。”（`a3_expired_login.png`）→ 登录 200 → 回到 `/runs/196`，两次执行都在（`a3_back.png`）。
  库里：改过期的两条 session（5、8）在重新登录时被 `create_session` 顺手删掉，只剩新的一条（9）；run 196 仍是 2 次执行（401 的重新生成没建出执行）。
- **结论**：通过。手动改过期的 session 已由重新登录自动清掉，不用另外还原。
- 第一次做时我用 `text=重新生成` 去点，点到的是“第 2 次执行（重新生成）”那行小标题，什么请求都没发；改用按钮选择器重做。是操作失误，不是代码问题。

#### A5 连续输错密码

- **操作**：test_b 用 curl 连续输错 10 次；第 11 次在浏览器里输入**正确**密码；隔一会儿再试一次；等最早那次滑出 10 分钟窗口，再点登录。
- **观察**：响应码和 `Retry-After`；Redis 里 `login:test_b:172.18.0.1` 的记录数（`ZCARD`）和过期时间；页面提示和按钮；API 日志的时刻。
- **预期**：第 11 次 429；窗口过去后能再试。
- **实际**（`a5_curl.txt`）：

  | 时刻 | 操作 | 结果 |
  |---|---|---|
  | 02:25:39.5～40.1 | curl 输错 10 次 | 10 次 401；Redis 10 条记录，键 598 秒后自己过期 |
  | 02:25:53.4 | 浏览器输入正确密码 | 429，`Retry-After: 587`；“尝试次数太多，请 10 分钟后再试。”，按钮不可点（`a5_locked.png`） |
  | 02:27:12 | 再试一次 | 429，`Retry-After: 507`；Redis 仍是 10 条（被拒的不计数） |
  | 02:27:57 | 看页面 | 倒计时显示“请 8 分钟后再试” |
  | 02:35:39.5 | （最早一次滑出窗口） | |
  | 02:35:41 | 看页面 | 按钮自动恢复、提示消失（前端算的是 02:25:53.4 + 587 秒 = 02:35:40.4，轮询间隔 2 秒） |
  | 02:36:03 | 点登录 | 200，进入首页，底部账号 test_b（`a5_after.png`）；Redis 里 `login:*` 一个都没有了（成功清零） |

  587 怎么来的：最早一次 02:25:39.54 + 600 秒 = 02:35:39.54，减去 02:25:53.36 是 586.2 秒，向上取整。
- **结论**：通过。被拒的请求不计数，所以锁着的时候一直试也不会越锁越久。

#### 实验没有发现代码问题

A1～A6 都一次通过，没有 M3a-7a 这类修复提交。顺带看到、按设计不改的：已经开着的事件流不会因为 session 过期被掐断
（只在连上时检查一次，断线重连时才发现过期、跳登录页，e2e“执行中登录过期”测的就是这条路径）；开发环境里所有人的 IP 都是
Docker 网关，限流靠键里的用户名区分人（取舍 36）。

#### 操作中出的一次事故（不是代码问题）

做 A1 时，我把 `curl --noproxy * -s` 存进了一个**没加引号**的 shell 变量再展开，`*` 被 shell 当成通配符换成了当前目录的文件名
（`README.md`、`AGENTS.md` 等），curl 把它们当成要访问的网址，经本机的 HTTP 代理把两次登录请求——请求体里有 test_a、test_b 的
**临时**密码——发给了这些同名的外部网站（返回的是 301 / 302 跳转页）。

- 当场处理：用 `create_user.py` 给两个账号换了新的随机密码（改密码会删掉他们所有 session，旧密码和可能泄露的 cookie 都作废），
  删掉相关 cookie 文件；之后 curl 一律写成 `c() { curl --noproxy '*' -s "$@"; }`，参数用 `"$@"` 原样传，不再经过通配符展开。
- 影响：只有这两个实验账号的随机临时密码，账号只存在于本机、本次实验，实验后已删除。`.env` 里的密钥没有出现在任何命令里。
- 教训：带 `*` 的参数一定加引号；往外发请求的命令先 `echo` 一遍展开后的样子再执行。

#### 还原

- 删掉 test_a、test_b（session 跟着删）和他们的题 run 196、197（连同 3 次执行、16 步、30 条事件、3 条 outbox）；
  现在 `users` 只剩 `dev`（仍然没有密码，等作者自己设）和 M2 时建的 `m2-other-user`，`sessions` 0 行，最大 run id 回到 195。
- A3 改过期的 session 已在重新登录时自动删掉；Redis 里没有 `login:*` 键。
- 临时密码文件、cookie 文件、“别的网站”和回显小服务都已删除 / 关掉；两个浏览器会话、Vite 已关。

### M3a-8：收尾

**改了什么**（只有文档）

- 本导读开头补“总览”：对应提交、改动文件、阅读顺序、一次登录的数据流、测试地图、实验结果表。
- 阶段报告 [../M3a_阶段报告.html](../M3a_阶段报告.html)（本地浏览器打开）：为什么做、改了什么、一次登录怎么走、
  登录页三个方案和选择、实验截图、发现的问题、取舍 28～41（每条“这是什么 → 我选了 → 另一种 → 代价 → 我的建议”，可以勾选记 review 进度）。
- STATUS：“所在阶段”、“M3a 记录”、“下一步”（作者 review M3a → M3b）、“本地环境备忘”里加了给 dev 设密码的命令；交接节标为已完成。
- PLAN §7 M3a 打勾；DECISIONS 新增 D32（实现时的取舍，待确认）。
- README：当前阶段、`auth.py` / `ratelimit.py`、建账号命令、curl 示例改成先登录并带 `Origin`；集成测试需要 PG 和 Redis。
- [运行链路](../01_运行链路.md)：调用链加 ⓪ 登录、① 的 Origin 检查；“Redis 挂了”补一句登录 503；服务表补 Redis 的登录限流。

**怎么验证的**

- 报告用 Playwright CLI 在浏览器里打开（限时的本地 http 服务），浅色、深色、手机宽度各看一遍：图片都能加载、没有横向滚动。
- 提交前 `pytest`、`pytest -m integration`、ruff、`npm run lint`、`npm test`、`npm run e2e` 再跑一遍；推送后 GitHub Actions 通过。

**收尾时发现并修掉的：限流测试偶尔失败（M3a-4a，`5bd7f0b`）**

提交前重跑 `pytest -m integration`，[test_window_slides_instead_of_resetting_all_at_once](../../tests/test_ratelimit_pg.py#L55) 失败一次
（第 2.1 秒那次本该放行，返回了“还要等 1 秒”）。查原因：测试用本机时钟 `sleep`，窗口按 Redis 的 `TIME` 算，
而本机的 Redis 跑在 WSL 虚拟机里——量了 5 次，Windows 睡 2.1 秒，Redis 只过 2.015～2.026 秒，慢约 4%。
原来那一步离窗口边界只留 0.1 秒，2.1 × 0.96 ≈ 2.02，再加上调用本身的抖动就可能还没到 2 秒。

- 改法：那一步改到第 2.5 秒，每个判断离边界都留 0.4 秒以上（能容忍 ±5% 的时钟差）；连跑 5 次通过。限流代码不用改。
- 为什么 CI 和 M3a-4 时没暴露：CI 里测试和 Redis 在同一台 Linux 上，同一个时钟；本机是两个时钟，偏差还会随 WSL 的时间同步变化。
- 对真实使用的影响：限流只按 Redis 一个时钟算，不受影响。前端的倒计时用的是浏览器的时钟，两边走得不一样快时，
  按钮可能早几秒恢复，这时点登录会再收到一个很短的 `Retry-After`、重新倒计时（A5 里按钮恢复的时刻和 Redis 算的只差 1 秒左右）。
