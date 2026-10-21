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
