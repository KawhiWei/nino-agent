# 第五章：让 Harness 真正成为产品后端

> Nino Agent Harness 工程实践（五）
> 主要提交：`46535be`、`1236bc7`、`175c506`、`32476c9`
> 主题：流式回答、会话客户端、断线恢复与容器化

一个 Agent Harness 即使拥有完善的 TaskGraph、Gate 和恢复语义，如果用户只能等待一个旋转图标，它仍然不是完整产品。用户需要知道任务是否已接受、正在规划还是查询、能否取消、断线后是否可以继续，以及页面刷新后会话是否还在。

Nino Agent 的产品化不是在后端外面套一个聊天框，而是把内部执行事实投影成稳定的 API 和交互状态。`46535be` 增加最终回答流式事件，`1236bc7` 增加 React 会话客户端和一体化 SSE，随后两个提交完善追问、容器部署和更细的执行进度。

## 1. 流式输出首先是协议问题

模型 Provider 返回的 stream 通常包含文本 delta、reasoning 分片和分片 Tool Call 参数。直接把这些原始 chunk 转发到浏览器会暴露内部推理和子 Agent 内容，也会让客户端依赖具体模型协议。

Nino Agent 的 Native OpenAI-compatible Adapter 先在基础设施层聚合：

```text
provider chunks
  -> 聚合 text
  -> 聚合 reasoning（仅内部使用）
  -> 拼接 Tool Call name/arguments
  -> 产出统一 ModelTurn
```

Harness 完成 Orchestrator 最终归并后，才将最终用户回答转成 `answer_delta` 事件。Planner、Analyst、Verifier 的内部文本不会作为回答 delta 暴露。

这条边界避免了两个常见问题：一是把 chain-of-thought 或内部工作草稿泄露给客户端；二是用户先看到未验证的 Analyst 结论，随后又被最终结果推翻。

## 2. 为什么只流最终 Orchestrator 回答

多 Agent 执行过程中会产生大量文本。如果全部流给用户，界面看起来很“实时”，但语义并不可靠：

- Planner 输出只是 proposal，不是 Graph Truth；
- Analyst 输出尚未通过 Verifier；
- Verifier concern 可能触发 repair；
- Orchestrator 归并前，节点结果不代表最终回答。

因此内部阶段通过结构化进度事件展示，只有最终 Orchestrator 文本作为回答流展示：

```text
内部状态 -> graph_planned / agent_started / tool_started / gate...
用户回答 -> answer_delta
权威终态 -> run_result
```

“进度透明”和“暴露内部推理”不是同一件事。产品应该展示可验证的执行状态，而不是模型思考直播。

## 3. 持久化 delta，而不是临时 WebSocket 消息

`answer_delta` 进入现有 Run Event 链并持久化到 SQLite。这样它和其他事件一样拥有 sequence，可以通过 SSE 实时读取，也可以在断线后重放。

最终还有一个必须满足的不变量：

```text
concat(all persisted answer_delta.delta) == terminal Run.answer
```

测试对真实 SSE 分片拼接和最终 Run answer 做一致性检查。若客户端漏了某个 delta，也可以在 `run_result` 到达时用权威答案收口；若连接提前断开，则按持久化事件或 Run 结果恢复。

这比只在进程内向当前连接推送 token 更可靠，因为页面刷新、反向代理重连和多客户端读取都不再依赖原始模型连接仍然存在。

## 4. 一体化 SSE 减少提交与订阅竞态

传统两步 API 是：

```text
POST message -> 得到 run_id
GET run/{id}/events/stream -> 开始订阅
```

这套方式仍然保留，适合独立控制。但 Web 客户端最常见的路径存在竞态：POST 成功后、SSE 建立前，Run 可能已经产生若干事件，客户端必须正确携带 after sequence 才能补齐。

`1236bc7` 增加：

```text
POST /api/v1/conversations/{id}/messages/stream
```

同一个响应依次返回：

```text
run_accepted
-> persisted run events
-> authoritative run_result
```

