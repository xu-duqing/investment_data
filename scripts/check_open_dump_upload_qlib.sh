#!/usr/bin/env bash
set -euo pipefail

INVESTMENT_DATA_DIR="/Users/appweb/investment_data"
LOG_DIR="${INVESTMENT_DATA_DIR}/logs"
STATE_DIR="${INVESTMENT_DATA_DIR}/.qlib_release_state"
LOCK_DIR="${STATE_DIR}/daily-qlib-release.lock"
mkdir -p "${LOG_DIR}" "${STATE_DIR}"

cd "${INVESTMENT_DATA_DIR}"

if [ -f "${INVESTMENT_DATA_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${INVESTMENT_DATA_DIR}/.env"
    set +a
fi

export https_proxy="http://127.0.0.1:7890"
export http_proxy="http://127.0.0.1:7890"
export all_proxy="socks5://127.0.0.1:7890"

if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "${INVESTMENT_DATA_DIR}/.venv/bin/python" ]; then
        PYTHON_BIN="${INVESTMENT_DATA_DIR}/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi
export PYTHON_BIN
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export MYSQL_EXCHANGE="${MYSQL_EXCHANGE:-SSE}"

iso_now() {
    date '+%Y-%m-%dT%H:%M:%S%z'
}

previous_day="${QLIB_RELEASE_PREVIOUS_DAY:-$(${PYTHON_BIN} - <<'PY'
import datetime as dt
print((dt.datetime.now().date() - dt.timedelta(days=1)).isoformat())
PY
)}"

sql_exchange="$(printf '%s' "${MYSQL_EXCHANGE}" | sed "s/'/''/g")"
is_open="$(${PYTHON_BIN} "${INVESTMENT_DATA_DIR}/qlib/mysql_query.py" --skip-column-names --execute "SELECT IF(COUNT(*) > 0, '1', '0') AS is_open FROM trade_calendar WHERE exchange = '${sql_exchange}' AND cal_date = '${previous_day}' AND is_open = 1")"
is_open="$(printf '%s' "${is_open}" | tr -d '[:space:]')"

run_log="${QLIB_RELEASE_RUN_LOG:-${LOG_DIR}/qlib_release_${previous_day}_$(date +%Y%m%d%H%M%S).log}"
handoff_log="${LOG_DIR}/qlib_release_handoff.log"
status_file="${STATE_DIR}/daily-qlib-release.status"
pid_file="${LOCK_DIR}/pid"

write_status() {
    local state="$1"
    local message="$2"
    {
        printf 'updated_at=%s\n' "$(iso_now)"
        printf 'state=%s\n' "${state}"
        printf 'previous_day=%s\n' "${previous_day}"
        printf 'exchange=%s\n' "${MYSQL_EXCHANGE}"
        printf 'pid=%s\n' "${QLIB_RELEASE_PID:-}"
        printf 'run_log=%s\n' "${run_log}"
        printf 'message=%s\n' "${message}"
    } >"${status_file}"
}

