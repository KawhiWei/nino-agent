# DeepSeek Agent 启动与验收

## 1. 当前完成度

后端 Agent 链路已经具备：FastAPI、持久化 Conversation/Run、ReAct Harness、共享 Skill/Agent、
多 MCP Registry、.NET 数据 MCP、PostgreSQL、SQLite 上下文和 SSE。接入 DeepSeek 后即可成为真实
模型驱动的可运行 Agent，不需要先实现 Web 或 ACP。

尚需外部条件：DeepSeek API Key、可用余额、访问 `api.deepseek.com` 的网络，以及一次真实 Tool
Calling 端到端验收。认证、前端、ACP、生产监控和写操作属于后续能力，不阻塞面试 MVP。

## 2. 推荐模型

DeepSeek 官方在 2026-07-16 提供 `deepseek-v4-pro` 和 `deepseek-v4-flash`，两者都支持 Tool
Calls、thinking/non-thinking 和 1M context。`deepseek-chat` 与 `deepseek-reasoner` 将于
2026-07-24 弃用，新配置不要继续使用这两个别名。

- `deepseek-v4-pro`：优先用于面试演示和复杂分析。
- `deepseek-v4-flash`：优先用于低成本联调。

官方文档：[Quick Start](https://api-docs.deepseek.com/)、[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)。

## 3. 配置

在项目根目录创建不提交 Git 的 `.env`：

```dotenv
NINO_RUNTIME_MODE=live
NINO_AGENT_ENGINE=lightweight
NINO_MODEL_ADAPTER=native
NINO_MODEL_NAME=deepseek-v4-pro
NINO_MODEL_API_KEY=<your-deepseek-api-key>
NINO_MODEL_BASE_URL=https://api.deepseek.com

# 首次联调先关闭 thinking，减少变量。跑通后可改为 enabled。
NINO_MODEL_THINKING=disabled
NINO_MODEL_REASONING_EFFORT=

# DeepSeek V4 支持 1M context；MVP 先用 128K 运营上限控制成本。
NINO_MODEL_CONTEXT_TOKENS=128000
NINO_CONTEXT_RESERVED_TOKENS=32000
NINO_CONTEXT_RECENT_TOKENS=48000
NINO_CONTEXT_SUMMARY_TOKENS=12000
```

Thinking Tool Calls 已支持 `reasoning_content` 回传。启用方式：

```dotenv
NINO_MODEL_THINKING=enabled
NINO_MODEL_REASONING_EFFORT=high
```

API Key 只能放在 `.env` 或 Secret Manager，不能写入 README、Skill、Dockerfile 或 Git。

## 4. 启动

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8090/health
```

健康响应必须包含：

```json
{
  "status": "ok",
  "runtime_mode": "live",
  "model_adapter": "native"
}
```

## 5. 真实 ReAct 验收

创建会话：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"DeepSeek live verification"}'
```

使用返回的 `conversation_id` 提问：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"查询订单 DEMO-202607-001，给出收入、成本、退款和毛利，并说明数据来源"}'
```

使用返回的 `run_id` 检查事件和结果：

```bash
curl -N http://127.0.0.1:8090/api/v1/runs/{run_id}/events/stream
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}
```

通过标准不是“模型返回了一段文字”，而是事件中依次出现：

```text
model_started: phase=orchestration
tool_started: nino_runtime_dispatch_agent
agent_started: nino-data.analyst + nino-data.analysis
skill_selected
model_started
tool_started: nino_data_get_order_detail
tool_completed
agent_completed
model_started
run_completed
```

再用统计、异常和同一 `conversation_id` 的省略式追问验证三个 Tool 与持久化上下文。

## 6. 常见失败

| 现象 | 检查项 |
|---|---|
| 启动即报 API Key/模型为空 | `.env` 是否被 Compose 读取，`NINO_RUNTIME_MODE` 是否为 `live` |
| DeepSeek HTTP 401 | Key 无效或环境变量包含多余引号 |
| DeepSeek HTTP 402 | 账户余额不足 |
| 第二个 Tool step HTTP 400 | Thinking 模式必须回传 `reasoning_content`；v0.10.0 已支持 |
| `TOOL_DISCOVERY_ERROR` | `nino-data` 容器、8091 MCP 和 Tool 名称白名单 |
| 主模型直接猜业务答案 | 检查能力描述，以及是否出现 `nino_runtime_dispatch_agent` 和业务 `tool_started` |
