-- 003_chat.sql 会话与消息
-- MySQL 是会话数据的真源；Redis 只缓存最近若干轮，缓存丢了可以从这里回填。

CREATE TABLE IF NOT EXISTS `chat_session` (
  id            CHAR(32)     NOT NULL COMMENT 'session_id，uuid4 hex',
  title         VARCHAR(64)  NULL COMMENT '首个问题摘要，仅用于列表展示',
  summary       TEXT         NULL COMMENT '滚动摘要：把早期对话压缩成一段话',
  summary_upto  INT          NOT NULL DEFAULT 0 COMMENT '已摘要到的消息 seq 上界，避免重复摘要',
  message_count INT          NOT NULL DEFAULT 0 COMMENT '消息条数，同时作为分配 seq 的依据',
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='问答会话';

CREATE TABLE IF NOT EXISTS `chat_message` (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id CHAR(32)     NOT NULL,
  seq        INT          NOT NULL COMMENT '会话内单调递增，用于摘要游标和稳定排序',
  role       VARCHAR(12)  NOT NULL COMMENT 'user | assistant',
  content    MEDIUMTEXT   NOT NULL,
  citations  JSON         NULL COMMENT '本轮检索到的引用快照，回放历史时渲染来源与图片',
  tokens     INT          NOT NULL DEFAULT 0 COMMENT '估算 token 数，用于上下文裁剪',
  created_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_session_seq (session_id, seq),
  KEY idx_session (session_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话消息';
