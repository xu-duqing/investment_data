import argparse
import csv
import datetime as dt
import os
from pathlib import Path

from mysql_utils import fetch_mysql_dicts, mysql_label, sql_literal


def available_markets():
    rows = fetch_mysql_dicts(
        """
        SELECT DISTINCT market_name
        FROM index_weight
        WHERE market_name <> ''
        ORDER BY market_name
        """
    )
    return [row["market_name"] for row in rows]


def change_dates(market_name):
    rows = fetch_mysql_dicts(
        f"""
        SET SESSION group_concat_max_len = 1048576;
        SELECT DATE_FORMAT(trade_date, '%Y-%m-%d') AS change_date
             , MD5(GROUP_CONCAT(qlib_code ORDER BY qlib_code SEPARATOR ',')) AS signature
        FROM index_weight
        WHERE market_name = {sql_literal(market_name)}
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    dates = []
    previous_signature = None
    for row in rows:
        if previous_signature is None or row["signature"] != previous_signature:
            dates.append(dt.date.fromisoformat(row["change_date"]))
        previous_signature = row["signature"]
    return dates


def constituents_for(market_name, trade_date, start_date, end_date):
    return fetch_mysql_dicts(
        f"""
        SELECT
            qlib_code AS symbol,
            {sql_literal(start_date)} AS start_date,
            {sql_literal(end_date)} AS end_date
        FROM index_weight
        WHERE market_name = {sql_literal(market_name)}
          AND trade_date = {sql_literal(trade_date)}
        ORDER BY qlib_code
        """
    )


def dump_market(output_dir, market_name, final_end_date):
    dates = change_dates(market_name)
    if not dates:
        print(f"No index_weight data found for {market_name}")
        return 0

    path = output_dir / f"{market_name}.txt"
    print("Dumping to file:", path)
    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp, delimiter="\t")
        for idx, start in enumerate(dates):
            end = final_end_date if idx == len(dates) - 1 else dates[idx + 1] - dt.timedelta(days=1)
            rows = constituents_for(
                market_name=market_name,
                trade_date=start.isoformat(),
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            if not rows:
                raise RuntimeError(f"No data for market_name={market_name} trade_date={start.isoformat()}")
            for row in rows:
                writer.writerow([row["symbol"], row["start_date"], row["end_date"]])
                row_count += 1
    return row_count


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("QLIB_INDEX_DIR"),
        help="Defaults to QLIB_INDEX_DIR or ./qlib_index",
    )
    parser.add_argument(
        "--markets",
        default=os.environ.get("QLIB_INDEX_MARKETS"),
        help="Comma-separated market_name list. Defaults to all markets in index_weight.",
    )
    parser.add_argument(
        "--end-date",
        default=os.environ.get("QLIB_INDEX_END_DATE", dt.date.today().isoformat()),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    script_path = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir) if args.output_dir else script_path / "qlib_index"
    output_dir.mkdir(parents=True, exist_ok=True)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()] if args.markets else available_markets()
    final_end_date = dt.date.fromisoformat(args.end_date)

    print(f"Reading index_weight from MySQL {mysql_label()}")
    total_rows = 0
    for market_name in markets:
        total_rows += dump_market(output_dir, market_name, final_end_date)
    print(f"Exported {total_rows} index constituent rows across {len(markets)} markets")


if __name__ == "__main__":
    main()
