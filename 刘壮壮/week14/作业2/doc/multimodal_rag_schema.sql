-- ============================================================
-- Multimodal RAG - SQLite Schema V1.0
-- 用途：全新数据库初始化
-- 注意：不是已有数据库的结构迁移脚本
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

BEGIN;

-- ============================================================
-- 1. documents：文档主表
-- ============================================================

CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_uid        TEXT NOT NULL UNIQUE,
    filename            TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    file_size           INTEGER NOT NULL CHECK (file_size > 0),
    file_hash           TEXT NOT NULL CHECK (length(file_hash) = 64),

    status              TEXT NOT NULL
                        CHECK (status IN (
                            'pending',
                            'processing',
                            'completed',
                            'failed',
                            'deleting'
                        )),

    current_version_id  INTEGER,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (id, current_version_id)
        REFERENCES document_versions(document_id, id)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 2. document_versions：文档解析版本表
-- ============================================================

CREATE TABLE IF NOT EXISTS document_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id         INTEGER NOT NULL,
    version_number      INTEGER NOT NULL CHECK (version_number >= 1),

    status              TEXT NOT NULL
                        CHECK (status IN (
                            'pending',
                            'processing',
                            'completed',
                            'failed',
                            'cancelled'
                        )),

    mineru_batch_id     TEXT,
    markdown_path       TEXT,
    json_path           TEXT,

    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,

    UNIQUE (document_id, version_number),
    UNIQUE (document_id, id),

    FOREIGN KEY (document_id)
        REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_versions_status
    ON document_versions(status);


-- ============================================================
-- 3. document_assets：解析产物资源表
-- ============================================================

CREATE TABLE IF NOT EXISTS document_assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_version_id INTEGER NOT NULL,

    asset_type          TEXT NOT NULL
                        CHECK (asset_type IN ('image')),

    filename            TEXT NOT NULL,
    file_path           TEXT NOT NULL,

    page_number         INTEGER
                        CHECK (page_number IS NULL OR page_number >= 1),

    mime_type           TEXT NOT NULL,
    file_size           INTEGER NOT NULL CHECK (file_size > 0),

    created_at          TEXT NOT NULL,

    FOREIGN KEY (document_version_id)
        REFERENCES document_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_assets_version
    ON document_assets(document_version_id);


-- ============================================================
-- 4. parse_tasks：PDF 解析任务表
-- ============================================================

