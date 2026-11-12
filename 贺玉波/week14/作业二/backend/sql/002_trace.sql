-- 002_trace.sql 全流程节点延迟

CREATE TABLE IF NOT EXISTS `trace_span` (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  trace_id   CHAR(32)     NOT NULL,
  span       VARCHAR(48)  NOT NULL COMMENT 'parse / chunk / retrieve_text / generate ...',
  parent     VARCHAR(48)  NULL,
  ref_id     VARCHAR(64)  NULL COMMENT 'doc_id 或 session_id',
  status     VARCHAR(12)  NOT NULL COMMENT 'ok | error',
  start_ts   DATETIME(3)  NOT NULL,
  dur_ms     INT          NOT NULL,
  from_cache TINYINT      NOT NULL DEFAULT 0,
  attrs      JSON         NULL COMMENT 'tokens / hits / queue_wait_ms 等',
  KEY idx_trace (trace_id),
  KEY idx_span_ts (span, start_ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='链路耗时明细';
