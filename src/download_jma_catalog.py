"""Download or convert JMA English hypocenter files into the project CSV.

The JMA English bulletin page publishes fixed-width hypocenter records in ZIP
files. If files already exist under data/raw/jma_downloads/hYYYY/hYYYY, this
helper reads those local files first and does not download them again.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = ROOT / "data" / "raw" / "jma_tohoku_2010_2023.csv"
LOCAL_DOWNLOADS = ROOT / "data" / "raw" / "jma_downloads"

BASE_URL = "https://www.data.jma.go.jp/eqev/data/bulletin/data/hypo"
YEARS = range(2010, 2024)

LAT_MIN, LAT_MAX = 36.0, 42.0
LON_MIN, LON_MAX = 140.0, 146.0
START = pd.Timestamp("2010-01-01 00:00:00")
END = pd.Timestamp("2023-12-31 23:59:59")


def parse_float(text: str) -> float | None:
    """Parse a fixed-width numeric field, returning None for blanks."""
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_magnitude(text: str) -> float | None:
    """Parse JMA two-column magnitude codes, including negative encodings."""
    text = text.strip()
    if not text:
        return None
    if len(text) == 2 and text[0].isalpha() and text[1].isdigit():
        # Official encoding: A0=-1.0, A9=-1.9, B0=-2.0, C0=-3.0.
        letter_offset = ord(text[0].upper()) - ord("A") + 1
        return -(letter_offset + int(text[1]) / 10.0)
    if len(text) == 2 and text[0] == "-" and text[1].isdigit():
        return -int(text[1]) / 10.0
    value = parse_float(text)
    if value is None:
        return None
    # JMA magnitude is stored in tenths, e.g. "34" means M3.4.
    return value / 10.0


def parse_record(line: str) -> dict[str, object] | None:
    """Parse one 96-byte JMA hypocenter record into the benchmark columns."""
    if len(line) < 55 or line[0] not in {"J", "U", "I"}:
        return None

    try:
        year = int(line[1:5])
        month = int(line[5:7])
        day = int(line[7:9])
        hour = int(line[9:11])
        minute = int(line[11:13])
    except ValueError:
        return None

    # JMA stores seconds and coordinate minutes as hundredths.
    second = (parse_float(line[13:17]) or 0.0) / 100.0
    lat_deg = parse_float(line[21:24])
    lat_min = parse_float(line[24:28])
    lon_deg = parse_float(line[32:36])
    lon_min = parse_float(line[36:40])
    # Depth is stored in hundredths of km. The first magnitude field follows it.
    depth = parse_float(line[44:49])
    magnitude = parse_magnitude(line[49:51])

    if None in {lat_deg, lat_min, lon_deg, lon_min, depth, magnitude}:
        return None

    whole_second = int(second)
    microsecond = int(round((second - whole_second) * 1_000_000))
    if whole_second >= 60:
        whole_second = 59
        microsecond = 999_999

    try:
        dt = pd.Timestamp(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=whole_second,
            microsecond=microsecond,
        )
    except ValueError:
        return None

    return {
        "Date": dt.strftime("%Y-%m-%d"),
        "Time": dt.strftime("%H:%M:%S"),
        "Latitude(°N)": float(lat_deg) + (float(lat_min) / 100.0) / 60.0,
        "Longitude(°E)": float(lon_deg) + (float(lon_min) / 100.0) / 60.0,
        "Depth(km)": float(depth) / 100.0,
        "Mag": float(magnitude),
    }


def download_zip(url: str) -> bytes | None:
    """Download a JMA ZIP file, returning None when a year is unavailable."""
    try:
        with urlopen(url, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 404:
            print(f"Not available: {url}")
            return None
        raise
    except URLError as exc:
        raise RuntimeError(f"Network error while downloading {url}: {exc}") from exc


def iter_local_lines(year: int) -> list[str] | None:
    """Return lines from an already extracted local hYYYY file, if present."""
    candidates = [
        LOCAL_DOWNLOADS / f"h{year}" / f"h{year}",
        LOCAL_DOWNLOADS / f"h{year}" / f"h{year}.txt",
        LOCAL_DOWNLOADS / f"h{year}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            print(f"Reading local file {candidate}")
            return candidate.read_text(encoding="shift_jis", errors="ignore").splitlines()
    return None


def main() -> None:
    """Download annual JMA files and save the pipeline-ready CSV."""
    rows: list[dict[str, object]] = []

    for year in YEARS:
        local_lines = iter_local_lines(year)
        if local_lines is not None:
            for line in local_lines:
                row = parse_record(line)
                if row is not None:
                    rows.append(row)
            continue

        url = f"{BASE_URL}/h{year}.zip"
        print(f"Downloading {url}")
        payload = download_zip(url)
        if payload is None:
            continue

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                print(f"  Parsing {name}")
                data = archive.read(name).decode("shift_jis", errors="ignore")
                for line in data.splitlines():
                    row = parse_record(line)
                    if row is not None:
                        rows.append(row)

    if not rows:
        raise RuntimeError("No JMA records were downloaded or parsed.")

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["Date"] + " " + df["Time"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df = df[
        df["datetime"].between(START, END)
        & df["Latitude(°N)"].between(LAT_MIN, LAT_MAX)
        & df["Longitude(°E)"].between(LON_MIN, LON_MAX)
    ].copy()
    df = df.sort_values("datetime")
    out = df[["Date", "Time", "Latitude(°N)", "Longitude(°E)", "Depth(km)", "Mag"]]

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"Saved {len(out)} events to {OUT_CSV}")
    print("Saved catalog covers downloaded years 2010-2023.")


if __name__ == "__main__":
    main()
