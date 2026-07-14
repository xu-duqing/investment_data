import argparse
import multiprocessing as mp
import sys
from pathlib import Path

import pandas as pd

from mysql_utils import fetch_mysql_dicts, sql_literal

CALENDAR_START_DATE = pd.Timestamp("2000-01-04")
CALENDAR_EXCHANGE = "SSE"

try:
    from data_collector.base import Normalize
    from data_collector.yahoo import collector as yahoo_collector
except ImportError as exc:
    print("============")
    print("ATTENTION: add the qlib repository and qlib/scripts directory to PYTHONPATH")
    print("============")
    raise exc


class CrowdSourceNormalize(yahoo_collector.YahooNormalizeCN1d):
    COLUMNS = ["open", "close", "high", "low", "vwap", "volume"]
    CALENDAR_LIST = None

    def _get_calendar_list(self):
        if self.CALENDAR_LIST is not None:
            return self.CALENDAR_LIST
        return super()._get_calendar_list()

    def _manual_adj_data(self, df: pd.DataFrame) -> pd.DataFrame:
        result_df = super()._manual_adj_data(df)
        if "amount" in df.columns:
            result_df["amount"] = df["amount"]
        return result_df


class CrowdSourceNormalizeRunner(Normalize):
    def format_data(self, df: pd.DataFrame):
        if self.interval == "1d":
            try:
                pd.to_datetime(df.iloc[-1][self._date_field_name], format="%Y-%m-%d", errors="raise")
            except Exception:
                df = df.iloc[:-1]
        return df


def load_trade_calendar_list(exchange: str = CALENDAR_EXCHANGE):
    rows = fetch_mysql_dicts(
        f"""
        SELECT DATE_FORMAT(cal_date, '%Y-%m-%d') AS cal_date
        FROM trade_calendar
        WHERE exchange = {sql_literal(exchange)}
          AND is_open = 1
          AND cal_date >= {sql_literal(CALENDAR_START_DATE.date())}
          AND cal_date <= CURRENT_DATE()
        ORDER BY cal_date
        """
    )
    calendar = pd.to_datetime([row["cal_date"] for row in rows]).normalize().tolist()
    if not calendar:
        raise ValueError(
            f"No open trade dates found for exchange={exchange} on or after {CALENDAR_START_DATE.date()}"
        )
    return calendar


def normalize_crowd_source_data(
    source_dir,
    normalize_dir,
    max_workers=1,
    date_field_name="tradedate",
    symbol_field_name="symbol",
    exchange=CALENDAR_EXCHANGE,
):
    mp.set_start_method("spawn", force=True)
    CrowdSourceNormalize.CALENDAR_LIST = load_trade_calendar_list(exchange=exchange)
    normalizer = CrowdSourceNormalizeRunner(
        source_dir=source_dir,
        target_dir=normalize_dir,
        normalize_class=CrowdSourceNormalize,
        max_workers=max_workers,
        date_field_name=date_field_name,
        symbol_field_name=symbol_field_name,
    )
    normalizer.normalize()


def parse_args():
    args = sys.argv[1:]
    if args and args[0] == "normalize_data":
        args = args[1:]

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", "--source_dir", required=True)
    parser.add_argument("--normalize-dir", "--normalize_dir", required=True)
    parser.add_argument("--max-workers", "--max_workers", type=int, default=1)
    parser.add_argument("--date-field-name", "--date_field_name", default="tradedate")
    parser.add_argument("--symbol-field-name", "--symbol_field_name", default="symbol")
    parser.add_argument("--exchange", default=CALENDAR_EXCHANGE)
    parsed = parser.parse_args(args)
    parsed.source_dir = str(Path(parsed.source_dir).expanduser())
    parsed.normalize_dir = str(Path(parsed.normalize_dir).expanduser())
    return parsed


def main():
    args = parse_args()
    normalize_crowd_source_data(
        source_dir=args.source_dir,
        normalize_dir=args.normalize_dir,
        max_workers=args.max_workers,
        date_field_name=args.date_field_name,
        symbol_field_name=args.symbol_field_name,
        exchange=args.exchange,
    )


if __name__ == "__main__":
    main()
