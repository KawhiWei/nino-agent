# Nino Agent 本地存储

Python Agent Runtime 默认把本地 SQLite 状态保存到 `nino-agent.db`。

数据库包含 Conversation、Message、Run、可重放 Event、上下文摘要，以及持久化的
TaskGraph/TaskNode/TaskGate/NodeAttempt。SQLite 数据库、WAL、共享内存文件和带时间戳的
`*.db.backup-*` 备份会被 Git 忽略。只有在 Agent Runtime 停止后才能备份或删除这些文件。

已提交的 `live-benchmark*.json` 是历史基准产物，其中记录的 Tool 名称和 Agent ID 表示生成报告时的
Runtime 版本，不能为了伪装成新架构而重写。需要比较当前 Planner、通用 Analyst、通用 Verifier、
History Answer 或 Assurance repair 链路时，应使用当前 Runtime 重新生成一份带新输出文件名的报告，
并保留原历史报告。
