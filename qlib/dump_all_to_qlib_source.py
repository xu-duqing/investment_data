import argparse
import csv
import datetime as dt
import os
from pathlib import Path
from typing import Dict, List

from mysql_utils import fetch_mysql_dicts, iter_mysql_dicts, mysql_label, sql_literal


FIELDNAMES = [
    "symbol",
    "tradedate",
    "high",
    "low",
    "open",
    "close",
    "volume",
    "adjclose",
    "amount",
    "vwap",
]


def log(message: str) -> None:
    print(f"[{dt.datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def fetch_symbols() -> List[Dict[str, str]]:
    limit_sql = ""
    symbol_limit = os.environ.get("QLIB_EXPORT_SYMBOL_LIMIT")
    if symbol_limit:
        limit_sql = f"LIMIT {int(symbol_limit)}"

    return fetch_mysql_dicts(
        f"""
        SELECT ts_code, qlib_code AS symbol
        FROM stock_basic
        WHERE qlib_code <> ''
        ORDER BY ts_code
        {limit_sql}
        """
    )


def export_symbol_sql(ts_code: str, symbol: str) -> str:
    where_clauses = [f"d.ts_code = {sql_literal(ts_code)}"]
    start_date = os.environ.get("QLIB_EXPORT_START_DATE")
    end_date = os.environ.get("QLIB_EXPORT_END_DATE")

    if start_date:
        where_clauses.append(f"d.trade_date >= {sql_literal(start_date)}")
    if end_date:
        where_clauses.append(f"d.trade_date <= {sql_literal(end_date)}")

    where_sql = " AND ".join(where_clauses)
    return f"""
    SELECT
        {sql_literal(symbol)} AS symbol,
        DATE_FORMAT(d.trade_date, '%Y-%m-%d') AS tradedate,
        d.high_price AS high,
        d.low_price AS low,
        d.open_price AS open,
        d.close_price AS close,
        d.vol AS volume,
        d.close_price * COALESCE(a.adj_factor, 1) AS adjclose,
        d.amount AS amount,
        CASE WHEN d.vol > 0 THEN d.amount / d.vol * 10 ELSE NULL END AS vwap
    FROM stock_daily d
    LEFT JOIN stock_adj_factor a
      ON a.ts_code = d.ts_code
     AND a.trade_date = d.trade_date
    WHERE {where_sql}
    ORDER BY d.trade_date
    """


def normalized_row(row: Dict[str, str]) -> Dict[str, str]:
    return {field: "" if row.get(field) == "NULL" else row.get(field, "") for field in FIELDNAMES}


def dump_all_to_qlib_source(output_dir: Path, skip_exists: bool = False) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"Reading qlib source rows from MySQL {mysql_label()}")
    log(f"Writing qlib source CSV files to {output_dir}")
    log("Loading stock_basic symbol list")
    symbols = fetch_symbols()
    log(f"Loaded {len(symbols)} symbols")

    row_count = 0
    file_count = 0
    skipped_count = 0

    for index, item in enumerate(symbols, start=1):
        symbol = item["symbol"]
        ts_code = item["ts_code"]
        path = output_dir / f"{symbol}.csv"
        if skip_exists and path.exists():
            skipped_count += 1
            log(f"[{index}/{len(symbols)}] Skipping existing {symbol}: {path}")
            continue

        log(f"[{index}/{len(symbols)}] Exporting {symbol} ({ts_code})")
        symbol_rows = 0
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=FIELDNAMES)
            writer.writeheader()
            for row in iter_mysql_dicts(export_symbol_sql(ts_code, symbol), quick=True):
                writer.writerow(normalized_row(row))
                symbol_rows += 1

        if symbol_rows == 0:
            path.unlink(missing_ok=True)
            log(f"[{index}/{len(symbols)}] No rows for {symbol}; removed empty file")
        else:
            row_count += symbol_rows
            file_count += 1
            log(f"[{index}/{len(symbols)}] Finished {symbol}: {symbol_rows} rows, total {row_count} rows")

    if skipped_count:
        log(f"Skipped {skipped_count} existing symbol files")
    log(f"Exported {row_count} rows across {file_count} files")
    return row_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-exists", action="store_true")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("QLIB_SOURCE_DIR"),
        help="Defaults to QLIB_SOURCE_DIR or ./qlib_source",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script_path = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir) if args.output_dir else script_path / "qlib_source"
    dump_all_to_qlib_source(output_dir=output_dir, skip_exists=args.skip_exists)


if __name__ == "__main__":
    main()