CREATE TABLE IF NOT EXISTS parse_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_uid            TEXT NOT NULL UNIQUE,

    document_id         INTEGER NOT NULL,
    document_version_id INTEGER NOT NULL UNIQUE,

    status              TEXT NOT NULL
                        CHECK (status IN (
                            'pending',
                            'processing',
                            'completed',
                            'failed',
                            'cancelled'
                        )),

    retry_count         INTEGER NOT NULL DEFAULT 0
                        CHECK (retry_count >= 0),

    next_retry_at       TEXT,

    error_type          TEXT,
    last_error          TEXT,

    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (document_id, document_version_id)
        REFERENCES document_versions(document_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_parse_tasks_status_retry
    ON parse_tasks(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_parse_tasks_document
    ON parse_tasks(document_id);


-- ============================================================
-- 5. task_outbox：Kafka 消息 Outbox 表
-- ============================================================

CREATE TABLE IF NOT EXISTS task_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    event_uid       TEXT NOT NULL UNIQUE,
    task_id         INTEGER NOT NULL UNIQUE,

    event_type      TEXT NOT NULL,
    topic           TEXT NOT NULL,
    payload         TEXT NOT NULL CHECK (json_valid(payload)),

    status          TEXT NOT NULL
                    CHECK (status IN (
                        'pending',
                        'publishing',
                        'sent'
                    )),

    publish_count   INTEGER NOT NULL DEFAULT 0
                    CHECK (publish_count >= 0),

    next_retry_at   TEXT,
    locked_until    TEXT,
    last_error      TEXT,

    created_at      TEXT NOT NULL,
    sent_at         TEXT,
    updated_at      TEXT NOT NULL,

    CHECK (
        (status = 'publishing' AND locked_until IS NOT NULL)
        OR
        (status <> 'publishing' AND locked_until IS NULL)
    ),

    CHECK (
        (status = 'sent' AND sent_at IS NOT NULL)
        OR
        (status <> 'sent' AND sent_at IS NULL)
    ),

    FOREIGN KEY (task_id)
        REFERENCES parse_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_outbox_status_retry
    ON task_outbox(status, next_retry_at, locked_until);


-- ============================================================
-- 6. conversations：对话表
-- ============================================================

CREATE TABLE IF NOT EXISTS conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    conversation_uid    TEXT NOT NULL UNIQUE,

    title               TEXT,

    is_title_manual     INTEGER NOT NULL DEFAULT 0
                        CHECK (is_title_manual IN (0, 1)),

    is_pinned           INTEGER NOT NULL DEFAULT 0
                        CHECK (is_pinned IN (0, 1)),

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_pinned_updated
    ON conversations(is_pinned DESC, updated_at DESC);


-- ============================================================
-- 7. messages：消息表
-- ============================================================

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    message_uid     TEXT NOT NULL UNIQUE,

    conversation_id INTEGER NOT NULL,

    role            TEXT NOT NULL
                    CHECK (role IN ('user', 'assistant')),

    content         TEXT NOT NULL DEFAULT '',

    status          TEXT NOT NULL,

    error_message   TEXT,

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    CHECK (
        (role = 'user' AND status = 'completed')
        OR
        (
            role = 'assistant'
            AND status IN (
                'pending',
                'streaming',
                'completed',
                'failed',
                'interrupted'
            )
        )
    ),

    FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at, id);


-- ============================================================
-- 8. message_attachments：用户上传图片附件表
-- ============================================================

CREATE TABLE IF NOT EXISTS message_attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    attachment_uid  TEXT NOT NULL UNIQUE,

    message_id      INTEGER NOT NULL,

    filename        TEXT NOT NULL,
    file_path       TEXT NOT NULL,

    mime_type       TEXT NOT NULL
                    CHECK (mime_type IN (
                        'image/jpeg',
                        'image/png',
                        'image/webp'
                    )),

    file_size       INTEGER NOT NULL
                    CHECK (
                        file_size > 0
                        AND file_size <= 10485760
                    ),

    sort_order      INTEGER NOT NULL
                    CHECK (sort_order >= 0),

    created_at      TEXT NOT NULL,

    UNIQUE (message_id, sort_order),

    FOREIGN KEY (message_id)
        REFERENCES messages(id) ON DELETE CASCADE
);


-- ============================================================
-- 9. message_sources：回答引用来源快照表
-- ============================================================

CREATE TABLE IF NOT EXISTS message_sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    message_id          INTEGER NOT NULL,

    document_id         INTEGER,
    document_version_id INTEGER,

    chunk_id            TEXT NOT NULL,

    asset_id            INTEGER,

    document_name       TEXT NOT NULL,

    page_number         INTEGER
                        CHECK (page_number IS NULL OR page_number >= 1),

    content_snippet     TEXT NOT NULL,

    source_order        INTEGER NOT NULL
                        CHECK (source_order >= 0),

    created_at          TEXT NOT NULL,

    UNIQUE (message_id, source_order),

    FOREIGN KEY (message_id)
        REFERENCES messages(id) ON DELETE CASCADE,

    FOREIGN KEY (document_id)
        REFERENCES documents(id) ON DELETE SET NULL,

    FOREIGN KEY (document_version_id)
        REFERENCES document_versions(id) ON DELETE SET NULL,

    FOREIGN KEY (asset_id)
        REFERENCES document_assets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_message_sources_document
    ON message_sources(document_id);

CREATE INDEX IF NOT EXISTS idx_message_sources_version
    ON message_sources(document_version_id);

CREATE INDEX IF NOT EXISTS idx_message_sources_chunk
    ON message_sources(chunk_id);


