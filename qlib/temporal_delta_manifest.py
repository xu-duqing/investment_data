import hashlib
import json
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
PACKAGE_TYPE = "qlib_temporal_delta"
FREQUENCY = "day"
DATASETS = frozenset({"market", "daily_basic"})
OPERATIONS = frozenset({"append", "create"})
HEX_DIGITS = frozenset("0123456789abcdef")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_calendar(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"calendar file not found: {path}")
    dates = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not dates:
        raise ValueError(f"calendar is empty: {path}")
    parsed = [datetime.strptime(value, "%Y-%m-%d").date() for value in dates]
    if any(current <= previous for previous, current in zip(parsed, parsed[1:])):
        raise ValueError("calendar must be strictly increasing without duplicates")
    return dates


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX_DIGITS


def validate_relative_path(value: object, prefixes: Sequence[str], suffixes: Sequence[str]) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts:
        raise ValueError(f"invalid feature path: {value}")
    if path.parts[0] not in prefixes or not any(value.endswith(suffix) for suffix in suffixes):
        raise ValueError(f"invalid feature path: {value}")
    return value


def validate_file_record(record: object, dataset: str, schema_version: int) -> None:
    if not isinstance(record, Mapping):
        raise ValueError("manifest file record must be an object")
    path = validate_relative_path(record.get("path"), ("features",), (".day.bin",))
    operation = record.get("operation")
    if operation not in OPERATIONS:
        raise ValueError(f"invalid operation for {path}: {operation!r}")
    if schema_version == 1:
        payload_suffix = ".day.append.bin" if operation == "append" else ".day.bin"
        validate_relative_path(record.get("payload_path"), ("features",), (payload_suffix,))
    else:
        if record.get("payload_path") != "payloads.bin":
            raise ValueError(f"invalid aggregate payload path for {path}")
    for key in ("new_size", "append_value_count"):
        if not isinstance(record.get(key), int) or record[key] < 0:
            raise ValueError(f"invalid {key} for {path}")
    if not is_sha256(record.get("new_sha256")) or record["new_size"] % 4:
        raise ValueError(f"invalid new file contract for {path}")
    if operation == "append":
        if not isinstance(record.get("expected_old_size"), int) or record["expected_old_size"] < 4:
            raise ValueError(f"invalid expected_old_size for {path}")
        if not is_sha256(record.get("expected_old_sha256")):
            raise ValueError(f"invalid expected_old_sha256 for {path}")
        if record["new_size"] != record["expected_old_size"] + record["append_value_count"] * 4:
            raise ValueError(f"invalid append size contract for {path}")
    else:
        if record.get("expected_old_size") is not None or record.get("expected_old_sha256") is not None:
            raise ValueError(f"create operation has an old-file contract: {path}")
        if record["append_value_count"] != record["new_size"] // 4 - 1:
            raise ValueError(f"invalid create value count for {path}")
    if schema_version == 2:
        offset = record.get("payload_offset")
        size = record.get("payload_size")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError(f"invalid payload_offset for {path}")
        expected_size = record["append_value_count"] * 4 if operation == "append" else record["new_size"]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size != expected_size:
            raise ValueError(f"invalid payload_size for {path}")


def validate_manifest(manifest: Mapping[str, object], dataset: str | None = None) -> None:
    schema_version = manifest.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"invalid manifest schema_version: {schema_version!r}")
    assert isinstance(schema_version, int)
    expected = {
        "package_type": PACKAGE_TYPE,
        "frequency": FREQUENCY,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"invalid manifest {key}: {manifest.get(key)!r}")
    actual_dataset = manifest.get("dataset")
    if actual_dataset not in DATASETS or (dataset is not None and actual_dataset != dataset):
        raise ValueError(f"invalid manifest dataset: {actual_dataset!r}")
    assert isinstance(actual_dataset, str)
    for key in ("release_tag", "base_release_tag"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError(f"invalid manifest {key}")
    if manifest["release_tag"] == manifest["base_release_tag"]:
        raise ValueError("base and target release tags must differ")
    for key in ("calendar_before_sha256", "calendar_after_sha256"):
        if not is_sha256(manifest.get(key)):
            raise ValueError(f"invalid manifest {key}")
    before = manifest.get("calendar_before_count")
    after = manifest.get("calendar_after_count")
    appended = manifest.get("calendar_append")
    if not isinstance(before, int) or before < 1 or not isinstance(after, int) or after <= before:
        raise ValueError("invalid calendar counts")
    if not isinstance(appended, list) or len(appended) != after - before:
        raise ValueError("calendar append count mismatch")
    parsed = [datetime.strptime(value, "%Y-%m-%d").date() for value in appended]
    if any(current <= previous for previous, current in zip(parsed, parsed[1:])):
        raise ValueError("calendar append must be strictly increasing")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest files must be a list")
    for record in files:
        validate_file_record(record, actual_dataset, schema_version)
    paths = [record["path"] for record in files]
    payload_paths = [record["payload_path"] for record in files]
    if len(paths) != len(set(paths)) or (schema_version == 1 and len(payload_paths) != len(set(payload_paths))):
        raise ValueError("manifest contains duplicate paths")
    if schema_version == 2:
        ranges: dict[str, list[tuple[int, int]]] = {}
        for record in files:
            start = record["payload_offset"]
            ranges.setdefault(record["payload_path"], []).append((start, start + record["payload_size"]))
        for payload_path, payload_ranges in ranges.items():
            ordered = sorted(payload_ranges)
            if any(current[0] < previous[1] for previous, current in zip(ordered, ordered[1:])):
                raise ValueError(f"overlapping payload ranges: {payload_path}")


def write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_checksums(package_root: Path) -> None:
    checksum_path = package_root / "checksums.sha256"
    paths = sorted(path for path in package_root.rglob("*") if path.is_file() and path != checksum_path)
    checksum_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(package_root).as_posix()}\n" for path in paths),
        encoding="utf-8",
    )


def verify_checksums(package_root: Path) -> None:
    checksum_path = package_root / "checksums.sha256"
    if not checksum_path.is_file():
        raise ValueError("checksums.sha256 is missing")
    checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative_path = line.partition("  ")
        validate_relative_path(relative_path, ("manifest.json", "calendars", "instruments", "features", "payloads.bin"), ("manifest.json", ".txt", ".bin"))
        if not separator or not is_sha256(digest) or relative_path in checksums:
            raise ValueError(f"invalid checksum line: {line!r}")
        checksums[relative_path] = digest
    actual = sorted(
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path != checksum_path
    )
    if sorted(checksums) != actual:
        raise ValueError("checksum inventory does not match package contents")
    for relative_path, expected in checksums.items():
        if sha256_file(package_root / relative_path) != expected:
            raise ValueError(f"checksum mismatch: {relative_path}")
