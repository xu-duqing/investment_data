import argparse
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast

try:
    from .temporal_delta_manifest import (
        read_calendar,
        sha256_file,
        validate_manifest,
        verify_checksums,
        write_json_atomic,
    )
except ImportError:
    from temporal_delta_manifest import (
        read_calendar,
        sha256_file,
        validate_manifest,
        verify_checksums,
        write_json_atomic,
    )


ROOT_NAMES = {"market": "market_delta", "daily_basic": "daily_basic_delta"}
MAX_MEMBERS = 200000
MAX_MEMBER_SIZE = 1024 * 1024 * 1024
MAX_TOTAL_SIZE = 20 * 1024 * 1024 * 1024


def copy_provider(source: Path, target: Path) -> None:
    shutil.copytree(source, target)


def safe_extract(archive_path: Path, destination: Path, dataset: str) -> Path:
    expected_root = ROOT_NAMES[dataset]
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ValueError("delta archive is empty or has too many members")
        seen: set[str] = set()
        total_size = 0
        for member in members:
            path = PurePosixPath(member.name)
            normalized = path.as_posix()
            if normalized in seen:
                raise ValueError(f"duplicate archive member: {normalized}")
            seen.add(normalized)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
                raise ValueError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported archive member: {member.name}")
            if member.isfile():
                if member.size > MAX_MEMBER_SIZE:
                    raise ValueError(f"archive member too large: {member.name}")
                total_size += member.size
                if total_size > MAX_TOTAL_SIZE:
                    raise ValueError("delta archive expands beyond 20 GiB")
        for member in members:
            output = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read archive member: {member.name}")
            with source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream)
    root = destination / expected_root
    verify_checksums(root)
    return root


def load_package(root: Path, dataset: str) -> Mapping[str, object]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("delta manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest, dataset)
    allowed = {"manifest.json", "checksums.sha256"}
    allowed.update(record["payload_path"] for record in manifest["files"])
    if dataset == "market":
        allowed.add("calendars/day.append.txt")
        allowed.update(
            path.relative_to(root).as_posix()
            for path in (root / "instruments").rglob("*")
            if path.is_file()
        )
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != allowed:
        raise ValueError(f"unexpected package paths: {sorted(actual - allowed)[:5]}")
    if manifest["schema_version"] == 2:
        payload_extents: dict[str, int] = {}
        for record in manifest["files"]:
            payload_path = record["payload_path"]
            extent = record["payload_offset"] + record["payload_size"]
            payload_extents[payload_path] = max(payload_extents.get(payload_path, 0), extent)
        for payload_path, extent in payload_extents.items():
            if (root / payload_path).stat().st_size < extent:
                raise ValueError(f"payload range exceeds file: {payload_path}")
    return manifest


def read_payload(package_root: Path, manifest: Mapping[str, object], record: Mapping[str, Any]) -> bytes:
    path = package_root / cast(str, record["payload_path"])
    if manifest["schema_version"] == 1:
        return path.read_bytes()
    offset = cast(int, record["payload_offset"])
    size = cast(int, record["payload_size"])
    with path.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"short payload range: {record['path']}")
    return data


