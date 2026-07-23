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
    if [ -x "${QLIB_REPO_DIR}/.venv/bin/python" ]; then
        PYTHON_BIN="${QLIB_REPO_DIR}/.venv/bin/python"
    elif [ -x "${SCRIPT_DIR}/.venv/bin/python" ]; then
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

run_python() {
    if [ -z "${PYTHON_BIN:-}" ] || [ ! -x "${PYTHON_BIN}" ] || [ -d "${PYTHON_BIN}" ]; then
        echo "PYTHON_BIN must be an executable Python file, got: ${PYTHON_BIN:-<empty>}" >&2
        exit 1
    fi
    "${PYTHON_BIN}" "$@"
}

mysql_query_scalar() {
    run_python "${INVESTMENT_DATA_DIR}/qlib/mysql_query.py" --skip-column-names --execute "$1"
}

if ! command -v git >/dev/null 2>&1; then
    echo "git is required but was not found in PATH" >&2
    exit 1
fi

if ! run_python -c "import pymysql, setuptools_scm" >/dev/null 2>&1; then
    echo "Project Python dependencies are missing." >&2
    echo "Run: ${PYTHON_BIN} -m pip install -r ${INVESTMENT_DATA_DIR}/requirements.txt" >&2
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

export PYTHONPATH="${QLIB_REPO_DIR}:${QLIB_REPO_DIR}/scripts:${PYTHONPATH:-}"
if ! run_python -c "import qlib; from qlib.data._libs.rolling import rolling_slope; from qlib.data._libs.expanding import expanding_slope; from data_collector.yahoo import collector" >/dev/null 2>&1; then
    echo "Qlib Python dependencies or compiled extensions are missing." >&2
    echo "Prepare Qlib with its own dependency sources:" >&2
    echo "  cd ${QLIB_REPO_DIR}" >&2
    echo "  python3 -m venv --system-site-packages .venv" >&2
    echo "  . .venv/bin/activate" >&2
    echo "  python -m pip install -e '.[test]'" >&2
    echo "  python -m pip install -r scripts/data_collector/yahoo/requirements.txt" >&2
    echo "  python setup.py build_ext --inplace" >&2
    exit 1
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
QLIB_SOURCE_DIR="${QLIB_SOURCE_DIR}" run_python ./qlib/dump_all_to_qlib_source.py \
    --max-workers="${QLIB_EXPORT_MAX_WORKERS}"

cd ./qlib
log_step "Normalizing qlib data with ${DUMP_QLIB_MAX_WORKERS} workers"
run_python ./normalize.py normalize_data \
    --source_dir "${QLIB_SOURCE_DIR}/" \
    --normalize_dir "${QLIB_NORMALIZE_DIR}" \
    --max_workers="${DUMP_QLIB_MAX_WORKERS}" \
    --date_field_name="tradedate"

log_step "Dumping normalized qlib data to binary files"
run_python "${QLIB_REPO_DIR}/scripts/dump_bin.py" dump_all \
    --data_path "${QLIB_NORMALIZE_DIR}/" \
    --qlib_dir "${QLIB_BIN_DIR}" \
    --date_field_name=tradedate \
    --exclude_fields=tradedate,symbol

mkdir -p "${QLIB_INDEX_DIR}"
log_step "Dumping qlib index constituents"
QLIB_INDEX_DIR="${QLIB_INDEX_DIR}" run_python ./dump_index_weight.py

cd "${INVESTMENT_DATA_DIR}"
log_step "Dumping qlib trade calendar"
run_python "${INVESTMENT_DATA_DIR}/tushare/dump_day_calendar.py" "${QLIB_BIN_DIR}" --exchange "${MYSQL_EXCHANGE}"

if [ "${CHECK_FRESHNESS:-0}" = "1" ]; then
    source_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(trade_date), '%Y-%m-%d') FROM stock_daily")
    # The after-close release includes the current trading day. An explicit
    # trade date keeps scheduled runs deterministic.
    freshness_as_of_date="${QLIB_FRESHNESS_AS_OF_DATE:-${QLIB_RELEASE_DATE:-}}"
    explicit_trade_date="${QLIB_RELEASE_TRADE_DATE:-}"
    if [ -n "${explicit_trade_date}" ]; then
        case "${explicit_trade_date}" in
            [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
            *)
                echo "Invalid QLIB_RELEASE_TRADE_DATE: ${explicit_trade_date}" >&2
                exit 1
                ;;
        esac
        expected_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(cal_date), '%Y-%m-%d') FROM trade_calendar WHERE exchange = '${MYSQL_EXCHANGE}' AND is_open = 1 AND cal_date <= '${explicit_trade_date}'")
        freshness_boundary="trade date ${explicit_trade_date}"
    elif [ -n "${freshness_as_of_date}" ]; then
        case "${freshness_as_of_date}" in
            [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
            *)
                echo "Invalid QLIB_FRESHNESS_AS_OF_DATE/QLIB_RELEASE_DATE: ${freshness_as_of_date}" >&2
                exit 1
                ;;
        esac
        expected_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(cal_date), '%Y-%m-%d') FROM trade_calendar WHERE exchange = '${MYSQL_EXCHANGE}' AND is_open = 1 AND cal_date <= '${freshness_as_of_date}'")
        freshness_boundary="as-of date ${freshness_as_of_date}"
    else
        expected_max_date=$(mysql_query_scalar "SELECT DATE_FORMAT(MAX(cal_date), '%Y-%m-%d') FROM trade_calendar WHERE exchange = '${MYSQL_EXCHANGE}' AND is_open = 1 AND cal_date <= CURRENT_DATE")
        freshness_boundary="CURRENT_DATE"
    fi
    qlib_max_date=$(tail -n 1 "${QLIB_BIN_DIR}/calendars/day.txt")

    echo "Expected latest source trade date (same-day after close, ${freshness_boundary}): ${expected_max_date}"
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
