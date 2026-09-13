"""initial_schema

Revision ID: 72c8b4e62692
Revises: 
Create Date: 2026-07-22 16:46:22.221039

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '72c8b4e62692'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic 根据受版本控制的 ORM 模型生成的初始 schema。
    op.create_table('roles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=32), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('tools',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tools', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tools_slug'), ['slug'], unique=True)

    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('failed_login_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('admin_audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('admin_user_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target_type', sa.String(length=64), nullable=False),
    sa.Column('target_id', sa.String(length=128), nullable=True),
    sa.Column('detail_json', sa.Text(), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('admin_audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_admin_audit_events_admin_user_id'), ['admin_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_admin_audit_events_request_id'), ['request_id'], unique=False)

    op.create_table('api_endpoints',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tool_id', sa.String(length=36), nullable=False),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('upstream_path', sa.String(length=512), nullable=False),
    sa.Column('operation_id', sa.String(length=128), nullable=True),
    sa.Column('summary', sa.String(length=255), nullable=False),
    sa.Column('content_types', sa.Text(), nullable=False),
    sa.Column('is_candidate', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tool_id', 'method', 'upstream_path', name='uq_endpoint_operation')
    )
    with op.batch_alter_table('api_endpoints', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_endpoints_tool_id'), ['tool_id'], unique=False)

    op.create_table('api_keys',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('prefix', sa.String(length=24), nullable=False),
    sa.Column('secret_hash', sa.String(length=128), nullable=False),
    sa.Column('scopes', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('secret_hash')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_prefix'), ['prefix'], unique=False)
        batch_op.create_index(batch_op.f('ix_api_keys_user_id'), ['user_id'], unique=False)

    op.create_table('external_resources',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tool_id', sa.String(length=36), nullable=False),
    sa.Column('owner_user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('upstream_id', sa.String(length=128), nullable=False),
    sa.Column('parent_upstream_id', sa.String(length=128), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('metadata_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tool_id', 'kind', 'upstream_id', name='uq_external_resource')
    )
    with op.batch_alter_table('external_resources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_external_resources_owner_user_id'), ['owner_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_external_resources_tool_id'), ['tool_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_external_resources_upstream_id'), ['upstream_id'], unique=False)

    op.create_table('openapi_specs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tool_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('document_json', sa.Text(), nullable=False),
    sa.Column('imported_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['imported_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('openapi_specs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_openapi_specs_imported_by_user_id'), ['imported_by_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_openapi_specs_tool_id'), ['tool_id'], unique=False)

    op.create_table('tool_upstreams',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tool_id', sa.String(length=36), nullable=False),
    sa.Column('base_url', sa.String(length=512), nullable=False),
    sa.Column('token_ciphertext', sa.Text(), nullable=False),
    sa.Column('rate_limit_per_minute', sa.Integer(), nullable=False),
    sa.Column('connect_timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('request_timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tool_upstreams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tool_upstreams_tool_id'), ['tool_id'], unique=True)

    op.create_table('user_import_batches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('admin_user_id', sa.String(length=36), nullable=False),
    sa.Column('source_sha256', sa.String(length=64), nullable=False),
    sa.Column('total_rows', sa.Integer(), nullable=False),
    sa.Column('accepted_rows', sa.Integer(), nullable=False),
    sa.Column('rejected_rows', sa.Integer(), nullable=False),
    sa.Column('errors_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_import_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_import_batches_admin_user_id'), ['admin_user_id'], unique=False)

    op.create_table('user_roles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'role_id', name='uq_user_role')
    )
    with op.batch_alter_table('user_roles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_roles_role_id'), ['role_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_roles_user_id'), ['user_id'], unique=False)

    op.create_table('gateway_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('api_key_id', sa.String(length=36), nullable=True),
    sa.Column('tool_id', sa.String(length=36), nullable=True),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('path', sa.String(length=512), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('gateway_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_gateway_requests_user_id'), ['user_id'], unique=False)

    op.create_table('published_routes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tool_id', sa.String(length=36), nullable=False),
    sa.Column('endpoint_id', sa.String(length=36), nullable=False),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('gateway_path', sa.String(length=512), nullable=False),
    sa.Column('upstream_path', sa.String(length=512), nullable=False),
    sa.Column('scopes', sa.Text(), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['endpoint_id'], ['api_endpoints.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tool_id'], ['tools.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('endpoint_id'),
    sa.UniqueConstraint('tool_id', 'method', 'gateway_path', name='uq_published_gateway_path')
    )
    with op.batch_alter_table('published_routes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_published_routes_tool_id'), ['tool_id'], unique=False)

    op.create_table('rate_limit_buckets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('api_key_id', sa.String(length=36), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('request_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('api_key_id', 'window_start', name='uq_rate_limit_window')
    )
    with op.batch_alter_table('rate_limit_buckets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rate_limit_buckets_api_key_id'), ['api_key_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rate_limit_buckets_window_start'), ['window_start'], unique=False)

    # 初始 schema 创建结束。


def downgrade() -> None:
    # 仅供本地开发回滚；生产回滚前必须完成数据备份和影响评估。
    with op.batch_alter_table('rate_limit_buckets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rate_limit_buckets_window_start'))
        batch_op.drop_index(batch_op.f('ix_rate_limit_buckets_api_key_id'))

    op.drop_table('rate_limit_buckets')
    with op.batch_alter_table('published_routes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_published_routes_tool_id'))

    op.drop_table('published_routes')
    with op.batch_alter_table('gateway_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gateway_requests_user_id'))

    op.drop_table('gateway_requests')
    with op.batch_alter_table('user_roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_roles_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_roles_role_id'))

    op.drop_table('user_roles')
    with op.batch_alter_table('user_import_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_import_batches_admin_user_id'))

    op.drop_table('user_import_batches')
    with op.batch_alter_table('tool_upstreams', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tool_upstreams_tool_id'))

    op.drop_table('tool_upstreams')
    with op.batch_alter_table('openapi_specs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_openapi_specs_tool_id'))
        batch_op.drop_index(batch_op.f('ix_openapi_specs_imported_by_user_id'))

    op.drop_table('openapi_specs')
    with op.batch_alter_table('external_resources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_external_resources_upstream_id'))
        batch_op.drop_index(batch_op.f('ix_external_resources_tool_id'))
        batch_op.drop_index(batch_op.f('ix_external_resources_owner_user_id'))

    op.drop_table('external_resources')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_user_id'))
        batch_op.drop_index(batch_op.f('ix_api_keys_prefix'))

    op.drop_table('api_keys')
    with op.batch_alter_table('api_endpoints', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_endpoints_tool_id'))

    op.drop_table('api_endpoints')
    with op.batch_alter_table('admin_audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_request_id'))
        batch_op.drop_index(batch_op.f('ix_admin_audit_events_admin_user_id'))

    op.drop_table('admin_audit_events')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')
    with op.batch_alter_table('tools', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tools_slug'))

    op.drop_table('tools')
    op.drop_table('roles')
    # 初始 schema 回滚结束。
