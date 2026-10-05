# M1 review

按技术点逐个 review，每章一个点：先讲它是什么，再结合项目代码举例，最后列出面试可能会问的地方。

---

## 一、数据库迁移（migrations / Alembic）

### 1. 一句话

`migrations/` 用来管理**数据库表结构的版本**，有点像给表结构用的 git：每次改表结构就新增一个版本文件，
按顺序执行，库就升级到最新结构，**原有数据保留**。工具是 Alembic（SQLAlchemy 官方配套的迁移工具）。

```
alembic.ini             告诉 Alembic：迁移脚本在 migrations/
migrations/
├─ env.py               入口：连哪个库（读 MARGIN_DATABASE_URL）、整个迁移放在一个事务里执行
├─ script.py.mako       生成新版本文件时用的模板
└─ versions/
   └─ 0001_initial.py   第一版：建出 M1 的 7 张表 + 预置开发用户 id=1
```

### 2. 先分清：models.py 和 migrations 各管什么

`models.py` 里的 model 是**数据模型（ORM 模型）**，不是 AI 模型。一个类对应一张表，一个对象对应一行：

```python
class Run(Base):            # ↔ runs 表
    question: Mapped[str]   # ↔ question 列

select(Run).where(Run.id == 100)   # SQLAlchemy 会翻译成 SELECT ... FROM runs WHERE id = 100
```

| | 给谁用 | 作用 |
|---|---|---|
| `models.py` | Python 代码 | 写查询时知道有哪些表、哪些字段 |
| `migrations/` | 数据库 | 把表**真正建出来、改出来** |

为什么不用 `Base.metadata.create_all()` 一键建表？因为它**只建不存在的表，不会修改已有的表**
（加列、加索引都做不了）。

ORM 不只是“少写 SQL”，它还负责：防 SQL 注入（参数绑定）、连接池、事务、屏蔽不同数据库的写法差异。

> 和 Java 对比：SQLAlchemy 的 ORM 用法更接近 **Hibernate / JPA**（用对象操作表）；
> **MyBatis** 是“自己写 SQL、帮你映射结果”，更像 SQLAlchemy Core 那一层。详见第 13 节。

### 3. 为什么需要迁移：例子

M1 已经有 7 张表和一些数据。M3 加登录，`users` 表要多一列 `password_hash`：

- 不用迁移：删库重建，**数据全没了**，线上绝对不行。
- 用迁移：新写一个 `0002`，只做这一件事：

```python
revision = "0002"
down_revision = "0001"            # 我接在 0001 后面

def upgrade():
    op.add_column("users", sa.Column("password_hash", sa.Text))
```

执行 `alembic upgrade head` 后：users 还是原来那些行，每行多了一列，值为 NULL。

### 4. 版本顺序由谁决定？head 是什么？

**不是按文件名字典序**，而是靠每个文件里的 `down_revision` 指针串起来，类似链表：

```
None ← 0001 ← 0002 ← 0003        head = 0003（没有任何版本指向它）
```

文件名里的 `0001` 只是方便人看（Alembic 默认生成的版本号是随机串，比如 `3fa85f64c2b1`）。

库里有一张 `alembic_version` 表，记录“当前升到了哪个版本”。执行 `upgrade head` 时只补缺的部分：

| 库当前版本 | `upgrade head`（head = 0002）做什么 |
|---|---|
| 空库 | 执行 0001 → 0002 |
| 0001 | 只执行 0002 |
| 0002 | 什么都不做（所以可以重复执行） |

**所以版本文件里不需要“表在不在”的判断**：能执行到 0002，说明 0001 一定已经执行过。
每个文件只写“比上一版改了什么”，就像 git 的一次 commit 只记录 diff。前提是：
**所有人都只通过迁移改表，不手动连上库改表结构。**

> 面试加分点：两个人在不同分支都写了 `down_revision="0001"`，合并代码后就有两个 head，
> `upgrade head` 会报 “Multiple head revisions”，要用 `alembic merge` 生成合并版本（和 git merge 同理）。

### 5. 什么时候运行？手动还是自动？会自动降级吗？

迁移**不会**在 api / worker / dispatcher 启动时运行，这些程序只管连库干活。

| 环境 | 怎么触发 |
|---|---|
| 本地开发 | 手动：`conda run -n margin alembic upgrade head` |
| Docker 部署 | 自动：compose 里有一次性的 `migrate` 服务，执行 `alembic upgrade head` 后退出；api / worker / dispatcher 等它成功后才启动 |
| 集成测试 | 自动：`conftest.py` 每次重建测试库并执行全部迁移 |

“哪个版本最新”是 Alembic 根据指针算出来的，Docker 和 yaml 只负责执行这条命令。

**`upgrade` 永远不会降级**。降级必须显式执行 `alembic downgrade 0002`，它会运行每个文件里的
`downgrade()`。比如 0001 的 downgrade 是删掉全部 7 张表，所以**降级常常等于丢数据**。
公司里很少真的降级，出了问题一般再写一个新版本改回来（向前修复）。

### 6. 不能删掉 migrations

这个项目**只靠迁移建表**，代码里没有调用 `create_all()`。删掉之后：
- 本机已经建好的库：暂时还能用；
- 新环境（别人 clone、新服务器、新 Docker 卷）：空库，api 一查库就报错；
- 集成测试：全部失败；
- 以后要改表：没有工具可用。

它既是“改版本”的工具，也是**从零建库的唯一途径**。

### 7. 什么情况要写迁移

判断标准：**表的“形状”变了就要写，表里的“内容”变了不用写。**

| 要写迁移 | 不用写 |
|---|---|
| 新建 / 删除表 | 用户提交题目、插入 run（业务数据） |
| 加列、删列、改列名、改类型 | 改 Python 业务逻辑、改查询语句 |
| 加索引、唯一约束、外键 | 改提示词、改前端 |
| 启用 PG 扩展（`CREATE EXTENSION vector`） | |
| 系统必须有的预置数据（开发用户 id=1） | |

本项目的流程：改 `models.py` → 写 `versions/000N_xxx.py` → `alembic upgrade head` → `alembic check`
（对比 models.py 和真实的库是否一致，防止改了代码忘了写迁移）。

### 8. 老数据会怎样：迁移只是帮你执行 SQL，不会替你保护数据

| 操作 | 风险 | 稳妥做法 |
|---|---|---|
| 删列 / 删表 | 数据真的会丢 | 确认没有代码在用，先备份 |
| 加 NOT NULL 列 | 老数据没有值，直接加会报错 | 三步：先加成可空 → 给老数据补值 → 再改成 NOT NULL |
| 改列名 | 还在运行的旧代码用旧名字，会报错 | 先加新列、两边都写，旧代码下线后再删旧列 |
| 给大表加索引 | 会长时间锁表 | 用 `CREATE INDEX CONCURRENTLY` |

### 9. 0001 里的一个细节：为什么要 `setval`

```python
op.execute("INSERT INTO users (id, username) VALUES (1, 'dev')")
op.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), 1)")
```

`op.execute()` 会把字符串**原样当 SQL 执行**，第一句就是普通的 INSERT。

第二句是必须的：PG 的自增 id 由一个独立的计数器（sequence）负责发号，**计数器不看表里有什么数据**。

```
建表后            计数器：下一个号是 1
手动插入 id=1     表里有了 1，但计数器没被用过，仍然是“下一个号是 1”
新用户注册        INSERT INTO users (username) VALUES ('flora')
                  → 计数器发号 1 → 和 dev 冲突 → 主键冲突报错 ✗
```

`setval(..., 1)` 告诉计数器“1 已经用过了”，下一次发 2。
规律：**手动指定了自增列的值，就要同步计数器**（从别的库导数据时也常遇到）。

### 10. 表被意外删了，升级又不检查，怎么办？

- 如果迁移用不到这张表：迁移照常成功，api 运行时报 `relation "runs" does not exist`。
- 如果迁移要改这张表：迁移报错。**PG 的 DDL（建表、改表语句）可以在事务里回滚**，env.py 又把
  整个迁移放在一个事务里，所以这次迁移会整体回滚，不会出现“升了一半”。
  （MySQL 的 DDL 会隐式提交，可能出现升了一半的情况。）
- 发现问题：`alembic check` 会报告缺表，公司一般在 CI 或部署前跑这一步。
- 修复：开发环境直接删库，再 `upgrade head` 从头重建；线上环境**表删了数据就没了**，只能从备份恢复。

所以公司真正靠的是**预防**：
1. 权限隔离：应用用的数据库账号没有 DROP / ALTER 权限，只有迁移账号能改表结构；
2. 禁止手动改线上表结构，所有改动都走迁移文件 + code review；
3. 定期备份，并且演练过恢复。

### 11. 换成 MySQL 行不行？

SQLAlchemy 不是 PG 专用的，它分三层：

```
你的代码（models.py、select(...)）
   ↓
方言层（dialect）：把同一句 Python 翻译成各家数据库的 SQL
   ↓
驱动：PG 用 psycopg，MySQL 用 pymysql 等
```

