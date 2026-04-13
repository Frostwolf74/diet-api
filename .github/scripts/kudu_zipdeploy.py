#!/usr/bin/env python3
"""POST /tmp/functionapp.zip to Kudu ZipDeploy using publish profile XML in PUBLISH_PROFILE."""
from __future__ import annotations

import base64
import os
import ssl
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def main() -> int:
    xml = os.environ.get("PUBLISH_PROFILE", "").strip()
    if not xml:
        print("ERROR: PUBLISH_PROFILE / AZURE_FUNCTIONAPP_PUBLISH_PROFILE is empty", file=sys.stderr)
        return 1

    root = ET.fromstring(xml)
    profiles = root.findall("publishProfile")
    zp = next((p for p in profiles if p.get("publishMethod") == "ZipDeploy"), None)
    if zp is None:
        print("ERROR: No publishProfile with publishMethod=ZipDeploy", file=sys.stderr)
        return 1

    host = (zp.get("publishUrl") or "").replace(":443", "").strip()
    user = zp.get("userName") or ""
    pwd = zp.get("userPWD") or ""
    if not host or not user or not pwd:
        print("ERROR: ZipDeploy profile missing publishUrl, userName, or userPWD", file=sys.stderr)
        return 1

    url = f"https://{host}/api/zipdeploy"
    print("ZipDeploy URL:", url)
    print("SCM user:", user)

    zip_path = os.environ.get("ZIP_PATH", "/tmp/functionapp.zip")
    with open(zip_path, "rb") as f:
        body = f.read()

    req = urllib.request.Request(url, data=body, method="POST")
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "application/zip")

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=900) as resp:
            print("HTTP", resp.status)
            data = resp.read(8000)
            if data:
                print(data[:2000])
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.reason, file=sys.stderr)
        err = e.read() or b""
        print(err[:4000].decode(errors="replace"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
