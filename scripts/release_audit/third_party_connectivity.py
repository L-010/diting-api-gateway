"""隔离第三方服务连通性检查；不执行写入、投递、上传或删除。"""

from __future__ import annotations

import argparse
import os
import socket
import ssl
from urllib.parse import urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 SMTP、TomoDD 或文件上游的网络连通性")
    parser.add_argument("provider", choices=("smtp", "tomodd", "file"))
    args = parser.parse_args()
    if os.getenv("THIRD_PARTY_ENV") != "isolated" or os.getenv("THIRD_PARTY_CONNECTIVITY_ACK") != "true":
        print("third_party_connectivity=refused reason=isolated_environment_ack")
        return 1
    prefix = {"smtp": "SMTP", "tomodd": "TOMODD", "file": "FILE"}[args.provider]
    raw = os.getenv(f"{prefix}_TEST_ENDPOINT", "").strip()
    parsed = urlparse(raw if args.provider != "smtp" else f"smtp://{raw}")
    host = parsed.hostname
    port = parsed.port or (587 if args.provider == "smtp" else 443)
    scheme_ok = args.provider == "smtp" or parsed.scheme == "https"
    if not host or not (1 <= port <= 65535) or not scheme_ok:
        print("third_party_connectivity=failed reason=endpoint_missing_or_invalid")
        return 1
    try:
        with socket.create_connection((host, port), timeout=10) as connection:
            if args.provider != "smtp" and parsed.scheme == "https":
                with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
                    pass
    except (OSError, ssl.SSLError):
        print("third_party_connectivity=failed reason=connection_failed")
        return 1
    print(f"provider={args.provider} host_check=ok tls_check={'required' if args.provider != 'smtp' else 'smtp_handshake_required_separately'}")
    print("third_party_connectivity=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
