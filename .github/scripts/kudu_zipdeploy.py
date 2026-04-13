#!/usr/bin/env python3
"""
POST a zip to Kudu ZipDeploy using publish profile XML in PUBLISH_PROFILE.

Uses async ZipDeploy (?isAsync=true) and polls /api/deployments/latest — synchronous
zipdeploy often hits HTTP 502 (gateway timeout) on larger Python function packages.
"""
from __future__ import annotations

import base64
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
    user = zp.get("userName") or ""
    pwd = zp.get("userPWD") or ""
    if not host or not user or not pwd:
        print("ERROR: ZipDeploy profile incomplete", file=sys.stderr)
        return 1

    auth = _basic_auth_header(user, pwd)
    ctx = ssl.create_default_context()

    zip_path = os.environ.get("ZIP_PATH", "/tmp/functionapp.zip")
    with open(zip_path, "rb") as f:
        body = f.read()
    print("Zip bytes:", len(body))

    # Async avoids front-door / ARR 502 while Kudu extracts a large zip.
    deploy_url = f"https://{host}/api/zipdeploy?isAsync=true"
    print("ZipDeploy (async):", deploy_url)
    print("SCM user:", user)

    req = urllib.request.Request(deploy_url, data=body, method="POST")
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/zip")

    deploy_id: str | None = None
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
            status = resp.status
            raw = resp.read(16000)
            print("ZipDeploy response HTTP", status)
            if raw:
                print(raw[:2000])
                try:
                    info = json.loads(raw.decode("utf-8"))
                    if isinstance(info, dict) and info.get("id"):
                        deploy_id = str(info["id"])
                        print("Deployment id from response:", deploy_id)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            if status not in (200, 202):
                print("ERROR: expected 200 or 202 from async zipdeploy", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as e:
        print("HTTPError on zipdeploy:", e.code, e.reason, file=sys.stderr)
        print((e.read() or b"")[:4000].decode(errors="replace"), file=sys.stderr)
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