换库只要装驱动、改连接地址（`mysql+pymysql://...`）。**但本项目用了不少 PG 独有的功能**，
换起来并不轻松，这些也是“为什么选 PG”的答案：

| 本项目用到的 | MySQL |
|---|---|
| `JSONB` | 只有 `JSON`，能力弱一些 |
| `ON CONFLICT DO NOTHING`（幂等） | 写法不同（`INSERT IGNORE` / `ON DUPLICATE KEY`） |
| `UPDATE ... RETURNING`（领取租约时直接拿到新的 epoch） | 不支持，要多查一次 |
| 部分索引（`WHERE status='pending'`） | 不支持 |
| pgvector 向量检索 | 没有 |
| DDL 能在事务里回滚 | 不能 |

### 12. 是公司常用做法吗？

是，正规后端团队基本都这么做，只是工具不同：

| 生态 | 工具 |
|---|---|
| Python + SQLAlchemy | Alembic（本项目） |
| Django | 自带 `makemigrations` / `migrate` |
| Java | Flyway、Liquibase |
| Node | Prisma Migrate、Knex |
| Go | golang-migrate |

常见规矩：
- 迁移文件和代码**一起提交、一起 review**；
- 上线时**先迁移、再发新代码**（本项目 compose 里 api 等 migrate 成功后才启动）；
- **已经上线的迁移文件不再修改**，有问题就写新版本修（像不改已经 push 的 commit）。

### 13. 对照：Java 的 MyBatis 是干什么的

**我的疑问**：SQL 是自己写的，参数不也能自己填吗？那 MyBatis 到底帮了什么？

“自己填参数”有两种做法，正好对应 MyBatis 解决的两个问题。

**做法一：自己拼字符串，有 SQL 注入风险**

```java
String sql = "SELECT * FROM runs WHERE question = '" + keyword + "'";
```

用户输入 `' OR '1'='1`，SQL 就变成：

```sql
SELECT * FROM runs WHERE question = '' OR '1'='1'   -- 条件恒为真，所有人的数据都被查出来
```

所以不能拼字符串，必须用**参数绑定**：SQL 里写占位符 `?`，值单独交给驱动，数据库把它当纯数据处理，不会当成 SQL 执行。

**做法二：正确地用参数绑定，但不用框架（JDBC 原生写法），体力活很多**

```java
String sql = "SELECT id, owner_id, question, status FROM runs WHERE id = ? AND owner_id = ?";
try (Connection conn = dataSource.getConnection();          // 1. 拿连接（结束时自动关闭，不然会泄漏）
     PreparedStatement ps = conn.prepareStatement(sql)) {
    ps.setInt(1, id);                                        // 2. 按位置填参数
    ps.setInt(2, ownerId);
    try (ResultSet rs = ps.executeQuery()) {                 // 3. 执行
        if (!rs.next()) return null;
        Run run = new Run();                                 // 4. 一列一列取出来装进对象
        run.setId(rs.getInt("id"));
        run.setOwnerId(rs.getInt("owner_id"));
        run.setQuestion(rs.getString("question"));
        run.setStatus(rs.getString("status"));
        return run;
    }
}
```

真正有用的只有第一行 SQL。几百条 SQL 每条都这样写，`setInt(2, …)` 的位置写错、某个字段漏取，都很难发现。

**用 MyBatis：SQL 还是自己写，写 SQL 之外的重复代码交给它**

```xml
<!-- RunMapper.xml -->
<select id="findById" resultType="Run">
  SELECT id, owner_id, question, status FROM runs
  WHERE id = #{id} AND owner_id = #{ownerId}
</select>
```

```java
@Mapper
public interface RunMapper {                 // 只声明接口，不写实现
    Run findById(@Param("id") int id, @Param("ownerId") int ownerId);
}

Run run = runMapper.findById(100, 1);        // 执行 XML 里的 SQL，结果自动装进 Run 对象
```

| JDBC 里手写的 | MyBatis |
|---|---|
| 开连接、关连接 | 自动 |
| `ps.setInt(1, id)` 按位置填参数 | `#{id}` 按名字填，内部仍是参数绑定，防注入 |
| `rs.getString("question")` 逐列取值 | 按列名自动装进对象（`owner_id` → `ownerId` 需开启驼峰映射） |
| 多行结果组装成 `List<Run>` | 自动 |

**三种框架写同一个查询**

```java
// MyBatis：SQL 写在 XML 里，完全由自己控制
SELECT ... FROM runs WHERE id = #{id} AND owner_id = #{ownerId}

// Hibernate / JPA：几乎不写 SQL，框架根据方法名生成
Run run = runRepository.findByIdAndOwnerId(100, 1);
```

```python
# SQLAlchemy（本项目）：用 Python 表达式拼查询，它负责翻译成 SQL
select(Run).where(Run.id == 100, Run.owner_id == 1)
```

| | 谁写 SQL | 优点 | 缺点 |
|---|---|---|---|
| MyBatis | 自己 | SQL 完全可控，复杂查询、调优方便 | XML 多，换数据库要改 SQL |
| Hibernate / JPA | 框架 | 简单的增删改查几乎不用写 | 生成的 SQL 不透明，容易出 N+1 查询等性能问题 |
| SQLAlchemy | 介于两者之间 | 既能用对象，也能精确写 `UPDATE ... WHERE ... RETURNING` | 学习曲线稍陡 |

**Python 和 Java 的对应关系**

| 层次 | Java | Python（本项目） |
|---|---|---|
| 数据库驱动：自己写 SQL、自己填参数、自己取列 | JDBC | psycopg（`cursor.execute(...)`、`row[0]`） |
| 帮你绑定参数、映射结果、管连接 | MyBatis | SQLAlchemy Core |
| 用对象操作表 | Hibernate / JPA | SQLAlchemy ORM |

例子：同样是“查 id=100 的题”，在 Python 里分别这样写：

```python
# 驱动层（psycopg），相当于 JDBC：自己开游标、按位置取列
with conn.cursor() as cur:
    cur.execute("SELECT id, question, status FROM runs WHERE id = %s AND owner_id = %s",
                (100, 1))                     # %s 是占位符，这里已经是参数绑定，不是拼字符串
    row = cur.fetchone()
    question = row[1]                         # 第几列是什么，要自己记住

# SQLAlchemy（本项目 runs.get_run 的写法），相当于 MyBatis / Hibernate
run = session.execute(
    select(Run).where(Run.id == 100, Run.owner_id == 1)
).scalar_one_or_none()
question = run.question                       # 直接按字段名取
```

再看项目里的领取租约（`lease.claim`），SQL 意图写得一清二楚，只是换成了 Python 语法：

```python
update(Attempt).where(Attempt.id == attempt_id, Attempt.status == "pending")
    .values(status="running", lease_epoch=Attempt.lease_epoch + 1)
    .returning(Attempt.lease_epoch)
# 等价于 MyBatis XML 里手写的：
# UPDATE attempts SET status='running', lease_epoch = lease_epoch + 1
# WHERE id = #{attemptId} AND status = 'pending' RETURNING lease_epoch
```

本项目的 `lease.py`、`runs.py` 大量使用 `update(...).where(...).returning(...)`，其实是在用 Python
精确地写 SQL，风格更接近 MyBatis，而不是 Hibernate 那种“全交给框架”。这也是代码里看不到
`cursor`、`row[3]` 这类代码的原因。国内公司 MyBatis（及 MyBatis-Plus）用得最多，原因就是 SQL 可控、
方便调优。

### 面试一句话

> 表结构变更走 Alembic 迁移：版本文件靠 `down_revision` 串成链，库里的 `alembic_version` 记录当前版本，
> `upgrade head` 只补缺的版本，可以重复执行；部署时用一次性的 migrate 容器在应用启动前执行；
> PG 的 DDL 可以事务回滚，迁移失败不会只升一半。

---

## 二、组件组装与检索数据的存放（assembly.py）

### 1. assembly.py 是干什么的

它是**组装车间**：按配置把零件造好交出去，自己不包含任何检索或推理逻辑。

```
build_retriever(settings) → (语料 corpus, 混合检索器 retriever)
build_llm(settings, model) → 模型客户端
```

为什么单独抽成一个文件：有**两个使用方**要用同一套零件。

```
scripts/run_episode.py（命令行跑一道题）──┐
                                          ├──> assembly.py
worker.py（后台服务跑题）────────────────┘
```

如果两边各写一份组装代码，迟早会不一致。比如脚本开了向量检索、worker 忘了开，结果就是
“脚本里能答对，服务里答错”，而且很难查。只写一份就没有这个问题。

harness 不知道 assembly 的存在，它只拿到造好的 `corpus` 和 `retriever`。

### 2. 检索数据在内存还是磁盘？四样东西各不相同

