# Nino Agent Web 前端（React）

基于 React、TypeScript、Vite 和 Semi Design AI Chat 组件的轻量会话客户端。

客户端通过 `POST /api/v1/conversations/{id}/messages/stream` 在一次请求中提交消息并消费完整
Run 事件流。页面刷新或网络连接中断后，会查询当前会话的 active Run，并通过
`GET /api/v1/runs/{id}/events/stream` 重放事件、继续接收结果，不会重复提交用户消息。

运行期间，页面会把 SSE 中的规划、数据查询、子 Agent、独立验证、修正和终态事件展示为
实时执行进度。最终回答通过 `answer_delta` 增量渲染；`run_result` 只负责确认最终完整答案与
`completed/failed/cancelled` 状态，不需要等待任务结束后再一次性显示文本。

## 本地运行

先启动 Nino Python Runtime（默认 `http://127.0.0.1:8090`），再启动前端：

```bash
cd web/react
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。开发服务器会把 `/api` 和 `/health` 代理到
`NINO_API_TARGET`，默认值为 `http://127.0.0.1:8090`。

## 配置

复制 `.env.example` 的配置项到 `.env.local` 后按需修改：

- `NINO_API_TARGET`：Vite 开发代理的后端地址。
- `VITE_NINO_API_BASE_URL`：浏览器直接请求的 API 地址；留空时使用同源代理。

生产部署时，推荐由反向代理把 `/api` 转发给 Nino Runtime；也可以在构建时设置
`VITE_NINO_API_BASE_URL`。后端需要通过 `NINO_CORS_ORIGINS` 放行对应前端域名。

## Docker Compose 部署

在项目根目录导出模型 API Key 后，重新构建并启动全部服务：

```bash
export OPENAI_API_KEY='<your-key>'
docker compose up -d --build
```

打开 `http://localhost:3000`。前端由 Nginx 提供静态文件，浏览器对同源 `/api` 和
`/health` 的请求由 Nginx 反向代理到 `agent-runtime:8090`。可通过 `NINO_WEB_PORT`
修改宿主机端口。