is_pid_running() {
    local pid="$1"
    [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null
}

cleanup_lock() {
    rm -rf "${LOCK_DIR}"
}

run_worker() {
    QLIB_RELEASE_PID="$$"
    export QLIB_RELEASE_PID
    daily_basic_base_root=""
    daily_basic_base=""
    cleanup_worker() {
        local status="$?"
        if [ -n "${daily_basic_base_root}" ]; then
            rm -rf "${daily_basic_base_root}"
        fi
        if [ "${status}" -eq 0 ]; then
            write_status completed "completed main/daily_basic dump and upload"
        else
            write_status failed "main/daily_basic dump or upload failed with exit ${status}"
        fi
        cleanup_lock
    }
    trap cleanup_worker EXIT

    write_status running "background main/daily_basic dump and upload running"

    if [ "${is_open}" != "1" ]; then
        printf '[%s] %s (%s) was not an open trading day; skipped dump/upload.\n' \
            "$(iso_now)" "${previous_day}" "${MYSQL_EXCHANGE}" >>"${LOG_DIR}/qlib_release_skips.log"
        write_status skipped "not an open trading day"
        exit 0
    fi

    if [ "${DRY_RUN:-0}" = "1" ]; then
        {
            echo "${previous_day} (${MYSQL_EXCHANGE}) was an open trading day; would run dump/upload. Log: ${run_log}"
            echo "DRY_RUN=1; would run: ./dump_qlib_bin.sh && ./dump_daily_basic_qlib_features.sh && ./upload_release.sh"
        } | tee -a "${run_log}"
        exit 0
    fi

    if [ "${QLIB_RELEASE_REUSE_MAIN_ARCHIVE:-0}" = "1" ]; then
        if [ ! -s "${INVESTMENT_DATA_DIR}/output/qlib_bin.tar.gz" ]; then
            echo "Cannot reuse missing main archive: ${INVESTMENT_DATA_DIR}/output/qlib_bin.tar.gz" >&2
            exit 1
        fi
        skip_main_dump=1
    else
        skip_main_dump=0
    fi

    if {
        if [ "${skip_main_dump}" = "1" ]; then
            echo "[$(iso_now)] Reusing existing output/qlib_bin.tar.gz"
        else
            echo "[$(iso_now)] Starting dump_qlib_bin.sh for ${previous_day} (${MYSQL_EXCHANGE})"
            CHECK_FRESHNESS="${CHECK_FRESHNESS:-1}" QLIB_RELEASE_PREVIOUS_DAY="${previous_day}" ./dump_qlib_bin.sh
        fi &&
            echo "[$(iso_now)] Preparing daily_basic base provider from qlib_bin.tar.gz" &&
            daily_basic_base_root="$(mktemp -d "${INVESTMENT_DATA_DIR}/output/.daily-basic-base.XXXXXX")" &&
            tar -xzf "${INVESTMENT_DATA_DIR}/output/qlib_bin.tar.gz" -C "${daily_basic_base_root}" qlib_bin &&
            daily_basic_base="${daily_basic_base_root}/qlib_bin" &&
            [ -s "${daily_basic_base}/calendars/day.txt" ] &&
            [ -d "${daily_basic_base}/features" ] &&
            echo "[$(iso_now)] Starting dump_daily_basic_qlib_features.sh" &&
            DAILY_BASIC_BASE_PROVIDER="${daily_basic_base}" \
                DAILY_BASIC_END_DATE="${previous_day}" \
                bash ./dump_daily_basic_qlib_features.sh &&
            echo "[$(iso_now)] Starting upload_release.sh" &&
            ./upload_release.sh &&
            echo "[$(iso_now)] Completed main/daily_basic dump and upload for ${previous_day}"
    } >"${run_log}" 2>&1; then
        echo "Completed main/daily_basic qlib dump/upload for ${previous_day} (${MYSQL_EXCHANGE}). Log: ${run_log}"
    else
        status=$?
        echo "main/daily_basic qlib dump/upload failed for ${previous_day} (${MYSQL_EXCHANGE}) with exit ${status}. Log: ${run_log}" >&2
        tail -n 80 "${run_log}" >&2 || true
        exit "${status}"
    fi
}

start_background_worker() {
    if mkdir "${LOCK_DIR}" 2>/dev/null; then
        :
    else
        local old_pid=""
        if [ -f "${pid_file}" ]; then
            old_pid="$(tr -d '[:space:]' <"${pid_file}" || true)"
        fi
        if is_pid_running "${old_pid}"; then
            echo "qlib release already running for ${previous_day} (${MYSQL_EXCHANGE}); pid=${old_pid}; status=${status_file}; log=$(cat "${LOCK_DIR}/run_log" 2>/dev/null || printf '%s' "${run_log}")"
            exit 0
        fi
        printf '[%s] removing stale lock %s (pid=%s)\n' "$(iso_now)" "${LOCK_DIR}" "${old_pid:-unknown}" >>"${handoff_log}"
        rm -rf "${LOCK_DIR}"
        mkdir "${LOCK_DIR}"
    fi

    printf '%s\n' "${run_log}" >"${LOCK_DIR}/run_log"
    printf '%s\n' "${previous_day}" >"${LOCK_DIR}/previous_day"
    write_status starting "starting background dump/upload"

    if command -v setsid >/dev/null 2>&1; then
        setsid env QLIB_RELEASE_WORKER=1 QLIB_RELEASE_PREVIOUS_DAY="${previous_day}" QLIB_RELEASE_RUN_LOG="${run_log}" QLIB_RELEASE_REUSE_MAIN_ARCHIVE="${QLIB_RELEASE_REUSE_MAIN_ARCHIVE:-0}" "$0" </dev/null >>"${handoff_log}" 2>&1 &
    else
        nohup env QLIB_RELEASE_WORKER=1 QLIB_RELEASE_PREVIOUS_DAY="${previous_day}" QLIB_RELEASE_RUN_LOG="${run_log}" QLIB_RELEASE_REUSE_MAIN_ARCHIVE="${QLIB_RELEASE_REUSE_MAIN_ARCHIVE:-0}" "$0" </dev/null >>"${handoff_log}" 2>&1 &
    fi
    local child_pid="$!"
    printf '%s\n' "${child_pid}" >"${pid_file}"
    QLIB_RELEASE_PID="${child_pid}" write_status running "background dump/upload started"

    echo "Started background qlib dump/upload for ${previous_day} (${MYSQL_EXCHANGE}); pid=${child_pid}; log=${run_log}; status=${status_file}"
}

if [ "${QLIB_RELEASE_WORKER:-0}" = "1" ]; then
    run_worker
    exit 0
fi

if [ "${is_open}" != "1" ]; then
    printf '[%s] %s (%s) was not an open trading day; skipped dump/upload.\n' \
        "$(iso_now)" "${previous_day}" "${MYSQL_EXCHANGE}" >>"${LOG_DIR}/qlib_release_skips.log"
    write_status skipped "not an open trading day"
    exit 0
fi

start_background_worker
