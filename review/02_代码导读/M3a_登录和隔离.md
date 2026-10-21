# 代码导读 · M3a：登录和隔离

方案见 docs/PLAN.md §5.10（M3a 部分）、§5.5，DECISIONS D30。运行方式见 `../01_运行链路.md`。

一句话：以前所有请求都算作开发用户 `dev`（`api.py` 里写死的 `DEV_USER_ID = 1`）。M3a 加上账号密码登录：
登录成功后服务端发一张随机“通行证”（session 令牌）放进 HttpOnly cookie，之后每个请求从 cookie 查出“你是谁”，
别人的题照样 404；再加两道防线——改数据的请求检查 `Origin`（防跨站伪造请求）、登录按“用户名 + IP”限流（防暴力猜密码）。

前半部分是总览（M3a-8 收尾时补），后半部分“逐步记录”是每个提交的说明（改了什么、为什么、例子、验证、review 重点）。
链接都是相对本文件的路径，带行号（行号对应写那一节时的那个提交）。

## 总览

（M3a-8 收尾时补。）

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
