"""在隔离内存数据库上测量 Gateway 基本开销，不连接真实上游。"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ApiEndpoint, ApiKey, ExternalResource, PublishedRoute, Tool, ToolUpstream, User
from app.services.resource_policies import execute_pre_checks


def percentile(values: list[float], ratio: float) -> float:
    """使用最近秩计算分位数，避免引入性能测试依赖。"""

    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


def main() -> int:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(username="performance-user", password_hash="synthetic", must_change_password=False)
        tool = Tool(slug="performance-tool", name="性能合成工具")
        session.add_all([user, tool])
        session.flush()
        endpoint = ApiEndpoint(tool_id=tool.id, method="GET", upstream_path="/resources/{resource_id}", status="published")
        session.add(endpoint)
        session.flush()
        route = PublishedRoute(
            tool_id=tool.id,
            endpoint_id=endpoint.id,
            method="GET",
            gateway_path="/resources/{resource_id}",
            upstream_path="/resources/{resource_id}",
            access_mode="owner",
            resource_policy_json='{"version":1,"pre_checks":[{"action":"require_owner","resource_kind":"dataset","source":"path","selector":"resource_id"}],"post_actions":[],"list_filter":null}',
        )
        session.add_all([route, ToolUpstream(tool_id=tool.id, base_url="http://127.0.0.1:19091"), ApiKey(user_id=user.id, label="性能测试", prefix="agw_perf", secret_hash="synthetic", scopes="*")])
        session.add(ExternalResource(tool_id=tool.id, owner_user_id=user.id, kind="dataset", upstream_id="dataset-1"))
        session.commit()

        durations: list[float] = []
        for _ in range(200):
            started = time.perf_counter()
            execute_pre_checks(session, route=route, user_id=user.id, path_params={"resource_id": "dataset-1"}, query_params={}, request_json={})
            durations.append((time.perf_counter() - started) * 1000)
        try:
            execute_pre_checks(session, route=route, user_id=user.id, path_params={"resource_id": "missing"}, query_params={}, request_json={})
        except HTTPException as error:
            assert error.status_code == 404
        else:
            raise AssertionError("不存在资源必须被拒绝")

    print(
        "local_owner_precheck_samples=200 "
        f"p50_ms={percentile(durations, 0.50):.3f} "
        f"p95_ms={percentile(durations, 0.95):.3f} "
        f"p99_ms={percentile(durations, 0.99):.3f} "
        f"mean_ms={statistics.fmean(durations):.3f}"
    )
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
