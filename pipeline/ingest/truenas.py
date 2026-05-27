import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))

import os
import sys
import requests
import pandas as pd
from datetime import date
from storage_writer import write_to_storage

TRUENAS_URL = f"http://{os.environ.get('TRUENAS_HOST', '')}/api/v2.0"
API_KEY = os.environ.get("TRUENAS_API_KEY", "")


def fetch_pool_stats() -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    pools = requests.get(f"{TRUENAS_URL}/pool", headers=headers, timeout=30)
    pools.raise_for_status()
    return pd.json_normalize(pools.json())


def fetch_disk_info() -> pd.DataFrame:
    """Fetch disk inventory + SMART status via TrueNAS REST API."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        disks = requests.get(f"{TRUENAS_URL}/disk", headers=headers, timeout=30)
        disks.raise_for_status()
        rows = []
        for d in disks.json():
            rows.append({
                "devname":      d.get("devname", ""),
                "serial":       d.get("serial", ""),
                "model":        d.get("model", ""),
                "size":         d.get("size", 0),
                "rotationrate": d.get("rotationrate"),
                "description":  d.get("description", ""),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"[ingest_truenas] disk info failed: {e}", file=sys.stderr)
        return pd.DataFrame()


if __name__ == "__main__":
    today = str(date.today())
    pool_df = fetch_pool_stats()
    pool_df["source_date"] = today
    write_to_storage(pool_df, "truenas", today)
    disk_df = fetch_disk_info()
    if not disk_df.empty:
        disk_df["source_date"] = today
        write_to_storage(disk_df, "truenas_disks", today)
