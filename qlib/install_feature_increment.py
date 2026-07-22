import argparse
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Sequence

try:
    from .feature_increment_manifest import (
        DATASET,
        FORBIDDEN_FEATURES,
        read_calendar,
        sha256_file,
        validate_bin_file,
        validate_manifest,
        verify_checksums,
        write_json_atomic,
    )
except ImportError:
    from feature_increment_manifest import (
        DATASET,
        FORBIDDEN_FEATURES,
        read_calendar,
        sha256_file,
        validate_bin_file,
        validate_manifest,
        verify_checksums,
        write_json_atomic,
    )


def validate_provider(target: Path) -> Path:
    target = target.resolve()
    calendar_path = target / "calendars" / "day.txt"
    read_calendar(calendar_path)
    if not (target / "features").is_dir() or not (target / "instruments").is_dir():
        raise ValueError(f"invalid Qlib provider: {target}")
    return calendar_path


def safe_target_path(target: Path, relative_path: str) -> Path:
    try:
        from feature_increment_manifest import validate_feature_paths
    except ImportError:
        from .feature_increment_manifest import validate_feature_paths
    validate_feature_paths([relative_path])
    target = target.resolve()
    features_root = target / "features"
    if features_root.is_symlink():
        raise ValueError("target features directory must not be a symlink")
    destination = target / relative_path
    current = destination.parent
    while current != target:
        if current.is_symlink():
            raise ValueError(f"target path contains a symlink: {current}")
        current = current.parent
    resolved_parent = destination.parent.resolve()
    if resolved_parent != features_root and features_root not in resolved_parent.parents:
        raise ValueError(f"target path escapes features directory: {relative_path}")
    return destination


def validate_record_files(target: Path, value: object) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(path, str) for path in value):
        raise ValueError("installed dataset record has invalid files")
    if len(value) != len(set(value)):
        raise ValueError("installed dataset record has duplicate files")
    for relative_path in value:
        safe_target_path(target, relative_path)
    return value


def safe_extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise ValueError("increment archive is empty")
        if len(members) > 100000:
            raise ValueError("increment archive has too many members")
        total_size = 0
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != DATASET:
                raise ValueError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive member: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"unsupported archive member: {member.name}")
            if member.isfile():
                if member.size > 1024 * 1024 * 1024:
                    raise ValueError(f"archive member is too large: {member.name}")
                total_size += member.size
                if total_size > 20 * 1024 * 1024 * 1024:
                    raise ValueError("increment archive expands beyond 20 GiB")
        for member in members:
            output = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read archive member: {member.name}")
            with source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
    package_root = destination / DATASET
    if not package_root.is_dir():
        raise ValueError("archive does not contain daily_basic root")
    return package_root


def load_package(package_root: Path) -> tuple[Dict[str, object], List[str]]:
    verify_checksums(package_root)
    manifest_path = package_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_files = sorted(
        path.relative_to(package_root).as_posix()
        for path in (package_root / "features").rglob("*.bin")
        if path.is_file()
    )
    validate_manifest(manifest, actual_files)
    allowed = {"manifest.json", "checksums.sha256", "reports/build_summary.json", *actual_files}
    package_files = {
        path.relative_to(package_root).as_posix() for path in package_root.rglob("*") if path.is_file()
    }
    if package_files != allowed:
        raise ValueError(f"package contains unexpected paths: {sorted(package_files - allowed)[:10]}")
    return manifest, actual_files


def record_path(target: Path, dataset: str = DATASET) -> Path:
    return target / ".feature_increments" / f"{dataset}.json"


def journal_path(target: Path) -> Path:
    return target / ".feature_increments" / ".install-journal.json"


def load_record(target: Path, dataset: str = DATASET) -> Dict[str, object] | None:
    path = record_path(target, dataset)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def assert_calendar_compatible(manifest: Mapping[str, object], calendar_path: Path) -> int:
    calendar = read_calendar(calendar_path)
    expected = (
        manifest.get("base_calendar_sha256"),
        manifest.get("base_calendar_start"),
        manifest.get("base_calendar_end"),
        manifest.get("base_calendar_count"),
    )
    actual = (sha256_file(calendar_path), calendar[0], calendar[-1], len(calendar))
    if expected != actual:
        raise ValueError(f"base calendar mismatch: expected={expected}, actual={actual}")
    return len(calendar)


def recover(target: Path) -> bool:
    path = journal_path(target)
    if not path.is_file():
        return False
    journal = json.loads(path.read_text(encoding="utf-8"))
    metadata_dir = (target / ".feature_increments").resolve()
    backup_root = Path(journal["backup_root"]).resolve()
    if metadata_dir not in backup_root.parents:
        raise ValueError("install journal backup path is unsafe")
    new_files = validate_record_files(target, journal.get("new_files", []))
    old_files = validate_record_files(target, journal.get("old_files", []))
    for relative_path in new_files:
        safe_target_path(target, relative_path).unlink(missing_ok=True)
    for relative_path in old_files:
        backup = backup_root / relative_path
        destination = safe_target_path(target, relative_path)
        if backup.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
    previous_record = journal.get("previous_record")
    if previous_record is None:
        record_path(target).unlink(missing_ok=True)
    else:
        write_json_atomic(record_path(target), previous_record)
    shutil.rmtree(backup_root, ignore_errors=True)
    path.unlink(missing_ok=True)
    return True


