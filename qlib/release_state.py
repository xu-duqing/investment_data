import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from .build_temporal_delta import DeltaUnavailable, build_delta_archive
    from .temporal_delta_manifest import read_calendar, sha256_file, write_json_atomic
except ImportError:
    from build_temporal_delta import DeltaUnavailable, build_delta_archive
    from temporal_delta_manifest import read_calendar, sha256_file, write_json_atomic


STATE_MANIFEST = "manifest.json"
MAIN_ASSET_NAME = "qlib_bin.tar.gz"
DAILY_ASSET_NAME = "daily_basic_qlib_features.tar.gz"


def select_previous_release(releases: list[dict[str, object]], target_tag: str) -> str:
    try:
        target_date = datetime.strptime(target_tag, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"target release tag is not YYYY-MM-DD: {target_tag}") from error
    candidates = []
    for release in releases:
        tag = release.get("tagName")
        assets = release.get("assets")
        if release.get("isDraft") or release.get("isPrerelease") or not isinstance(tag, str):
            continue
        if isinstance(assets, list):
            asset_names = {asset.get("name") for asset in assets if isinstance(asset, dict)}
            if {MAIN_ASSET_NAME, DAILY_ASSET_NAME} - asset_names:
                continue
        try:
            tag_date = datetime.strptime(tag, "%Y-%m-%d").date()
        except ValueError:
            continue
        if tag_date < target_date:
            candidates.append((tag_date, tag))
    if not candidates:
        raise ValueError(f"no earlier successful GitHub release found before {target_tag}")
    return max(candidates)[1]


def bootstrap_state_from_github(state_dir: Path, repository: str, target_tag: str) -> dict[str, object]:
    manifest_path = state_dir / STATE_MANIFEST
    if manifest_path.is_file():
        state = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"bootstrapped": False, "reason": "release baseline already exists", "release_tag": state.get("release_tag")}
    result = subprocess.run(
        ["gh", "release", "list", "--repo", repository, "--limit", "100", "--json", "tagName,isDraft,isPrerelease,assets"],
        check=True,
        capture_output=True,
        text=True,
    )
    releases = json.loads(result.stdout)
    if not isinstance(releases, list):
        raise ValueError("GitHub release list response is not an array")
    base_tag = select_previous_release(releases, target_tag)
    with tempfile.TemporaryDirectory(prefix="qlib-release-bootstrap-") as name:
        download_dir = Path(name)
        subprocess.run(
            [
                "gh", "release", "download", base_tag, "--repo", repository,
                "--pattern", MAIN_ASSET_NAME, "--pattern", DAILY_ASSET_NAME,
                "--dir", str(download_dir),
            ],
            check=True,
        )
        main_archive = download_dir / MAIN_ASSET_NAME
        daily_archive = download_dir / DAILY_ASSET_NAME
        if not main_archive.is_file() or not daily_archive.is_file():
            raise ValueError(f"release {base_tag} does not contain both required full archives")
        manifest = advance_state(state_dir, main_archive, daily_archive, base_tag)
    return {"bootstrapped": True, "release_tag": base_tag, "calendar_sha256": manifest["calendar_sha256"]}


def extract_archive(archive: Path, destination: Path, expected_root: str) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members or len(members) > 1_000_000:
            raise ValueError("full archive is empty or has too many members")
        seen: set[str] = set()
        total_size = 0
        for member in members:
            parts = Path(member.name).parts
            normalized = Path(member.name).as_posix()
            if normalized in seen:
                raise ValueError(f"duplicate full archive member: {member.name}")
            seen.add(normalized)
            if not parts or parts[0] != expected_root or ".." in parts or member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsafe full archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported full archive member: {member.name}")
            total_size += member.size
            if total_size > 100 * 1024**3:
                raise ValueError("full archive expands beyond 100 GiB")
        for member in members:
            output = destination.joinpath(*Path(member.name).parts)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read full archive member: {member.name}")
            with source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
    root = destination / expected_root
    if not root.is_dir():
        raise ValueError(f"archive root missing: {expected_root}")
    return root