它并没有建立第二套执行协议，而是复用相同的 Run、Event Repository 和 SSE 生成逻辑。两步 API 与一体化 API 共享权威状态，客户端只是在不同交互场景选择不同入口。

## 5. SSE 终态不能靠连接关闭猜测

HTTP 流关闭可能意味着任务完成，也可能意味着代理超时、网络中断或服务重启。客户端不能把 EOF 当作成功。

React Client 的规则是：

- 收到 `run_accepted` 后记录 active Run；
- 按顺序应用持久化 Event；
- 收到 `run_result` 才以权威终态收口；
- 如果流在 terminal 前结束，视为连接错误；
- 页面恢复时查询 Conversation Runs，并重新订阅 active Run；
- terminal 后以 Run answer 或持久化消息校正本地增量文本。

这套状态机比“fetch 流结束就取消 loading”复杂，但它与后端的 Run 生命周期一致。

## 6. 前端展示的是任务阶段，不是日志列表

`32476c9` 强化流式执行进度后，React 客户端会把低层事件归并成用户可理解的阶段，例如：

```text
正在规划任务
正在执行数据分析
正在调用数据工具
正在独立核验
正在归并结果
正在流式生成回答
```

这不是把每条事件原样打印出来。产品层需要做语义投影：多个 `tool_started/tool_completed` 可能属于同一个“查询数据”阶段，子 Worker checkpoint 也不应该让页面频繁抖动。

后端 Event 仍保留完整诊断细节，前端则选择对用户有意义的稳定阶段。内部可观测性和用户体验共享事实源，但不共享展示粒度。

## 7. 会话持久化不仅是保存聊天气泡

客户端把 `conversation_id` 保存在本地，并从 API 恢复消息与 Runs。但真正的会话连续性发生在后端：

- 原始 user/assistant messages 持久化；
- context manager 在预算内保留近期消息；
- 较早历史生成持久化紧凑摘要；
- summary 带 `through_message_id` 游标；
- 后续 Run 复用摘要加游标后的原始消息；
- 纯历史追问和新业务查询走不同路径。

因此刷新页面后恢复的不只是 UI 列表，还包括可以继续执行的语义上下文。前端不需要重新把所有消息拼回 Prompt，也不拥有压缩策略。

## 8. 取消必须终止后台任务并持久化状态

一个“停止生成”按钮如果只关闭浏览器流，后台模型和 MCP 仍可能继续运行，既浪费资源，也可能产生用户以为已经取消的动作。

Nino Agent 的取消路径调用 Runtime：

```text
POST cancel
  -> 定位 active asyncio task
  -> task.cancel()
  -> Harness 捕获 CancelledError
  -> 持久化 loop terminal / run_cancelled
  -> Run 进入 cancelled
```

客户端中止 SSE 只负责连接生命周期，业务取消必须调用后端接口。两者不能混为一个 AbortController 操作。

对于未来写操作，取消语义还要进一步区分“尚未提交”“外部提交中”“已提交但结果未知”，当前只读场景避免了最难的副作用状态。

## 9. 代理配置也是 SSE 正确性的一部分

容器化时，React 使用多阶段镜像构建静态资源，由 Nginx 提供页面并反向代理 API。SSE 对代理配置有特殊要求：

- 关闭响应缓冲，否则 delta 会攒成一大块；
- 设置足够长的 read timeout；
- 禁止缓存事件流；
- 正确传递连接与转发头；
- 暴露 `Location` 和 `X-Run-ID` 等客户端需要的响应头。

如果本地直连正常、部署后总是一次性输出，问题通常不在模型，而在反向代理 buffering。产品化 Harness 必须把传输链路也纳入验收。

## 10. Compose 的价值是提供可复现边界

最终 Compose 组合 PostgreSQL、.NET MCP、Python Runtime 和 React/Nginx Web。这里的重点不只是“一条命令启动”，而是把运行边界显式化：

```text
Browser -> Nginx/Web -> Python Runtime -> MCP -> PostgreSQL
                              |
                           SQLite storage
```

模型配置、MCP 地址和存储路径通过环境注入；API Key 不进入 Skill、Dockerfile 或版本库；Web 使用独立 API base 配置；持久化目录通过 volume 保存。