| 组件 | 存在哪 | 查询时 |
|---|---|---|
| 语料 Corpus（17,596 个 block 原文） | **全部加载进内存** | 字典查找 |
| BM25 索引（稀疏矩阵 + 词表） | 启动时从磁盘缓存（41MB）**整体读进内存** | 内存里做矩阵运算打分 |
| 实体别名表 | **全部加载进内存** | 内存里做字符串匹配 |
| 向量（dense） | **不在 worker 内存里，在 PG 里** | 每次查询现算查询向量，交给 PG 找 |

**Corpus**：`corpus.py` 的 `load` 把 `blocks.jsonl` 逐行读成字典，再建两个索引：

```python
self.by_id  = {"blk_123": {...}, ...}          # block_id → block，read_section 用
self.by_doc = {"doc_7": [block, block, ...]}   # 文档 → 它的所有 block（按原文顺序）
```

模型调用 `read_section` 时，原文直接从内存字典里取，不读磁盘。

**BM25**：磁盘缓存只用来加快启动，查询完全在内存里算。

```
第一次启动：分词全部语料、构建矩阵（约 100 秒）→ 存成 .cache/bm25/tf.npz + vocab.json
之后启动：  直接把这两个文件读进内存（约 1.3 秒），不用再分词
```

**Dense（向量）**：`dense.py` 的 `search` 流程：

```
查询 "营收增长"
  ① HTTP 调用 embedding 服务 → 得到 1024 维的查询向量
  ② 交给 PG：SELECT ... ORDER BY embedding <=> 查询向量 LIMIT 256
     PG 顺序扫描 1.76 万条向量，逐条算余弦距离（约 40ms）
  ③ 拿回 (doc_id, block_id, 相似度)；原文再到内存里的 Corpus 取
```

向量放在 PG 里，多个 worker 共用一份，也为以后建 HNSW 索引留了位置（为什么现在不建，见 D15）。

> “在 PG 里”不等于“每次读磁盘”：PG 有自己的缓存（shared_buffers + 操作系统页缓存）。
> 1.76 万 × 1024 维 × 4 字节 ≈ 72MB，查过一次后基本常驻在 PG 的内存里，只是这块内存属于 PG 进程。

### 3. ⭐面试点：多进程 worker 会把数据复制几份？

`worker.py` 里：

```python
@cache
def _components():
    return build_retriever(get_settings())
```

`@cache` 保证**每个进程只组装一次**，之后的任务复用。但 Celery `--concurrency 4` 会开 **4 个子进程**，
**进程之间不共享内存**，所以语料和 BM25 要各加载一份：

```
worker 子进程 1：Corpus + BM25   ┐
worker 子进程 2：Corpus + BM25   ├─ 同样的数据 4 份
worker 子进程 3：Corpus + BM25   │
worker 子进程 4：Corpus + BM25   ┘
PG：向量 1 份，4 个进程共用
```

**项目实测（D18）**：1 个子进程加载完约 **1.2GB**，4 个子进程估计约 **4.5GB**（本机 23GB，暂时够用）。
磁盘上的缓存只有 41MB，内存里却有 1GB 多，因为 Python 的字典和字符串对象开销很大，
文本本身只占一小部分。

问题在于：**内存随并发数线性增长**。想开 16 个并发就要约 18GB，并发数先被内存卡住，而不是 CPU。

**为什么不用线程代替进程？** 线程之间可以共享内存，但 Python 有 GIL，BM25 打分这种纯计算，
多线程同一时间只能跑一个；而且 Celery 的 prefork（多进程）模式隔离性更好，一个任务崩了不会拖垮别的任务。

### 4. 以后怎么升级：拆成独立的检索服务

```
现在：                                  以后：
worker 1 [Corpus+BM25]                  worker 1 ┐
worker 2 [Corpus+BM25]                  worker 2 ├──HTTP──> 检索服务 [Corpus+BM25] × 1~2 份
worker 3 [Corpus+BM25]                  worker 3 │
worker 4 [Corpus+BM25]                  worker 4 ┘
```

| | 现在（每个 worker 自带） | 独立检索服务 |
|---|---|---|
| 内存 | 并发数 × 1.2GB | 固定 1~2 份 |
| worker 启动 | 每个子进程都要加载 | 很快，worker 只调接口 |
| 扩容 | worker 和检索绑在一起扩 | 模型调用多就加 worker，检索慢就加检索实例，各自扩 |
| 代价 | 无 | 多一次网络调用（毫秒级，和秒级的模型调用比可以忽略）；多一个要部署、要监控的服务 |

其他常见做法：
- 直接用现成的检索引擎，比如 **Elasticsearch / OpenSearch**（自带 BM25），数据下沉到共享存储，和现在向量放在 PG 里是同一个思路；
- 多进程共享内存（fork 后写时复制、mmap 映射同一个文件）。省内存，但 Python 对象很难真正共享，不如拆服务直接。

**本项目的切入点**：harness 只依赖 `retriever` 的 `search_docs`、`search_in_document` 等几个方法，
以后写一个“通过 HTTP 调检索服务”的同名类，在 `assembly.py` 里替换掉就行，harness 一行都不用改。
这也是“组装集中在一处”的另一个好处。

### 面试一句话

> 语料和 BM25 在每个 worker 进程里各加载一份，Celery 多进程模式下内存随并发线性增长，
> 实测单进程约 1.2GB；向量放在 PG 里共享。并发再往上提时，把检索拆成独立服务，
> 让 worker 和检索各自扩容，代价是多一次毫秒级的网络调用。

---

## 三、项目里用到的设计模式（面试问答）

M1 后端的可靠性几乎都建立在下面这些模式上。每条按“面试官怎么问 → 一句话答 → 项目里的例子”整理。

### 总览

| 模式 | 解决什么 | 项目位置 |
|---|---|---|
| Transactional Outbox | 写库和发消息的一致性 | `runs._enqueue_attempt` + `dispatcher.py` |
| 幂等键 | 用户重复提交 | `runs.create_run` |
| 幂等消费者 | 消息重复投递 | `lease.claim` |
| 至少一次 + 幂等 = 效果上恰好一次 | exactly-once 的实际做法 | dispatcher + claim 合起来 |
| 竞争消费者 | 横向扩展 | 多个 worker 抢同一个队列 |
| 租约 + 心跳 | 发现 worker 死掉 | `lease.LeaseKeeper`、`expire_leases` |
| Fencing Token | 挡住复活的旧 worker | `lease._fence` |
| 条件更新（类似 CAS） | 避免“先查再改”的竞态 | `lease.claim` |
| SKIP LOCKED 工作队列 | 多个进程取任务不重复 | `dispatcher.dispatch_once` |
| 指数退避 | 下游挂了不被重试打爆 | `dispatcher.dispatch_once` |
| 事件日志 + 断点续传 | SSE 断线不丢不重 | `runs.add_event`、`events_after` |
| 组装根 / 依赖注入 | 好测试、好替换 | `assembly.py`、FastAPI `Depends` |

### 1. Transactional Outbox（事务发件箱）

**问**：写数据库和发消息队列是两个系统，怎么保证一致？

**答**：发消息这件事先和业务数据在**同一个事务**里写进 outbox 表，再由独立的 dispatcher 读表投递。

```
不用 Outbox：① 写库 ✓  ② 进程崩了 ✗  → 任务记在库里，永远没人执行
用 Outbox：  一个事务 { 写 attempt + 写 outbox }  → 要么都有，要么都没有
             dispatcher 反复投递 outbox，直到成功
```

**追问**：投递那一步怎么做？ → 轮询（本项目，每秒扫表）或 CDC（Debezium 读 PG 的 WAL）。
Java 的 RocketMQ 事务消息是解决同一个问题的另一种做法（半消息 + 回查）。

### 2. 幂等键（Idempotency Key）

**问**：用户连点两次“提交”，或者请求超时后前端重试，会不会建出两个任务？

**答**：前端每次提交带一个 `Idempotency-Key` 请求头，重试时用同一个键。数据库对
`(owner_id, idempotency_key)` 建唯一约束，插入时 `ON CONFLICT DO NOTHING`：

```
第一次 POST（key=abc） → 插入成功 → 新 run 100，返回 202
第二次 POST（key=abc） → 唯一约束冲突，什么都不插 → 返回同一个 run 100
同一个 key 但题目不同   → 409 Conflict（说明前端有 bug）
```

两个请求**同时**到达也没问题：后到的 INSERT 会等先到的事务结束，再发现冲突。

### 3. 幂等消费者 + “至少一次 + 幂等 = 效果上恰好一次”

**问**：消息队列能做到 exactly-once 吗？

**答**：投递层面很难做到，实际做法是**投递保证至少一次，消费端去重**。

```
dispatcher：发了“执行 200”，但标记 sent 前崩了 → 下一轮再发一次（至少一次）
worker A：claim(200) → UPDATE ... WHERE status='pending' → 1 行，领到了
worker B：claim(200) → 已经是 running → 0 行，返回 None，跳过（幂等）
```

### 4. 竞争消费者（Competing Consumers）

**问**：任务多了怎么扩展？

