#!/usr/bin/env bash
set -euo pipefail

if [ "${TRACE:-0}" = "1" ]; then
    set -x
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${SCRIPT_DIR}/.env"
    set +a
fi

WORKING_DIR=${1:-"$(dirname "${SCRIPT_DIR}")"}
QLIB_REPO=${2:-https://github.com/microsoft/qlib.git}
INVESTMENT_DATA_DIR="${INVESTMENT_DATA_DIR:-${WORKING_DIR}/investment_data}"
if [ ! -d "${INVESTMENT_DATA_DIR}" ] || [ ! -f "${INVESTMENT_DATA_DIR}/dump_qlib_bin.sh" ]; then
    INVESTMENT_DATA_DIR="${SCRIPT_DIR}"
fi

QLIB_REPO_DIR="${QLIB_REPO_DIR:-${WORKING_DIR}/qlib}"
RUN_ID="${QLIB_BUILD_ID:-$(date +%Y%m%d%H%M%S)-$$}"
BUILD_ROOT="${QLIB_BUILD_ROOT:-${WORKING_DIR}/qlib_build_${RUN_ID}}"
QLIB_SOURCE_DIR="${BUILD_ROOT}/qlib_source"
QLIB_NORMALIZE_DIR="${BUILD_ROOT}/qlib_normalize"
QLIB_INDEX_DIR="${BUILD_ROOT}/qlib_index"
QLIB_BIN_DIR="${BUILD_ROOT}/qlib_bin"
QLIB_EXPORT_MAX_WORKERS="${QLIB_EXPORT_MAX_WORKERS:-8}"
DUMP_QLIB_MAX_WORKERS="${DUMP_QLIB_MAX_WORKERS:-8}"

if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "${SCRIPT_DIR}/.venv/bin/python" ]; then
        PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi
export PYTHON_BIN
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export MYSQL_EXCHANGE="${MYSQL_EXCHANGE:-SSE}"

require_env() {
    local name="$1"
    if [ -z "${!name:-}" ]; then
        echo "${name} is required" >&2
        exit 1
    fi
}

cleanup() {
    if [ "${CLEAN_QLIB_BUILD_ROOT:-1}" = "1" ]; then
        case "${BUILD_ROOT}" in
            ""|"/")
                ;;
            *)
                rm -rf "${BUILD_ROOT}"
                ;;
        esac
    fi
}
trap cleanup EXIT

log_step() {
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"
}

mysql_query_scalar() {
    MYSQL_PWD="${MYSQL_PASSWORD}" mysql \
        -h "${MYSQL_HOST}" \
        -P "${MYSQL_PORT}" \
        -u "${MYSQL_USER}" \
        -D "${MYSQL_DATABASE}" \
        --batch \
        --raw \
        --skip-column-names \
        --default-character-set=utf8mb4 \
        -e "$1"
}

if ! command -v mysql >/dev/null 2>&1; then
    echo "mysql client is required but was not found in PATH" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "git is required but was not found in PATH" >&2
    exit 1
fi

if ! "${PYTHON_BIN}" -c "import setuptools_scm" >/dev/null 2>&1; then
    echo "Python dependency setuptools_scm is missing. Run: pip install -r requirements.txt" >&2
    exit 1
fi

require_env MYSQL_HOST
require_env MYSQL_PORT
require_env MYSQL_USER
require_env MYSQL_PASSWORD
require_env MYSQL_DATABASE

if [ ! -d "${QLIB_REPO_DIR}/.git" ]; then
    git clone "${QLIB_REPO}" "${QLIB_REPO_DIR}"
fi

log_step "Checking MySQL source connection"
mysql_query_scalar "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()" >/dev/null

case "${BUILD_ROOT}" in
    ""|"/")
        echo "Refusing to remove unsafe BUILD_ROOT='${BUILD_ROOT}'" >&2
        exit 1
        ;;
esac

cd "${INVESTMENT_DATA_DIR}"
rm -rf "${BUILD_ROOT}"
mkdir -p "${QLIB_SOURCE_DIR}" "${QLIB_NORMALIZE_DIR}" "${QLIB_INDEX_DIR}" "${QLIB_BIN_DIR}"

log_step "Dumping MySQL data to qlib source CSVs with ${QLIB_EXPORT_MAX_WORKERS} workers"
QLIB_SOURCE_DIR="${QLIB_SOURCE_DIR}" "${PYTHON_BIN}" ./qlib/dump_all_to_qlib_source.py \
    --max-workers="${QLIB_EXPORT_MAX_WORKERS}"

export PYTHONPATH="${QLIB_REPO_DIR}/scripts:${PYTHONPATH:-}"
cd ./qlib
log_step "Normalizing qlib data with ${DUMP_QLIB_MAX_WORKERS} workers"
"${PYTHON_BIN}" ./normalize.py normalize_data \
    --source_dir "${QLIB_SOURCE_DIR}/" \
    --normalize_dir "${QLIB_NORMALIZE_DIR}" \
    --max_workers="${DUMP_QLIB_MAX_WORKERS}" \
    --date_field_name="tradedate"

log_step "Dumping normalized qlib data to binary files"
"${PYTHON_BIN}" "${QLIB_REPO_DIR}/scripts/dump_bin.py" dump_all \
    --data_path "${QLIB_NORMALIZE_DIR}/" \
    --qlib_dir "${QLIB_BIN_DIR}" \
    --date_field_name=tradedate \
    --exclude_fields=tradedate,symbol

mkdir -p "${QLIB_INDEX_DIR}"
log_step "Dumping qlib index constituents"
QLIB_INDEX_DIR="${QLIB_INDEX_DIR}" "${PYTHON_BIN}" ./dump_index_weight.py

cd "${INVESTMENT_DATA_DIR}"
log_step "Dumping qlib trade calendar"
"${PYTHON_BIN}" ./tushare/dump_day_calendar.py "${QLIB_BIN_DIR}/" --exchange "${MYSQL_EXCHANGE}"

if [ "${CHECK_FRESHNESS:-0}" = "1" ]; then
    source_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(trade_date), '%Y-%m-%d') FROM stock_daily")
    expected_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(cal_date), '%Y-%m-%d') FROM trade_calendar WHERE exchange = '${MYSQL_EXCHANGE}' AND is_open = 1 AND cal_date <= CURRENT_DATE")
    qlib_max_date=$(tail -n 1 "${QLIB_BIN_DIR}/calendars/day.txt")

    echo "Expected latest trade date: ${expected_max_date}"
    echo "MySQL source latest trade date: ${source_max_date}"
    echo "Qlib archive latest trade date: ${qlib_max_date}"

    if [ "$source_max_date" != "$expected_max_date" ]; then
        echo "MySQL source data is stale; refusing to publish release." >&2
        exit 1
    fi

    if [ "$qlib_max_date" != "$source_max_date" ]; then
        echo "Qlib archive is stale; refusing to publish release." >&2
        exit 1
    fi
fi

cp "${QLIB_INDEX_DIR}"/csi* "${QLIB_BIN_DIR}/instruments/"

log_step "Creating qlib_bin.tar.gz"
tar -czvf ./qlib_bin.tar.gz -C "${BUILD_ROOT}" qlib_bin/
ls -lh ./qlib_bin.tar.gz
OUTPUT_DIR=${OUTPUT_DIR:-${INVESTMENT_DATA_DIR}/output}
mkdir -p "${OUTPUT_DIR}"
mv ./qlib_bin.tar.gz "${OUTPUT_DIR}/"
ls -lh "${OUTPUT_DIR}/qlib_bin.tar.gz"
