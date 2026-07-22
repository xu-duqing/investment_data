import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_loader import load_qlib_module


dump_module = load_qlib_module("dump_feature_increment")
installer = load_qlib_module("install_feature_increment")
manifest_module = load_qlib_module("feature_increment_manifest")


class FeatureIncrementInstallerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.provider = self.root / "cn_data"
        (self.provider / "calendars").mkdir(parents=True)
        (self.provider / "instruments").mkdir()
        (self.provider / "features" / "sh600000").mkdir(parents=True)
        (self.provider / "calendars" / "day.txt").write_text(
            "2019-12-30\n2019-12-31\n2020-01-02\n2020-01-03\n"
        )
        (self.provider / "instruments" / "all.txt").write_text("SH600000\t2019-12-30\t2020-01-03\n")
        self.source = self.root / "source"
        self.source.mkdir()
        fields = list(manifest_module.FEATURES)
        (self.source / "SH600000.csv").write_text(
            "symbol,tradedate," + ",".join(fields) + "\n"
            + "SH600000,2020-01-02," + ",".join("1" for _ in fields) + "\n"
        )
        self.archive = self.build_archive("package1")

    def tearDown(self):
        self.temporary.cleanup()

    def build_archive(self, name):
        package = self.root / name / "daily_basic"
        dump_module.build_increment(self.source, self.provider, package)
        archive = self.root / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(package, arcname="daily_basic")
        return archive

    def test_install_update_and_uninstall(self):
        close_path = self.provider / "features" / "sh600000" / "close.day.bin"
        close_path.write_bytes(b"base-price")
        record = installer.install_archive(self.archive, self.provider)
        pe_path = self.provider / "features" / "sh600000" / "pe.day.bin"
        self.assertTrue(pe_path.is_file())
        self.assertEqual(close_path.read_bytes(), b"base-price")
        self.assertEqual(len(record["files"]), len(manifest_module.FEATURES))

        with self.assertRaises(FileExistsError):
            installer.install_archive(self.archive, self.provider)
        installer.install_archive(self.archive, self.provider, replace_same_dataset=True)
        removed = installer.uninstall(self.provider)
        self.assertEqual(removed, len(manifest_module.FEATURES))
        self.assertFalse(pe_path.exists())
        self.assertEqual(close_path.read_bytes(), b"base-price")

    def test_unknown_conflict_has_zero_writes(self):
        conflict = self.provider / "features" / "sh600000" / "pe.day.bin"
        conflict.write_bytes(b"unknown")
        with self.assertRaises(FileExistsError):
            installer.install_archive(self.archive, self.provider)
        self.assertEqual(conflict.read_bytes(), b"unknown")
        self.assertFalse((self.provider / "features" / "sh600000" / "pb.day.bin").exists())

    def test_modified_file_blocks_uninstall(self):
        installer.install_archive(self.archive, self.provider)
        pe_path = self.provider / "features" / "sh600000" / "pe.day.bin"
        pe_path.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "modified"):
            installer.uninstall(self.provider)

    def test_modified_file_blocks_same_dataset_update(self):
        installer.install_archive(self.archive, self.provider)
        pe_path = self.provider / "features" / "sh600000" / "pe.day.bin"
        pe_path.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "modified"):
            installer.install_archive(self.archive, self.provider, replace_same_dataset=True)

    def test_tampered_record_cannot_escape_provider(self):
        installer.install_archive(self.archive, self.provider)
        record_path = installer.record_path(self.provider)
        record = json.loads(record_path.read_text())
        record["files"] = ["../../outside.day.bin"]
        record_path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            installer.uninstall(self.provider)

    def test_symlinked_instrument_directory_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        instrument = self.provider / "features" / "sh600000"
        instrument.rmdir()
        instrument.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            installer.install_archive(self.archive, self.provider)

    def test_calendar_mismatch_is_rejected(self):
        (self.provider / "calendars" / "day.txt").write_text("2020-01-02\n")
        with self.assertRaisesRegex(ValueError, "calendar mismatch"):
            installer.install_archive(self.archive, self.provider)

    def test_safe_extract_rejects_path_traversal_and_symlink(self):
        for member_name, member_type in (("../escape", tarfile.REGTYPE), ("daily_basic/link", tarfile.SYMTYPE)):
            archive = self.root / f"unsafe-{member_type}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo(member_name)
                member.type = member_type
                if member_type == tarfile.SYMTYPE:
                    member.linkname = "/tmp"
                    tar.addfile(member)
                else:
                    payload = b"bad"
                    member.size = len(payload)
                    tar.addfile(member, io.BytesIO(payload))
            with self.assertRaises(ValueError):
                installer.safe_extract(archive, self.root / "extract")

    def test_recover_restores_previous_file_and_record(self):
        installer.install_archive(self.archive, self.provider)
        pe_path = self.provider / "features" / "sh600000" / "pe.day.bin"
        original = pe_path.read_bytes()
        previous = installer.load_record(self.provider)
        backup = self.provider / ".feature_increments" / "backup" / "features" / "sh600000" / "pe.day.bin"
        backup.parent.mkdir(parents=True)
        pe_path.replace(backup)
        pe_path.write_bytes(b"new")
        installer.write_json_atomic(
            installer.journal_path(self.provider),
            {
                "dataset": "daily_basic",
                "backup_root": str(self.provider / ".feature_increments" / "backup"),
                "old_files": ["features/sh600000/pe.day.bin"],
                "new_files": ["features/sh600000/pe.day.bin"],
                "previous_record": previous,
            },
        )
        self.assertTrue(installer.recover(self.provider))
        self.assertEqual(pe_path.read_bytes(), original)
        self.assertEqual(installer.load_record(self.provider), previous)


if __name__ == "__main__":
    unittest.main()
