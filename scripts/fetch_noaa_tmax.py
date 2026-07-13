#!/usr/bin/env python3
"""Download Montana GHCN-Daily station files and calculate statewide TMAX.

This uses NCEI's public per-station bulk archive instead of the rate-limited CDO
API. Original all-element .csv.gz files are retained, and a TMAX-only CSV is
written for each station. Values with a nonblank GHCN quality flag are kept in
the station output but excluded from statewide maxima.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASE_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily"
STATIONS_URL = f"{BASE_URL}/ghcnd-stations.txt"
INVENTORY_URL = f"{BASE_URL}/ghcnd-inventory.txt"
BY_STATION_URL = f"{BASE_URL}/by_station"
TMAX_COLUMNS = (
    "station_id",
    "station_name",
    "latitude",
    "longitude",
    "elevation_m",
    "date",
    "tmax_c",
    "tmax_f",
    "measurement_flag",
    "quality_flag",
    "source_flag",
    "observation_time",
)
USER_AGENT = "montana-hottest-days-analysis/1.0 (NOAA GHCN-Daily research)"


@dataclass(frozen=True)
class Station:
    station_id: str
    name: str
    latitude: str
    longitude: str
    elevation: str
    first_year: str = ""
    last_year: str = ""


def download(url: str, destination: Path, refresh: bool = False) -> Path:
    """Download atomically with retries; leave an existing file untouched."""
    if destination.exists() and destination.stat().st_size and not refresh:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
            partial.replace(destination)
            return destination
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def parse_noaa_catalog(stations_path: Path, inventory_path: Path) -> list[Station]:
    """Return every catalogued Montana station with unflagged TMAX inventory."""
    tmax_years: dict[str, tuple[str, str]] = {}
    with inventory_path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            parts = line.split()
            if len(parts) >= 6 and parts[3] == "TMAX":
                tmax_years[parts[0]] = (parts[4], parts[5])

    stations: list[Station] = []
    with stations_path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            station_id = line[0:11].strip()
            if line[38:40] != "MT" or station_id not in tmax_years:
                continue
            first_year, last_year = tmax_years[station_id]
            stations.append(
                Station(
                    station_id=station_id,
                    name=line[41:71].strip(),
                    latitude=line[12:20].strip(),
                    longitude=line[21:30].strip(),
                    elevation=line[31:37].strip(),
                    first_year=first_year,
                    last_year=last_year,
                )
            )
    return sorted(stations, key=lambda station: station.station_id)


def parse_station_csv(path: Path) -> list[Station]:
    """Read one of the repository's CDO-style station lists."""
    stations: list[Station] = []
    with path.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            station_id = row["id"].removeprefix("GHCND:")
            stations.append(
                Station(
                    station_id=station_id,
                    name=row.get("name", ""),
                    latitude=row.get("latitude", ""),
                    longitude=row.get("longitude", ""),
                    elevation=row.get("elevation", ""),
                    first_year=row.get("mindate", "")[:4],
                    last_year=row.get("maxdate", "")[:4],
                )
            )
    return stations


def write_catalog(path: Path, stations: Iterable[Station]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            ["station_id", "station_name", "latitude", "longitude", "elevation_m", "tmax_first_year", "tmax_last_year"]
        )
        for station in stations:
            writer.writerow(
                [station.station_id, station.name, station.latitude, station.longitude, station.elevation, station.first_year, station.last_year]
            )


def archive_url(station: Station) -> str:
    return f"{BY_STATION_URL}/{station.station_id}.csv.gz"


def download_station(station: Station, archives_dir: Path, refresh: bool) -> Path:
    return download(archive_url(station), archives_dir / f"{station.station_id}.csv.gz", refresh)


def iter_raw_rows(archive: Path):
    with gzip.open(archive, "rt", newline="", encoding="ascii") as source:
        yield from csv.reader(source)


def write_station_tmax(archive: Path, destination: Path, station: Station) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with partial.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(TMAX_COLUMNS)
        for row in iter_raw_rows(archive):
            if len(row) < 7 or row[2] != "TMAX" or row[3] == "-9999":
                continue
            value_c = int(row[3]) / 10
            writer.writerow(
                [
                    station.station_id,
                    station.name,
                    station.latitude,
                    station.longitude,
                    station.elevation,
                    f"{row[1][0:4]}-{row[1][4:6]}-{row[1][6:8]}",
                    f"{value_c:.1f}",
                    f"{value_c * 9 / 5 + 32:.1f}",
                    row[4],
                    row[5],
                    row[6],
                    row[7] if len(row) > 7 else "",
                ]
            )
    partial.replace(destination)


def make_station_csvs(stations: list[Station], archives_dir: Path, tmax_dir: Path, refresh: bool) -> None:
    for index, station in enumerate(stations, 1):
        output = tmax_dir / f"{station.station_id}.csv"
        archive = archives_dir / f"{station.station_id}.csv.gz"
        if refresh or not output.exists():
            write_station_tmax(archive, output, station)
        if index % 50 == 0 or index == len(stations):
            print(f"Prepared {index}/{len(stations)} station TMAX files")


