# 前后端基础知识 review

## 1. 前后端基础知识

```
用户输入
https://margin.com
       │
       │ GET /
       ↓
     Nginx
       │
       │ 返回 HTML/JS/CSS
       ↓
    Browser
       │
       │ 执行 React
       ↓
   页面显示出来
       │
       │ 用户点击提交
       ↓
React: POST /api/runs
       │
       │ 实际完整 URL：
       │ https://margin.com/api/runs
       ↓
     Nginx
       │
       │ /api/* → FastAPI:8000
       ↓
    FastAPI
       │
       ├→ 验证用户
       ├→ 验证参数
       ├→ 执行业务逻辑
       └→ 操作数据库
```

---

### ⭐流程解释

1. 假设目前用户访问 "https://margin.com" 网站
   此时浏览器会默认拼接成: "https://margin.com:443/" 进行 GET / 操作返回前端页面，如"index.html/CSS/JS"文件，再渲染

   - 为什么 浏览器 会默认访问 443 端口？因为这是 HTTPS 服务协议默认的端口，约定成俗的，HTTP 服务就是默认 80端口

     - 假如服务在别的端口，得自己写网址端口访问："https://margin.com:12345"

   - GET / 操作是浏览器直接进行的，默认的，为了得到服务的前端页面

   - **Nginx** 监听 443 端口，并进行服务的转发，如果是 /api 开头就转发给FastAPI，如果是 / 就转发获取编译后的前端文件

     - 注意：Nginx 这些都是可以配置的，包括监听的端口

     - 可以理解成：

       ```
                           Nginx
                             │
                      看 URL 路径是什么
                     ┌───────┴────────┐
                     ↓                ↓
                    "/"             "/api/*"
                     ↓                ↓
             React 静态文件       FastAPI:8000
       ```

     - 能实现这个的原因是：我们提前给 Nginx 写了配置。概念上类似：

       ```
       # /api 开头 → FastAPI
       location /api/ {
           proxy_pass http://fastapi:8000;
       }
       
       # 其他路径 → React 编译后的文件
       location / {
           root /var/www/frontend;
           try_files $uri /index.html;
       }
       ```

2. 如果用户进行服务请求操作，此时会按照返回的前端页面进行逻辑处理，像搜索服务访问什么 "/api/runs" 都是前端页面写，用户是看得到的，就是前端源码里有这种请求，此时完整请求为：

   ```
   https://margin.com:443/api/runs
     │        │       │      │
   协议      域名     端口    路径
   ```

   只不过 `HTTPS + 443` 太常见，所以通常省略成：

   ```
   https://margin.com/api/runs
   ```

所以 **不能因为“前端没有按钮”就认为用户无法调用某个 API** 这是一条安全设计原则，攻击者完全可以自己构造

第二条后端安全原则就是：**永远不能相信前端传来的任何参数，都要做校验**

用户可以在 F12 里看到 `/api/runs`，那他完全可以绕过 React，自己疯狂 POST 这个 API 

这正好就能引出后端存在的核心意义： **认证、Session、权限、429 限流、幂等**

另外其实完整服务一般是：https://margin.com/api/runs?page=2&limit=20

这个是一个query string查询参数的例子：

```
/api/runs       ← path
?               ← 是 查询参数 query 开始的标志
page=2
limit=20        ← query parameters
```

具体比如说用户点击 看聊天记录，点击第2页，此时你就要去查询 第2页的内容并显示出来，同时限制查询结果为20项

这种 **代码逻辑是提前写好的，但具体数字通常是运行时动态拼出来的**

```
https://margin.com/api/runs?page=2&limit=20
│       │          │          │
│       │          │          └─ Query Parameters
│       │          │
│       │          └─ Path
│       │
│       └─ Domain
│
└─ Scheme
```

---

### 前端解释

浏览器本质上需要拿到一些文件：

```
HTML     → 页面骨架
CSS      → 页面长什么样
JavaScript → 页面怎么动、怎么交互
```

比如你访问：

```
https://margin.com
```

第一件事情其实是：

```
浏览器
  │ GET /
  ↓
服务器
  │ 返回 index.html + JS + CSS
  ↓
浏览器
  │
  ↓
把页面画出来
```

React 最终也逃不掉这个过程

```
React + TypeScript
```

经过 Vite build 以后，会产生类似：

```
dist/
├── index.html
├── assets/
│   ├── index.js
│   └── index.css
```

**真正给浏览器的，是这些编译后的静态文件**

---

### 后端解释

