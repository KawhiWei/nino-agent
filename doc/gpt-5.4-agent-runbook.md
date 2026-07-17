# gpt-5.4 Agent 启动与验收

## 1. 固定配置契约

当前 Python Runtime 固定使用 `gpt-5.4`。模型名写在 `agent/python/src/bootstrap.py` 中，
不读取 `OPENAI_MODEL`、`NINO_MODEL_NAME` 或其他模型名称环境变量。

模型连接只读取：

| 配置 | 来源 | 说明 |
|---|---|---|
| 模型 | 代码固定 `gpt-5.4` | 不允许环境变量覆盖 |
| `OPENAI_API_KEY` | Runtime 进程环境 | 必填，禁止写入项目文件 |
| `INCERRY_OPENAI_BASE_URL` | Runtime 进程环境 | 必填，当前网关为 `http://core.dns-pro.net:13001/v1` |

`NINO_MODEL_ADAPTER` 只选择 `native` 或 `langchain` Adapter，不改变模型。

## 2. 本地环境变量

当前终端临时配置：

```bash
export OPENAI_API_KEY='<your-key>'
export INCERRY_OPENAI_BASE_URL='http://core.dns-pro.net:13001/v1'
export NINO_RUNTIME_MODE=live
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=native
export NINO_MCP_URL='http://127.0.0.1:8091/mcp'
```

需要让 macOS 新开的 GUI 应用继承时，可在登录会话中设置：

```bash
launchctl setenv OPENAI_API_KEY '<your-key>'
launchctl setenv INCERRY_OPENAI_BASE_URL 'http://core.dns-pro.net:13001/v1'
```

设置后需要重新打开对应终端或应用。不要执行会打印 Key 的 `env`、`printenv` 或配置转储命令，
也不要把 Key 写入项目 `.env`。

## 3. 本地启动

先启动 PostgreSQL 和 .NET MCP，Python Runtime 在本机运行：

```bash
cd /Users/wangzewei/Documents/Code/github/luck/AiAgent/newagent-vv/nino-agent
docker compose up -d db nino-data

cd agent/python
.venv/bin/python -m uvicorn api.app:app --host 127.0.0.1 --port 8090 --reload
```

检查：

```bash
curl -s http://127.0.0.1:8090/health
curl -s 'http://127.0.0.1:8090/api/v1/mcp/servers?discover=true'
```

健康响应必须包含 `"runtime_mode":"live"` 和 `"model_adapter":"native"`；MCP 发现结果中的
`nino-data` 必须可用。

## 4. ReAct 端到端验收

创建会话：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"gpt-5.4 live verification"}'
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
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}/loop-checkpoint
```

验收重点不是只有最终文本，而是事件链中出现：

```text
orchestration model_started
-> nino_runtime_dispatch_agent tool_started/tool_completed
-> agent_started + skill_selected
-> worker model_started
-> nino_data_* tool_started/tool_completed
-> agent_completed
-> run_completed
```

同一会话继续追问“那退款占收入的比例是多少”，复用原 `conversation_id`，验证 SQLite 多轮上下文。

## 5. 常见失败

| 现象 | 检查项 |
|---|---|
| 启动时报 `OPENAI_API_KEY is required` | Key 是否存在于启动 Runtime 的同一进程环境 |
| 启动时报 `INCERRY_OPENAI_BASE_URL is required` | 变量名是否准确，以及是否包含 `/v1` |
| 模型 HTTP 401 | Key 无效、过期或包含多余引号 |
| 模型 HTTP 404 | 网关是否提供 `/v1/chat/completions`，并支持 `gpt-5.4` |
| `TOOL_DISCOVERY_ERROR` | `nino-data`、8091 MCP endpoint 和 Tool 白名单 |
| 主模型直接猜业务答案 | 检查 dispatch 和 MCP `tool_started` 事件是否出现 |
