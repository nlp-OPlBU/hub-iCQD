# SQL Code Agent 前端

面向桌面端的 AI 数据问答工作台。前端通过 FastAPI 的 POST SSE 接口展示 Agent 阶段进度和逐块生成的最终答案，并将 SQL、完整结果和执行轨迹收纳到按需打开的详情抽屉。

## 环境与安装

- Node.js 20.19 或更高版本
- 已在 `127.0.0.1:8000` 启动 SQL Agent API

```bash
cd frontend
npm install
npm run dev
```

Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`，浏览器中不保存模型 API Key。

## 可用命令

```bash
npm run dev      # 开发服务器
npm run lint     # Oxlint
npm test         # Vitest 单元测试
npm run build    # TypeScript 检查和生产构建
npm run preview  # 预览 dist
```

## 流式事件

`POST /api/chat/stream` 返回 `text/event-stream`：

```text
meta → progress × N → answer_delta × N → result
                                      ↘ error
```

- `progress`：当前 Agent 阶段。
- `answer_delta`：最终自然语言答案的增量文本。
- `result`：完整答案、SQL、选表和查询结果。
- `error`：可安全展示的公开错误。

前端使用 reducer 管理页面内的多轮消息和每一轮流状态，使用 TanStack Query
管理健康检查。请求只携带最近 5 个成功轮次的问题、答案和 SQL，不发送完整
结果集；刷新页面或“新建对话”会清空上下文。当前不持久化历史，后续
`/chat/:conversationId` 的服务端历史需在用户鉴权和数据治理就绪后再启用。
