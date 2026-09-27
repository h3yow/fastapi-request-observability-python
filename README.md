# FastAPI 请求追踪与可观测性

在原有服务上补充了请求追踪与可观测性能力：关联标识、结构化日志、错误归一化、
按接口维度的指标与健康/就绪探针。**现有接口的对外返回结构不变**（`GET /` 仍返回
`{"Hello": "World"}`），所有能力通过中间件与新增端点叠加。

## 关联标识（Correlation ID）传递规则

- 请求头：`X-Correlation-ID`。
- **沿用**：客户端传入的标识若合法（1~128 位，仅字母数字与 `-`、`_`、`.`），
  服务端原样沿用，并回写到响应头 `X-Correlation-ID`。
- **补发**：未携带、或标识非法（伪造、含空白/控制字符、超长）时，服务端生成
  UUID4（hex）作为本请求的标识。
- **传播**：标识存放于 `contextvars.ContextVar`，`await` 与 `asyncio` 子任务
  自动继承同一标识，高并发下不同请求互不串号（见 `GET /work` 演示端点）；
  同时写入 `request.scope["correlation_id"]`，保证未捕获异常冒泡到最外层
  中间件后，异常处理器仍能取到同一标识。
- 异常处理、日志、指标、下游调用均可通过 `app.correlation.get_correlation_id()`
  获取；无请求上下文时返回占位值 `-`。

## 结构化日志口径

所有日志为单行 JSON（`app.logging_config.JsonFormatter`），固定字段：

| 字段 | 说明 |
| --- | --- |
| `timestamp` | UTC ISO8601 |
| `level` | `INFO` / `WARNING` / `ERROR` |
| `logger` | logger 名（如 `app.request`、`app.errors`） |
| `message` | 日志消息 |
| `correlation_id` | 当前请求的关联标识 |
| `exception` | 仅异常日志：完整 traceback（含 `__cause__` 异常链） |

级别约定（稳定可断言）：

- 请求完成：`INFO`，`logger=app.request`，附 `method/path/status_code/duration_seconds`；
- 业务错误（`AppError`）与参数校验失败：`WARNING`，`logger=app.errors`；
- 未捕获异常：`ERROR`，`logger=app.errors`，`exception` 字段保留完整内部异常链，
  **只在日志中可见，不会出现在响应体里**。

## 指标口径

`GET /metrics` 返回内存指标（`app.metrics.registry`）：

- 维度：`METHOD 路径模板`（如 `GET /items/{item_id}`），路径参数归一到模板，避免基数爆炸；
- 字段：`request_count`、`error_count`、`total_duration_seconds`、`avg_duration_seconds`；
- `error_count` 只统计 **5xx**（服务端错误），4xx 不计入；
- 注册表全程加锁，并发写入与并发读取快照互不污染，快照为一致性拷贝。

## 健康与就绪探针

- `GET /health/live`：存活探针，恒 200；
- `GET /health/ready`：就绪探针，任一探针 `fail` 时整体返回 **503**；
- 每个探针返回 `{status, reason}`，`status ∈ {ok, degraded, fail}`，
  判定原因精确到探针名与具体原因（如 `connection refused`），
  探针自身抛异常会被归一为 `fail` 并记录异常原因，不影响其他探针。

## 错误归一化

对外错误体固定为：

```json
{"error": {"code": "...", "message": "...", "correlation_id": "..."}}
```

| 场景 | code | HTTP |
| --- | --- | --- |
| 业务异常（`AppError` 子类） | 自带 code，如 `NOT_FOUND` | 自带状态码 |
| 请求参数校验失败 | `VALIDATION_ERROR` | 400 |
| 未捕获异常 | `INTERNAL_ERROR` | 500 |

内部异常链（traceback、`raise ... from ...`）完整保留在 ERROR 日志中；
响应体只含归一化信息，不泄露内部细节。错误响应同样携带
`X-Correlation-ID` 响应头，可用它与日志对账。

## 本地验证

```bash
# 安装依赖
uv sync

# 跑全部测试（-s 可看到每个用例打印的关联标识与判定依据）
uv run pytest -s

# 启动服务
uv run uvicorn main:app --port 8000

# 手动验证
curl -i http://127.0.0.1:8000/                       # 响应头带 X-Correlation-ID
curl -i -H 'X-Correlation-ID: my-trace-1' http://127.0.0.1:8000/work
curl -i http://127.0.0.1:8000/items/0                # 404 归一化错误
curl -i http://127.0.0.1:8000/boom                   # 500 归一化错误，内部链在日志
curl -s http://127.0.0.1:8000/metrics | jq
curl -s http://127.0.0.1:8000/health/ready | jq
```

用响应头或错误体里的 `correlation_id` 在服务日志中检索，即可复现该请求
从进入到返回（或出错）的完整链路。
