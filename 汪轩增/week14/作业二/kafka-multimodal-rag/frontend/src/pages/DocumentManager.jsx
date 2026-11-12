import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Empty,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FilePdfOutlined,
  InboxOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import {
  apiError,
  deleteDocument,
  listDocuments,
  retryDocument,
  uploadDocuments,
} from '../api'
import { formatSize, formatTime } from '../utils'

const { Dragger } = Upload
const { Text } = Typography

const STATUS_META = {
  pending: { label: '待解析', color: 'default', icon: <ClockCircleOutlined /> },
  parsing: { label: '解析中', color: 'processing', icon: <SyncOutlined spin /> },
  completed: { label: '解析完成', color: 'success', icon: <CheckCircleOutlined /> },
  failed: { label: '解析失败', color: 'error', icon: <CloseCircleOutlined /> },
}

export default function DocumentManager() {
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [fileList, setFileList] = useState([])
  const [uploading, setUploading] = useState(false)
  const errorShownRef = useRef(false)

  const refresh = useCallback(async (showError = false) => {
    try {
      const data = await listDocuments()
      setDocuments(data)
      errorShownRef.current = false
    } catch (error) {
      if (showError && !errorShownRef.current) {
        message.error(apiError(error))
        errorShownRef.current = true
      }
    } finally {
      setLoading(false)
    }
  }, [])

  // 轮询解析状态：待解析 -> 解析中 -> 解析完成 / 解析失败
  useEffect(() => {
    refresh(true)
    const timer = setInterval(() => refresh(false), 2500)
    return () => clearInterval(timer)
  }, [refresh])

  const stats = useMemo(() => {
    const result = {
      pending: 0,
      parsing: 0,
      completed: 0,
      failed: 0,
    }
    documents.forEach((doc) => {
      if (result[doc.status] !== undefined) result[doc.status] += 1
    })
    return result
  }, [documents])

  const handleUpload = async () => {
    const files = fileList.map((item) => item.originFileObj).filter(Boolean)
    if (!files.length) {
      message.warning('请先选择 PDF 文件')
      return
    }
    setUploading(true)
    try {
      const result = await uploadDocuments(files)
      const failed = result.documents.filter((doc) => doc.status === 'failed')
      if (failed.length) {
        message.warning(`${failed.length} 个文件提交 Kafka 失败，可稍后点击“重试”`)
      } else {
        message.success(
          `已提交 ${result.documents.length} 个文件，正在排队解析（状态会自动刷新）`,
        )
      }
      setFileList([])
      refresh()
    } catch (error) {
      message.error(apiError(error))
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id) => {
    try {
      await deleteDocument(id)
      message.success('已删除文档及其解析结果')
      refresh()
    } catch (error) {
      message.error(apiError(error))
    }
  }

  const handleRetry = async (id) => {
    try {
      const result = await retryDocument(id)
      if (result.document?.status === 'failed') {
        message.error(result.message || '重试失败')
      } else {
        message.success(result.message || '重试任务已提交')
      }
      refresh()
    } catch (error) {
      message.error(apiError(error))
    }
  }

  const columns = [
    {
      title: '文件',
      dataIndex: 'original_name',
      render: (_, record) => (
        <Space size={10} align="start">
          <FilePdfOutlined style={{ color: '#e5484d', fontSize: 18, marginTop: 2 }} />
          <div>
            <Text
              style={{ display: 'block', maxWidth: 360 }}
              ellipsis={{ tooltip: record.original_name }}
            >
              {record.original_name}
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {formatSize(record.file_size)}
              {record.chunk_count > 0 ? ` · ${record.chunk_count} 个切片` : ''}
            </Text>
          </div>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 130,
      render: (status, record) => {
        const meta = STATUS_META[status] || STATUS_META.pending
        const tag = (
          <Tag color={meta.color} icon={meta.icon} style={{ marginInlineEnd: 0 }}>
            {meta.label}
          </Tag>
        )
        return record.error_message ? (
          <Tooltip title={record.error_message}>{tag}</Tooltip>
        ) : (
          tag
        )
      },
    },
    {
      title: '上传时间',
      dataIndex: 'created_at',
      width: 190,
      render: (value) => <Text type="secondary">{formatTime(value)}</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_, record) => (
        <Space size={4}>
          {record.status === 'failed' && (
            <Tooltip title="重新投递解析任务">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => handleRetry(record.id)}
              >
                重试
              </Button>
            </Tooltip>
          )}
          <Popconfirm
            title="删除该文档？"
            description="解析结果和向量数据会一并删除"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Row gutter={[12, 12]} className="stat-row">
        <Col flex="1 1 150px">
          <Card className="stat-card" bordered={false}>
            <Statistic
              title="文档总数"
              value={documents.length}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col flex="1 1 150px">
          <Card className="stat-card" bordered={false}>
            <Statistic
              title="待解析"
              value={stats.pending}
              valueStyle={{ color: '#8a90a2' }}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col flex="1 1 150px">
          <Card className="stat-card" bordered={false}>
            <Statistic
              title="解析中"
              value={stats.parsing}
              valueStyle={{ color: '#5b5bd6' }}
              prefix={<SyncOutlined spin={stats.parsing > 0} />}
            />
          </Card>
        </Col>
        <Col flex="1 1 150px">
          <Card className="stat-card" bordered={false}>
            <Statistic
              title="解析完成"
              value={stats.completed}
              valueStyle={{ color: '#16a34a' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col flex="1 1 150px">
          <Card className="stat-card" bordered={false}>
            <Statistic
              title="解析失败"
              value={stats.failed}
              valueStyle={{ color: '#dc2626' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card
        className="panel-card upload-card"
        bordered={false}
        title="上传 PDF（可多选）"
        extra={<Text type="secondary" style={{ fontSize: 12 }}>解析状态每 2.5 秒自动刷新</Text>}
      >
        <Dragger
          multiple
          accept=".pdf,application/pdf"
          fileList={fileList}
          maxCount={20}
          beforeUpload={() => false}
          onChange={({ fileList: nextList }) => setFileList(nextList)}
          onRemove={(file) => {
            setFileList((prev) => prev.filter((item) => item.uid !== file.uid))
          }}
          className="upload-dragger"
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽多个 PDF 到这里</p>
          <p className="ant-upload-hint">
            文件先落盘并投递 Kafka，由后台 worker 调用 MinerU 异步解析，不阻塞上传请求
          </p>
        </Dragger>

        <div className="upload-actions">
          <Button
            type="primary"
            icon={<InboxOutlined />}
            loading={uploading}
            disabled={!fileList.length}
            onClick={handleUpload}
          >
            开始上传{fileList.length ? `（${fileList.length}）` : ''}
          </Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            仅支持 PDF
          </Text>
        </div>
      </Card>

      <Card className="panel-card" bordered={false} title="文档列表">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={documents}
          loading={loading && documents.length === 0}
          pagination={false}
          scroll={{ x: 760 }}
          locale={{
            emptyText: <Empty description="还没有文档，先上传 PDF 吧" />,
          }}
        />
      </Card>
    </div>
  )
}