**答**：多个 worker 从同一个队列取任务，每条消息只交给其中一个。任务多了就加 worker
（`--concurrency` 或多开容器），不用改代码。`worker_prefetch_multiplier=1` 让每个子进程手里
只有正在执行的那一条，避免任务压在一个忙碌的 worker 那里排队。

### 5. 租约 + 心跳（Lease + Heartbeat）

**问**：worker 执行到一半挂了，怎么发现？

**答**：执行权有过期时间。`lease_until = now() + 90 秒`，续租线程每 15 秒推后一次；
dispatcher 每 15 秒巡检一次，`lease_until < now()` 的就是失联了。

```
12:00:00 领取   lease_until = 12:01:30
12:00:15 续租   lease_until = 12:01:45
12:00:20 💥 进程崩溃，没人续租了
12:01:45 过期 → 巡检把 attempt 判为 failed
```

**追问**：时间用谁的？ → 统一用数据库的 `now()`，不用各台机器自己的时钟（各机器的时钟可能不一致）。

**追问**：和 Redisson 看门狗有什么区别？ → 结构一样（Redisson 默认 30 秒过期、每 10 秒续），
但普通的 Redisson 锁不带下面的 fencing token。

### 6. Fencing Token（epoch）

**问**：worker 卡顿了很久（GC 停顿、网络卡住），租约过期后它又醒来继续写，怎么办？

**答**：每次领取或判定过期都让 `lease_epoch + 1`。worker 每次写库都带上
`WHERE lease_epoch = 我的epoch`，更新到 0 行就说明执行权已被取代，整个事务回滚，停止执行。

```
worker A 领取：epoch = 5
A 卡住 → 租约过期 → 巡检：epoch = 6
A 醒来写第 8 步：UPDATE ... WHERE lease_epoch = 5 → 0 行 → LeaseLost → 回滚，第 8 步不留痕迹
```

这是分布式锁的经典问题：**只有锁不够，写入方必须能校验“我是不是最新的持有者”**
（Martin Kleppmann 评 Redlock 的文章讲的就是这个）。

### 7. 条件更新（类似 CAS）

**问**：领取任务时，先 SELECT 看看是不是 pending，再 UPDATE，有什么问题？

**答**：两个 worker 可能同时 SELECT 到 pending，都以为自己领到了（check-then-act 竞态）。
正确做法是把检查和修改放进**同一条 UPDATE**：

```sql
UPDATE attempts SET status='running', lease_epoch = lease_epoch + 1
WHERE id = 200 AND status = 'pending'
RETURNING lease_epoch;
```

PG 执行 UPDATE 时会锁住这一行再检查 WHERE，所以只有一个人能更新成功。思路和 CAS
（compare-and-swap：值还是我预期的那个，才改成新值）相同。

### 8. SKIP LOCKED 工作队列

**问**：开多个 dispatcher，会不会把同一条 outbox 发两次？

**答**：`SELECT ... FOR UPDATE SKIP LOCKED`：取出的行被锁住，别的进程遇到被锁的行直接跳过，
去取下一行，不用等待。

```
dispatcher 1：锁住 outbox 1~50
dispatcher 2：跳过 1~50，取 51~100
```

### 9. 指数退避（Exponential Backoff）

**问**：Redis（或 RabbitMQ）挂了，dispatcher 会不会疯狂重试？

**答**：投递失败的记录推迟 2、4、8……最多 60 秒再试（`next_attempt_at`），
本轮剩下的记录也先不发。下游恢复后自动继续，不会被重试请求打爆。

### 10. 事件日志 + 断点续传（SSE）

**问**：SSE 断线重连，怎么保证事件不丢不重？

**答**：事件存进 PG，每个 run 内按 `seq` 连续编号（1、2、3……）。浏览器重连时在请求头
`Last-Event-ID` 里带上最后收到的序号，服务端从它之后补发。

**追问**：为什么不用全表自增 id 当序号？

```
事务 A 拿到 id=10，事务 B 拿到 id=11
B 先提交 → 读者读到 11，记住“收到 11 了”
A 后提交 → 10 比 11 小，永远不会再被读到 → 丢了
```

本项目用 `runs.last_seq + 1` 分配序号：这个 UPDATE 会锁住 run 这一行直到事务提交，
所以后拿到序号的事务一定后提交。

### 11. 组装根 + 依赖注入

**问**：代码怎么做到好测试、组件好替换？

**答**：
- 组件只在 `assembly.py` 一个地方组装，脚本和 worker 共用；以后把检索换成远程服务，只改这里。
- FastAPI 用 `Depends` 声明“我需要数据库、需要当前用户”，测试时整体替换成测试库和假用户。
- worker 的 `execute` 把数据库、语料、模型都作为参数传进去，测试时传 FakeLLM，不调用真实模型。

### 面试一句话

> 提交走幂等键防重复；建任务用 Outbox 保证和投递一致；投递至少一次，worker 用条件 UPDATE
> 领取实现幂等消费；执行权用租约 + 心跳发现失联，用 epoch 做 fencing token 挡住复活的旧 worker；
> 进度事件按 run 内连续 seq 存库，SSE 断线按 Last-Event-ID 补发。

---

## 四、dispatcher / worker / lease 与消息队列

这一章是 M1 后端的核心：一道题怎么从“写进数据库”变成“有 worker 在执行”，出了故障怎么发现、怎么恢复。

### 1. 三者是一条流水线，dispatcher 是自己写的，不是中间件

| 名字 | 是什么 | 谁写的 |
|---|---|---|
| Redis（将换成 RabbitMQ） | **消息中转站（broker）**：独立运行的服务，暂存“执行 attempt 200”这类消息 | 现成的中间件 |
| Celery | Python 库（包名 `celery`），负责发消息、收消息、管理 worker 进程 | 现成的库 |
| `dispatcher.py` | 一个死循环小程序，compose 里单独一个容器 | 自己写的 |
| `worker.py` | 用 Celery 注册的任务函数，收到消息后执行 Harness | 自己写的 |
| `lease.py` | 一组函数（领取、续租、提交、巡检），**不是进程**，被 worker 和 dispatcher 调用 | 自己写的 |

`import celery` 在 `worker.py`：`celery_app = Celery("margin", broker=...)`。dispatcher 直接
`from .worker import celery_app` 借用同一个 app，保证发消息和收消息用的是同一套配置。

分工一句话：**dispatcher 管“送出去”，worker 管“做完”，lease 管“现在归谁做”。**

```
            PostgreSQL（唯一的裁判）
   ┌──────────────┬───────────────────┐
   │ outbox 表    │ attempts 表（租约）│
   └──────▲───────┴─────▲─────────▲───┘
          │①扫描        │③领取/续租 │④巡检过期
          │             │  /提交    │
   ┌──────┴──────┐      │     ┌────┴─────┐
   │ dispatcher  │──②发消息──► 消息中转站 ──► worker │
   └─────────────┘                          └──────────┘
```

### 2. 一条任务的完整过程，以及每个状态由谁来改

以 attempt 200 为例：

1. **API**：在一个事务里写 run、attempt（pending）、outbox（pending）、事件。
2. **dispatcher**：每秒扫 outbox，把“执行 200”发进消息中转站，然后把 outbox 标成 sent。
3. **worker**：取到消息，执行 `claim(200)`。领到就开始跑 Harness；领不到（重复消息）就跳过。
4. **worker 执行中**：续租线程每 15 秒续一次；每跑完一步，`commit_step` 校验 epoch 并写库。
5. **结束**：worker 自己写 completed / failed；如果 worker 死了，由 dispatcher 的巡检写 failed。

| 表.字段 | 改成 | 谁改的 | 代码位置 |
|---|---|---|---|
| outbox.status | pending | API（和 attempt 同一个事务） | `runs._enqueue_attempt` |
| | sent | dispatcher（投递成功后） | `dispatcher.dispatch_once` |
| attempts.status | pending | API | `runs._enqueue_attempt` |
| | running | **worker 领取：改成 running 本身就是领取** | `lease.claim` |
| | completed | worker 正常结束 | `lease.finish` |
| | failed | worker 捕获到异常（网关报错、超时） | `lease.fail` |
| | failed | **dispatcher 的巡检**（租约过期，epoch + 1） | `lease.expire_leases` |
| attempts.lease_until | 往后推 90 秒 | worker 的续租线程 / 每提交一步 | `lease.renew` / `commit_step` |
| runs.status | 跟随最新的 attempt | 改 attempt 的那一方，同一个事务里一起改 | 同上 |

总结：**API 负责“建”，dispatcher 负责“送”和“判死”，worker 负责“领、续、写结果”。**

**为什么 running 不是“领取后顺手改”**：领取只有一条条件 UPDATE：

```sql
UPDATE attempts SET status='running', lease_owner='主机:进程', lease_epoch=lease_epoch+1,
                    lease_until=now()+90秒
WHERE id=200 AND status='pending'
```

更新到 1 行就是领到了，0 行就是被别人抢先了。“检查”和“修改”是同一个动作。