-- ============================================================
-- 10. llm_call_records：模型调用记录表
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_call_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    call_uid        TEXT NOT NULL UNIQUE,

    operation_uid   TEXT NOT NULL,

    conversation_id INTEGER,
    message_id      INTEGER,

    operation_type  TEXT NOT NULL
                    CHECK (operation_type IN (
                        'query_rewrite',
                        'response_generation',
                        'title_generation'
                    )),

    provider        TEXT NOT NULL,
    model_name      TEXT NOT NULL,

    attempt_number  INTEGER NOT NULL
                    CHECK (attempt_number >= 1),

    status          TEXT NOT NULL
                    CHECK (status IN (
                        'running',
                        'success',
                        'failed',
                        'interrupted'
                    )),

    input_tokens    INTEGER
                    CHECK (input_tokens IS NULL OR input_tokens >= 0),

    output_tokens   INTEGER
                    CHECK (output_tokens IS NULL OR output_tokens >= 0),

    total_tokens    INTEGER
                    CHECK (total_tokens IS NULL OR total_tokens >= 0),

    latency_ms      INTEGER
                    CHECK (latency_ms IS NULL OR latency_ms >= 0),

    error_type      TEXT,
    error_message   TEXT,

    started_at      TEXT NOT NULL,
    completed_at    TEXT,

    created_at      TEXT NOT NULL,

    UNIQUE (operation_uid, attempt_number),

    CHECK (
        (status = 'running' AND completed_at IS NULL)
        OR
        (status <> 'running' AND completed_at IS NOT NULL)
    ),

    FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE SET NULL,

    FOREIGN KEY (message_id)
        REFERENCES messages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_records_created
    ON llm_call_records(created_at);

CREATE INDEX IF NOT EXISTS idx_llm_records_model_status
    ON llm_call_records(model_name, status);

CREATE INDEX IF NOT EXISTS idx_llm_records_operation_status
    ON llm_call_records(operation_type, status);

CREATE INDEX IF NOT EXISTS idx_llm_records_conversation
    ON llm_call_records(conversation_id);

CREATE INDEX IF NOT EXISTS idx_llm_records_message
    ON llm_call_records(message_id);


-- ============================================================
-- 11. user_settings：用户偏好设置表
-- ============================================================

CREATE TABLE IF NOT EXISTS user_settings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    setting_key     TEXT NOT NULL UNIQUE,

    setting_value   TEXT NOT NULL
                    CHECK (json_valid(setting_value)),

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);


-- ============================================================
-- 12. operation_logs：系统操作日志表
-- ============================================================

CREATE TABLE IF NOT EXISTS operation_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    log_uid         TEXT NOT NULL UNIQUE,

    level           TEXT NOT NULL
                    CHECK (level IN (
                        'INFO',
                        'WARNING',
                        'ERROR'
                    )),

    category        TEXT NOT NULL
                    CHECK (category IN (
                        'document',
                        'task',
                        'conversation',
                        'system',
                        'dependency',
                        'api',
                        'file',
                        'database'
                    )),

    action          TEXT NOT NULL,

    resource_type   TEXT,
    resource_id     TEXT,

    status          TEXT
                    CHECK (
                        status IS NULL
                        OR status IN (
                            'success',
                            'failed',
                            'running',
                            'warning'
                        )
                    ),

    message         TEXT NOT NULL,

    details         TEXT
                    CHECK (
                        details IS NULL
                        OR json_valid(details)
                    ),

    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operation_logs_created
    ON operation_logs(created_at);

CREATE INDEX IF NOT EXISTS idx_operation_logs_category_created
    ON operation_logs(category, created_at);

CREATE INDEX IF NOT EXISTS idx_operation_logs_level_created
    ON operation_logs(level, created_at);


-- ============================================================
-- 初始化完成
-- ============================================================

COMMIT;


-- ============================================================
-- 数据库检查
-- ============================================================

PRAGMA foreign_key_check;

PRAGMA integrity_check;