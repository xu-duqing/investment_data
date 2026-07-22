import json
import tempfile
import unittest
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


manifest_module = load_qlib_module("feature_increment_manifest")
dump_module = load_qlib_module("dump_feature_increment")


class FeatureIncrementCoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.provider = self.root / "cn_data"
        (self.provider / "calendars").mkdir(parents=True)
        (self.provider / "instruments").mkdir()
        (self.provider / "features" / "sh600000").mkdir(parents=True)
        self.calendar = ["2019-12-30", "2019-12-31", "2020-01-02", "2020-01-03"]
        (self.provider / "calendars" / "day.txt").write_text("\n".join(self.calendar) + "\n")
        (self.provider / "instruments" / "all.txt").write_text("SH600000\t2019-12-30\t2020-01-03\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_calendar_rejects_duplicates(self):
        path = self.root / "bad.txt"
        path.write_text("2020-01-02\n2020-01-02\n")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            manifest_module.read_calendar(path)

    def test_write_feature_binary_uses_base_calendar_index(self):
        path = self.root / "pe.day.bin"
        dump_module.write_feature_binary(path, 2, [6.2, 6.3], len(self.calendar))
        values = np.fromfile(path, dtype="<f4")
        np.testing.assert_allclose(values, [2.0, 6.2, 6.3])

    def test_build_increment_aligns_dates_and_writes_manifest(self):
        source = self.root / "source"
        source.mkdir()
        fields = list(manifest_module.FEATURES)
        header = "symbol,tradedate," + ",".join(fields)
        row1 = "SH600000,2020-01-02," + ",".join(str(index + 1) for index in range(len(fields)))
        row2 = "SH600000,2020-01-03," + ",".join("" if index == 0 else str(index + 2) for index in range(len(fields)))
        (source / "SH600000.csv").write_text("\n".join([header, row1, row2]) + "\n")
        package = self.root / "package" / "daily_basic"

        manifest = dump_module.build_increment(source, self.provider, package)

        pe = np.fromfile(package / "features" / "sh600000" / "pe.day.bin", dtype="<f4")
        self.assertEqual(pe[0], 2.0)
        self.assertEqual(manifest["base_calendar_count"], 4)
        self.assertEqual(manifest["file_count"], len(fields))
        self.assertFalse((package / "calendars").exists())
        self.assertFalse((package / "instruments").exists())
        manifest_module.verify_checksums(package)
        stored = json.loads((package / "manifest.json").read_text())
        manifest_module.validate_manifest(stored, stored["files"])

    def test_build_rejects_date_after_calendar(self):
        source = self.root / "source"
        source.mkdir()
        fields = list(manifest_module.FEATURES)
        (source / "SH600000.csv").write_text(
            "symbol,tradedate," + ",".join(fields) + "\n"
            + "SH600000,2020-01-06," + ",".join("1" for _ in fields) + "\n"
        )
        with self.assertRaisesRegex(ValueError, "outside base calendar"):
            dump_module.build_increment(source, self.provider, self.root / "package")

    def test_forbidden_feature_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            manifest_module.validate_feature_paths(["features/sh600000/close.day.bin"])


if __name__ == "__main__":
    unittest.main()