**为什么 failed 有两个来源**：worker 还活着时（异常被 try/except 接住），它可以自己报告失败；
**死掉的进程没法报告“我死了”**，只能由外部观察者（巡检）根据“90 秒没续租”来判定。
这就是租约的本质：**用“沉默超时”判断“死亡”**（心跳、看门狗、ZooKeeper 临时节点都是这个思路）。

另外，outbox 停在 sent 是对的：它只表示“投递完成”，不负责执行结果，执行结果看 attempts。

### 3. 为什么要自己写 dispatcher：Outbox 模式

如果 API 直接发消息：

```
① 写库（建 attempt） ✓
② 进程崩了 ✗          → 任务记在库里，永远没人执行
（反过来先发消息后写库：worker 收到消息，库里却查不到任务）
```

Outbox：API 只在**同一个事务**里写 attempt + outbox，dispatcher 负责反复投递。

- dispatcher 保证**至少送到一次**（发出去了但标记 sent 前崩溃 → 下一轮再发）；
- lease 的 `claim` 保证**最多执行一次**；
- 合起来就是**效果上恰好一次**。

**投递这一环的其他做法：**

| 做法 | 说明 |
|---|---|
| 轮询（本项目） | 每秒扫表，简单，延迟秒级 |
| 监听 / 通知（PG 的 LISTEN / NOTIFY） | 插入 outbox 时通知 dispatcher 立刻投递。**只对新消息有用**：通知不持久，没人在听就丢了，退避后到期的重试也不会触发通知。所以仍要保留低频扫描兜底 |
| CDC（变更数据捕获） | Debezium 读 PG 的 WAL，把新行发到队列。Debezium 是 Java 写的，但作为独立服务运行，不分语言；Python 没有成熟的同类工具 |
| RocketMQ 事务消息（Java） | 中间件原生解决同一个问题：先发“半消息”→ 执行本地事务 → 提交或回滚，丢了通知会回查 |

### 4. 消息中转站：Redis 还是 RabbitMQ

- **Celery 默认的消息中转站是 RabbitMQ**，M1 是主动选的 Redis（D3：Redis 后面还要做限流、推流、缓存，顺便兼任队列）。
- Redis 做队列是正式可用的方案，但它是**兼职**：

| | RabbitMQ（专门的消息队列） | Redis（兼职） |
|---|---|---|
| 消息确认 | 原生支持，worker 断开时消息**立即**回到队列 | Celery 用“可见性超时”模拟：取走 2 小时没确认才放回 |
| 持久化 | 队列和消息都能落盘 | 主要在内存；本项目的 compose 没挂数据卷，重启后队列清空 |
| 发送确认 | publisher confirm：中转站确认“收到了”，dispatcher 才标记 sent | 无 |
| 可视化 | 自带网页管理界面，能看到队列长度、消费者数量、待确认数量 | 只能用 redis-cli 翻内部结构 |

**决定：切换到 RabbitMQ**（D19）。RabbitMQ 只负责任务队列，Redis 继续负责推流、限流、缓存。
代码改动很小（Celery 屏蔽了大部分差异），真正的代价是多运维一个服务，测试环境也要起它。

### 5. Celery 的进程结构与三种“心跳”

我原来以为“主进程执行任务，子进程负责心跳”，**实际正好不是这样**：

```
worker 容器
└─ 主进程（Celery 框架自己的“管家”，不跑业务代码）
   │  和消息中转站保持连接、发连接心跳、收消息、分给子进程、确认消息
   ├─ 子进程 1（执行任务）
   │    ├─ 主线程：跑 Harness
   │    └─ 续租线程 LeaseKeeper：每 15 秒续租      ← 线程，不是进程
   ├─ 子进程 2 …
   └─ 子进程 4 …          （--concurrency 4）
```

| “心跳” | 谁负责 | 检测什么 |
|---|---|---|
| **连接心跳**（AMQP 协议的心跳） | 主进程，Celery 自动完成 | TCP 连接还在不在。对方断电时 TCP 自己发现不了，连续两次收不到心跳就判定断开，并把未确认的消息放回队列 |
| **确认期限**（`consumer_timeout`） | RabbitMQ 计时 | 消息交出去后多久没确认 |
| **租约心跳** | 本项目自己写的续租线程 | 执行者还活着吗？执行权还是它的吗？ |

**有了确认机制，为什么还要租约？** 子进程卡死时，主进程的连接心跳照发，RabbitMQ 认为一切正常；
而租约由子进程自己续，子进程卡死，续租就停了。另外确认机制管不了“旧 worker 往 PG 乱写”，
那是 epoch 的职责。**确认管消息，租约管执行权。**

**卡死的两种情况：**

| 情况 | 影响 | 可能性 |
|---|---|---|
| 某个子进程卡死 | 只影响它手上那一个任务，进程之间隔离 | 常见 |
| 主进程卡死 / 崩溃 | 等于整个 worker 容器挂了，4 个任务都受影响 | 少见 |

两种情况最终走同一条恢复路径：租约过期 → 巡检判失败；连接断开 → 未确认的消息回到队列
→ 被重新投递后 `claim` 发现不是 pending → 跳过；compose 的 restart 策略把容器重新拉起。

**子进程卡死时，MQ 不知道，要不要通知它？不需要。** 时间线：

```
12:00  子进程 1 领取 attempt 200（epoch=5）
12:01  子进程 1 卡死，续租也停了；主进程正常，MQ 认为一切正常
12:02  租约过期 → 巡检：200 = failed，epoch=6            ← PG 先知道
12:25  Celery 硬时限：主进程杀掉子进程 1，补一个新的
       acks_late 下被杀的任务默认也会被确认 → 消息删除
```

MQ 怎么看这条消息不重要，**PG 才是裁判**：消息就算再被投递，`claim` 也会挡住。
重跑靠用户点“重新生成”（M1）或巡检写新的 outbox 接管（M4）。

> M1 的一个弱点：如果只是**主线程卡住、续租线程还活着**，续租会一直进行，巡检发现不了。
> 目前靠模型调用自带的超时（总时长 180 秒）和 25 分钟硬时限兜底；M4 让续租线程同时检查主线程有没有进展。

### 6. RabbitMQ 的消息确认：生命周期、通道、超时分层

**消息的生命周期：**

```
就绪（ready）──交给 worker──► 待确认（unacked）──确认──► 删除
                                    │
                                    └─ 连接断开 / 超过确认期限 / worker 拒绝 ──► 回到“就绪”
```

- 被取走后消息**还在 RabbitMQ 里**，处于“待确认”状态，别的 worker 看不到；只有确认了才真正删除。
- 管理界面能看到“就绪 N 条、待确认 M 条”这样的**数量**，但**不能按内容搜索某一条消息**。
  消息队列不是数据库，所以“attempt 200 的消息还在不在”只能靠 PG 对账来判断。

**通道是什么**：一条 TCP 连接内部划分出来的“子线路”，像一根网线里的几路对讲频道
（建 TCP 连接贵，所以协议允许一条连接开多个轻量通道）。Celery 主进程在**一个通道**上收所有消息，
再分给 4 个子进程。

**确认超时时 RabbitMQ 做什么**：既不丢弃，也不报警，而是**挂断这个通道**。RabbitMQ 关不掉 worker 进程，
只能关闭协议层的通道。通道上**所有**待确认的消息都会放回队列，不只是超时的那一条：

```
通道 1 上待确认：200（超时）、201、202、203（正常执行中）
→ 关闭通道 1 → 200~203 全部回到“就绪”
```

**正在执行 201 的子进程会知道吗？** 不知道，也不会停，而且这正是正确的行为：通道属于主进程，
子进程的租约仍然有效，照常写 PG 直到完成。被放回队列的 201 号消息再被投递时，`claim`
发现它是 running → 跳过并确认；原子进程跑完后，主进程想在旧通道上确认 → 通道已经没了，只记一条错误日志。
**数据完全正确，代价只是几次无用的重复投递。**

**确认期限为什么设 1 小时**：和 Celery 硬时限都是“超时”，但层次和动作不同：

| | Celery 硬时限（25 分钟） | RabbitMQ 确认期限 |
|---|---|---|
| 谁计时 | worker 主进程 | 消息中转站 |
| 到期动作 | 精确杀掉那一个子进程，确认那一条消息 | 关闭整个通道，所有待确认消息回队列 |
| 定位 | 正常保护 | 最后一道保险（防止有 bug 的消费者永远占着消息） |

所以超时要**从内到外一层比一层大**，内层先触发：

```
模型调用 180 秒  <  Celery 硬时限 25 分钟  <  RabbitMQ 确认期限 1 小时（默认 30 分钟太接近，调大）
```

### 7. 恢复机制：PG 是唯一的真相，各环节自愈

原则：**其他组件要么没有状态，要么状态能从 PG 推出来。** 没有统一的“恢复总指挥”，
靠的是“每个环节都会自己反复检查，而且重复执行也没有害处”。

