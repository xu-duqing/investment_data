import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


module = load_qlib_module("dump_daily_basic_source")


class DumpDailyBasicSourceTest(unittest.TestCase):
    def test_sql_is_parameterized_and_excludes_market_fields(self):
        sql = module.export_symbol_sql("2020-01-01", "2020-01-31")
        self.assertIn("DATE_FORMAT(d.trade_date, '%%Y-%%m-%%d')", sql)
        self.assertIn("d.ts_code = %s", sql)
        self.assertIn("d.trade_date >= %s", sql)
        self.assertNotIn("d.close", sql)
        self.assertNotIn("limit_status", sql)

    def test_export_symbol_passes_parameters_and_blanks_null(self):
        with self.subTest("normalized row"):
            normalized = module.normalized_row({"symbol": "SH600000", "tradedate": "2020-01-02", "pe": "NULL"})
            self.assertEqual(normalized["pe"], "")
        with mock.patch.object(module, "iter_mysql_dicts", return_value=[] ) as iterator:
            with self.assertRaises(Exception):
                # An invalid path proves parameters are prepared before I/O fails.
                module.export_symbol(Path("/not/existing/path"), 1, 1, {"symbol": "SH600000", "ts_code": "600000.SH"}, "2020-01-01", "2020-01-31", False)
            iterator.assert_not_called()

    def test_fetch_symbols_uses_limit_parameter(self):
        with mock.patch.object(module, "fetch_mysql_dicts", return_value=[]) as fetch:
            module.fetch_symbols(2)
        self.assertEqual(fetch.call_args.kwargs["params"], (2,))
        self.assertIn("LIMIT %s", fetch.call_args.args[0])

    def test_export_rejects_source_newer_than_base_calendar(self):
        with mock.patch.object(module, "source_date_bounds", return_value=("2020-01-02", "2020-01-06")):
            with self.assertRaisesRegex(RuntimeError, "after requested/base calendar"):
                module.dump_daily_basic_source(Path("/tmp/not-created"), end_date="2020-01-03")


if __name__ == "__main__":
    unittest.main()