def prepare_release(
    state_dir: Path,
    main_archive: Path,
    daily_archive: Path,
    output_dir: Path,
    release_tag: str,
) -> dict[str, object]:
    manifest_path = state_dir / STATE_MANIFEST
    if not manifest_path.is_file():
        return {"available": False, "reason": "no successful release baseline"}
    state = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_tag = state.get("release_tag")
    if not isinstance(base_tag, str) or not base_tag:
        raise ValueError("release state has no valid release_tag")
    if base_tag == release_tag:
        return {"available": False, "already_published": True, "reason": "release baseline already matches target"}
    old_market = state_dir / "market_provider"
    old_daily = state_dir / "daily_basic"
    if not old_market.is_dir() or not old_daily.is_dir():
        raise ValueError("release state snapshots are missing")
    with tempfile.TemporaryDirectory(prefix="qlib-release-current-") as name:
        temporary = Path(name)
        new_market = extract_archive(main_archive, temporary / "market", "qlib_bin")
        new_daily = extract_archive(daily_archive, temporary / "daily", "daily_basic")
        market_output = output_dir / "qlib_bin_delta.tar.gz"
        daily_output = output_dir / "daily_basic_qlib_features_delta.tar.gz"
        try:
            build_delta_archive(old_market, new_market, market_output, "market", base_tag, release_tag)
            build_delta_archive(
                old_daily,
                new_daily,
                daily_output,
                "daily_basic",
                base_tag,
                release_tag,
                old_market / "calendars/day.txt",
                new_market / "calendars/day.txt",
            )
        except DeltaUnavailable as error:
            market_output.unlink(missing_ok=True)
            daily_output.unlink(missing_ok=True)
            return {"available": False, "reason": str(error), "base_release_tag": base_tag}
    return {"available": True, "base_release_tag": base_tag, "release_tag": release_tag}


def advance_state(state_dir: Path, main_archive: Path, daily_archive: Path, release_tag: str) -> dict[str, object]:
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{state_dir.name}.", dir=state_dir.parent))
    try:
        market = extract_archive(main_archive, staging, "qlib_bin")
        daily = extract_archive(daily_archive, staging, "daily_basic")
        os.replace(market, staging / "market_provider")
        calendar = read_calendar(staging / "market_provider/calendars/day.txt")
        manifest = {
            "release_tag": release_tag,
            "calendar_sha256": sha256_file(staging / "market_provider/calendars/day.txt"),
            "calendar_start": calendar[0],
            "calendar_end": calendar[-1],
            "calendar_count": len(calendar),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        write_json_atomic(staging / STATE_MANIFEST, manifest)
        old = state_dir.with_name(f".{state_dir.name}.old")
        if old.exists():
            shutil.rmtree(old)
        moved_old = False
        if state_dir.exists():
            os.replace(state_dir, old)
            moved_old = True
        try:
            os.replace(staging, state_dir)
        except BaseException:
            if moved_old and old.exists() and not state_dir.exists():
                os.replace(old, state_dir)
            raise
        shutil.rmtree(old, ignore_errors=True)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare temporal deltas or advance the last-successful Qlib release baseline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-github")
    bootstrap.add_argument("--state-dir", type=Path, required=True)
    bootstrap.add_argument("--repository", required=True)
    bootstrap.add_argument("--target-tag", required=True)
    for command in ("prepare", "advance"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--state-dir", type=Path, required=True)
        subparser.add_argument("--main-archive", type=Path, required=True)
        subparser.add_argument("--daily-basic-archive", type=Path, required=True)
        subparser.add_argument("--release-tag", required=True)
        if command == "prepare":
            subparser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "bootstrap-github":
        result = bootstrap_state_from_github(args.state_dir, args.repository, args.target_tag)
    elif args.command == "prepare":
        result = prepare_release(args.state_dir, args.main_archive, args.daily_basic_archive, args.output_dir, args.release_tag)
    else:
        result = advance_state(args.state_dir, args.main_archive, args.daily_basic_archive, args.release_tag)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
