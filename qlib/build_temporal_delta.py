import argparse
import json
import os
import shutil
import struct
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

try:
    from .temporal_delta_manifest import (
        PACKAGE_TYPE,
        SCHEMA_VERSION,
        read_calendar,
        sha256_file,
        validate_manifest,
        write_checksums,
        write_json_atomic,
    )
except ImportError:
    from temporal_delta_manifest import (
        PACKAGE_TYPE,
        SCHEMA_VERSION,
        read_calendar,
        sha256_file,
        validate_manifest,
        write_checksums,
        write_json_atomic,
    )


class DeltaUnavailable(ValueError):
    """The two snapshots cannot be represented as a tail-only temporal delta."""


def feature_files(root: Path) -> dict[str, Path]:
    features = root / "features"
    if not features.is_dir():
        raise ValueError(f"features directory not found: {features}")
    return {
        path.relative_to(root).as_posix(): path
        for path in features.rglob("*.day.bin")
        if path.is_file()
    }


def validate_binary(path: Path, calendar_count: int) -> int:
    size = path.stat().st_size
    if size < 8 or size % 4:
        raise ValueError(f"invalid Qlib binary: {path}")
    first = struct.unpack("<f", path.read_bytes()[:4])[0]
    if not first.is_integer() or first < 0:
        raise ValueError(f"invalid Qlib offset: {path}")
    value_count = size // 4 - 1
    if int(first) + value_count > calendar_count:
        raise ValueError(f"Qlib binary exceeds target calendar: {path}")
    return value_count


def append_payload(source: Path, output, start: int = 0) -> tuple[int, int]:
    offset = output.tell()
    with source.open("rb") as stream:
        stream.seek(start)
        shutil.copyfileobj(stream, output, length=1024 * 1024)
    return offset, output.tell() - offset


def compare_file(old_path: Path, new_path: Path, relative_path: str, payload, old_count: int, new_count: int) -> Mapping[str, object] | None:
    old_size = old_path.stat().st_size
    new_size = new_path.stat().st_size
    validate_binary(old_path, old_count)
    validate_binary(new_path, new_count)
    if new_size < old_size or new_path.read_bytes()[:old_size] != old_path.read_bytes():
        raise DeltaUnavailable(f"historical content changed: {relative_path}")
    if new_size == old_size:
        return None
    append_count = (new_size - old_size) // 4
    payload_offset, payload_size = append_payload(new_path, payload, old_size)
    return {
        "path": relative_path,
        "payload_path": "payloads.bin",
        "payload_offset": payload_offset,
        "payload_size": payload_size,
        "operation": "append",
        "expected_old_size": old_size,
        "expected_old_sha256": sha256_file(old_path),
        "append_value_count": append_count,
        "new_size": new_size,
        "new_sha256": sha256_file(new_path),
    }


def create_record(new_path: Path, relative_path: str, payload, calendar_count: int) -> Mapping[str, object]:
    value_count = validate_binary(new_path, calendar_count)
    payload_offset, payload_size = append_payload(new_path, payload)
    return {
        "path": relative_path,
        "payload_path": "payloads.bin",
        "payload_offset": payload_offset,
        "payload_size": payload_size,
        "operation": "create",
        "expected_old_size": None,
        "expected_old_sha256": None,
        "append_value_count": value_count,
        "new_size": new_path.stat().st_size,
        "new_sha256": sha256_file(new_path),
    }


def build_delta_directory(
    old_root: Path,
    new_root: Path,
    package_root: Path,
    dataset: str,
    base_release_tag: str,
    release_tag: str,
    old_calendar_path: Path | None = None,
    new_calendar_path: Path | None = None,
) -> Mapping[str, object]:
    old_root = old_root.resolve()
    new_root = new_root.resolve()
    if dataset not in {"market", "daily_basic"}:
        raise ValueError(f"unsupported dataset: {dataset}")
    old_calendar_path = (old_calendar_path or old_root / "calendars/day.txt").resolve()
    new_calendar_path = (new_calendar_path or new_root / "calendars/day.txt").resolve()
    before = read_calendar(old_calendar_path)
    after = read_calendar(new_calendar_path)
    if len(after) <= len(before) or after[: len(before)] != before:
        raise DeltaUnavailable("calendar is not a strict tail extension")
    package_root.mkdir(parents=True, exist_ok=False)

    old_files = feature_files(old_root)
    new_files = feature_files(new_root)
    deleted = sorted(set(old_files) - set(new_files))
    if deleted:
        raise DeltaUnavailable(f"feature files were deleted: {deleted[:5]}")
    records = []
    with (package_root / "payloads.bin").open("wb") as payload:
        for relative_path, new_path in sorted(new_files.items()):
            if relative_path in old_files:
                record = compare_file(old_files[relative_path], new_path, relative_path, payload, len(before), len(after))
                if record is not None:
                    records.append(record)
            else:
                records.append(create_record(new_path, relative_path, payload, len(after)))

    if dataset == "market":
        append_path = package_root / "calendars/day.append.txt"
        append_path.parent.mkdir(parents=True)
        append_path.write_text("\n".join(after[len(before) :]) + "\n", encoding="utf-8")
        old_instruments = old_root / "instruments"
        new_instruments = new_root / "instruments"
        if not old_instruments.is_dir() or not new_instruments.is_dir():
            raise ValueError("market snapshots must contain instruments")
        for source in sorted(new_instruments.rglob("*")):
            if source.is_symlink() or (not source.is_dir() and not source.is_file()):
                raise ValueError(f"unsupported instrument member: {source}")
            if source.is_file():
                destination = package_root / source.relative_to(new_root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_type": PACKAGE_TYPE,
        "dataset": dataset,
        "frequency": "day",
        "release_tag": release_tag,
        "base_release_tag": base_release_tag,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calendar_before_sha256": sha256_file(old_calendar_path),
        "calendar_after_sha256": sha256_file(new_calendar_path),
        "calendar_before_count": len(before),
        "calendar_after_count": len(after),
        "calendar_append": after[len(before) :],
        "files": records,
    }
    validate_manifest(manifest, dataset)
    write_json_atomic(package_root / "manifest.json", manifest)
    write_checksums(package_root)
    return manifest


def build_delta_archive(
    old_root: Path,
    new_root: Path,
    output: Path,
    dataset: str,
    base_release_tag: str,
    release_tag: str,
    old_calendar_path: Path | None = None,
    new_calendar_path: Path | None = None,
) -> Mapping[str, object]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    root_name = "market_delta" if dataset == "market" else "daily_basic_delta"
    with tempfile.TemporaryDirectory(prefix="temporal-delta-build-") as name:
        package_root = Path(name) / root_name
        manifest = build_delta_directory(
            old_root, new_root, package_root, dataset, base_release_tag, release_tag,
            old_calendar_path, new_calendar_path,
        )
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        with tarfile.open(temporary, "w:gz") as archive:
            archive.add(package_root, arcname=root_name, recursive=True)
        os.replace(temporary, output)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a strict tail-only Qlib temporal delta archive")
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", choices=("market", "daily_basic"), required=True)
    parser.add_argument("--base-release-tag", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--old-calendar", type=Path)
    parser.add_argument("--new-calendar", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_delta_archive(
        args.old_root, args.new_root, args.output, args.dataset,
        args.base_release_tag, args.release_tag, args.old_calendar, args.new_calendar,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
