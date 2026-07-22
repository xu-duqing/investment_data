import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qlib"))

from mysql_utils import fetch_one_value, mysql_connection, mysql_label, require_env


TUSHARE_API_URL = "https://api.tushare.pro"
TUSHARE_FIELDS = [
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
]
DB_COLUMNS = [
    "ts_code",
    "qlib_code",
    "stock_code",
    "trade_date",
    *TUSHARE_FIELDS[2:],
]
UPDATE_COLUMNS = DB_COLUMNS[4:]


def log(message: str) -> None:
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def parse_date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def latest_open_trade_date(exchange: str, before: dt.date) -> dt.date:
    value = fetch_one_value(
        """
        SELECT DATE_FORMAT(MAX(cal_date), '%%Y-%%m-%%d')
        FROM trade_calendar
        WHERE exchange = %s
          AND is_open = 1
          AND cal_date < %s
        """,
        params=(exchange, before),
    )
    if not value or value == "NULL":
        raise RuntimeError(f"no open trade date found for exchange={exchange} before {before}")
    return dt.date.fromisoformat(value)


def open_trade_dates(exchange: str, start_date: dt.date, end_date: dt.date) -> List[dt.date]:
    with mysql_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cal_date
            FROM trade_calendar
            WHERE exchange = %s
              AND is_open = 1
              AND cal_date BETWEEN %s AND %s
            ORDER BY cal_date
            """,
            (exchange, start_date, end_date),
        )
        return [row["cal_date"] for row in cursor.fetchall()]


def existing_trade_dates(start_date: dt.date, end_date: dt.date) -> set[dt.date]:
    with mysql_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT trade_date
            FROM daily_basic
            WHERE trade_date BETWEEN %s AND %s
            """,
            (start_date, end_date),
        )
        return {row["trade_date"] for row in cursor.fetchall()}


