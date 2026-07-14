import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qlib"))

from mysql_utils import fetch_mysql_dicts, sql_literal


def dump_calendar_to_qlib_dir(qlib_dir, exchange="SSE"):
    qlib_path = Path(qlib_dir)
    old_days_file = qlib_path / "calendars" / "day.txt"
    old_calendar_df = pd.read_csv(old_days_file, header=None)
    min_date = pd.to_datetime(old_calendar_df.iloc[0][0]).date()

    rows = fetch_mysql_dicts(
        f"""
        SELECT DATE_FORMAT(cal_date, '%Y-%m-%d') AS cal_date
        FROM trade_calendar
        WHERE exchange = {sql_literal(exchange)}
          AND is_open = 1
          AND cal_date >= {sql_literal(min_date)}
        ORDER BY cal_date
        """
    )

    filename = qlib_path / "calendars" / "day_future.txt"
    print("Dumping to file:", filename)
    filename.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row["cal_date"] for row in rows]).to_csv(filename, index=False, header=False, sep="\t")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("qlib_dir")
    parser.add_argument("--exchange", default="SSE")
    return parser.parse_args()


def main():
    args = parse_args()
    dump_calendar_to_qlib_dir(args.qlib_dir, exchange=args.exchange)


if __name__ == "__main__":
    main()
