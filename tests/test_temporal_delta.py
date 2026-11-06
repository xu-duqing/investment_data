import json
import math
import struct
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


manifest_module = load_qlib_module("temporal_delta_manifest")
builder = load_qlib_module("build_temporal_delta")
applier = load_qlib_module("apply_temporal_delta")


def write_floats(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack(f"<{len(values)}f", *values))


def make_provider(root: Path, dates, values, instruments="SH600000\t2020-01-02\t2099-12-31\n"):
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("\n".join(dates) + "\n")
    (root / "instruments" / "all.txt").write_text(instruments)
    write_floats(root / "features" / "sh600000" / "close.day.bin", values)


def make_daily(root: Path, values):
    write_floats(root / "features" / "sh600000" / "pe.day.bin", values)


class TemporalDeltaTest(unittest.TestCase):
    def test_build_apply_pair_matches_current(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old_market, new_market = root / "old_market", root / "new_market"
            old_daily, new_daily = root / "old_daily", root / "new_daily"
            dates_old = ["2020-01-02", "2020-01-03"]
            dates_new = dates_old + ["2020-01-06"]
            make_provider(old_market, dates_old, [0, 10, 11])
            make_provider(new_market, dates_new, [0, 10, 11, 12], "SH600000\t2020-01-02\t2020-01-06\n")
            make_daily(old_daily, [0, 20, 21])
            make_daily(new_daily, [0, 20, 21, math.nan])
            write_floats(new_daily / "features" / "sz000001" / "pe.day.bin", [2, 30])

            market_archive = root / "market.tar.gz"
            daily_archive = root / "daily.tar.gz"
            builder.build_delta_archive(old_market, new_market, market_archive, "market", "2020-01-03", "2020-01-06")
            builder.build_delta_archive(
                old_daily,
                new_daily,
                daily_archive,
                "daily_basic",
                "2020-01-03",
                "2020-01-06",
                old_market / "calendars/day.txt",
                new_market / "calendars/day.txt",
            )

            with tarfile.open(market_archive, "r:gz") as archive:
                file_members = [member.name for member in archive.getmembers() if member.isfile()]
                self.assertIn("market_delta/payloads.bin", file_members)
                self.assertFalse(any("features/" in member for member in file_members))
                manifest_stream = archive.extractfile("market_delta/manifest.json")
                if manifest_stream is None:
                    self.fail("manifest missing from archive")
                manifest = json.loads(manifest_stream.read())
                self.assertEqual(manifest["schema_version"], 2)
                self.assertEqual({record["payload_path"] for record in manifest["files"]}, {"payloads.bin"})

            target = root / "target"
            applier.copy_provider(old_market, target)
            for source in (old_daily / "features").rglob("*.bin"):
                destination = target / source.relative_to(old_daily)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            metadata = target / ".investment_data_meta.json"
            metadata.write_text(json.dumps({"release_tag": "2020-01-03"}))

            result = applier.apply_paired_archives(market_archive, daily_archive, target)
            self.assertEqual(result["release_tag"], "2020-01-06")
            self.assertEqual((target / "calendars/day.txt").read_bytes(), (new_market / "calendars/day.txt").read_bytes())
            self.assertEqual((target / "instruments/all.txt").read_bytes(), (new_market / "instruments/all.txt").read_bytes())
            for expected_root in (new_market, new_daily):
                for expected in (expected_root / "features").rglob("*.bin"):
                    actual = target / expected.relative_to(expected_root)
                    self.assertEqual(actual.read_bytes(), expected.read_bytes())

    def test_rejects_historical_rewrite_and_calendar_insertion(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old, new = root / "old", root / "new"
            make_provider(old, ["2020-01-02", "2020-01-03"], [0, 10, 11])
            make_provider(new, ["2020-01-02", "2020-01-03", "2020-01-06"], [0, 99, 11, 12])
            with self.assertRaisesRegex(builder.DeltaUnavailable, "historical content changed"):
                builder.build_delta_directory(old, new, root / "out", "market", "old", "new")

            (new / "calendars/day.txt").write_text("2020-01-02\n2020-01-02\n2020-01-03\n")
            with self.assertRaises(ValueError):
                builder.build_delta_directory(old, new, root / "out2", "market", "old", "new")

    def test_old_hash_mismatch_causes_zero_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            old, new = root / "old", root / "new"
            make_provider(old, ["2020-01-02", "2020-01-03"], [0, 10, 11])
            make_provider(new, ["2020-01-02", "2020-01-03", "2020-01-06"], [0, 10, 11, 12])
            archive = root / "market.tar.gz"
            builder.build_delta_archive(old, new, archive, "market", "old", "new")
            daily_old, daily_new = root / "daily_old", root / "daily_new"
            make_daily(daily_old, [0, 20, 21])
            make_daily(daily_new, [0, 20, 21, 22])
            daily_archive = root / "daily.tar.gz"
            builder.build_delta_archive(
                daily_old, daily_new, daily_archive, "daily_basic", "old", "new",
                old / "calendars/day.txt", new / "calendars/day.txt",
            )
            target = root / "target"
            applier.copy_provider(old, target)
            target_file = target / "features/sh600000/close.day.bin"
            target_file.write_bytes(target_file.read_bytes() + b"bad!")
            before = (target / "calendars/day.txt").read_bytes()
            (target / ".investment_data_meta.json").write_text(json.dumps({"release_tag": "old"}))
            with self.assertRaisesRegex(ValueError, "old (size|SHA-256) mismatch"):
                applier.apply_paired_archives(archive, daily_archive, target)
            self.assertEqual((target / "calendars/day.txt").read_bytes(), before)

    def test_manifest_rejects_traversal_and_duplicate_paths(self):
        valid = {
            "schema_version": 1,
            "package_type": "qlib_temporal_delta",
            "dataset": "market",
            "frequency": "day",
            "release_tag": "new",
            "base_release_tag": "old",
            "calendar_before_sha256": "0" * 64,
            "calendar_after_sha256": "1" * 64,
            "calendar_before_count": 1,
            "calendar_after_count": 2,
            "calendar_append": ["2020-01-03"],
            "files": [],
        }
        manifest_module.validate_manifest(valid)
        record = {
            "path": "features/../escape.day.bin",
            "payload_path": "features/x/close.day.append.bin",
            "operation": "append",
            "expected_old_size": 8,
            "expected_old_sha256": "2" * 64,
            "append_value_count": 1,
            "new_size": 12,
            "new_sha256": "3" * 64,
        }
        valid["files"] = [record]
        with self.assertRaisesRegex(ValueError, "invalid feature path"):
            manifest_module.validate_manifest(valid)

    def test_schema_v2_shared_payload_ranges_and_contracts(self):
        valid = {
            "schema_version": 2, "package_type": "qlib_temporal_delta",
            "dataset": "daily_basic", "frequency": "day",
            "release_tag": "new", "base_release_tag": "old",
            "calendar_before_sha256": "0" * 64, "calendar_after_sha256": "1" * 64,
            "calendar_before_count": 1, "calendar_after_count": 2,
            "calendar_append": ["2020-01-03"],
            "files": [
                {
                    "path": "features/a/x.day.bin", "payload_path": "payloads.bin",
                    "payload_offset": 0, "payload_size": 4, "operation": "append",
                    "expected_old_size": 8, "expected_old_sha256": "2" * 64,
                    "append_value_count": 1, "new_size": 12, "new_sha256": "3" * 64,
                },
                {
                    "path": "features/b/x.day.bin", "payload_path": "payloads.bin",
                    "payload_offset": 4, "payload_size": 8, "operation": "create",
                    "expected_old_size": None, "expected_old_sha256": None,
                    "append_value_count": 1, "new_size": 8, "new_sha256": "4" * 64,
                },
            ],
        }
        manifest_module.validate_manifest(valid)
        valid["files"][1]["payload_offset"] = 2
        with self.assertRaisesRegex(ValueError, "overlapping payload ranges"):
            manifest_module.validate_manifest(valid)
        valid["files"][1]["payload_offset"] = 4
        valid["files"][1]["payload_size"] = 4
        with self.assertRaisesRegex(ValueError, "invalid payload_size"):
            manifest_module.validate_manifest(valid)


if __name__ == "__main__":
    unittest.main()