def summarize(stations: list[Station], tmax_dir: Path, output_dir: Path, months: set[int]) -> int:
    # date -> [max Celsius, winners, number of accepted station readings]
    days: dict[str, list] = {}
    accepted = flagged = 0
    for station in stations:
        with (tmax_dir / f"{station.station_id}.csv").open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if int(row["date"][5:7]) not in months:
                    continue
                if row["quality_flag"].strip():
                    flagged += 1
                    continue
                accepted += 1
                date = row["date"]
                value = float(row["tmax_c"])
                winner = (station.station_id, station.name)
                if date not in days:
                    days[date] = [value, [winner], 1]
                else:
                    days[date][2] += 1
                    if value > days[date][0]:
                        days[date][0] = value
                        days[date][1] = [winner]
                    elif value == days[date][0]:
                        days[date][1].append(winner)

    output_dir.mkdir(parents=True, exist_ok=True)
    daily_path = output_dir / "montana_statewide_daily_tmax.csv"
    columns = [
        "date",
        "tmax_c",
        "tmax_f",
        "stations_reporting_tmax",
        "winning_station_count",
        "station_ids",
        "station_names",
    ]

    def summary_row(date: str):
        value, winners, reporting = days[date]
        return [
            date,
            f"{value:.1f}",
            f"{value * 9 / 5 + 32:.1f}",
            reporting,
            len(winners),
            "|".join(item[0] for item in winners),
            "|".join(item[1] for item in winners),
        ]

    with daily_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(columns)
        for date in sorted(days):
            writer.writerow(summary_row(date))

    ranked_path = output_dir / "montana_statewide_daily_tmax_ranked.csv"
    with ranked_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(columns)
        for date in sorted(days, key=lambda item: (-days[item][0], item)):
            writer.writerow(summary_row(date))

    print(f"Wrote {len(days):,} daily maxima from {accepted:,} accepted TMAX observations")
    print(f"Excluded {flagged:,} observations with nonblank NOAA quality flags")
    if days:
        hottest = min(days, key=lambda item: (-days[item][0], item))
        print(f"Highest value: {days[hottest][0] * 9 / 5 + 32:.1f} F on {hottest}")
        print(f"Latest available date: {max(days)}")
    return len(days)


def parse_months(value: str) -> set[int]:
    try:
        if "-" in value and "," not in value:
            first, last = (int(part) for part in value.split("-", 1))
            months = set(range(first, last + 1))
        else:
            months = {int(part) for part in value.split(",")}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("months must look like 1-12 or 3,4,5,6,7,8,9,10") from exc
    if not months or min(months) < 1 or max(months) > 12:
        raise argparse.ArgumentTypeError("months must be between 1 and 12")
    return months


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--station-source",
        choices=("noaa", "all-csv", "airports"),
        default="noaa",
        help="NOAA's complete historical MT/TMAX catalog (default), stations_2026_sorted.csv, or airport-list.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("weather-data"))
    parser.add_argument("--months", type=parse_months, default=set(range(1, 13)), help="months to include in summaries; default 1-12")
    parser.add_argument("--workers", type=int, default=4, help="simultaneous downloads; default 4")
    parser.add_argument("--refresh", action="store_true", help="redownload archives and rebuild station CSVs")
    parser.add_argument("--limit", type=int, help="process only the first N stations (for testing)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

    metadata_dir = args.output_dir / "metadata"
    archives_dir = args.output_dir / "raw-station-archives"
    tmax_dir = args.output_dir / "station-tmax"

    if args.station_source == "noaa":
        print("Fetching NOAA station metadata and TMAX inventory...")
        stations_file = download(STATIONS_URL, metadata_dir / "ghcnd-stations.txt", args.refresh)
        inventory_file = download(INVENTORY_URL, metadata_dir / "ghcnd-inventory.txt", args.refresh)
        stations = parse_noaa_catalog(stations_file, inventory_file)
    else:
        source = Path("stations_2026_sorted.csv" if args.station_source == "all-csv" else "airport-list.csv")
        stations = parse_station_csv(source)

    if args.limit:
        stations = stations[: args.limit]
    if not stations:
        raise SystemExit("No stations selected")
    write_catalog(metadata_dir / "selected_stations.csv", stations)
    print(f"Selected {len(stations)} stations; downloading full-period station archives")

    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_station, station, archives_dir, args.refresh): station for station in stations}
        completed = 0
        for future in as_completed(futures):
            station = futures[future]
            try:
                future.result()
            except Exception as exc:  # continue so the failure report is useful
                failures.append((station.station_id, str(exc)))
            completed += 1
            if completed % 25 == 0 or completed == len(stations):
                print(f"Downloaded/verified {completed}/{len(stations)} station archives")

    if failures:
        failure_path = args.output_dir / "download_failures.csv"
        with failure_path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(["station_id", "error"])
            writer.writerows(failures)
        print(f"ERROR: {len(failures)} downloads failed; see {failure_path}", file=sys.stderr)
        return 1

    (args.output_dir / "download_failures.csv").unlink(missing_ok=True)
    make_station_csvs(stations, archives_dir, tmax_dir, args.refresh)
    summarize(stations, tmax_dir, args.output_dir, args.months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
