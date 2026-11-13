"""Initial schema, MySQL 8.0.16+. Frozen: do not import live ORM models."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('next_sequence', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations')),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_conversations_created_id', 'conversations', ['created_at', 'id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('latest_version', sa.Integer(), nullable=False),
    sa.Column('current_searchable_version', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint('current_searchable_version IS NULL OR (current_searchable_version >= 1 AND current_searchable_version <= latest_version)', name=op.f('ck_documents_searchable_version')),
    sa.CheckConstraint('latest_version >= 1', name=op.f('ck_documents_positive_version')),
    sa.CheckConstraint('size_bytes > 0', name=op.f('ck_documents_positive_size')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_documents_storage_key')),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_documents_created_id', 'documents', ['created_at', 'id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('request_key', sa.String(length=64), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql'), nullable=False),
    sa.Column('image_ids', sa.JSON(), nullable=False),
    sa.Column('document_scope', sa.JSON(), nullable=True),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=255), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint("role IN ('user','assistant')", name=op.f('ck_messages_role')),
    sa.CheckConstraint("status IN ('pending','completed','failed')", name=op.f('ck_messages_status')),
    sa.CheckConstraint('sequence >= 1', name=op.f('ck_messages_positive_sequence')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    sa.UniqueConstraint('conversation_id', 'request_key', 'role', name='uq_message_request_role'),
    sa.UniqueConstraint('conversation_id', 'sequence', name='uq_message_sequence'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_table('parse_tasks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('retry_key', sa.String(length=64), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('error_details', sa.JSON(), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('lease_owner', sa.String(length=128), nullable=True),
    sa.Column('lease_token', sa.String(length=36), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('started_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('finished_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('artifact_key', sa.String(length=512), nullable=True),
    sa.Column('index_name', sa.String(length=255), nullable=True),
    sa.Column('indexed_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('retrieval_mode', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint("status IN ('pending','processing','completed','failed')", name=op.f('ck_parse_tasks_status')),
    sa.CheckConstraint('version >= 1 AND attempts >= 0 AND max_retries >= 0', name=op.f('ck_parse_tasks_counters')),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_parse_tasks_document_id_documents')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parse_tasks')),
    sa.UniqueConstraint('document_id', 'retry_key', name='uq_task_retry_key'),
    sa.UniqueConstraint('document_id', 'version', name='uq_task_document_version'),
    sa.UniqueConstraint('id', 'document_id', 'version', name='uq_task_identity'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_tasks_lease', 'parse_tasks', ['status', 'lease_expires_at'], unique=False)
    op.create_index('ix_tasks_status_next', 'parse_tasks', ['status', 'next_attempt_at'], unique=False)
    op.create_table('image_resources',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('conversation_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint("(kind = 'user_upload' AND conversation_id IS NOT NULL AND document_id IS NULL AND version IS NULL AND page IS NULL) OR (kind = 'document_image' AND document_id IS NOT NULL AND version IS NOT NULL AND page >= 1 AND page IS NOT NULL AND conversation_id IS NULL)", name=op.f('ck_image_resources_ownership')),
    sa.CheckConstraint("kind IN ('user_upload','document_image')", name=op.f('ck_image_resources_kind')),
    sa.CheckConstraint('size_bytes > 0 AND width > 0 AND height > 0', name=op.f('ck_image_resources_dimensions')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_image_resources_conversation_id_conversations')),
    sa.ForeignKeyConstraint(['document_id', 'version'], ['parse_tasks.document_id', 'parse_tasks.version'], name='fk_image_task'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_image_resources')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_image_resources_storage_key')),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_table('outbox_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('topic', sa.String(length=255), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_retries', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('lease_owner', sa.String(length=128), nullable=True),
    sa.Column('lease_token', sa.String(length=36), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('published_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
    sa.CheckConstraint("status IN ('pending','publishing','published','failed')", name=op.f('ck_outbox_events_status')),
    sa.CheckConstraint('attempts >= 0 AND max_retries >= 0 AND version >= 1', name=op.f('ck_outbox_events_counters')),
    sa.ForeignKeyConstraint(['task_id', 'document_id', 'version'], ['parse_tasks.id', 'parse_tasks.document_id', 'parse_tasks.version'], name='fk_outbox_task_identity'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox_events')),
    sa.UniqueConstraint('task_id', 'event_type', name='uq_outbox_task_event'),
    mysql_charset='utf8mb4',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_outbox_dispatch', 'outbox_events', ['status', 'available_at'], unique=False)
    op.create_index('ix_outbox_lease', 'outbox_events', ['status', 'lease_expires_at'], unique=False)
    # Add the circular reference only after both tables exist.
    op.create_foreign_key('fk_document_searchable_task', 'documents', 'parse_tasks',
                          ['id', 'current_searchable_version'], ['document_id', 'version'])


def downgrade():
    op.drop_constraint('fk_document_searchable_task', 'documents', type_='foreignkey')
    op.drop_index('ix_outbox_lease', table_name='outbox_events')
    op.drop_index('ix_outbox_dispatch', table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_table('image_resources')
    op.drop_index('ix_tasks_status_next', table_name='parse_tasks')
    op.drop_index('ix_tasks_lease', table_name='parse_tasks')
    op.drop_table('parse_tasks')
    op.drop_table('messages')
    op.drop_index('ix_documents_created_id', table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_conversations_created_id', table_name='conversations')
    op.drop_table('conversations')
