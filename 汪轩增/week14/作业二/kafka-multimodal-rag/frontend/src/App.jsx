import { Layout, Tabs, Tag } from 'antd'
import { FileTextOutlined, MessageOutlined } from '@ant-design/icons'
import DocumentManager from './pages/DocumentManager'
import ChatPanel from './pages/ChatPanel'

const { Header, Content } = Layout

export default function App() {
  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <div className="brand">
          <div className="brand-logo">M</div>
          <div>
            <div className="brand-title">多模态 RAG 知识库</div>
            <div className="brand-sub">KAFKA · MINERU · MILVUS · QWEN-VL</div>
          </div>
        </div>
        <div className="header-tags">
          <Tag color="geekblue">前后端分离</Tag>
          <Tag color="purple">图文混排</Tag>
          <Tag color="cyan">图问图答</Tag>
        </div>
      </Header>

      <Content className="app-content">
        <Tabs
          defaultActiveKey="documents"
          size="large"
          items={[
            {
              key: 'documents',
              label: (
                <span>
                  <FileTextOutlined /> 文档管理
                </span>
              ),
              children: <DocumentManager />,
            },
            {
              key: 'chat',
              label: (
                <span>
                  <MessageOutlined /> 图文问答
                </span>
              ),
              children: <ChatPanel />,
            },
          ]}
        />
      </Content>
    </Layout>
  )
}