def safe_target_path(target: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe target path: {relative_path}")
    allowed = relative.parts[0] == "features" or relative_path == "calendars/day.txt" or relative.parts[0] == "instruments"
    if not allowed:
        raise ValueError(f"target path is not allowlisted: {relative_path}")
    target = target.resolve()
    destination = target.joinpath(*relative.parts)
    current = destination.parent
    while current != target:
        if current.is_symlink():
            raise ValueError(f"target path contains symlink: {current}")
        current = current.parent
    resolved_parent = destination.parent.resolve()
    if resolved_parent != target and target not in resolved_parent.parents:
        raise ValueError(f"target path escapes provider: {relative_path}")
    return destination


def paired_contract(market: Mapping[str, object], daily: Mapping[str, object]) -> None:
    keys = (
        "base_release_tag", "release_tag", "calendar_before_sha256", "calendar_after_sha256",
        "calendar_before_count", "calendar_after_count", "calendar_append",
    )
    for key in keys:
        if market.get(key) != daily.get(key):
            raise ValueError(f"paired delta {key} mismatch")


def metadata_path(target: Path) -> Path:
    return target / ".investment_data_meta.json"


def journal_path(target: Path) -> Path:
    return target / ".temporal_delta" / "install-journal.json"


def recover(target: Path) -> bool:
    journal_file = journal_path(target)
    if not journal_file.is_file():
        return False
    journal = json.loads(journal_file.read_text(encoding="utf-8"))
    metadata_root = (target / ".temporal_delta").resolve()
    backup_root = Path(journal.get("backup_root", "")).resolve()
    if metadata_root not in backup_root.parents:
        raise ValueError("unsafe journal backup path")
    paths = journal.get("paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError("invalid recovery paths")
    for relative_path in paths:
        destination = safe_target_path(target, relative_path)
        backup = backup_root / relative_path
        existed = (backup_root / f"{relative_path}.existed").is_file()
        if existed:
            if not backup.is_file():
                raise ValueError(f"missing recovery backup: {relative_path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, destination)
        else:
            destination.unlink(missing_ok=True)
    previous_metadata = journal.get("previous_metadata")
    if previous_metadata is None:
        metadata_path(target).unlink(missing_ok=True)
    elif isinstance(previous_metadata, Mapping):
        write_json_atomic(metadata_path(target), previous_metadata)
    else:
        raise ValueError("invalid previous metadata")
    journal_file.unlink()
    shutil.rmtree(backup_root, ignore_errors=True)
    return True


def assert_preconditions(target: Path, market: Mapping[str, object], daily: Mapping[str, object]) -> list[str]:
    calendar_path = target / "calendars/day.txt"
    calendar = read_calendar(calendar_path)
    if len(calendar) != market["calendar_before_count"] or sha256_file(calendar_path) != market["calendar_before_sha256"]:
        raise ValueError("installed calendar does not match delta base")
    metadata = json.loads(metadata_path(target).read_text(encoding="utf-8")) if metadata_path(target).is_file() else {}
    installed_release = metadata.get("paired_release_tag", metadata.get("release_tag"))
    if installed_release != market["base_release_tag"]:
        raise ValueError(f"installed release mismatch: {installed_release!r}")
    paths: list[str] = []
    for manifest in (market, daily):
        for record in cast(list[dict[str, Any]], manifest["files"]):
            relative_path = record["path"]
            path = safe_target_path(target, relative_path)
            if record["operation"] == "append":
                if not path.is_file() or path.stat().st_size != record["expected_old_size"]:
                    raise ValueError(f"old size mismatch: {relative_path}")
                if sha256_file(path) != record["expected_old_sha256"]:
                    raise ValueError(f"old SHA-256 mismatch: {relative_path}")
            elif path.exists():
                raise ValueError(f"create target already exists: {relative_path}")
            paths.append(relative_path)
    paths.append("calendars/day.txt")
    instrument_root = target / "instruments"
    paths.extend(path.relative_to(target).as_posix() for path in instrument_root.rglob("*") if path.is_file())
    return sorted(set(paths))


def apply_paired_archives(market_archive: Path, daily_archive: Path, target: Path) -> Mapping[str, object]:
    target = target.resolve()
    if journal_path(target).exists():
        raise RuntimeError("unfinished temporal delta journal exists; recover first")
    with tempfile.TemporaryDirectory(prefix="temporal-delta-") as temporary_name:
        temporary = Path(temporary_name)
        market_root = safe_extract(market_archive, temporary / "market", "market")
        daily_root = safe_extract(daily_archive, temporary / "daily", "daily_basic")
        market = load_package(market_root, "market")
        daily = load_package(daily_root, "daily_basic")
        paired_contract(market, daily)
        paths = assert_preconditions(target, market, daily)
        paths.extend(
            path.relative_to(market_root).as_posix()
            for path in (market_root / "instruments").rglob("*")
            if path.is_file()
        )
        paths = sorted(set(paths))
        new_calendar = read_calendar(target / "calendars/day.txt") + cast(list[str], market["calendar_append"])
        rendered_calendar = "\n".join(new_calendar) + "\n"
        if __import__("hashlib").sha256(rendered_calendar.encode()).hexdigest() != market["calendar_after_sha256"]:
            raise ValueError("delta target calendar hash mismatch")

        metadata_dir = target / ".temporal_delta"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        backup_root = Path(tempfile.mkdtemp(prefix="backup-", dir=metadata_dir))
        for relative_path in paths:
            source = safe_target_path(target, relative_path)
            if source.is_file():
                backup = backup_root / relative_path
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, backup)
                marker = backup_root / f"{relative_path}.existed"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
        previous_metadata = json.loads(metadata_path(target).read_text(encoding="utf-8")) if metadata_path(target).is_file() else None
        journal = {"backup_root": str(backup_root), "paths": paths, "previous_metadata": previous_metadata}
        write_json_atomic(journal_path(target), journal)
        try:
            for package_root, manifest in ((market_root, market), (daily_root, daily)):
                for record in cast(list[dict[str, Any]], manifest["files"]):
                    destination = safe_target_path(target, record["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    payload = read_payload(package_root, manifest, record)
                    if record["operation"] == "append":
                        with destination.open("ab") as stream:
                            stream.write(payload)
                    else:
                        temporary_file = destination.with_name(f".{destination.name}.tmp")
                        temporary_file.write_bytes(payload)
                        os.replace(temporary_file, destination)
            calendar_path = target / "calendars/day.txt"
            temporary_calendar = calendar_path.with_name(".day.txt.tmp")
            temporary_calendar.write_text(rendered_calendar, encoding="utf-8")
            os.replace(temporary_calendar, calendar_path)
            packaged_instruments = market_root / "instruments"
            target_instruments = target / "instruments"
            replacement = target / ".instruments.tmp"
            if replacement.exists():
                shutil.rmtree(replacement)
            shutil.copytree(packaged_instruments, replacement)
            old_instruments = target / ".instruments.old"
            if old_instruments.exists():
                shutil.rmtree(old_instruments)
            os.replace(target_instruments, old_instruments)
            os.replace(replacement, target_instruments)
            shutil.rmtree(old_instruments)

            for manifest in (market, daily):
                for record in cast(list[dict[str, Any]], manifest["files"]):
                    destination = safe_target_path(target, record["path"])
                    if destination.stat().st_size != record["new_size"] or sha256_file(destination) != record["new_sha256"]:
                        raise ValueError(f"new file verification failed: {record['path']}")
            result = {
                "paired_release_tag": market["release_tag"],
                "release_tag": market["release_tag"],
                "market_release_tag": market["release_tag"],
                "daily_basic_release_tag": daily["release_tag"],
                "calendar_sha256": market["calendar_after_sha256"],
                "update_mode": "temporal_delta",
            }
            write_json_atomic(metadata_path(target), result)
        except BaseException:
            recover(target)
            raise
        journal_path(target).unlink()
        shutil.rmtree(backup_root, ignore_errors=True)
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply paired market and daily_basic temporal deltas")
    parser.add_argument("--market-archive", type=Path)
    parser.add_argument("--daily-basic-archive", type=Path)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--recover", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.recover:
        print("recovered" if recover(args.target_dir) else "no recovery needed")
        return
    if not args.market_archive or not args.daily_basic_archive:
        raise SystemExit("both --market-archive and --daily-basic-archive are required")
    result = apply_paired_archives(args.market_archive, args.daily_basic_archive, args.target_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