def fetch_daily_basic(
    trade_date: dt.date,
    token: str,
    *,
    api_url: str = TUSHARE_API_URL,
    timeout: int = 60,
) -> List[Dict[str, object]]:
    payload = {
        "api_name": "daily_basic",
        "token": token,
        "params": {"trade_date": trade_date.strftime("%Y%m%d")},
        "fields": ",".join(TUSHARE_FIELDS),
    }
    try:
        response = requests.post(api_url, json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Tushare daily_basic request failed for {trade_date}: {exc}") from exc

    if body.get("code") != 0:
        raise RuntimeError(
            f"Tushare daily_basic failed for {trade_date}: code={body.get('code')} msg={body.get('msg')}"
        )

    data = body.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    missing_fields = set(TUSHARE_FIELDS) - set(fields)
    if missing_fields:
        raise RuntimeError(f"Tushare response is missing fields: {', '.join(sorted(missing_fields))}")
    return [dict(zip(fields, item)) for item in items]


def map_rows(
    rows: Iterable[Mapping[str, object]],
    stock_codes: Mapping[str, Mapping[str, str]],
) -> tuple[List[tuple[object, ...]], List[str]]:
    values: List[tuple[object, ...]] = []
    missing_codes: List[str] = []
    for row in rows:
        ts_code = str(row["ts_code"])
        stock = stock_codes.get(ts_code)
        if stock is None:
            missing_codes.append(ts_code)
            continue
        trade_date = dt.datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
        values.append(
            (
                ts_code,
                stock["qlib_code"],
                stock["stock_code"],
                trade_date,
                *(row.get(column) for column in TUSHARE_FIELDS[2:]),
            )
        )
    return values, missing_codes


def load_stock_codes() -> Dict[str, Dict[str, str]]:
    with mysql_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ts_code, qlib_code, stock_code
            FROM stock_basic
            WHERE ts_code <> '' AND qlib_code <> '' AND stock_code <> ''
            """
        )
        return {
            row["ts_code"]: {
                "qlib_code": row["qlib_code"],
                "stock_code": row["stock_code"],
            }
            for row in cursor.fetchall()
        }


def upsert_rows(values: Sequence[tuple[object, ...]], batch_size: int = 1000) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if not values:
        return 0

    placeholders = ", ".join(["%s"] * len(DB_COLUMNS))
    updates = ", ".join(f"{column}=VALUES({column})" for column in UPDATE_COLUMNS)
    sql = f"""
        INSERT INTO daily_basic ({', '.join(DB_COLUMNS)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {updates}
    """

    with mysql_connection(autocommit=False) as connection:
        try:
            with connection.cursor() as cursor:
                for offset in range(0, len(values), batch_size):
                    cursor.executemany(sql, values[offset : offset + batch_size])
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return len(values)


def sync_daily_basic(trade_date: dt.date, exchange: str, batch_size: int, timeout: int) -> int:
    token = require_env("TUSHARE_TOKEN")
    log(f"Fetching Tushare daily_basic for {trade_date}")
    rows = fetch_daily_basic(trade_date, token, timeout=timeout)
    if not rows:
        log(f"No daily_basic rows returned for {trade_date}; nothing written")
        return 0

    stock_codes = load_stock_codes()
    values, missing_codes = map_rows(rows, stock_codes)
    if missing_codes:
        preview = ", ".join(missing_codes[:10])
        suffix = "..." if len(missing_codes) > 10 else ""
        log(f"Skipping {len(missing_codes)} symbols absent from stock_basic: {preview}{suffix}")
    if not values:
        raise RuntimeError("none of the Tushare symbols could be mapped through stock_basic")

    log(f"Upserting {len(values)} rows into daily_basic at MySQL {mysql_label()}")
    count = upsert_rows(values, batch_size=batch_size)
    log(f"Synced {count} daily_basic rows for {trade_date} ({exchange})")
    return count


def backfill_daily_basic(
    start_date: dt.date,
    end_date: dt.date,
    exchange: str,
    batch_size: int,
    timeout: int,
    request_interval: float,
    skip_existing: bool,
) -> tuple[int, int]:
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if request_interval < 0:
        raise ValueError("request_interval must not be negative")

    dates = open_trade_dates(exchange, start_date, end_date)
    if skip_existing:
        existing = existing_trade_dates(start_date, end_date)
        dates = [trade_date for trade_date in dates if trade_date not in existing]
        log(f"Skipping {len(existing)} existing trade dates in requested range")

    log(f"Backfilling {len(dates)} open trade dates from {start_date} to {end_date}")
    total_rows = 0
    for index, trade_date in enumerate(dates, start=1):
        log(f"[{index}/{len(dates)}] Starting {trade_date}")
        total_rows += sync_daily_basic(trade_date, exchange, batch_size, timeout)
        if request_interval and index < len(dates):
            time.sleep(request_interval)
    log(f"Backfill completed: {len(dates)} trade dates, {total_rows} rows")
    return len(dates), total_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Tushare daily_basic data into MySQL")
    parser.add_argument("--trade-date", type=parse_date, help="Trade date (YYYY-MM-DD); defaults to previous open day")
    parser.add_argument("--start-date", type=parse_date, help="Backfill start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=parse_date, help="Backfill end date; defaults to latest open day")
    parser.add_argument("--skip-existing", action="store_true", help="Skip dates already present in daily_basic")
    parser.add_argument("--request-interval", type=float, default=0.15, help="Seconds between backfill API requests")
    parser.add_argument("--exchange", default=os.environ.get("MYSQL_EXCHANGE", "SSE"))
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    args = parse_args()
    if args.trade_date and args.start_date:
        raise SystemExit("--trade-date and --start-date cannot be used together")
    if args.start_date:
        end_date = args.end_date or latest_open_trade_date(args.exchange, dt.date.today() + dt.timedelta(days=1))
        backfill_daily_basic(
            args.start_date,
            end_date,
            args.exchange,
            args.batch_size,
            args.timeout,
            args.request_interval,
            args.skip_existing,
        )
        return
    if args.end_date:
        raise SystemExit("--end-date requires --start-date")
    trade_date = args.trade_date or latest_open_trade_date(args.exchange, dt.date.today())
    sync_daily_basic(trade_date, args.exchange, args.batch_size, args.timeout)


if __name__ == "__main__":
    main()
