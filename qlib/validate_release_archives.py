import argparse
import hashlib
import json
import tarfile
from pathlib import PurePosixPath

from feature_increment_manifest import validate_manifest


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


def validate_release_archives(main_archive, increment_archive) -> str:
    with tarfile.open(main_archive, "r:gz") as tar:
        member = find_member(tar, "/calendars/day.txt")
        stream = tar.extractfile(member)
        if stream is None:
            raise ValueError("unable to read main archive calendar")
        with stream:
            calendar_sha256 = hash_stream(stream)

    with tarfile.open(increment_archive, "r:gz") as tar:
        member = find_member(tar, "/manifest.json")
        stream = tar.extractfile(member)
        if stream is None:
            raise ValueError("unable to read increment manifest")
        with stream:
            manifest = json.load(stream)
        feature_members = [
            PurePosixPath(item.name).relative_to("daily_basic").as_posix()
            for item in tar.getmembers()
            if item.isfile() and item.name.startswith("daily_basic/features/") and item.name.endswith(".bin")
        ]

    validate_manifest(manifest, sorted(feature_members))
    expected = manifest.get("base_calendar_sha256")
    if expected != calendar_sha256:
        raise ValueError(
            f"release archive calendar mismatch: main={calendar_sha256}, daily_basic={expected}"
        )
    return calendar_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate main and daily_basic Qlib release archive compatibility")
    parser.add_argument("main_archive")
    parser.add_argument("increment_archive")
    args = parser.parse_args()
    digest = validate_release_archives(args.main_archive, args.increment_archive)
    print(f"Release archives share calendar SHA-256: {digest}")


if __name__ == "__main__":
    main()
