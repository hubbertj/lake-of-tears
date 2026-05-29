import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import os
from datetime import date

import pandas as pd
import requests
from storage_writer import write_to_storage

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "")
API_KEY = os.environ.get("JELLYFIN_API_KEY", "")


def fetch_activity() -> pd.DataFrame:
    resp = requests.get(
        f"{JELLYFIN_URL}/System/ActivityLog/Entries",
        headers={"X-Emby-Token": API_KEY},
        params={"limit": 1000},
        timeout=30,
    )
    resp.raise_for_status()
    return pd.DataFrame(resp.json().get("Items", []))


if __name__ == "__main__":
    today = str(date.today())
    df = fetch_activity()
    df["source_date"] = today
    write_to_storage(df, "jellyfin", today)
