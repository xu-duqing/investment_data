import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

try:
    from .apply_temporal_delta import load_package, paired_contract, safe_extract
    from .feature_increment_manifest import validate_manifest as validate_feature_manifest
except ImportError:
    from apply_temporal_delta import load_package, paired_contract, safe_extract
    from feature_increment_manifest import validate_manifest as validate_feature_manifest


def hash_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def find_member(tar: tarfile.TarFile, suffix: str) -> tarfile.TarInfo:
    matches = [
        member
        for member in tar.getmembers()
        if member.isfile() and PurePosixPath(member.name).as_posix().endswith(suffix)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {suffix} in archive, found {len(matches)}")
    return matches[0]


def read_json_member(tar: tarfile.TarFile, suffix: str):
    member = find_member(tar, suffix)
    stream = tar.extractfile(member)
    if stream is None:
        raise ValueError(f"unable to read {suffix}")
    with stream:
        return json.load(stream)


def full_archive_calendar_sha256(main_archive: Path) -> str:
    with tarfile.open(main_archive, "r:gz") as tar:
        member = find_member(tar, "/calendars/day.txt")
        stream = tar.extractfile(member)
        if stream is None:
            raise ValueError("unable to read main archive calendar")
        with stream:
            return hash_stream(stream)


def validate_full_archives(main_archive: Path, increment_archive: Path) -> str:
    calendar_sha256 = full_archive_calendar_sha256(main_archive)
    with tarfile.open(increment_archive, "r:gz") as tar:
        manifest = read_json_member(tar, "/manifest.json")
        feature_members = [
            PurePosixPath(item.name).relative_to("daily_basic").as_posix()
            for item in tar.getmembers()
            if item.isfile() and item.name.startswith("daily_basic/features/") and item.name.endswith(".bin")
        ]
    validate_feature_manifest(manifest, sorted(feature_members))
    expected = manifest.get("base_calendar_sha256")
    if expected != calendar_sha256:
        raise ValueError(f"release archive calendar mismatch: main={calendar_sha256}, daily_basic={expected}")
    return calendar_sha256


def validate_release_archives(
    main_archive: Path,
    increment_archive: Path,
    market_delta_archive: Path | None = None,
    daily_basic_delta_archive: Path | None = None,
    release_tag: str | None = None,
) -> str:
    if (market_delta_archive is None) != (daily_basic_delta_archive is None):
        raise ValueError("both temporal delta archives must be provided together")
    calendar_sha256 = validate_full_archives(main_archive, increment_archive)
    if market_delta_archive is None:
        return calendar_sha256
    assert daily_basic_delta_archive is not None
    with tempfile.TemporaryDirectory(prefix="validate-temporal-deltas-") as name:
        root = Path(name)
        market_root = safe_extract(market_delta_archive, root / "market", "market")
        daily_root = safe_extract(daily_basic_delta_archive, root / "daily", "daily_basic")
        market = load_package(market_root, "market")
        daily = load_package(daily_root, "daily_basic")
        paired_contract(market, daily)
        if market["calendar_after_sha256"] != calendar_sha256:
            raise ValueError("market delta target calendar does not match full market archive")
        if release_tag is not None and market["release_tag"] != release_tag:
            raise ValueError(f"delta release tag mismatch: expected={release_tag}, actual={market['release_tag']}")
    return calendar_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate paired full and optional temporal-delta Qlib release archives")
    parser.add_argument("main_archive", type=Path)
    parser.add_argument("increment_archive", type=Path)
    parser.add_argument("market_delta_archive", type=Path, nargs="?")
    parser.add_argument("daily_basic_delta_archive", type=Path, nargs="?")
    parser.add_argument("--release-tag")
    args = parser.parse_args()
    digest = validate_release_archives(
        args.main_archive,
        args.increment_archive,
        args.market_delta_archive,
        args.daily_basic_delta_archive,
        args.release_tag,
    )
    print(f"Release archives share calendar SHA-256: {digest}")


if __name__ == "__main__":
    main()