1. **Nginx 是 一种反向代理**

   先理解普通的“代理”：假设你找中介租房：

   ```
   你 → 中介 → 房东
   ```

   房东不知道你直接怎么找来的，这个中介站在**客户端这一边**替你访问别人，可以理解成“正向代理”。

   例如：

   ```
   客户端
      ↓
    Proxy
      ↓
   Internet 上的服务器
   ```

   ------

   反向代理正好反过来，现在你是用户，你访问：

   ```
   margin.com
   ```

   你根本不知道后面有什么：

   ```
                 ┌→ FastAPI 1
   Browser → Nginx ─→ FastAPI 2
                 └→ 静态文件
   ```

   Nginx 是**替服务器们站在前面接客**的。

   所以：

   ```
   正向代理：代理客户端
   反向代理：代理服务器
   ```

   这就是“反向”两个字的来源

   *⭐你可能会疑问：Nginx和FastAPI不都是一种转发器？的确是，但是职责不同*

   ```
   Nginx
   “这个请求应该交给谁？”
           ↓
   FastAPI
   “这个请求具体应该做什么？”
   ```

   可以用餐厅来类比：

   ```
   顾客
    ↓
   前台接待（Nginx）
    ↓
   “你要吃饭？去餐厅”
   “你要住宿？去酒店”
   “你要取快递？去服务台”
    ↓
   真正办事的人（FastAPI）
    ↓
   创建订单、查数据库、检查权限……
   ```

   所以 Nginx 是**基础设施层的路由/入口**，FastAPI 是**业务应用**

2. **GET 和 POST 的区别**
   GET  = 我要读取东西 
   POST = 我要提交/创建东西

3. **HTTP Status Code**

   | 状态码                      | 意思                   | Margin 中的例子                    |
   | --------------------------- | ---------------------- | ---------------------------------- |
   | `200 OK`                    | 成功                   | 查 Run 成功                        |
   | `201 Created`               | 创建成功               | 创建了某个资源                     |
   | `202 Accepted`              | 已接受，稍后处理       | **Agent 任务已经接收，但还没跑完** |
   | `400 Bad Request`           | 请求本身有问题         | 参数格式错误                       |
   | `401 Unauthorized`          | 没登录/身份无效        | Session 失效                       |
   | `403 Forbidden`             | 知道你是谁，但你没权限 | 无权执行某操作                     |
   | `404 Not Found`             | 找不到                 | Run 不存在                         |
   | `409 Conflict`              | 发生冲突               | 幂等 key 相同但参数不同            |
   | `429 Too Many Requests`     | 请求太多               | 超过用户配额                       |
   | `500 Internal Server Error` | 后端炸了               | Python 未处理异常                  |

   HTTP 状态码总体分成 `1xx` 信息、`2xx` 成功、`3xx` 重定向、`4xx` 客户端问题、`5xx` 服务端问题

   比如：假设有人直接 POST /api/check，Nginx 同样会转发到 FastAPI 但是 FastAPI 根本没这个路径的处理，就会返回 404

   又比如这个项目用 `202` 特别合理：

   ```
   POST /api/runs
          ↓
   FastAPI 创建任务
          ↓
   202 Accepted
          ↓
   “我接到了，但 Agent 还在后台跑”
   ```

4. **Owner_ID 相关登录问题，后端是如何知道你是哪一个用户的**

 当你第一次在一个浏览器的前端输入密码登录时，后端检索数据库成功后，服务器会生成一个非常随机的字符串，并返回回来

 浏览器会保存这个字符串 叫做 Session Cookie ，之后的每次请求都自动附带 Cookie

 Cookie 本来就是浏览器在后续请求中自动带回服务器的一种机制
 这样可以防止 Owner_ID 等相关信息的明文泄露，
 Cookie 本身也有保护措施，比如 HttpOnly Cookie 这个能保证你进入并加载恶意的前端页面时无法窃取到浏览器的 Cookie 

 > **即：运行在网页里的 JavaScript 无法通过正常 Web API 读取 HttpOnly Cookie。**

 这主要是为了防 **XSS**

 假设攻击者成功让你的页面运行了一段恶意 JS 前端代码：

 ```
 偷走(document.cookie)
 ```

 如果 Session Cookie 不是 HttpOnly，就可能被偷走。

 设置 HttpOnly 后，恶意 JS 也受到浏览器权限限制：

 ```
 “Session Cookie 是多少？”
 Browser：不给。
 ```

5. **HTTP 参数请求方法**

   HTTP 最常见的 **5 个**

   | 方法       | 直觉理解  | 例子                |
   | ---------- | --------- | ------------------- |
   | **GET**    | 查        | 获取任务信息        |
   | **POST**   | 新增/提交 | 创建一个 Agent 任务 |
   | **PUT**    | 整体修改  | 整体替换某个资源    |
   | **PATCH**  | 部分修改  | 只修改任务名称      |
   | **DELETE** | 删除      | 删除一个任务        |

   比如假设我们有一个 Run：

   ```
   /api/runs/123
   ```

   可以设计成：

   ```
   GET    /api/runs/123     → 查看 123
   DELETE /api/runs/123     → 删除 123
   PATCH  /api/runs/123     → 修改 123
   
   POST   /api/runs         → 创建一个新 Run
   ```

   ### PUT 和 PATCH 最容易混

   假设用户资料是：

   ```
   {
     "name": "Tom",
     "age": 20,
     "city": "Miami"
   }
   ```

   **PUT** 更接近“整个换掉”：

   ```
   PUT /api/users/123
   {
     "name": "Jack",
     "age": 21,
     "city": "Boston"
   }
   ```

   而 **PATCH** 是“我只改其中一部分”：

   ```
   PATCH /api/users/123
   {
     "age": 21
   }
   ```

   其他字段不动

