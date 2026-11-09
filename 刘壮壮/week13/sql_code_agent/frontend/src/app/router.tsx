import { createBrowserRouter } from 'react-router'
import { ChatWorkspace } from '../features/chat/ChatWorkspace'

// 历史会话启用后在此增加 /chat/:conversationId，不改变工作台组件边界。
export const router = createBrowserRouter([
  {
    path: '/',
    element: <ChatWorkspace />,
  },
])