Demo 模式还保留无外部模型、无真实 MCP 的确定性路径，适合验证 API、事件、恢复和 UI。Live 模式则用于真实 Tool Calling 与题库验收。两种模式共享产品协议。

## 11. 端到端验收应该检查什么

一个完整会话链路至少需要验证：

```text
1. message 被接受并返回 run_id
2. Run Event sequence 连续且可重放
3. 规划、节点、Tool 和 Gate 事件顺序合法
4. answer_delta 只来自最终 Orchestrator
5. delta 拼接等于 terminal Run.answer
6. Assistant message 已持久化
7. 页面刷新能恢复 Conversation 和 active Run
8. 取消后后台 Run 真正进入 cancelled
9. Nginx/Compose 下 SSE 不被缓冲
```

这也是为什么 `1236bc7` 同时增加 API 集成测试、React TypeScript 构建和 ESLint 验证，而不是只检查页面能否打开。UI、传输和 Runtime 共同构成产品行为。

## 12. 从执行内核到产品后端

回顾五章，Nino Agent 的演进顺序可以概括为：

```text
稳定分层
-> 受控 ReAct 与执行证据
-> TaskGraph 和 Planner 控制权
-> revision、恢复与多轮状态语义
-> 可重放 SSE 和会话产品
```

最后一步并不是给后端增加一个漂亮界面。它要求 Harness 内部每个关键状态都能被持久化、解释和投影。只有这样，客户端才能在不理解 Prompt、LangGraph 或 MCP 细节的情况下，可靠地提交任务、显示进度、恢复连接和确认终态。

一个真正可产品化的 Agent 后端，应该让模型的不确定性停留在受控决策范围内，而让任务身份、权限、证据、状态和传输尽可能确定。

## 13. 当前是企业 Harness 内核，不是完整企业平台

Nino 的运行所有权位于服务端：Conversation、Run、Event、Graph、Node、Gate 和 Attempt 由 Runtime 统一管理；业务 Tool 通过 MCP 服务访问；客户端只通过 API/SSE 观察和控制任务。用户关闭浏览器不会中断已持久化的 Graph Truth，页面恢复后可以继续读取 Run 状态和事件。

但从当前代码事实出发，仍应明确尚未完成的企业平台能力：

- Runtime 默认使用 SQLite 保存 Agent 状态，尚未提供面向多实例的共享生产 Repository 实现；
- Run 由进程内 `asyncio` Task 调度，没有外部消息队列和独立 Worker 集群；
- runtime heartbeat、Node lease 和 Graph CAS 已建立并发及恢复语义，但尚未完成多副本故障转移验收；
- API 当前没有用户认证、租户隔离、RBAC、审计主体和配额治理；
- MCP 熔断状态位于单个 Runtime 内存中，多实例间不会共享；
- approval 状态只是契约预留，写操作的审批、幂等和补偿尚未落地。

因此更准确的定位是：当前版本完成了企业 Agent Harness 的执行内核和服务化闭环，下一阶段才是把它扩展成可多租户、可横向扩展、可运维治理的企业平台。博客中的“产品后端”指已经具备稳定 API、持久化执行状态、流式交互和部署边界，不等同于生产平台建设已经结束。

---

## 代码考据

- `46535be`：Native Adapter 流式聚合与最终 `answer_delta`。
- `1236bc7`：React 会话客户端和一体化 message stream API。
- `175c506`：历史追问、React/Nginx 镜像与 Compose Web 服务。
- `32476c9`：更细的执行进度和运行镜像完善。
- `agent/python/src/api/app.py`：SSE、统一提交流和重放接口。
- `agent/python/src/runtime/service.py`：Run 事件、取消与 Conversation 上下文。
- `web/react/src/api/client.ts`：SSE 解析、提交和 active Run 重连。
- `web/react/src/App.tsx`：会话恢复、流式回答和阶段投影。
- `docker-compose.yml`、`web/react/nginx.conf`：容器与 SSE 代理边界。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
