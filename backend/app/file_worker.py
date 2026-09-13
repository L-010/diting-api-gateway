"""远程文件同步与清理进程。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import delete

from .config import get_settings
from .database import SessionLocal
from .models import ExternalResource, FileWorkerHeartbeat, RemoteFile, utc_now
from .redaction import external_error_summary
from .services.remote_files import claim_sync_jobs, complete_sync_job, delete_remote_file, expire_remote_files, fail_sync_job, sync_resource, verify_remote_file


logger = logging.getLogger("api_gateway.file_worker")
WORKER_INSTANCE_ID = str(uuid.uuid4())


def record_heartbeat(instance_id: str, *, processed_jobs: int = 0, last_error: str | None = None) -> None:
    """记录进程存活状态；监控只展示近期心跳，不把过期实例误报为在线。"""
    with SessionLocal() as session:
        heartbeat = session.get(FileWorkerHeartbeat, instance_id)
        if heartbeat is None:
            heartbeat = FileWorkerHeartbeat(instance_id=instance_id, process_id=os.getpid())
            session.add(heartbeat)
        heartbeat.last_seen_at = utc_now()
        heartbeat.processed_jobs = int(heartbeat.processed_jobs or 0) + max(0, processed_jobs)
        if last_error is not None:
            heartbeat.last_error = last_error[:1_000]
        session.execute(delete(FileWorkerHeartbeat).where(FileWorkerHeartbeat.last_seen_at < utc_now() - timedelta(days=7)))
        session.commit()


async def process_once(instance_id: str = WORKER_INSTANCE_ID) -> int:
    record_heartbeat(instance_id)
    with SessionLocal() as session:
        expire_remote_files(session)
        session.commit()
        _, jobs = claim_sync_jobs(session)
    processed = 0
    for pending in jobs:
        with SessionLocal() as session:
            job = session.get(type(pending), pending.id)
            if not job or job.status != "running":
                continue
            try:
                terminal = False
                if job.job_type == "delete" and job.remote_file_id:
                    item = session.get(RemoteFile, job.remote_file_id)
                    if item:
                        await delete_remote_file(session, item, request_id=str(uuid.uuid4()))
                    terminal = True
                elif job.job_type == "verify" and job.remote_file_id:
                    item = session.get(RemoteFile, job.remote_file_id)
                    if item:
                        await verify_remote_file(session, item, request_id=str(uuid.uuid4()))
                elif job.resource_id:
                    resource = session.get(ExternalResource, job.resource_id)
                    terminal = True if not resource else await sync_resource(session, resource, request_id=str(uuid.uuid4()))
                complete_sync_job(session, job, terminal=terminal)
                session.commit()
            except Exception as error:
                session.rollback()
                job = session.get(type(pending), pending.id)
                if job:
                    fail_sync_job(job, error)
                    session.commit()
                logger.warning(
                    "file_sync_failed",
                    extra={"job_id": pending.id, "error_code": external_error_summary(error, category="FILE_SYNC_FAILED")},
                )
            processed += 1
    record_heartbeat(instance_id, processed_jobs=processed, last_error="")
    return processed


def main() -> None:
    get_settings().validate_runtime()
    record_heartbeat(WORKER_INSTANCE_ID)
    while True:
        try:
            processed = asyncio.run(process_once(WORKER_INSTANCE_ID))
        except Exception as error:
            summary = external_error_summary(error, category="FILE_WORKER_CYCLE_FAILED")
            record_heartbeat(WORKER_INSTANCE_ID, last_error=summary)
            logger.error("file_worker_cycle_failed", extra={"error_code": summary})
            processed = 0
        time.sleep(1 if processed else 5)


if __name__ == "__main__":
    main()
