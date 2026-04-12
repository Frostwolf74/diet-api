from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd


NUMERIC_COLS = ["Protein(g)", "Carbs(g)", "Fat(g)"]


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            mean_val = float(df[col].mean()) if df[col].notna().any() else 0.0
            df[col] = df[col].fillna(mean_val)
    if "Diet_type" in df.columns:
        df["Diet_type"] = df["Diet_type"].astype(str).str.strip()
    if "Recipe_name" in df.columns:
        df["Recipe_name"] = df["Recipe_name"].astype(str)
    return df


def build_insights_payload(df: pd.DataFrame, source_etag: str | None = None) -> dict[str, Any]:
    """Precomputed chart + summary data stored in Redis/blob after cleaning."""
    if df.empty:
        return {
            "labels": [],
            "protein": [],
            "carbs": [],
            "fat": [],
            "scatter": [],
            "pie": {"labels": [], "data": []},
            "heatmap": {"labels": [], "matrix": []},
            "clusters": [],
            "recipe_count": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_etag": source_etag,
        }

    avg_macros = (
        df.groupby("Diet_type")[NUMERIC_COLS].mean().round(2)
        if "Diet_type" in df.columns
        else pd.DataFrame()
    )

    labels = avg_macros.index.tolist() if len(avg_macros) else []
    scatter_df = df.sample(min(400, len(df)), random_state=42) if len(df) > 0 else df
    scatter = []
    if all(c in scatter_df.columns for c in NUMERIC_COLS):
        for _, row in scatter_df.iterrows():
            scatter.append(
                {
                    "x": float(row["Protein(g)"]),
                    "y": float(row["Carbs(g)"]),
                    "diet": str(row.get("Diet_type", "")),
                }
            )

    pie_labels: list[str] = []
    pie_data: list[int] = []
    if "Diet_type" in df.columns:
        counts = df["Diet_type"].value_counts()
        pie_labels = counts.index.astype(str).tolist()
        pie_data = counts.astype(int).tolist()

    heatmap_labels: list[str] = []
    matrix: list[list[float]] = []
    if len(NUMERIC_COLS) >= 2 and len(df) > 1:
        corr = df[NUMERIC_COLS].corr().round(3)
        heatmap_labels = corr.columns.tolist()
        matrix = corr.values.tolist()

    clusters = _simple_macro_clusters(df)

    return {
        "labels": labels,
        "protein": avg_macros["Protein(g)"].tolist() if len(avg_macros) else [],
        "carbs": avg_macros["Carbs(g)"].tolist() if len(avg_macros) else [],
        "fat": avg_macros["Fat(g)"].tolist() if len(avg_macros) else [],
        "scatter": scatter,
        "pie": {"labels": pie_labels, "data": pie_data},
        "heatmap": {"labels": heatmap_labels, "matrix": matrix},
        "clusters": clusters,
        "recipe_count": int(len(df)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_etag": source_etag,
    }


def _simple_macro_clusters(df: pd.DataFrame, k: int = 3) -> list[dict[str, Any]]:
    """Lightweight 'clusters' for demo: quantile buckets on protein+carbs."""
    if not all(c in df.columns for c in NUMERIC_COLS) or len(df) < k:
        return []
    score = df["Protein(g)"] + df["Carbs(g)"]
    try:
        bucket = pd.qcut(score, q=k, labels=False, duplicates="drop")
    except ValueError:
        bucket = pd.cut(score, bins=k, labels=False)
    bucket = pd.Series(bucket).fillna(0).astype(int)
    out = (
        df.assign(cluster=bucket)
        .groupby("cluster")
        .size()
        .reset_index(name="count")
        .to_dict(orient="records")
    )
    return out


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def insights_to_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def insights_from_bytes(data: bytes) -> dict[str, Any]:
    return json.loads(data.decode("utf-8"))


def load_csv_bytes(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data))
