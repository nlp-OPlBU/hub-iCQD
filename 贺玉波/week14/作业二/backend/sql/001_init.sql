-- 001_init.sql 文档与分块
-- 状态机真源在 MySQL；Redis 只做投影

CREATE TABLE IF NOT EXISTS `document` (
  id            CHAR(32)      NOT NULL COMMENT 'doc_id，uuid4 hex',
  filename      VARCHAR(255)  NOT NULL,
  sha256        CHAR(64)      NOT NULL,
  size_bytes    BIGINT        NOT NULL,
  page_count    INT           NULL,
  status        VARCHAR(16)   NOT NULL DEFAULT 'pending',
  stage         VARCHAR(24)   NULL,
  progress      TINYINT       NOT NULL DEFAULT 0,
  attempts      INT           NOT NULL DEFAULT 0,
  chunk_count   INT           NOT NULL DEFAULT 0,
  image_count   INT           NOT NULL DEFAULT 0,
  md_path       VARCHAR(512)  NULL,
  error_code    VARCHAR(48)   NULL,
  error_message TEXT          NULL,
  created_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_sha256 (sha256),
  KEY idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='PDF 文档与解析状态';

CREATE TABLE IF NOT EXISTS `chunk` (
  id         CHAR(40)      NOT NULL COMMENT 'sha1(doc_id:idx)，保证重放幂等',
  doc_id     CHAR(32)      NOT NULL,
  idx        INT           NOT NULL,
  kind       VARCHAR(16)   NOT NULL COMMENT 'heading | paragraph | table | figure',
  page_no    INT           NULL,
  text       MEDIUMTEXT    NOT NULL,
  image_path VARCHAR(512)  NULL,
  created_at DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_doc_idx (doc_id, idx),
  KEY idx_doc (doc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='解析后的文本/图块';

-- 表结构由 SQL 唯一真源维护，ORM 只做映射；这里 -1 仅用于显式声明分块无外键约束（级联删除在应用层做）