def install_archive(archive: Path, target: Path, replace_same_dataset: bool = False) -> Mapping[str, object]:
    calendar_path = validate_provider(target)
    if journal_path(target).exists():
        raise RuntimeError("unfinished install journal exists; run --recover first")
    with tempfile.TemporaryDirectory(prefix="qlib-increment-") as temporary_name:
        package_root = safe_extract(archive, Path(temporary_name))
        manifest, files = load_package(package_root)
        calendar_count = assert_calendar_compatible(manifest, calendar_path)
        for relative_path in files:
            validate_bin_file(package_root / relative_path, calendar_count)

        previous = load_record(target)
        previous_file_list = validate_record_files(target, previous.get("files", [])) if previous else []
        previous_files = set(previous_file_list)
        if replace_same_dataset and previous is None:
            raise ValueError("no installed daily_basic dataset is available to replace")
        destinations = {path: safe_target_path(target, path) for path in files}
        conflicts = [path for path in files if destinations[path].exists() and path not in previous_files]
        if conflicts:
            raise FileExistsError(f"feature conflicts detected: {conflicts[:10]}")
        if previous and not replace_same_dataset:
            existing = [path for path in files if destinations[path].exists()]
            raise FileExistsError(f"daily_basic is already installed: {existing[:10]}")
        if replace_same_dataset:
            assert previous is not None
            previous_checksums = previous.get("file_sha256")
            if not isinstance(previous_checksums, dict):
                raise ValueError("installed dataset record has invalid checksums")
            modified = [
                item
                for item in previous_file_list
                if safe_target_path(target, item).is_file()
                and sha256_file(safe_target_path(target, item)) != previous_checksums.get(item)
            ]
            if modified:
                raise ValueError(f"installed features were modified; refusing replacement: {modified[:10]}")

        metadata_dir = target / ".feature_increments"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        backup_root = Path(tempfile.mkdtemp(prefix="daily-basic-backup-", dir=metadata_dir))
        old_files = sorted(previous_files) if replace_same_dataset else []
        new_files = sorted(files)
        journal = {
            "dataset": DATASET,
            "backup_root": str(backup_root),
            "old_files": old_files,
            "new_files": new_files,
            "previous_record": previous,
        }
        for relative_path in old_files:
            source = safe_target_path(target, relative_path)
            if source.exists():
                backup = backup_root / relative_path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, backup)
        write_json_atomic(journal_path(target), journal)
        try:
            for relative_path in new_files:
                source = package_root / relative_path
                destination = safe_target_path(target, relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.install.tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
            record = dict(manifest)
            record["files"] = new_files
            record["file_sha256"] = {
                item: sha256_file(safe_target_path(target, item)) for item in new_files
            }
            write_json_atomic(record_path(target), record)
        except BaseException:
            recover(target)
            raise
        journal_path(target).unlink(missing_ok=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        for relative_path in old_files:
            parent = safe_target_path(target, relative_path).parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        return record


def uninstall(target: Path, dataset: str = DATASET) -> int:
    validate_provider(target)
    if journal_path(target).exists():
        raise RuntimeError("unfinished install journal exists; run --recover first")
    record = load_record(target, dataset)
    if record is None:
        raise ValueError(f"dataset is not installed: {dataset}")
    files = validate_record_files(target, record.get("files"))
    checksums = record.get("file_sha256")
    if not isinstance(checksums, dict):
        raise ValueError("installed dataset record is invalid")
    for relative_path in files:
        path = safe_target_path(target, relative_path)
        if path.is_file() and sha256_file(path) != checksums.get(relative_path):
            raise ValueError(f"installed feature was modified; refusing to remove: {relative_path}")
    removed = 0
    for relative_path in files:
        path = safe_target_path(target, relative_path)
        if path.is_file():
            path.unlink()
            removed += 1
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    record_path(target, dataset).unlink()
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install, update, recover, or uninstall a Qlib feature increment")
    parser.add_argument("archive", nargs="?", type=Path)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--replace-same-dataset", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--uninstall", metavar="DATASET")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = args.target_dir.expanduser().resolve()
    operations = sum(bool(value) for value in (args.archive, args.recover, args.uninstall))
    if operations != 1:
        raise SystemExit("provide exactly one archive, --recover, or --uninstall")
    if args.recover:
        print("recovered" if recover(target) else "no recovery needed")
        return
    if args.uninstall:
        print(f"removed {uninstall(target, args.uninstall)} files")
        return
    record = install_archive(args.archive.expanduser().resolve(), target, args.replace_same_dataset)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
