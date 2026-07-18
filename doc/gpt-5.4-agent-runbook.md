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

当前真实调用采用四角色顺序：`nino.orchestrator` 先路由，`nino.planner` 只提交候选节点，
Orchestrator 校验并持久化后调度 `nino.analyst`，再由 `nino.verifier` 独立复查，最后 Orchestrator
在无 Tool 模式下汇总。自然语言请求不需要也不应该包含 Agent、Skill 或 Tool 名称。

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
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}/task-graph
```

验收重点不是只有最终文本，而是事件链中出现：

```text
规划模型启动：planning model_started (`nino.planner`)
-> 提交候选节点：nino_runtime_submit_task_graph_node
-> 任务图已规划或已修订：graph_planned / graph_reconciled
-> 分析节点启动并选定 Skill：agent_started (`nino.analyst`) + skill_selected
-> Worker 模型启动：worker model_started
-> 数据工具启动并完成：nino_data_* tool_started/tool_completed
-> 分析节点完成：agent_completed (`nino.analyst`)
-> 独立验证节点启动：agent_started (`nino.verifier`)
-> 独立只读 Tool 调用和结构化裁决：independent read-only Tool call + structured verdict
-> 证据与独立验证门禁通过：evidence and independent_verification Gates passed
-> 总控无工具归并启动：reconciliation model_started (`nino.orchestrator`)
-> 整个运行完成：run_completed
```

`task-graph` 中不会出现 Planner Node；Planner proposal 只有被 Orchestrator 接受后才投影为 Specialist
和 Verification Node。最终应看到 `orchestration/specialist/verification` 三类 Node 全部 completed。
恢复或 reconcile 验收还应检查 Node metadata：相同 Fingerprint 才允许复用；显式替换失败/blocked
工作的 repair Node 应记录 `supersedes_node_id`，被影响且尚未完成的旧下游状态应为 `superseded`。

如果 Specialist 已 Completed、但独立 Verification 失败，原 Specialist 的 status/result/gate 必须保持
冻结。Planner 应创建新的独立只读 repair Node，不设置 `supersedes_node_id`，也不依赖原 Completed
Node；事件中应出现新的 `graph_reconciled` 和后续验证链。

同一会话继续追问“为什么收入和毛利率排名不同”，复用原 `conversation_id`。如果问题只需要解释、
比较、改写或计算先前已接受回答，Planner 可选择 `nino_runtime_answer_from_history`，随后应观察到：

```text
选择仅历史回答：nino_runtime_answer_from_history
-> 无 Tool 历史归并：history_reconciliation model_started/completed
-> 以历史回答完成：run_completed (outcome=history_answer)
```

该分支不应启动 Analyst/Verifier 或业务 MCP Tool。需要新数据的追问仍必须进入完整 Worker + Gate 链路。

## 5. Docker Compose 全栈启动

导出模型配置后，可一次启动 React Web、Runtime、.NET MCP 和 PostgreSQL：

```bash
docker compose up -d --build
docker compose ps
```

入口：Web `http://127.0.0.1:3000`，Runtime `http://127.0.0.1:8090`，MCP `8091`，PostgreSQL
`55432`。Web 由 Nginx 提供静态文件，并同源代理 `/api`、`/health` 和 SSE。

## 6. 常见失败

| 现象 | 检查项 |
|---|---|
| 启动时报 `OPENAI_API_KEY is required` | Key 是否存在于启动 Runtime 的同一进程环境 |
| 启动时报 `INCERRY_OPENAI_BASE_URL is required` | 变量名是否准确，以及是否包含 `/v1` |
| 模型 HTTP 401 | Key 无效、过期或包含多余引号 |
| 模型 HTTP 404 | 网关是否提供 `/v1/chat/completions`，并支持 `gpt-5.4` |
| `TOOL_DISCOVERY_ERROR` | `nino-data`、8091 MCP endpoint 和 Tool 白名单 |
| 模型直接猜业务答案 | 检查 planning、graph_planned 和 MCP `tool_started` 事件是否出现 |
