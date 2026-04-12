from __future__ import annotations

import os
from typing import Any

from azure.storage.blob import BlobServiceClient, ContentSettings

from shared import config
from shared.pipeline import insights_from_bytes, insights_to_json_bytes


def _client() -> BlobServiceClient | None:
    conn = os.environ.get("AzureWebJobsStorage")
    if not conn:
        return None
    return BlobServiceClient.from_connection_string(conn)


def read_blob_bytes(container: str, name: str) -> bytes | None:
    client = _client()
    if not client:
        return None
    try:
        bc = client.get_blob_client(container=container, blob=name)
        if not bc.exists():
            return None
        return bc.download_blob().readall()
    except Exception:
        return None


def write_blob_bytes(container: str, name: str, data: bytes, content_type: str | None = None) -> None:
    client = _client()
    if not client:
        raise RuntimeError("AzureWebJobsStorage is not configured")
    cc = client.get_container_client(container)
    try:
        cc.create_container()
    except Exception:
        pass
    kwargs: dict[str, Any] = {}
    if content_type:
        kwargs["content_settings"] = ContentSettings(content_type=content_type)
    cc.upload_blob(name=name, data=data, overwrite=True, **kwargs)


def read_insights_cache() -> dict[str, Any] | None:
    redis_payload = _read_redis_insights()
    if redis_payload is not None:
        return redis_payload
    raw = read_blob_bytes(config.CONTAINER_NAME, config.BLOB_INSIGHTS)
    if not raw:
        return None
    return insights_from_bytes(raw)


def write_insights_cache(payload: dict[str, Any]) -> None:
    data = insights_to_json_bytes(payload)
    if _write_redis_insights(data):
        return
    write_blob_bytes(
        config.CONTAINER_NAME,
        config.BLOB_INSIGHTS,
        data,
        content_type="application/json",
    )


def _redis_from_env():
    if not config.REDIS_CONNECTION_STRING:
        return None
    try:
        import redis
    except ImportError:
        return None
    return redis.from_url(config.REDIS_CONNECTION_STRING, decode_responses=False)


def _read_redis_insights() -> dict[str, Any] | None:
    r = _redis_from_env()
    if not r:
        return None
    try:
        raw = r.get(config.REDIS_KEY_INSIGHTS)
        if not raw:
            return None
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return insights_from_bytes(raw)
    except Exception:
        return None


def _write_redis_insights(data: bytes) -> bool:
    r = _redis_from_env()
    if not r:
        return False
    try:
        r.set(config.REDIS_KEY_INSIGHTS, data)
        return True
    except Exception:
        return False