| 什么挂了 | 损失 | 谁负责恢复 |
|---|---|---|
| API | 正在处理的请求失败 | 前端带同一个幂等键重试 |
| dispatcher | 暂时没人投递 | 重启后接着扫 outbox 的 pending |
| 消息中转站 | 队列里的消息丢了 | ⚠️ M1 没人处理（见第 9 节） |
| worker 子进程 / 容器 | 正在跑的任务中断 | 租约过期 → 巡检判失败（M1）/ 从最后一步接着跑（M4） |
| PG | 全部停摆 | PG 保证已提交的事务不丢；恢复后连接池自动重连（`pool_pre_ping`） |

“顺序”只有两处：**启动顺序**由 compose 的 `depends_on` 决定（PG / 消息中转站健康 → 迁移 → api、worker、dispatcher）；
**运行时**各自的定时循环（dispatcher 每 1 秒投递、每 15 秒巡检；worker 每 15 秒续租）。
“从最新的记录接着跑”是 M4 的能力，M1 是判失败后整道题重跑。

### 8. 面试题：为什么不直接把 PG 当队列？

worker 直接从 PG 抢任务（`SKIP LOCKED` + LISTEN / NOTIFY），如 procrastinate、PGMQ：

| 优点 | 缺点 |
|---|---|
| 少一个中间件 | PG 压力变大：所有 worker 都来锁行抢任务，和业务查询争资源 |
| **不需要 Outbox**：建任务就是发消息，天然同一个事务 | 每个监听者占一条连接，PG 是每条连接一个进程，连接很贵 |
| 不存在“消息丢了”的问题 | 任务行频繁更新删除，表膨胀，清理压力大 |
| | 失去 Celery 的进程池、时限、重试、监控 |
| | 吞吐上限比专门的消息队列低 |

现在这条“PG → Outbox → dispatcher → 消息中转站 → Celery”链路的优势：解耦（数据库只存真相）、
worker 可以独立扩容、Celery 现成的 worker 管理、消息中转站可替换、和企业主流架构一致。
代价是两个系统之间的一致性问题，用 Outbox + 租约 + 幂等消费来补。

> 面试这样答：“以当前规模（任务少、单个任务长），PG 当队列其实够用而且更简单。选消息队列是为了解耦和
> 独立扩展，也为了用上 Celery 成熟的 worker 管理；代价是引入了一致性问题，我用 Outbox + 租约 + 幂等消费解决。”
> 说清“我知道更简单的方案，也知道为什么没选”，比背优点更有说服力。

### 9. review 中发现的缺口：消息丢了，attempt 永远停在 pending

PLAN 设计了“给消息丢失的 pending attempt 补一条 outbox”，但 M1 的巡检只处理了 running：

```
① dispatcher 把“执行 200”发进 Redis，outbox 标成 sent
② Redis 重启（没挂数据卷、没开 AOF）→ 队列里的消息全丢
③ attempt 200 永远 pending：没人收到消息，巡检也不处理 pending
④ 用户点“重新生成” → 发现还有 pending 的执行 → 409 → 这道题永远卡住
```

**换成 RabbitMQ 也不能完全解决**：它让消息“很少丢”，不能保证“一定不丢”（队列被手动清空、磁盘坏了）。
成熟系统都是两层：**可靠的队列（尽量不丢）+ 数据库对账（丢了能补回来）**。

**对账难点：PG 分不清“还在排队”和“丢了”**，而消息队列又不能按内容搜索。方案经历了三次修正：

1. 纯超时：pending 超过 N 分钟就补发。问题是积压时会误判，补发多了会塞满队列。
2. 队列为空 + 仍 pending：队列空了却还没被领取，说明丢了。
   **漏洞（我自己发现的）**：一直有用户提交，队列永远不空，就只能退回到长超时。
3. **水位线（watermark）**：利用队列**先进先出**——
   **比我晚投递的任务都已经被领走了，我却还是 pending，那我的消息一定丢了。**

```
12:00:01 投递 200    12:00:02 投递 201    12:00:03 投递 202
12:00:05 201、202 已被领走，200 还是 pending → 200 本该比 201 先被领 → 丢了，补发
```

流量越大，水位线涨得越快，丢失反而**发现得越快**。完整方案（三条判定规则、补发上限、测试）见 PLAN §5.7。

### 10. 运维：把这些检查点变成告警

企业级应用基本都有**可观测性 + 告警**：

```
各服务暴露指标 → Prometheus 定时采集 → Grafana 面板（给人看）
                         ↓
               告警规则（“X 持续 5 分钟大于 Y”）→ Alertmanager → 企业微信 / 钉钉 / 飞书
```

可观测性三部分：**指标**（数字）、**日志**（文字记录）、**链路追踪**（一个请求经过了哪些服务、各花多久）。

本项目可以设的规则：

| 告警规则 | 说明什么 |
|---|---|
| outbox 最早一条 pending 超过 1 分钟 | dispatcher 挂了，或连不上消息中转站 |
| 对账补发次数 > 0 | 消息丢了，消息中转站有问题 |
| 每分钟租约过期数突增 | worker 大面积崩溃或卡死 |
| 队列积压持续增长 / 消费者数 = 0 | worker 处理不过来或全挂了 |
| attempt 失败率 > 20% | 模型网关出问题 |
| 模型调用 P95 耗时突增 | 网关变慢 |
| 数据库连接池耗尽 | 连接泄漏或并发过高 |

**自愈保证用户感觉不到故障，告警保证运维知道故障发生了，两者缺一不可。**
“对账补发”就是典型：系统已经自己修好了，但它在提醒你消息中转站坏了。

告警设计两个原则：**对症状告警而不是对原因**（“任务 5 分钟没开始执行”比“CPU 80%”更值得叫醒人）；
**避免告警疲劳**（设持续时间、分级别）。计划在 M6 加上 `/metrics` + Prometheus + Grafana。

### 面试一句话

> 建任务时用 Outbox 把投递和业务写入放进同一个事务，dispatcher 至少一次投递，worker 用条件 UPDATE 领取做幂等消费；
> 执行权靠租约 + 续租线程判断存活，死掉的进程由外部巡检按“沉默超时”判失败，epoch 挡住复活的旧 worker。
> 消息队列只是“叫醒信号”，PG 是唯一的裁判，所以重复投递、通道关闭都不影响正确性；
> 消息丢失则由巡检用“水位线”对账补发，并配告警让运维知道。

---

## 五、程序入口、运行流程与代码细读（dispatcher / worker / lease）

第四章讲的是“为什么这样设计”，这一章对着代码看“具体怎么跑”。

### 1. 程序入口：没有唯一的 main，是 4 个独立程序

**我的疑问**：入口是不是 `runs.py`？这个项目没有入口吗？

`runs.py` 不是入口，它是 API 这一侧的数据库操作函数库，被 `api.py` 调用。整个系统的入口是一条 Docker 命令：

```bash
wsl -d Ubuntu-22.04 -- bash -lc "cd /mnt/d/competition/margin-agent && docker compose up -d --build --wait"
```

它读取 `compose.yaml`，按依赖顺序在 **WSL 的 Docker 里**拉起 7 个容器：

```
docker compose up
├─ ① 基础服务（现成镜像）：postgres  redis  embedding（M1.5 起再加 rabbitmq）
│        │ 健康检查通过
├─ ② migrate：alembic upgrade head        建表，执行完就退出
│        │ 迁移成功
└─ ③ 三个应用程序同时启动，各跑各的，互不调用，启动后都不退出：
     api         uvicorn margin.api:app          等 HTTP 请求
     worker      celery -A margin.worker worker  等队列里的消息
     dispatcher  python -m margin.dispatcher     循环扫 outbox
```

| 程序 | 谁的 main 在跑 | 我的代码什么时候被调用 |
|---|---|---|
| api | uvicorn（Web 服务器） | 来一个 HTTP 请求，调用 `api.py` 里对应的函数，如 `create_run` |
| worker | celery | 来一条消息，调用 `execute_attempt` |
| dispatcher | **自己写的** `main()` | 没用框架，自己写死循环 |

api 和 worker 里看不到 `main()`，因为用的是**框架**：框架自己有 main，我的代码被框架调用。
这叫**控制反转**（“别打给我们，我们会打给你”）。Java 一样：Spring Boot 有 main，但 `@PostMapping`
方法是被 Tomcat 调用的。

开发时也可以分开启动，更能看出它们是独立程序：

```powershell
wsl docker compose up -d postgres redis                                   # 基础服务
conda run -n margin alembic upgrade head                                  # 建表
conda run -n margin --no-capture-output uvicorn margin.api:app --reload   # API 在 Windows 上，改代码自动重启
wsl docker compose up worker dispatcher                                   # worker 只能在 Linux（Celery 多进程不支持 Windows）
```

> 小细节：现在接口路径是 `/runs`，没有 `/api` 前缀。M6 加 Nginx 后，浏览器访问 `/api/runs`，
> Nginx 去掉 `/api` 再转给 FastAPI。

