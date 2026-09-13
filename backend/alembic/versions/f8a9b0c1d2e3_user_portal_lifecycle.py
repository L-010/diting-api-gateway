"""增加用户门户生命周期、通知和任务适配配置。

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""

from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("pending_email", sa.String(length=255), nullable=True))
    op.execute(
        "UPDATE users SET email_verified_at = COALESCE(approved_at, created_at) "
        "WHERE email IS NOT NULL AND approval_status = 'approved'"
    )
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.add_column(sa.Column("task_adapter_json", sa.Text(), nullable=False, server_default=sa.text("('{}')")))
    bind = op.get_bind()
    tomodd_id = bind.execute(sa.text("SELECT id FROM tools WHERE slug = 'tomodd' LIMIT 1")).scalar()
    if tomodd_id:
        routes: dict[str, str] = {}
        for route_id, path in bind.execute(
            sa.text(
                "SELECT id, gateway_path FROM published_routes "
                "WHERE tool_id = :tool_id AND method = 'GET' AND is_enabled = 1"
            ),
            {"tool_id": tomodd_id},
        ):
            normalized = str(path).rstrip("/")
            if normalized.endswith("/jobs/{job_id}/logs"):
                routes["logs"] = str(route_id)
            elif normalized.endswith("/jobs/{job_id}/manifest"):
                routes["manifest"] = str(route_id)
            elif normalized.endswith("/jobs/{job_id}/artifacts"):
                routes["artifacts"] = str(route_id)
            elif normalized.endswith("/jobs/{job_id}/download.zip"):
                routes["download"] = str(route_id)
            elif normalized.endswith("/jobs/{job_id}"):
                routes["status"] = str(route_id)
        if routes:
            adapter = {
                "version": 1,
                "id_param": "job_id",
                "status_selector": "$.status",
                "terminal_statuses": ["succeeded", "failed", "cancelled"],
                "routes": routes,
            }
            bind.execute(
                sa.text("UPDATE tools SET task_adapter_json = :adapter WHERE id = :tool_id"),
                {"adapter": json.dumps(adapter, ensure_ascii=False), "tool_id": tomodd_id},
            )

    op.create_table(
        "auth_action_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("target_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_action_tokens_user_id", "auth_action_tokens", ["user_id"], unique=False)
    op.create_index("ix_auth_action_tokens_user_purpose", "auth_action_tokens", ["user_id", "purpose"], unique=False)
    op.create_index("ix_auth_action_tokens_expires_at", "auth_action_tokens", ["expires_at"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("action_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"], unique=False)
    op.create_index("ix_notifications_user_created_at", "notifications", ["user_id", "created_at"], unique=False)

    op.create_table(
        "email_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_outbox_recipient", "email_outbox", ["recipient"], unique=False)
    op.create_index("ix_email_outbox_status_next_attempt", "email_outbox", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_next_attempt", table_name="email_outbox")
    op.drop_index("ix_email_outbox_recipient", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index("ix_notifications_user_created_at", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_auth_action_tokens_expires_at", table_name="auth_action_tokens")
    op.drop_index("ix_auth_action_tokens_user_purpose", table_name="auth_action_tokens")
    op.drop_index("ix_auth_action_tokens_user_id", table_name="auth_action_tokens")
    op.drop_table("auth_action_tokens")
    with op.batch_alter_table("tools", schema=None) as batch_op:
        batch_op.drop_column("task_adapter_json")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("pending_email")
        batch_op.drop_column("email_verified_at")


