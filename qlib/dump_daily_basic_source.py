import argparse
import csv
import datetime as dt
import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_increment_manifest import FEATURES
from mysql_utils import fetch_mysql_dicts, fetch_one_value, iter_mysql_dicts, mysql_label


FIELDNAMES = ("symbol", "tradedate", *FEATURES)


def log(message: str) -> None:
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def fetch_symbols(limit: int | None = None) -> List[Dict[str, str]]:
    sql = """
        SELECT ts_code, qlib_code AS symbol
        FROM stock_basic
        WHERE ts_code <> '' AND qlib_code <> ''
        ORDER BY ts_code
    """
    params: Sequence[object] | None = None
    if limit is not None:
        if limit < 1:
            raise ValueError("symbol limit must be at least 1")
        sql += " LIMIT %s"
        params = (limit,)
    symbols = fetch_mysql_dicts(sql, params=params)
    seen: set[str] = set()
    for item in symbols:
        symbol = item["symbol"].upper()
        if not symbol.isalnum() or symbol in seen:
            raise ValueError(f"invalid or duplicate qlib_code: {symbol}")
        seen.add(symbol)
        item["symbol"] = symbol
    return symbols


def source_date_bounds() -> tuple[str, str]:
    minimum = fetch_one_value("SELECT DATE_FORMAT(MIN(trade_date), '%Y-%m-%d') FROM daily_basic")
    maximum = fetch_one_value("SELECT DATE_FORMAT(MAX(trade_date), '%Y-%m-%d') FROM daily_basic")
    if not minimum or minimum == "NULL" or not maximum or maximum == "NULL":
        raise RuntimeError("daily_basic has no source dates")
    return minimum, maximum


def export_symbol_sql(start_date: str | None, end_date: str | None) -> str:
    where_clauses = ["d.ts_code = %s"]
    if start_date:
        where_clauses.append("d.trade_date >= %s")
    if end_date:
        where_clauses.append("d.trade_date <= %s")
    select_fields = ",\n        ".join(f"d.{field}" for field in FEATURES)
    sql = f"""
        SELECT
            %s AS symbol,
            DATE_FORMAT(d.trade_date, '%%Y-%%m-%%d') AS tradedate,
            {select_fields}
        FROM daily_basic d
        WHERE {' AND '.join(where_clauses)}
        ORDER BY d.trade_date
    """
    return sql


def normalized_row(row: Dict[str, str]) -> Dict[str, str]:
    return {field: "" if row.get(field) in {None, "NULL"} else row.get(field, "") for field in FIELDNAMES}


def export_symbol(
    output_dir: Path,
    index: int,
    total: int,
    item: Dict[str, str],
    start_date: str | None,
    end_date: str | None,
    skip_exists: bool,
) -> Tuple[int, str, int, bool]:
    symbol = item["symbol"].upper()
    ts_code = item["ts_code"]
    path = output_dir / f"{symbol}.csv"
    if skip_exists and path.is_file() and path.stat().st_size > 0:
        return index, symbol, 0, True
    sql = export_symbol_sql(start_date, end_date)
    params: List[object] = [symbol, ts_code]
    if start_date:
        params.append(start_date)
    if end_date:
        params.append(end_date)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    count = 0
    try:
        with temporary.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(FIELDNAMES))
            writer.writeheader()
            for row in iter_mysql_dicts(sql, quick=True, params=params):
                writer.writerow(normalized_row(row))
                count += 1
        if count:
            temporary.replace(path)
        else:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return index, symbol, count, False


def dump_daily_basic_source(
    output_dir: Path,
    max_workers: int = 8,
    start_date: str | None = None,
    end_date: str | None = None,
    symbol_limit: int | None = None,
    skip_exists: bool = False,
) -> int:
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_min_date, source_max_date = source_date_bounds()
    if end_date and source_max_date != end_date:
        raise RuntimeError(
            f"daily_basic source max date {source_max_date} does not match requested/base calendar end {end_date}"
        )
    if start_date and start_date > source_max_date:
        raise RuntimeError(f"daily_basic start date {start_date} is after source max date {source_max_date}")
    symbols = fetch_symbols(symbol_limit)
    if not symbols:
        raise RuntimeError("no mapped stock_basic symbols found")
    log(
        f"Exporting daily_basic from MySQL {mysql_label()} for {len(symbols)} symbols "
        f"(source {source_min_date}..{source_max_date})"
    )
    total_rows = 0
    futures: List[Future[Tuple[int, str, int, bool]]] = []
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="daily-basic-export") as executor:
        for index, item in enumerate(symbols, 1):
            futures.append(
                executor.submit(
                    export_symbol,
                    output_dir,
                    index,
                    len(symbols),
                    item,
                    start_date,
                    end_date,
                    skip_exists,
                )
            )
        try:
            for future in as_completed(futures):
                index, symbol, count, skipped = future.result()
                status = "skipped" if skipped else f"{count} rows"
                log(f"[{index}/{len(symbols)}] {symbol}: {status}")
                total_rows += count
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    if total_rows == 0 and not skip_exists:
        raise RuntimeError("daily_basic export produced no rows")
    return total_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MySQL daily_basic to per-symbol CSV files")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("DAILY_BASIC_EXPORT_MAX_WORKERS", "8")))
    parser.add_argument("--start-date", default=os.environ.get("DAILY_BASIC_START_DATE"))
    parser.add_argument("--end-date", default=os.environ.get("DAILY_BASIC_END_DATE"))
    parser.add_argument("--symbol-limit", type=int, default=os.environ.get("DAILY_BASIC_SYMBOL_LIMIT"))
    parser.add_argument("--skip-exists", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    count = dump_daily_basic_source(
        args.output_dir.expanduser().resolve(),
        args.max_workers,
        args.start_date,
        args.end_date,
        args.symbol_limit,
        args.skip_exists,
    )
    log(f"Exported {count} daily_basic rows")


if __name__ == "__main__":
    main()
