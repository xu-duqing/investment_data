import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tushare" / "sync_daily_basic.py"
SPEC = importlib.util.spec_from_file_location("sync_daily_basic", MODULE_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class SyncDailyBasicTest(unittest.TestCase):
    def test_fetch_daily_basic_posts_expected_request(self):
        fields = list(MODULE.TUSHARE_FIELDS)
        item = ["600000.SH", "20260720", *range(1, len(fields) - 1)]
        response = FakeResponse({"code": 0, "msg": None, "data": {"fields": fields, "items": [item]}})

        with mock.patch.object(MODULE.requests, "post", return_value=response) as post:
            rows = MODULE.fetch_daily_basic(dt.date(2026, 7, 20), "secret", timeout=12)

        self.assertEqual(rows[0]["ts_code"], "600000.SH")
        self.assertEqual(rows[0]["circ_mv"], len(fields) - 2)
        post.assert_called_once_with(
            MODULE.TUSHARE_API_URL,
            json={
                "api_name": "daily_basic",
                "token": "secret",
                "params": {"trade_date": "20260720"},
                "fields": ",".join(fields),
            },
            timeout=12,
        )

    def test_fetch_daily_basic_rejects_api_error(self):
        response = FakeResponse({"code": -2001, "msg": "permission denied", "data": None})
        with mock.patch.object(MODULE.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                MODULE.fetch_daily_basic(dt.date(2026, 7, 20), "secret")

    def test_map_rows_adds_stock_codes_and_skips_unknown_symbols(self):
        source = [
            {
                "ts_code": "600000.SH",
                "trade_date": "20260720",
                "close": 10.5,
                **{field: None for field in MODULE.TUSHARE_FIELDS[3:]},
            },
            {
                "ts_code": "999999.SH",
                "trade_date": "20260720",
                **{field: None for field in MODULE.TUSHARE_FIELDS[2:]},
            },
        ]
        stocks = {
            "600000.SH": {
                "qlib_code": "SH600000",
                "stock_code": "600000",
            }
        }

        values, missing = MODULE.map_rows(source, stocks)

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0][:4], ("600000.SH", "SH600000", "600000", dt.date(2026, 7, 20)))
        self.assertEqual(values[0][4], 10.5)
        self.assertEqual(missing, ["999999.SH"])

    def test_latest_open_trade_date_includes_requested_date(self):
        with mock.patch.object(MODULE, "fetch_one_value", return_value="2026-07-20") as fetch:
            result = MODULE.latest_open_trade_date("SSE", dt.date(2026, 7, 21))

        self.assertEqual(result, dt.date(2026, 7, 20))
        self.assertEqual(fetch.call_args.kwargs["params"], ("SSE", dt.date(2026, 7, 21)))
        self.assertIn("cal_date <= %s", fetch.call_args.args[0])

    def test_backfill_skips_existing_dates(self):
        dates = [dt.date(2026, 7, 17), dt.date(2026, 7, 20)]
        with (
            mock.patch.object(MODULE, "open_trade_dates", return_value=dates),
            mock.patch.object(MODULE, "existing_trade_dates", return_value={dates[0]}),
            mock.patch.object(MODULE, "sync_daily_basic", return_value=12) as sync,
        ):
            count, rows = MODULE.backfill_daily_basic(
                dates[0], dates[1], "SSE", 1000, 60, 0, True
            )

        self.assertEqual((count, rows), (1, 12))
        sync.assert_called_once_with(dates[1], "SSE", 1000, 60)


if __name__ == "__main__":
    unittest.main()
