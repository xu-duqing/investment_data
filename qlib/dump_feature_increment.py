import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_increment_manifest import (
    FEATURES,
    build_manifest,
    read_calendar,
    validate_bin_file,
    write_checksums,
    write_json_atomic,
)


def parse_numeric(value: str) -> float:
    if value == "" or value.upper() == "NULL":
        return math.nan
    return float(value)


def read_source_csv(path: Path) -> tuple[str, Dict[str, Dict[str, float]]]:
    with path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        expected = {"symbol", "tradedate", *FEATURES}
        if set(reader.fieldnames or []) != expected:
            raise ValueError(f"unexpected CSV columns in {path}: {reader.fieldnames}")
        symbol: str | None = None
        rows: Dict[str, Dict[str, float]] = {}
        for row in reader:
            current_symbol = row["symbol"].strip().upper()
            if not current_symbol:
                raise ValueError(f"empty symbol in {path}")
            if symbol is None:
                symbol = current_symbol
            elif current_symbol != symbol:
                raise ValueError(f"multiple symbols in {path}")
            trade_date = row["tradedate"]
            if trade_date in rows:
                raise ValueError(f"duplicate date {trade_date} in {path}")
            rows[trade_date] = {field: parse_numeric(row[field]) for field in FEATURES}
    if not symbol or not rows:
        raise ValueError(f"empty source CSV: {path}")
    return symbol, rows


def write_feature_binary(path: Path, start_index: int, values: Sequence[float], calendar_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    np.hstack(([start_index], np.asarray(values, dtype="<f4"))).astype("<f4").tofile(temporary)
    temporary.replace(path)
    validate_bin_file(path, calendar_count)


def dump_source_file(source_path: Path, package_root: Path, calendar: Sequence[str]) -> tuple[str, List[Path], int]:
    symbol, rows = read_source_csv(source_path)
    calendar_index = {trade_date: index for index, trade_date in enumerate(calendar)}
    unknown_dates = sorted(set(rows) - set(calendar_index))
    if unknown_dates:
        raise ValueError(f"source dates are outside base calendar for {symbol}: {unknown_dates[:5]}")
    first_index = min(calendar_index[trade_date] for trade_date in rows)
    last_index = max(calendar_index[trade_date] for trade_date in rows)
    date_range = calendar[first_index : last_index + 1]
    output_paths: List[Path] = []
    for field in FEATURES:
        values = [rows.get(trade_date, {}).get(field, math.nan) for trade_date in date_range]
        output_path = package_root / "features" / symbol.lower() / f"{field}.day.bin"
        write_feature_binary(output_path, first_index, values, len(calendar))
        output_paths.append(output_path)
    return symbol, output_paths, len(rows)


def load_base_instruments(base_provider: Path) -> set[str]:
    result = {path.name.lower() for path in (base_provider / "features").iterdir() if path.is_dir()}
    instrument_file = base_provider / "instruments" / "all.txt"
    if instrument_file.is_file():
        for line in instrument_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.add(line.split()[0].lower())
    return result


def build_increment(
    source_dir: Path,
    base_provider: Path,
    package_root: Path,
    compatible_base_release: str | None = None,
    max_unmapped: int = 0,
) -> Mapping[str, object]:
    calendar_path = base_provider / "calendars" / "day.txt"
    calendar = read_calendar(calendar_path)
    if not (base_provider / "features").is_dir() or not (base_provider / "instruments").is_dir():
        raise ValueError(f"invalid base provider: {base_provider}")
    source_files = sorted(source_dir.glob("*.csv"))
    if not source_files:
        raise ValueError(f"no source CSV files found: {source_dir}")

    base_instruments = load_base_instruments(base_provider)
    package_root.mkdir(parents=True, exist_ok=True)
    all_feature_paths: List[Path] = []
    skipped: List[str] = []
    row_count = 0
    source_dates: List[str] = []
    for source_path in source_files:
        symbol, rows = read_source_csv(source_path)
        if symbol.lower() not in base_instruments:
            skipped.append(symbol)
            continue
        unknown_dates = sorted(set(rows) - set(calendar))
        if unknown_dates:
            raise ValueError(f"source dates are outside base calendar for {symbol}: {unknown_dates[:5]}")
        _, feature_paths, symbol_rows = dump_source_file(source_path, package_root, calendar)
        all_feature_paths.extend(feature_paths)
        row_count += symbol_rows
        source_dates.extend(rows)

    if len(skipped) > max_unmapped:
        raise ValueError(f"{len(skipped)} source instruments are absent from base provider: {skipped[:10]}")
    if not all_feature_paths:
        raise ValueError("no feature files were generated")

    reports_dir = package_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "source_file_count": len(source_files),
        "instrument_count": len(all_feature_paths) // len(FEATURES),
        "row_count": row_count,
        "skipped_instruments": skipped,
    }
    write_json_atomic(reports_dir / "build_summary.json", report)
    manifest = build_manifest(
        calendar_path,
        all_feature_paths,
        package_root,
        min(source_dates),
        max(source_dates),
        compatible_base_release,
    )
    write_json_atomic(package_root / "manifest.json", manifest)
    checksum_paths = [path.relative_to(package_root).as_posix() for path in all_feature_paths]
    checksum_paths.extend(["manifest.json", "reports/build_summary.json"])
    write_checksums(package_root, checksum_paths, package_root / "checksums.sha256")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Qlib daily_basic feature increment package directory")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--base-provider", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--compatible-base-release")
    parser.add_argument("--max-unmapped", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_increment(
        args.source_dir.expanduser().resolve(),
        args.base_provider.expanduser().resolve(),
        args.package_root.expanduser().resolve(),
        args.compatible_base_release,
        args.max_unmapped,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
