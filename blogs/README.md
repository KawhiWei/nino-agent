# Nino Agent Harness 工程实践系列

这组文章不按功能清单介绍 Nino Agent，而是沿 Git 提交回答一个问题：一个能调用模型和工具的 Agent，如何逐步变成可治理、可验证、可恢复的工程系统。

## 系列目录

1. [第一章：别急着写智能体，先把执行边界立起来](./01-from-agent-demo-to-harness-boundary.md)
   - 对应基线：`ee55b76`（2026-07-17，初始化项目）
   - 主题：为什么最早版本就拆分 Runtime、Harness、Framework 与 Infrastructure；Harness 到底负责什么。
2. [第二章：从 ReAct 循环到受控执行内核](./02-from-react-loop-to-controlled-kernel.md)
   - 对应提交：`9e67f80`、`be4427b`、`e7286f2`
   - 主题：Skill/Agent/MCP 权限交集、Loop Budget、停止原因、事件与评测如何把“不稳定推理”变成“可验证执行”。
3. [第三章：TaskGraph 不是模型画出来的一张图](./03-taskgraph-is-not-a-model-drawing.md)
   - 对应提交：`be4427b`、`facbc9e`、`8e2a47a`
   - 主题：Planner 独立、TaskGraph/Node/Gate 契约、调度与修订，以及为何 Tool Call 不应直接成为图节点。
4. [第四章：可靠性来自状态语义，而不是多加一个 Agent](./04-reliability-comes-from-state-semantics.md)
   - 对应提交：`8e2a47a`、`46535be`、`175c506`
   - 主题：节点结果校验、安全复用、追问上下文、幂等边界、失败与恢复语义。
5. [第五章：让 Harness 真正成为产品后端](./05-turning-the-harness-into-a-product-backend.md)
   - 对应提交：`1236bc7`、`175c506`、`32476c9`
   - 主题：REST/SSE、可重放事件、流式进度、React 会话客户端、容器化，以及端到端验收。
6. [第六章：企业 Harness 为什么不能属于某个 Agent 框架](./06-enterprise-harness-must-be-framework-neutral.md)
   - 对应代码：`framework/ports.py`、`bootstrap.py`、`react.py`、`langgraph.py`、`langchain_model.py`
   - 主题：Runtime 事实与框架解耦，LangChain/LangGraph 的准确位置，两类 TaskGraph 的区别，以及框架中立对企业治理的意义。

## 写作约定

- 文章中的“最早版本”指第一个完整项目提交 `ee55b76`，不是更早的占位提交 `4fd8492`。
- 每章先讲当时要解决的问题，再讲设计取舍和代码落点，最后说明它如何推动下一次演进。
- Git 提交和仓库代码是事实来源；文章不会把当前能力倒写成初始版本已有的能力。
- 示例会适度简化，但关键类型名、模块边界和调用方向与代码保持一致。
- 本系列讨论部署在服务端的 Nino Agent Harness，重点是 Graph State、Gate、fresh context、reconcile、持久化执行和服务接口。
- “企业级”在本文中指执行治理、状态、证据、恢复和服务接口的工程方向，不表示当前版本已经具备认证、租户、RBAC、外部任务队列和多实例生产部署等完整平台能力。
