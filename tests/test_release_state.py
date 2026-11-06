import json
import struct
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


module = load_qlib_module("release_state")


def write_floats(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack(f"<{len(values)}f", *values))


def make_archives(root: Path, dates, market_values, daily_values):
    market = root / "qlib_bin"
    daily = root / "daily_basic"
    (market / "calendars").mkdir(parents=True)
    (market / "instruments").mkdir()
    (market / "calendars/day.txt").write_text("\n".join(dates) + "\n")
    (market / "instruments/all.txt").write_text("SH600000\t2020-01-02\t2099-12-31\n")
    write_floats(market / "features/sh600000/close.day.bin", market_values)
    write_floats(daily / "features/sh600000/pe.day.bin", daily_values)
    main_archive = root / "qlib_bin.tar.gz"
    daily_archive = root / "daily_basic.tar.gz"
    with tarfile.open(main_archive, "w:gz") as archive:
        archive.add(market, arcname="qlib_bin")
    with tarfile.open(daily_archive, "w:gz") as archive:
        archive.add(daily, arcname="daily_basic")
    return main_archive, daily_archive


class ReleaseStateTest(unittest.TestCase):
    def test_select_previous_release_uses_latest_earlier_release(self):
        releases = [
            {"tagName": "2026-11-02", "isDraft": False, "isPrerelease": False},
            {"tagName": "2026-10-30", "isDraft": False, "isPrerelease": False},
            {"tagName": "2026-10-31", "isDraft": True, "isPrerelease": False},
            {"tagName": "2026-11-01", "isDraft": False, "isPrerelease": False, "assets": [{"name": "qlib_bin.tar.gz"}]},
            {"tagName": "latest", "isDraft": False, "isPrerelease": False},
            {"tagName": "2026-10-29", "isDraft": False, "isPrerelease": False},
        ]
        self.assertEqual(module.select_previous_release(releases, "2026-11-02"), "2026-10-30")

    def test_first_release_has_no_delta_then_next_release_builds_pair(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first_main, first_daily = make_archives(root / "first", ["2020-01-02"], [0, 10], [0, 20])
            state = root / "state"
            output = root / "output"
            output.mkdir()
            result = module.prepare_release(state, first_main, first_daily, output, "2020-01-02")
            self.assertFalse(result["available"])
            module.advance_state(state, first_main, first_daily, "2020-01-02")
            self.assertEqual(json.loads((state / "manifest.json").read_text())["release_tag"], "2020-01-02")

            second_main, second_daily = make_archives(
                root / "second", ["2020-01-02", "2020-01-03"], [0, 10, 11], [0, 20, 21]
            )
            result = module.prepare_release(state, second_main, second_daily, output, "2020-01-03")
            self.assertTrue(result["available"])
            self.assertTrue((output / "qlib_bin_delta.tar.gz").is_file())
            self.assertTrue((output / "daily_basic_qlib_features_delta.tar.gz").is_file())

    def test_historical_change_disables_both_deltas(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first_main, first_daily = make_archives(root / "first", ["2020-01-02"], [0, 10], [0, 20])
            state = root / "state"
            module.advance_state(state, first_main, first_daily, "old")
            new_main, new_daily = make_archives(
                root / "new", ["2020-01-02", "2020-01-03"], [0, 99, 11], [0, 20, 21]
            )
            output = root / "output"
            output.mkdir()
            result = module.prepare_release(state, new_main, new_daily, output, "new")
            self.assertFalse(result["available"])
            self.assertFalse((output / "qlib_bin_delta.tar.gz").exists())
            self.assertFalse((output / "daily_basic_qlib_features_delta.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