### 2. 运行流程图：一道题从提交到结束

```
【api】浏览器 POST /runs
  └─ api.create_run                                   api.py:129
       └─ runs.create_run    ← 一个事务                 runs.py:63
            ├─ INSERT runs（幂等：ON CONFLICT）
            └─ _enqueue_attempt                        runs.py:46
                 ├─ INSERT attempts (pending)
                 ├─ INSERT outbox   (pending)
                 └─ add_event("attempt_queued")        runs.py:30
  ← 202 + run_id

【dispatcher】main() 死循环                            dispatcher.py:74
  ├─ 每 1 秒 dispatch_once                             dispatcher.py:39
  │    ├─ SELECT outbox pending … FOR UPDATE SKIP LOCKED
  │    ├─ publish → celery send_task → 消息中转站
  │    └─ outbox.status = sent
  └─ 每 15 秒 lease.expire_leases   巡检                lease.py:142

【worker】Celery 收到消息
  └─ execute_attempt(id)                               worker.py:103
       ├─ _components()   加载语料 + 检索器（每个子进程只加载一次）
       └─ execute                                      worker.py:72
            ├─ lease.claim        pending → running，epoch+1；领不到就 return（重复消息）
            ├─ with LeaseKeeper   续租线程每 15 秒 renew
            │    └─ run_episode(on_step=…)    跑 Harness             harness/loop.py:65
            │         └─ 每完成一步 → lease.commit_step → _fence 校验 epoch → 写 steps + events
            ├─ 正常结束 → lease.finish   completed
            ├─ LeaseLost → 什么都不写，直接退出
            └─ 其他异常 → lease.fail     failed

【api】浏览器 GET /runs/{id}/events（SSE）
  └─ stream_events                                     api.py:153
       └─ 每 1 秒 runs.events_after 取新事件推给浏览器，结束后关闭
```

### 3. 调用关系图：dispatcher、worker、lease

```
              dispatcher 容器                          worker 容器（某个子进程）
        ┌─────────────────────────┐            ┌──────────────────────────────────┐
 入口   │ main()   自己写的死循环   │            │ execute_attempt(id)  由 Celery 调用 │
        │  ├ 每 1 秒 dispatch_once │            │  └ execute()                      │
        │  │   └ publish ─────────┼──消息──────►│     ├ ① claim          领取        │
        │  └ 每 15 秒 ┐            │  中转站     │     ├ ② LeaseKeeper    续租线程     │
        └────────────┼────────────┘            │     │     └ 每 15 秒 renew          │
                     │                         │     ├ ③ run_episode    跑 Harness   │
                     │                         │     │     └ 每一步 commit_step       │
                     │                         │     └ ④ finish / fail  写结果       │
                     │                         └────────────────┬─────────────────┘
                     ▼                                          ▼
        ┌────────────────────────── lease.py（函数库，不是进程）───────────────────────┐
        │ expire_leases    claim    renew    commit_step    finish    fail             │
        │                                         └────────┴─────────┴─► _fence      │
        │                                                           （epoch 校验）     │
        └──────────────────────────────────────┬──────────────────────────────────────┘
                                               ▼
                           PostgreSQL：attempts / runs / steps / events
```

| 函数 | 谁调用 | 改了什么 | 校验 epoch？ |
|---|---|---|---|
| `claim` | worker，开始时 | attempt：pending → running，epoch+1；run → running；写事件 | 否，条件是 `status='pending'` |
| `renew` | **续租线程**，每 15 秒 | `lease_until` 往后推 90 秒 | 是（失败就停止续租，不抛异常） |
| `commit_step` | **子进程主线程**（`on_step` 回调），每一步 | 续租 + 插入 step + 写事件 | 是，经 `_fence` |
| `finish` | 子进程主线程，正常结束 | attempt → completed，run → completed，写事件 | 是，经 `_fence` |
| `fail` | 子进程主线程，捕获异常 | attempt → failed（记错误类型），run → failed，写事件 | 是，经 `_fence` |
| `expire_leases` | dispatcher，每 15 秒 | 过期的 attempt → failed，epoch+1，run → failed，写事件 | 否，**它是让 epoch 变化的一方** |

要点：
- **`claim` 和 `expire_leases` 改变 epoch，其余 4 个检查 epoch。**
- lease 是被两方调用的**函数库**，不是进程；所有改 attempts 表的地方（除了建 attempt）都集中在这里。
- dispatcher 和 worker **不互相调用**，只通过消息中转站（叫醒）和 PG（账本）联系。
- **两个线程分工**：主线程干活并写结果（`commit_step` / `finish` / `fail`），续租线程只做 `renew`，证明“我还活着”。

### 4. 幂等，一句话

**同一个操作做一次和做很多次，结果都一样。** 电梯按钮按 1 次和按 10 次，电梯都只去那一层一次；转账就不是幂等的。

后端在意它，是因为**网络会让请求重复**：

```
点“提交” → 服务器建好了任务 → 响应在网络上丢了 → 前端以为失败，自动重试 → 不做幂等就建出两个任务
```

本项目两处幂等：API 层用**幂等键**防重复提交；worker 层用 **`claim`** 防重复投递。

### 5. ORM 的变更跟踪：`row.status = "sent"` 为什么会变成 SQL

从 session 查出来的对象，session 会一直盯着它；改了哪个字段，提交事务时自动生成 UPDATE：

```python
row = session.execute(select(Outbox)...).scalars()...   # 查出来，session 开始跟踪
row.status = "sent"                                      # 只改了 Python 对象
row.sent_at = func.now()
# 走到 with session.begin() 结尾提交时，自动执行：
# UPDATE outbox SET status='sent', sent_at=now() WHERE outbox.id = 7
```

这叫**工作单元（Unit of Work）**，Hibernate / JPA 的“脏检查”是同一机制。`func.now()` 翻译成 SQL 的 `now()`，用数据库的时间。

### 6. lease.py 为什么不用 `row.status = ...` 这种对象写法

**我的疑问**：lease.py 里很多像 SQL 的语句，不能像 `row.status = "sent"` 或者 pandas 那样操作吗？

先澄清：`update(Attempt).where(...).values(...)` 不是手写 SQL，是 SQLAlchemy 的表达式写法，只是长得像 SQL。
不用对象写法，是因为**会出并发 bug**：

```python
attempt = session.get(Attempt, 200)        # ① SELECT：看到 pending
if attempt.status != "pending":
    return None
attempt.status = "running"                 # ② UPDATE ... WHERE id=200
attempt.lease_epoch += 1                   #    在 Python 里 +1 再写回
```

```
worker A：读到 pending ─────────────► 改成 running，epoch = 6
worker B：      读到 pending ────────────► 也改成 running，epoch = 6
→ 两个都以为领到了，epoch 还一样
```

- **先查再改竞态**：① 和 ② 之间有缝隙。
- **丢失更新**：`+= 1` 是“读出 5、算出 6、写回 6”，两人同时做结果都是 6。
- 现在的写法用**一条条件 UPDATE** 完成检查和修改，`lease_epoch = lease_epoch + 1` 在数据库里算，没有缝隙。

**对象写法也能做对：先加锁（悲观锁）**

```python
attempt = session.scalars(select(Attempt).where(Attempt.id == 200).with_for_update()).one()
if attempt.status != "pending":
    return None
attempt.status = "running"
attempt.lease_epoch += 1          # 行被锁住，别人只能等，安全
```

更好读，代价是两次往返、锁一直持有到事务结束。项目里的 `regenerate` 就是这样写的，两种写法都有。

**为什么不像 pandas 那样批量操作**：`expire_leases` 要一次改掉所有过期的 attempt。对象写法要先把行全部读进
Python，再逐条写回；批量 UPDATE 一条语句在数据库里完成。**数据在哪里，就在哪里算。**

| 场景 | 写法 |
|---|---|
| 简单的增删改查 | 对象写法，好读 |
| 并发安全（检查后修改、计数器加一）、批量修改 | 条件 UPDATE（或先加锁） |

### 7. `_fence` 逐行看

**我的疑问**：`_fence` 是不是检查还有没有写的权限？

是，而且“检查权限”和“写入”是同一条语句：

```python
def _fence(session, lease, **values):
    result = session.execute(
        update(Attempt)
        .where(Attempt.id == lease.attempt_id,       # 条件 1：是这个 attempt
               Attempt.lease_epoch == lease.epoch,   # 条件 2：令牌号还是我领取时的那个
               Attempt.status == "running")          # 条件 3：还在运行（没被判失败）
        .values(**values)                            # 要写的内容，由调用方传入
    )
    if result.rowcount == 0:                         # 一行都没改到
        raise LeaseLost                              # → 执行权已经不是我的了
```

- `**values`：调用方想改什么就传什么。`commit_step` 传 `last_step=8, lease_until=...`，`finish` 传 `status="completed", final=...`。
  `_fence` 不关心改什么，只负责“带上权限条件去改”。
