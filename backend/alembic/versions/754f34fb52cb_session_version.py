"""session_version

Revision ID: 754f34fb52cb
Revises: 72c8b4e62692
Create Date: 2026-07-22 20:30:56.318000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '754f34fb52cb'
down_revision = '72c8b4e62692'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 使用服务端默认值兼容已经存在的用户记录。
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default=sa.text('1')))

    # 会话版本字段创建结束。


def downgrade() -> None:
    # 回滚将移除服务端会话撤销能力。
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('session_version')

    # 会话版本字段回滚结束。
