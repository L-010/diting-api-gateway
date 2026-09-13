"""修复接口治理状态与线上路由状态不一致的数据。

Revision ID: f9a0b1c2d3e4
Revises: e4f5a6b7c8d9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE published_routes
            SET is_enabled = false,
                route_version = route_version + 1,
                publish_reason = 'migration: 治理状态不是已发布，自动停用遗留路由',
                updated_at = CURRENT_TIMESTAMP
            WHERE is_enabled = true
              AND EXISTS (
                  SELECT 1
                  FROM api_endpoints
                  WHERE api_endpoints.id = published_routes.endpoint_id
                    AND api_endpoints.status <> 'published'
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE openapi_diff_items
            SET decision = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM api_endpoints
                        WHERE api_endpoints.id = openapi_diff_items.endpoint_id
                          AND api_endpoints.status = 'blocked'
                    ) THEN 'block'
                    WHEN EXISTS (
                        SELECT 1 FROM api_endpoints
                        WHERE api_endpoints.id = openapi_diff_items.endpoint_id
                          AND api_endpoints.status = 'excluded'
                    ) THEN 'exclude'
                    WHEN EXISTS (
                        SELECT 1
                        FROM api_endpoints
                        JOIN published_routes ON published_routes.endpoint_id = api_endpoints.id
                        WHERE api_endpoints.id = openapi_diff_items.endpoint_id
                          AND api_endpoints.status = 'published'
                          AND published_routes.is_enabled = true
                    ) THEN 'accept'
                    ELSE decision
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE diff_type = 'unchanged'
              AND decision = 'pending'
              AND batch_id IN (
                  SELECT latest.id
                  FROM tool_import_batches AS latest
                  WHERE latest.created_at = (
                      SELECT MAX(candidate.created_at)
                      FROM tool_import_batches AS candidate
                      WHERE candidate.tool_id = latest.tool_id
                  )
              )
            """
        )
    )


def downgrade() -> None:
    # 数据修复不可安全逆转，降级时不重新启用已停用的高风险路由。
    pass
