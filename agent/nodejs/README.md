# Node.js 实现（预留）

该目录为未来的 Node.js Agent Runtime 预留。实现必须加载 `agent/shared` 下的契约和 Skill，不能导入
Python Runtime 源码。

目标分层为 `api/`、`runtime/`、`harness/`、`framework/` 和 `infrastructure/`。实现应从共享契约测试
开始，而不是逐行移植 Python 代码。
