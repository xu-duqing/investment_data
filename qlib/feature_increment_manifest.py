import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = 1
PACKAGE_TYPE = "qlib_feature_increment"
DATASET = "daily_basic"
FREQUENCY = "day"
FEATURES = (
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)
FORBIDDEN_FEATURES = frozenset(
    {"open", "high", "low", "close", "volume", "amount", "vwap", "adjclose", "factor", "change"}
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_calendar(path: Path) -> List[str]:
    if not path.is_file():
        raise ValueError(f"calendar file not found: {path}")
    dates = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not dates:
        raise ValueError(f"calendar is empty: {path}")
    parsed = [datetime.strptime(value, "%Y-%m-%d").date() for value in dates]
    if any(current <= previous for previous, current in zip(parsed, parsed[1:])):
        raise ValueError("calendar must be strictly increasing without duplicates")
    return dates


def feature_path_parts(relative_path: str) -> tuple[str, str]:
    path = Path(relative_path)
    if len(path.parts) != 3 or path.parts[0] != "features":
        raise ValueError(f"invalid feature path: {relative_path}")
    instrument = path.parts[1]
    suffix = f".{FREQUENCY}.bin"
    if not path.name.endswith(suffix):
        raise ValueError(f"invalid feature filename: {relative_path}")
    field = path.name[: -len(suffix)]
    if not instrument or not field:
        raise ValueError(f"invalid feature path: {relative_path}")
    return instrument, field


def validate_feature_paths(relative_paths: Iterable[str], features: Sequence[str] = FEATURES) -> None:
    allowed = set(features)
    if not allowed or not allowed.issubset(FEATURES) or allowed & FORBIDDEN_FEATURES:
        raise ValueError("manifest feature list is invalid")
    for relative_path in relative_paths:
        _, field = feature_path_parts(relative_path)
        if field not in allowed or field in FORBIDDEN_FEATURES:
            raise ValueError(f"feature is not allowed: {field}")


def validate_bin_file(path: Path, calendar_count: int) -> None:
    if path.stat().st_size < 4 or path.stat().st_size % 4:
        raise ValueError(f"invalid Qlib binary size: {path}")
    values = np.fromfile(path, dtype="<f4")
    start = float(values[0])
    if not math.isfinite(start) or start < 0 or not start.is_integer():
        raise ValueError(f"invalid Qlib binary start index: {path}")
    if int(start) + len(values) - 1 > calendar_count:
        raise ValueError(f"Qlib binary exceeds calendar: {path}")


def build_manifest(
    calendar_path: Path,
    feature_paths: Sequence[Path],
    package_root: Path,
    source_min_date: str,
    source_max_date: str,
    compatible_base_release: str | None = None,
) -> Dict[str, object]:
    calendar = read_calendar(calendar_path)
    relative_paths = sorted(path.relative_to(package_root).as_posix() for path in feature_paths)
    validate_feature_paths(relative_paths)
    instruments = {feature_path_parts(path)[0] for path in relative_paths}
    manifest: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "package_type": PACKAGE_TYPE,
        "dataset": DATASET,
        "frequency": FREQUENCY,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source_min_date": source_min_date,
        "source_max_date": source_max_date,
        "base_calendar_sha256": sha256_file(calendar_path),
        "base_calendar_start": calendar[0],
        "base_calendar_end": calendar[-1],
        "base_calendar_count": len(calendar),
        "features": list(FEATURES),
        "instrument_count": len(instruments),
        "file_count": len(relative_paths),
        "files": relative_paths,
    }
    if compatible_base_release:
        manifest["compatible_base_release"] = compatible_base_release
    return manifest


def validate_manifest(manifest: Mapping[str, object], relative_paths: Sequence[str] | None = None) -> None:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "package_type": PACKAGE_TYPE,
        "dataset": DATASET,
        "frequency": FREQUENCY,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"invalid manifest {key}: {manifest.get(key)!r}")
    features = manifest.get("features")
    files = manifest.get("files")
    if not isinstance(features, list) or set(features) != set(FEATURES):
        raise ValueError("invalid manifest features")
    if not isinstance(files, list) or any(not isinstance(path, str) for path in files):
        raise ValueError("invalid manifest files")
    if len(files) != len(set(files)) or manifest.get("file_count") != len(files):
        raise ValueError("manifest file count is invalid")
    validate_feature_paths(files, features)
    instruments = {feature_path_parts(path)[0] for path in files}
    if manifest.get("instrument_count") != len(instruments):
        raise ValueError("manifest instrument count is invalid")
    if relative_paths is not None and sorted(files) != sorted(relative_paths):
        raise ValueError("manifest files do not match package contents")


def write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_checksums(package_root: Path, relative_paths: Sequence[str], output_path: Path) -> None:
    lines = [f"{sha256_file(package_root / path)}  {path}" for path in sorted(relative_paths)]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_checksums(path: Path) -> Dict[str, str]:
    checksums: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative_path = line.partition("  ")
        if not separator or len(digest) != 64 or relative_path in checksums:
            raise ValueError(f"invalid checksum line: {line!r}")
        checksums[relative_path] = digest
    return checksums


def verify_checksums(package_root: Path) -> None:
    checksum_path = package_root / "checksums.sha256"
    if not checksum_path.is_file():
        raise ValueError("checksums.sha256 is missing")
    checksums = read_checksums(checksum_path)
    expected = sorted(
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path != checksum_path
    )
    if sorted(checksums) != expected:
        raise ValueError("checksum inventory does not match package contents")
    for relative_path, digest in checksums.items():
        if sha256_file(package_root / relative_path) != digest:
            raise ValueError(f"checksum mismatch: {relative_path}")
