#!/usr/bin/env python3
"""
POST a zip to Kudu ZipDeploy using publish profile XML in PUBLISH_PROFILE.

Uses async ZipDeploy (?isAsync=true) and polls /api/deployments/latest — synchronous
zipdeploy often hits HTTP 502 (gateway timeout) on larger Python function packages.

Uploads the zip in chunks via http.client (does not buffer the whole file in RAM) and
retries on transient 502/503/504 — ARR/frontends sometimes drop long uploads.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def _basic_auth_header(user: str, pwd: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()


def _post_zipdeploy_streaming(
    host: str,
    auth_header: str,
    zip_path: str,
    *,
    ssl_ctx: ssl.SSLContext,
    timeout_sec: float,
    chunk_bytes: int = 1024 * 1024,
) -> tuple[int, bytes]:
    """POST /api/zipdeploy?isAsync=true with Content-Length; stream body from disk."""
    size = os.path.getsize(zip_path)
    conn = http.client.HTTPSConnection(host, context=ssl_ctx, timeout=timeout_sec)
    try:
        conn.putrequest("POST", "/api/zipdeploy?isAsync=true")
        conn.putheader("Authorization", auth_header)
        conn.putheader("Content-Type", "application/zip")
        conn.putheader("Content-Length", str(size))
        conn.putheader("Connection", "close")
        conn.endheaders()
        with open(zip_path, "rb") as fp:
            while True:
                chunk = fp.read(chunk_bytes)
                if not chunk:
                    break
                conn.send(chunk)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read(65536)
        return status, raw
    finally:
        conn.close()


def main() -> int:
    xml = os.environ.get("PUBLISH_PROFILE", "").strip()
    if not xml:
        print("ERROR: PUBLISH_PROFILE is empty", file=sys.stderr)
        return 1

    root = ET.fromstring(xml)
    profiles = root.findall("publishProfile")
    zp = next((p for p in profiles if p.get("publishMethod") == "ZipDeploy"), None)
    if zp is None:
        print("ERROR: No ZipDeploy publishProfile", file=sys.stderr)
        return 1

    host = (zp.get("publishUrl") or "").replace(":443", "").strip()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    user = zp.get("userName") or ""
    pwd = zp.get("userPWD") or ""
    if not host or not user or not pwd:
        print("ERROR: ZipDeploy profile incomplete", file=sys.stderr)
        return 1

    auth = _basic_auth_header(user, pwd)
    ctx = ssl.create_default_context()

    zip_path = os.environ.get("ZIP_PATH", "/tmp/functionapp.zip")
    zip_size = os.path.getsize(zip_path)
    print("Zip bytes:", zip_size)

    post_timeout = float(os.environ.get("KUDU_ZIPDEPLOY_POST_TIMEOUT_SEC", "1800"))
    max_attempts = max(1, int(os.environ.get("KUDU_ZIPDEPLOY_RETRIES", "5")))
    backoff = float(os.environ.get("KUDU_ZIPDEPLOY_RETRY_BACKOFF_SEC", "25"))

    deploy_url = f"https://{host}/api/zipdeploy?isAsync=true"
    print("ZipDeploy (async):", deploy_url)
    print("SCM user:", user)
    print("POST timeout sec:", post_timeout, "retries:", max_attempts)

    deploy_id: str | None = None
    last_status = -1
    last_raw = b""
    for attempt in range(1, max_attempts + 1):
        try:
            status, raw = _post_zipdeploy_streaming(
                host, auth, zip_path, ssl_ctx=ctx, timeout_sec=post_timeout
            )
            last_status, last_raw = status, raw
            print("ZipDeploy response HTTP", status, "(attempt", attempt, "of", max_attempts, ")")
            if raw:
                print(raw[:2000])
                try:
                    info = json.loads(raw.decode("utf-8"))
                    if isinstance(info, dict) and info.get("id"):
                        deploy_id = str(info["id"])
                        print("Deployment id from response:", deploy_id)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            if status in (200, 202):
                break
            if status in (502, 503, 504) and attempt < max_attempts:
                print(
                    f"WARN: ZipDeploy HTTP {status}; retrying in {backoff:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                continue
            print("ERROR: unexpected HTTP status from async zipdeploy:", status, file=sys.stderr)
            return 1
        except (TimeoutError, OSError, http.client.HTTPException) as e:
            print(f"WARN: ZipDeploy attempt {attempt} failed: {e!r}", file=sys.stderr)
            if attempt >= max_attempts:
                print("ERROR: ZipDeploy failed after retries", file=sys.stderr)
                return 1
            time.sleep(backoff)

    if last_status not in (200, 202):
        print("HTTPError on zipdeploy:", last_status, file=sys.stderr)
        print(last_raw[:4000].decode(errors="replace"), file=sys.stderr)
        return 1

    time.sleep(5)

    if deploy_id:
        latest_url = f"https://{host}/api/deployments/{deploy_id}"
    else:
        latest_url = f"https://{host}/api/deployments/latest"
    deadline = time.time() + float(os.environ.get("KUDU_POLL_TIMEOUT_SEC", "900"))
    last_log = 0.0

    while time.time() < deadline:
        req2 = urllib.request.Request(latest_url, method="GET")
        req2.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(req2, context=ctx, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            print("HTTPError polling latest:", e.code, file=sys.stderr)
            time.sleep(10)
            continue

        complete = payload.get("complete")
        status = payload.get("status")
        status_text = payload.get("status_text") or ""
        deploy_id = payload.get("id", "")

        now = time.time()
        if now - last_log > 20:
            print(
                f"deployment id={deploy_id!r} complete={complete!r} status={status!r} status_text={status_text[:200]!r}"
            )
            last_log = now

        if complete is True:
            if status in (3, "3") or "fail" in status_text.lower() or "exception" in status_text.lower():
                print("ERROR: deployment failed:", payload, file=sys.stderr)
                return 1
            if status in (4, "4"):
                print("Deployment complete (status=4).")
                return 0
            if payload.get("end_time") and status not in (1, 2, "1", "2"):
                print("Deployment marked complete (end_time set).")
                return 0

        time.sleep(8)

    print("ERROR: timed out waiting for deployment to complete", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
