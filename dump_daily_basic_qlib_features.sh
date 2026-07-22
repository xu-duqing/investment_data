#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/.env"
    set +a
fi

PYTHON_BIN="${PYTHON_BIN:-${SCRIPT_DIR}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi
BASE_PROVIDER="${DAILY_BASIC_BASE_PROVIDER:-${HOME}/.qlib/qlib_data/cn_data}"
RUN_ID="${DAILY_BASIC_BUILD_ID:-$(date +%Y%m%d%H%M%S)-$$}"
BUILD_ROOT="${DAILY_BASIC_BUILD_ROOT:-${TMPDIR:-/tmp}/daily_basic_qlib_build_${RUN_ID}}"
SOURCE_DIR="${BUILD_ROOT}/daily_basic_source"
PACKAGE_PARENT="${BUILD_ROOT}/package"
PACKAGE_ROOT="${PACKAGE_PARENT}/daily_basic"
OUTPUT_DIR="${DAILY_BASIC_OUTPUT_DIR:-${OUTPUT_DIR:-${SCRIPT_DIR}/output}}"
ARCHIVE_NAME="${DAILY_BASIC_ASSET_NAME:-daily_basic_qlib_features.tar.gz}"
ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}"
MAX_WORKERS="${DAILY_BASIC_EXPORT_MAX_WORKERS:-8}"
MAX_UNMAPPED="${DAILY_BASIC_MAX_UNMAPPED:-0}"

cleanup() {
    if [[ "${CLEAN_DAILY_BASIC_BUILD_ROOT:-1}" == "1" ]]; then
        case "${BUILD_ROOT}" in
            ""|"/") return ;;
            *) rm -rf -- "${BUILD_ROOT}" ;;
        esac
    fi
}
trap cleanup EXIT

for name in MYSQL_HOST MYSQL_PORT MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE; do
    if [[ -z "${!name:-}" ]]; then
        echo "Error: ${name} is required" >&2
        exit 1
    fi
done

if [[ ! -f "${BASE_PROVIDER}/calendars/day.txt" || ! -d "${BASE_PROVIDER}/features" ]]; then
    echo "Error: invalid base Qlib provider: ${BASE_PROVIDER}" >&2
    exit 1
fi

build_root_abs="$(cd "$(dirname "${BUILD_ROOT}")" && pwd -P)/$(basename "${BUILD_ROOT}")"
home_abs="$(cd "${HOME}" && pwd -P)"
case "${build_root_abs}" in
    ""|"/"|"${home_abs}"|"${SCRIPT_DIR}"|"$(dirname "${SCRIPT_DIR}")")
        echo "Error: unsafe build root: ${build_root_abs}" >&2
        exit 1
        ;;
esac
tmp_root="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
system_tmp_real="$(python3 -c 'import os; print(os.path.realpath("/tmp"))')"
tmp_root_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${tmp_root}")"
build_root_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${build_root_abs}")"
if [[ "${build_root_real}" != "${tmp_root_real}"/* && "${build_root_real}" != "${system_tmp_real}"/* && "${build_root_real}" != "${SCRIPT_DIR}"/* ]]; then
    echo "Error: build root must be below TMPDIR or the repository: ${build_root_abs}" >&2
    exit 1
fi
BUILD_ROOT="${build_root_abs}"
SOURCE_DIR="${BUILD_ROOT}/daily_basic_source"
PACKAGE_PARENT="${BUILD_ROOT}/package"
PACKAGE_ROOT="${PACKAGE_PARENT}/daily_basic"
rm -rf -- "${BUILD_ROOT}"
mkdir -p "${SOURCE_DIR}" "${PACKAGE_PARENT}" "${OUTPUT_DIR}"

calendar_end="$(tail -n 1 "${BASE_PROVIDER}/calendars/day.txt")"
end_date="${DAILY_BASIC_END_DATE:-${calendar_end}}"
if [[ "${end_date}" > "${calendar_end}" ]]; then
    echo "Error: daily_basic end date ${end_date} is after base calendar ${calendar_end}" >&2
    exit 1
fi

export_args=(
    --output-dir "${SOURCE_DIR}"
    --max-workers "${MAX_WORKERS}"
    --end-date "${end_date}"
)
if [[ -n "${DAILY_BASIC_START_DATE:-}" ]]; then
    export_args+=(--start-date "${DAILY_BASIC_START_DATE}")
fi
if [[ -n "${DAILY_BASIC_SYMBOL_LIMIT:-}" ]]; then
    export_args+=(--symbol-limit "${DAILY_BASIC_SYMBOL_LIMIT}")
fi

"${PYTHON_BIN}" qlib/dump_daily_basic_source.py "${export_args[@]}"

build_args=(
    --source-dir "${SOURCE_DIR}"
    --base-provider "${BASE_PROVIDER}"
    --package-root "${PACKAGE_ROOT}"
    --max-unmapped "${MAX_UNMAPPED}"
)
if [[ -n "${DAILY_BASIC_COMPATIBLE_BASE_RELEASE:-}" ]]; then
    build_args+=(--compatible-base-release "${DAILY_BASIC_COMPATIBLE_BASE_RELEASE}")
fi
"${PYTHON_BIN}" qlib/dump_feature_increment.py "${build_args[@]}"

temporary_archive="${ARCHIVE_PATH}.tmp.$$"
rm -f -- "${temporary_archive}"
tar -czf "${temporary_archive}" -C "${PACKAGE_PARENT}" daily_basic
mv -f -- "${temporary_archive}" "${ARCHIVE_PATH}"
[[ -s "${ARCHIVE_PATH}" ]] || { echo "Error: archive is empty: ${ARCHIVE_PATH}" >&2; exit 1; }
echo "Created ${ARCHIVE_PATH}"
