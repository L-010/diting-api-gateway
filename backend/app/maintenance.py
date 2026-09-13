"""可由计划任务调用的数据库维护命令。"""

from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import delete

from .config import get_settings
from .database import SessionLocal
from .models import AuthActionToken, GatewayRequest, IdempotencyRecord, RateLimitBucket, UserRateLimitBucket
from .services.user_lifecycle import utc_now


def cleanup() -> dict[str, int]:
    settings = get_settings()
    now = utc_now()
    with SessionLocal() as session:
        calls = session.execute(
            delete(GatewayRequest).where(GatewayRequest.created_at < now - timedelta(days=settings.call_log_retention_days))
        ).rowcount
        tokens = session.execute(
            delete(AuthActionToken).where(
                (AuthActionToken.expires_at < now - timedelta(days=7))
                | (AuthActionToken.consumed_at < now - timedelta(days=7))
            )
        ).rowcount
        bucket_cutoff = now - timedelta(days=2)
        key_rate_buckets = session.execute(
            delete(RateLimitBucket).where(RateLimitBucket.window_start < bucket_cutoff)
        ).rowcount
        user_rate_buckets = session.execute(
            delete(UserRateLimitBucket).where(UserRateLimitBucket.window_start < bucket_cutoff)
        ).rowcount
        idempotency_records = session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < now)
        ).rowcount
        session.commit()
    return {
        "gateway_requests": int(calls or 0),
        "auth_action_tokens": int(tokens or 0),
        "rate_limit_buckets": int(key_rate_buckets or 0),
        "user_rate_limit_buckets": int(user_rate_buckets or 0),
        "idempotency_records": int(idempotency_records or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="清理 API Gateway 过期数据")
    parser.add_argument("cleanup", choices=["cleanup"])
    parser.parse_args()
    result = cleanup()
    print(
        f"已清理调用记录 {result['gateway_requests']} 条，认证令牌 {result['auth_action_tokens']} 条，"
        f"Key 限流桶 {result['rate_limit_buckets']} 条，用户限流桶 {result['user_rate_limit_buckets']} 条"
    )


if __name__ == "__main__":
    main()
