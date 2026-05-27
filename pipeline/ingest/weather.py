import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[2]))

import os
import requests
import pandas as pd
from datetime import date, timedelta
from storage_writer import write_to_storage

# State College, PA — override via env if needed
LATITUDE = float(os.environ.get("WEATHER_LAT", "40.7934"))
LONGITUDE = float(os.environ.get("WEATHER_LON", "-77.8600"))

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_weather(lat: float, lon: float, target_date: str) -> pd.DataFrame:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "wind_direction_10m",
            "apparent_temperature",
            "cloud_cover",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
        "start_date": target_date,
        "end_date": target_date,
    }
    resp = requests.get(OPEN_METEO_URL, params=params)
    resp.raise_for_status()
    data = resp.json()

    hourly = data["hourly"]
    df = pd.DataFrame(hourly)
    df.rename(columns={"time": "timestamp"}, inplace=True)
    df["source_date"] = target_date
    df["latitude"] = lat
    df["longitude"] = lon
    return df


if __name__ == "__main__":
    yesterday = str(date.today() - timedelta(days=1))
    df = fetch_weather(LATITUDE, LONGITUDE, yesterday)
    write_to_storage(df, "weather", yesterday)