- `rowcount`：实际改了几行。`id` 是主键，所以**最多 1 行**（数据库保证，不只是理想情况）：
  1 行 = 执行权还在，写入成功；0 行 = 执行权丢了。不可能是 2 行，所以代码只判断 `== 0`。
- **为什么抛异常而不是返回 False**：调用方都在 `with session.begin():` 里，异常一抛**整个事务回滚**，
  同事务里准备写的 step、事件一起作废；异常再传到 worker 的 `except LeaseLost:`，worker 直接退出。

```
A 拿着 epoch=5 提交第 8 步；巡检已把 attempt 判失败，epoch=6
UPDATE ... WHERE id=200 AND lease_epoch=5 AND status='running' → 0 行 → LeaseLost
→ 第 8 步的 step 和事件随事务回滚 → A 退出
```

fence 是“栅栏”：拿着过期令牌的人被挡在外面。JPA 的 `@Version`（版本号乐观锁）是同一个思路，这里是手写的。

### 8. `finish` / `fail`：不只是改状态

一个事务做三件事，而且带 epoch 校验：

```python
with session.begin():
    _fence(session, lease, status="completed", final=..., finished_at=now())   # 0 行就整个回滚
    UPDATE runs SET status='completed'                                          # 题目状态跟着变
    add_event("attempt_finished", {...})                                        # SSE 推给前端
```

`fail` 结构相同，写 `failed` 和错误类型（如 `llm_http_502`）。
不直接 `UPDATE attempts SET status='completed'`：如果执行权已被巡检收走，这个 worker **不能**把巡检判的“失败”覆盖成“完成”。

### 9. Celery worker 的内部：容器、主进程、子进程

**我原来以为是“Celery 主进程里有一个 worker 容器”，实际正好相反：容器 ⊃ Celery 主进程 ⊃ 子进程。**

```
worker 容器（Docker 隔离出来的“小盒子”）
└─ celery -A margin.worker worker      ← 容器里跑的就是这条命令
   └─ Celery 主进程
      ├─ 子进程 1 … 子进程 4            （--concurrency 4：最多同时跑 4 道题）
```

“worker”这个词两用：Celery 里指“主进程 + 子进程池”这一整套，compose 里是容器服务名。现在一个容器里正好一个 Celery worker。

- **子进程一直活着**：worker 启动时就建好 4 个子进程（进程池，prefork），之后反复使用，不是来任务才创建。
  所以 `@cache` 有效：语料只在子进程第一次执行任务时加载。
- **每个子进程各有一份语料和 BM25**（共 4 份，每份约 1.2GB，见第二章）。
  主进程先加载再 fork 理论上能靠写时复制共享内存，但 Python 访问对象就会改引用计数，内存页照样被复制，效果很差。
- **主进程把消息交给空闲的子进程**：Celery 内部自动完成（分配、子进程意外退出后补新的），只需配置并发数和预取数。

**多开几个 worker 容器，谁来统一管理？** 没有一个“总的 Celery 主进程”，Celery 没有中心节点：

| 管什么 | 谁负责 |
|---|---|
| 消息分给哪个 worker | 消息中转站（下一节） |
| 容器的启动、重启、数量 | Docker Compose（`docker compose up --scale worker=3`），生产环境一般是 K8s |
| 容器内部的子进程 | 每个容器自己的 Celery 主进程 |

各 worker 互相不知道对方存在，只是一起从同一个队列取任务（**竞争消费者**）。加机器就再开一个容器连上同一个队列。

### 10. worker 怎么知道有新任务：推送，不是扫描

- `publish` 用 `send_task` 把“执行 attempt 200”直接发给消息中转站，dispatcher 和 worker 没有直接联系。
- worker **不轮询扫描**，而是等消息送上门：
  - **RabbitMQ**：主进程保持一条长连接并订阅队列，有消息时 **RabbitMQ 主动推送**。
  - **Redis（M1）**：主进程执行**阻塞式取出**（`BRPOP`），队列空时挂起等待，来消息立刻返回，不空转。
- 代码里看不到 get，因为“收消息、分给子进程”是 Celery 替我做的，我只写了 `execute_attempt`。

### 11. RabbitMQ 怎么分配消息、怎么做到负载均衡

**我的疑问**：多个 worker 是抢消息吗？是原子的吗？RabbitMQ 怎么知道谁忙谁闲？

- **原子**：每个队列在 RabbitMQ 内部按顺序处理，一条消息同一时刻**只交给一个消费者**。
- **不是抢，是派发**：RabbitMQ 主动推。（也有主动拉取单条的 `basic.get`，效率低，Celery 不用。）
- “同一时刻只给一个人”**≠ 只执行一次**：连接断开、超过确认期限时消息会回队列再派给别人，所以 `claim` 仍然要有。

**负载均衡靠“预取上限 + 确认”，RabbitMQ 不需要知道 CPU、内存：**

1. worker 第一次连接时**先声明上限**：预取上限 = 子进程数 × `worker_prefetch_multiplier` = 4 × 1 = 4，
   意思是“交给我但还没确认的消息，最多 4 条”。
2. **RabbitMQ 给每个 worker 计数**：交出一条 +1，收到确认 −1；到上限就不再发给它；有空位的不止一个时轮流分配。
3. 配合 `task_acks_late=True`（做完才确认），“没确认” = “正在做”，所以这个计数就是“正在执行几个任务”。

```
两个 worker 各 1 个子进程（上限各 1）。队列：[A 10 分钟] [B 1 分钟] [C 1 分钟] [D 1 分钟]

0:00  A → worker1（满）    B → worker2（满）
1:00  worker2 做完 B 确认 → 有空位 → C → worker2
2:00  worker2 做完 C → D → worker2；worker1 一直在跑 A，一条都不会再给它
```

**如果不设上限**（AMQP 默认不限）：RabbitMQ 一次性把消息轮流推出去，A、C 给 worker1，B、D 给 worker2；
C 被压在 worker1 手里干等 10 分钟，worker2 却早就闲了。本项目任务长短差异很大（几十秒到十几分钟），
所以 `worker_prefetch_multiplier=1` 很重要。

| 问题 | 答案 |
|---|---|
| RabbitMQ 知道 worker 的负载吗？ | 不知道，也不需要 |
| 按什么分配？ | 谁的“未确认数”没到上限就给谁，多个就轮流 |
| 机器强弱怎么体现？ | 多开子进程 → 上限变大 → 分到的活多 |
| 本质 | **用确认机制做的反压**：消费者处理多快，消息就发多快 |

> 对比 Kafka：消费者主动拉，每个分区固定给一个消费者，分区里有慢任务后面的消息都被堵住——这也是 D3 说 Kafka
> 不适合少量长任务的原因之一。

### 12. SSE 现状：M1 还没有“Redis → SSE → 前端”

M1 实际路径：

```
worker 每一步 → 写 PG 的 events 表
api 的 SSE → 每 1 秒查一次 events 表（runs.events_after）→ 推给浏览器
```

- `worker.py` 里 `on_delta=lambda ...: None` 是空函数，模型逐字输出的思考片段**现在直接丢掉**；
- 传空函数是为了走流式调用，让“60 秒收不到数据就放弃”对每一块都生效。

M2 计划：逐字片段走 Redis 发布订阅（不落库）；“有新事件”的通知代替每秒轮询，用 PG LISTEN / NOTIFY 还是 Redis，M2 开工时比较（PLAN §5.4）。

### 13. LISTEN / NOTIFY 放在哪（已写进计划）

| 位置 | 现在 | 改成通知后 | 安排 |
|---|---|---|---|
| dispatcher 等新 outbox | `time.sleep(1)` 后扫一遍 | 新 outbox 提交即被叫醒 | **M1.5**（PLAN §5.7 末尾） |
| SSE 等新事件 | 每个 SSE 连接每秒查一次 | 新事件提交即推送 | **M2 再定**（PLAN §5.4） |

关键细节：
- **NOTIFY 跟着事务走**：提交后才发出，回滚就取消，所以被叫醒时那一行一定已经能查到。
- **通知不持久**：没人在听就丢了，退避到期的重试也不会发通知 → **保留兜底扫描**（最长 10 秒）。
- LISTEN 要一条**专用的自动提交连接**，不从连接池借。
- SSE 那边**不能每个连接各开一条 LISTEN**（会耗尽 PG 连接），每个 API 进程只开一条，在内存里转发。
- 逐字片段仍走 Redis：不落库、量大，NOTIFY 单条有 8KB 上限且会经过 PG。

### 面试一句话

> 系统是 api、worker、dispatcher 三个独立程序加一次性的 migrate，由 docker compose 拉起；api 和 worker 的入口是框架
> （uvicorn、Celery）回调我的函数。worker 是一个 Celery 主进程管 4 个常驻子进程，主进程订阅 RabbitMQ，RabbitMQ 按预取上限
> 推送消息、做完才确认，从而实现负载均衡；子进程领取任务靠一条条件 UPDATE，每一步写库都经过 `_fence` 校验 epoch，
> 更新 0 行就整个事务回滚并停止。
