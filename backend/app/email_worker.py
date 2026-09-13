"""项目内邮件 Outbox 发送进程。"""

from __future__ import annotations

import argparse
import time

from .config import get_settings
from .database import SessionLocal
from .services.email_delivery import deliver_due_email


def main() -> None:
    parser = argparse.ArgumentParser(description="发送 API Gateway 邮件 Outbox")
    parser.add_argument("--once", action="store_true", help="只处理当前到期邮件后退出")
    parser.add_argument("--interval", type=float, default=5.0, help="空队列轮询间隔秒数")
    args = parser.parse_args()
    get_settings().validate_runtime()
    while True:
        with SessionLocal() as session:
            handled = deliver_due_email(session)
        if args.once and not handled:
            return
        if not handled:
            time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
