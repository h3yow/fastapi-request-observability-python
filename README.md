# FastAPI 请求追踪与可观测性

在不改变既有接口（`GET /` 返回 `{"Hello": "World"}`）对外结构的前提下，
为服务补上：贯穿全链路的请求关联标识、结构化日志与错误归一化、
按接口维度的指标，以及健康/就绪探针。

## 目录结构

```
app/
├── __init__.py             # create_app()：组装应用
├── context.py              # 基于 contextvars 的关联标识存取
├── middleware.py           # 纯 ASGI 中间件：标识/日志/指标/兜底
├── logging_config.py       # JSON 结构化日志与关联标识过滤器
├── errors.py               # 错误码、AppError 与错误归一化
├── exception_handlers.py   # FastAPI 异常处理器
├── metrics.py              # 线程安全的接口维度指标注册表
├── health.py               # /health/live 与 /health/ready
└── routes.py               # 业务路由、/async-demo、/metrics
tests/                      # 正常/异常/并发场景测试
main.py                     # uvicorn 入口
```

## 1. 请求关联标识（Request ID）

- 请求头：`X-Request-ID`（大小写不敏感）。
- 传递规则：
  1. **客户端未携带**：服务端生成 32 位 `uuid4().hex`；
  2. **客户端携带且合法**：原样沿用。合法字符集为 `[A-Za-z0-9_-]`，
     长度 1–128；
  3. **缺失或伪造（含空格、`/`、`<>`、超长等）**：丢弃客户端值，
     生成新值，并打一条 `WARNING`（`invalid client request id
     rejected; generated a new one`），避免日志注入与串号。
- 生效的关联标识一定会通过**响应头** `X-Request-ID` 回传，客户端可
  凭此对账；响应体结构保持不变（`/` 仍是 `{"Hello": "World"}`）。
- 实现基于 `contextvars.ContextVar`，由纯 ASGI 中间件在请求开始时
  设置：
  - 高并发下每个请求持有独立上下文副本，互不串号；
  - 请求内通过 `asyncio.gather` 等派生的异步分支会复制当前上下文，
    下游调用（`app.context.get_request_id()`）与异常处理器拿到的是
    同一个标识（见 `GET /async-demo`）。
- 访问日志字段 `request_id_source` 标识来源：`client` /
  `generated` / `invalid`。

## 2. 结构化日志

每条日志为**单行 JSON**（UTF-8，不转义非 ASCII），稳定字段：

| 字段 | 含义 |
| --- | --- |
| `timestamp` | UTC ISO-8601 时间 |
| `level` | `INFO` / `WARNING` / `ERROR` |
| `logger` | logger 名称，如 `app.access`、`app.routes` |
| `message` | 稳定的事件文案 |
| `request_id` | 当前请求关联标识，无上下文时为 `null` |
| 其余 | 通过 `extra` 传入的上下文（`method`/`path`/`endpoint`/`status_code`/`duration_ms` 等） |

异常时额外输出 `exc_type`、`exc_message`、`traceback`（含完整异常链），
内部异常只进日志，不进入响应。

级别口径（稳定可断言）：

- `2xx/3xx` → `INFO`，消息 `request completed`；
- `4xx` → `WARNING`（业务错误另有一条 `app.errors: application error`
  并带 `error_code`）；
- `5xx` → `ERROR`；未预期异常消息为 `unhandled exception during
  request`，并附完整 traceback。

配置入口：`app.logging_config.configure_logging()`，幂等，重复调用
不会叠加 handler。

## 3. 指标

`GET /metrics` 返回按**路由模板**（如 `/items/{item_id}`）聚合的
JSON 快照，每个接口包含：

- `requests`：请求总数；
- `errors`：状态码 ≥ 400 的错误数；
- `total_duration_ms` / `avg_duration_ms` / `min_duration_ms` /
  `max_duration_ms`：耗时口径（毫秒，`time.perf_counter()` 计时）；
- `status_codes`：按状态码分桶的计数。

口径说明：

- 注册表所有读写在同一把锁下进行，`snapshot()` 返回深拷贝，并发
  记录与并发抓取互不污染；
- `/metrics`、`/health/live`、`/health/ready` **不计入**业务指标，
  探针被高频/并发访问时不会污染接口统计；
- 未匹配路由记在 `unmatched` 标签下。

## 4. 错误归一化

所有错误响应统一为：

```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "safe message",
    "request_id": "当前关联标识"
  }
}
```

| 场景 | HTTP 状态码 | `code` |
| --- | --- | --- |
| 资源不存在（`NotFoundError`） | 404 | `not_found` |
| 下游依赖失败（`UpstreamError`） | 502 | `upstream_error` |
| 参数校验失败 | 422 | `bad_request`（不回显输入） |
| 其它 HTTPException | 原状态码 | 4xx `bad_request`/`not_found`，其余 `internal_error` |
| 未预期异常 | 500 | `internal_error`（固定文案 `internal server error`） |

内部异常的类型、消息、堆栈只出现在服务端结构化日志中；例如
`GET /items/0` 会抛出含 “secret” 的 `RuntimeError`，但响应中
看不到任何内部细节，只能凭 `request_id` 与日志对账。

## 5. 健康检查

- `GET /health/live`：存活探针，进程能响应即 200，`status=alive`，
  并给出 `checks.process.reason`；
- `GET /health/ready`：就绪探针，聚合各项检查并逐项给出
  `status`（`up`/`down`）与 `reason`：
  - 全部 `up` → 200，`status=ready`；
  - 任一失败 → 503，`status=not_ready`，原因逐项可区分（多个原因
    以 `; ` 连接），而不是笼统的成功/失败。
  可通过 `app.health.readiness.set_ready(False, ["...原因..."])`
  在启动/停机钩子中切换状态。

## 6. 本地验证

```bash
# 安装依赖（uv）
uv sync

# 运行全部测试（18 个用例：正常/异常/并发交织/结构化日志）
uv run pytest -s        # -s 可看到每条用例打印的 [TEST-LOG]，含 request_id 与判定依据

# 启动服务
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

手工对账示例：

```bash
# 不带标识 -> 自动补齐
curl -i http://127.0.0.1:8000/

# 携带标识 -> 全链路沿用（含异步分支）
curl -s -H 'X-Request-ID: my-trace-001' http://127.0.0.1:8000/async-demo

# 伪造标识 -> 被替换，日志出现 WARNING
curl -i -H 'X-Request-ID: bad/../ x' http://127.0.0.1:8000/

# 未预期异常 -> 对外归一化 500，内部细节只在日志
curl -s -H 'X-Request-ID: crash-001' http://127.0.0.1:8000/items/0

# 指标 / 探针
curl -s http://127.0.0.1:8000/metrics
curl -s http://127.0.0.1:8000/health/live
curl -s -w ' [%{http_code}]\n' http://127.0.0.1:8000/health/ready
```

## 7. 演示接口一览

| 路径 | 说明 |
| --- | --- |
| `GET /` | 既有接口，返回结构不变 |
| `GET /async-demo` | 派生两个并发异步分支，验证关联标识一致 |
| `GET /items/{item_id}` | `0`→500，`1`→404，`2`→502，其余→200 |
| `GET /metrics` | 接口维度指标快照 |
| `GET /health/live` / `/health/ready` | 存活 / 就绪探针 |
