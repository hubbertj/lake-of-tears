import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import json
from datetime import date
from pathlib import Path

import pandas as pd
from storage_writer import write_to_storage

EXPORT_DIR = "/home/openclaw/alexa-exports"


def parse_alexa_export(export_dir: str) -> pd.DataFrame:
    records = []
    for f in Path(export_dir).glob("*.json"):
        data = json.loads(f.read_text())
        if isinstance(data, list):
            records.extend(data)
        elif isinstance(data, dict):
            records.append(data)
    return pd.json_normalize(records)


if __name__ == "__main__":
    df = parse_alexa_export(EXPORT_DIR)
    write_to_storage(df, "alexa", str(date.today()))
