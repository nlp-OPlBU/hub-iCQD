import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Collapse,
  Empty,
  Image as AntImage,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  ClearOutlined,
  CloseOutlined,
  PictureOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { apiError, listDocuments, sendChat } from '../api'

const { Text } = Typography

const MAX_IMAGES = 4

const SAMPLE_QUESTIONS = [
  '总结一下文档的主要内容',
  '文档中的图表说明了什么？',
  '把与问题相关的图片展示出来并解释',
]

export default function ChatPanel() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState([])
  const [sending, setSending] = useState(false)
  const [docOptions, setDocOptions] = useState([])
  const [selectedDocs, setSelectedDocs] = useState([])
  const bottomRef = useRef(null)

  // 只允许选择“解析完成”的文档作为检索范围
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const docs = await listDocuments('completed')
        if (!alive) return
        setDocOptions(
          docs.map((doc) => ({ value: doc.id, label: doc.original_name })),
        )
      } catch {
        // 静默失败，问答时后端会给出明确错误
      }
    }
    load()
    const timer = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  const handlePickImage = (file) => {
    if (!file.type?.startsWith('image/')) {
      message.warning('请选择图片文件')
      return Upload.LIST_IGNORE
    }
    if (attachments.length >= MAX_IMAGES) {
      message.warning(`最多同时上传 ${MAX_IMAGES} 张图片`)
      return Upload.LIST_IGNORE
    }
    const preview = URL.createObjectURL(file)
    setAttachments((prev) => [
      ...prev,
      { file, name: file.name, preview },
    ])
    return false
  }

  const handleRemoveAttachment = (index) => {
    setAttachments((prev) => {
      const target = prev[index]
      if (target) URL.revokeObjectURL(target.preview)
      return prev.filter((_, i) => i !== index)
    })
  }

  const handleSend = async () => {
    const question = input.trim()
    if (!question) {
      message.warning('请输入问题')
      return
    }
    if (sending) return

    const files = attachments.map((item) => item.file)
    const previews = attachments.map((item) => ({
      url: item.preview,
      name: item.name,
    }))

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: question, images: previews },
    ])
    setInput('')
    setAttachments([])
    setSending(true)

    try {
      const result = await sendChat({
        question,
        files,
        docIds: selectedDocs,
      })
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: result.answer,
          sources: result.sources,
          images: result.images,
        },
      ])
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', error: apiError(error) },
      ])
    } finally {
      setSending(false)
    }
  }

  const clearChat = () => {
    attachments.forEach((item) => URL.revokeObjectURL(item.preview))
    setAttachments([])
    setMessages([])
    setInput('')
  }

  return (
    <Card className="panel-card chat-card" bordered={false}>
      <div className="chat-toolbar">
        <div>
          <Text strong style={{ fontSize: 15 }}>图文问答</Text>
          <Text type="secondary" style={{ marginLeft: 10, fontSize: 12 }}>
            问题与图片会一起送入多模态大模型，答案支持图文混排
          </Text>
        </div>
        <Space wrap>
          <Select
            mode="multiple"
            allowClear
            placeholder="检索范围：全部已完成文档"
            options={docOptions}
            value={selectedDocs}
            onChange={setSelectedDocs}
            optionFilterProp="label"
            maxTagCount="responsive"
            style={{ minWidth: 260, maxWidth: 380 }}
          />
          <Button
            icon={<ClearOutlined />}
            onClick={clearChat}
            disabled={!messages.length && !attachments.length}
          >
            清空对话
          </Button>
        </Space>
      </div>

      <div className="chat-window">
        {messages.length === 0 && !sending ? (
          <div className="chat-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="上传 PDF 并等待解析完成后，就可以基于文档进行图文问答了"
            />
            <div className="sample-questions">
              {SAMPLE_QUESTIONS.map((question) => (
                <Button key={question} size="small" onClick={() => setInput(question)}>
                  {question}
                </Button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, index) => (
              <MessageRow key={index} message={msg} />
            ))}
            {sending && (
              <div className="message-row assistant">
                <Avatar className="assistant-avatar" icon={<RobotOutlined />} />
                <div className="bubble assistant typing">
                  <Spin size="small" /> 正在检索资料并生成答案…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      <div className="chat-composer">
        {attachments.length > 0 && (
          <div className="attachment-strip">
            {attachments.map((item, index) => (
              <div className="attachment" key={`${item.name}-${index}`}>
                <img src={item.preview} alt={item.name} />
                <button
                  type="button"
                  className="remove"
                  aria-label="移除图片"
                  onClick={() => handleRemoveAttachment(index)}
                >
                  <CloseOutlined />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="composer-row">
          <Upload
            accept="image/*"
            multiple
            showUploadList={false}
            beforeUpload={handlePickImage}
          >
            <Tooltip title="上传图片一起提问">
              <Button icon={<PictureOutlined />} shape="circle" type="text" />
            </Tooltip>
          </Upload>
          <Input.TextArea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="输入问题，例如：文档中的图表说明了什么？按 Enter 发送，Shift+Enter 换行"
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault()
                handleSend()
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={sending}
            disabled={!input.trim()}
            onClick={handleSend}
          >
            发送
          </Button>
        </div>
      </div>
    </Card>
  )
}

function MessageRow({ message }) {
  if (message.role === 'user') {
    return (
      <div className="message-row user">
        <Avatar className="user-avatar" icon={<UserOutlined />} />
        <div className="bubble user">
          <div className="user-text">{message.content}</div>
          {message.images?.length > 0 && (
            <div className="message-images">
              {message.images.map((image, index) => (
                <img key={index} src={image.url} alt={image.name} />
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="message-row assistant">
      <Avatar className="assistant-avatar" icon={<RobotOutlined />} />
      <div className="bubble assistant">
        {message.error ? (
          <Alert
            type="error"
            showIcon
            message="回答失败"
            description={message.error}
          />
        ) : (
          <>
            <MarkdownAnswer content={message.content || ''} />
            {message.sources?.length > 0 && <SourceList sources={message.sources} />}
          </>
        )}
      </div>
    </div>
  )
}

function MarkdownAnswer({ content }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ node, ...props }) => <AntImage {...props} className="answer-image" />,
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node, ...props }) => (
            <div className="table-wrap">
              <table {...props} />
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function SourceList({ sources }) {
  return (
    <Collapse
      ghost
      size="small"
      className="source-collapse"
      items={[
        {
          key: 'sources',
          label: <Text type="secondary">参考资料（{sources.length}）</Text>,
          children: (
            <div className="source-list">
              {sources.map((source, index) => (
                <div className="source-item" key={`${source.doc_id}-${index}`}>
                  <div className="source-head">
                    <Tag
                      color="blue"
                      style={{
                        maxWidth: 320,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {source.file_name}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      相关度 {(source.score * 100).toFixed(0)}%
                    </Text>
                  </div>
                  <Text type="secondary" className="source-snippet">
                    {source.snippet}
                  </Text>
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  )
}
