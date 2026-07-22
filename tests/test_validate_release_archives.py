import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


module = load_qlib_module("validate_release_archives")


class ValidateReleaseArchivesTest(unittest.TestCase):
    def test_matching_calendars_pass_and_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            calendar = b"2020-01-02\n2020-01-03\n"
            main = root / "main.tar.gz"
            with tarfile.open(main, "w:gz") as tar:
                info = tarfile.TarInfo("qlib_bin/calendars/day.txt")
                info.size = len(calendar)
                tar.addfile(info, io.BytesIO(calendar))
            digest = module.hashlib.sha256(calendar).hexdigest()
            increment = root / "increment.tar.gz"
            manifest_data = {
                "schema_version": 1,
                "package_type": "qlib_feature_increment",
                "dataset": "daily_basic",
                "frequency": "day",
                "features": list(load_qlib_module("feature_increment_manifest").FEATURES),
                "files": [],
                "file_count": 0,
                "instrument_count": 0,
                "base_calendar_sha256": digest,
            }
            manifest = json.dumps(manifest_data).encode()
            with tarfile.open(increment, "w:gz") as tar:
                info = tarfile.TarInfo("daily_basic/manifest.json")
                info.size = len(manifest)
                tar.addfile(info, io.BytesIO(manifest))

            self.assertEqual(module.validate_release_archives(main, increment), digest)
            manifest_data["base_calendar_sha256"] = "0" * 64
            bad = json.dumps(manifest_data).encode()
            with tarfile.open(increment, "w:gz") as tar:
                info = tarfile.TarInfo("daily_basic/manifest.json")
                info.size = len(bad)
                tar.addfile(info, io.BytesIO(bad))
            with self.assertRaisesRegex(ValueError, "calendar mismatch"):
                module.validate_release_archives(main, increment)


if __name__ == "__main__":
    unittest.main()
