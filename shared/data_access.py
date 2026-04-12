from __future__ import annotations

import os

import pandas as pd

from shared import config
from shared.cache import read_blob_bytes
from shared.pipeline import clean_dataframe, load_csv_bytes


def load_diet_dataframe() -> pd.DataFrame | None:
    """Prefer cleaned blob; else raw blob; else local All_Diets.csv for dev."""
    raw = read_blob_bytes(config.CONTAINER_NAME, config.BLOB_CLEAN)
    if raw:
        return load_csv_bytes(raw)
    raw_src = read_blob_bytes(config.CONTAINER_NAME, config.BLOB_SOURCE)
    if raw_src:
        return clean_dataframe(load_csv_bytes(raw_src))
    here = os.path.dirname(__file__)
    local = os.path.join(here, "..", "All_Diets.csv")
    if os.path.isfile(local):
        return clean_dataframe(pd.read_csv(local))
    return None
