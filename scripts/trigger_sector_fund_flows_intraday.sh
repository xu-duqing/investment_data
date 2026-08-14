#!/usr/bin/env bash
set -euo pipefail

INVESTMENT_DATA_DIR="/Users/appweb/investment_data"
STATE_DIR="${INVESTMENT_DATA_DIR}/.sector_fund_flows_state"
LOG_DIR="${INVESTMENT_DATA_DIR}/logs"
LOCK_DIR="${STATE_DIR}/intraday-trigger.lock"
STATUS_FILE="${STATE_DIR}/intraday-trigger.status"
PID_FILE="${LOCK_DIR}/pid"
SECRET_FILE="${HOME}/.hermes/secrets/sector-fund-flows.env"
PYTHON_BIN="${INVESTMENT_DATA_DIR}/.venv/bin/python"
API_URL="http://client.wacai.info/deep-alpha-server/api/gateway/sector-fund-flows/trigger?symbol=A&frequency=30m&feature_key=vol_single_ma_V230214"
TZ_NAME="Asia/Shanghai"

mkdir -p "${STATE_DIR}" "${LOG_DIR}"
cd "${INVESTMENT_DATA_DIR}"

if [[ -f "${INVESTMENT_DATA_DIR}/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "${INVESTMENT_DATA_DIR}/.env"
    set +a
fi

if [[ -f "${SECRET_FILE}" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "${SECRET_FILE}"
    set +a
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

now_iso() {
    TZ="${TZ_NAME}" date '+%Y-%m-%dT%H:%M:%S%z'
}

write_status() {
    local state="$1"
    local message="$2"
    {
        printf 'updated_at=%s\n' "$(now_iso)"
        printf 'state=%s\n' "${state}"
        printf 'trade_date=%s\n' "${TRADE_DATE:-}"
        printf 'pid=%s\n' "${WORKER_PID:-}"
        printf 'run_log=%s\n' "${RUN_LOG:-}"
        printf 'success_count=%s\n' "${SUCCESS_COUNT:-0}"
        printf 'failure_count=%s\n' "${FAILURE_COUNT:-0}"
        printf 'message=%s\n' "${message}"
    } >"${STATUS_FILE}"
}

is_pid_running() {
    local pid="$1"
    [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

TRADE_DATE="${TRIGGER_TRADE_DATE:-$(TZ="${TZ_NAME}" date '+%Y-%m-%d')}"
sql_date="$(printf '%s' "${TRADE_DATE}" | sed "s/'/''/g")"
is_open="$(${PYTHON_BIN} "${INVESTMENT_DATA_DIR}/qlib/mysql_query.py" --skip-column-names --execute "SELECT IF(COUNT(*) > 0, '1', '0') FROM trade_calendar WHERE exchange = 'SSE' AND cal_date = '${sql_date}' AND is_open = 1")"
is_open="$(printf '%s' "${is_open}" | tr -d '[:space:]')"

if [[ "${is_open}" != "1" ]]; then
    write_status skipped "not an SSE trading day"
    exit 0
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    printf 'DRY_RUN: %s is an SSE trading day; would trigger every 10 seconds during 09:30-11:30 and 13:00-15:00 %s.\n' "${TRADE_DATE}" "${TZ_NAME}"
    exit 0
fi

if [[ -z "${SECTOR_FUND_FLOWS_TOKEN:-}" ]]; then
    write_status failed "SECTOR_FUND_FLOWS_TOKEN is missing"
    echo "Missing API token file: ${SECRET_FILE}" >&2
    exit 1
fi

if [[ "${WORKER:-0}" != "1" ]]; then
    if [[ -d "${LOCK_DIR}" ]]; then
        existing_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
        if is_pid_running "${existing_pid}"; then
            exit 0
        fi
        rm -rf "${LOCK_DIR}"
    fi
    mkdir "${LOCK_DIR}"
fi

RUN_LOG="${RUN_LOG:-${LOG_DIR}/sector_fund_flows_${TRADE_DATE}_$(TZ="${TZ_NAME}" date '+%Y%m%d%H%M%S').log}"

if [[ "${WORKER:-0}" != "1" ]]; then
    launcher_log="${LOG_DIR}/sector_fund_flows_handoff.log"
    if command -v setsid >/dev/null 2>&1; then
        setsid env WORKER=1 TRIGGER_TRADE_DATE="${TRADE_DATE}" RUN_LOG="${RUN_LOG}" "$0" </dev/null >>"${launcher_log}" 2>&1 &
    else
        nohup env WORKER=1 TRIGGER_TRADE_DATE="${TRADE_DATE}" RUN_LOG="${RUN_LOG}" "$0" </dev/null >>"${launcher_log}" 2>&1 &
    fi
    child_pid=$!
    printf '%s\n' "${child_pid}" >"${PID_FILE}"
    WORKER_PID="${child_pid}"
    write_status starting "intraday worker handed off"
    exit 0
fi

WORKER_PID="$$"
printf '%s\n' "${WORKER_PID}" >"${PID_FILE}"
SUCCESS_COUNT=0
FAILURE_COUNT=0
write_status running "intraday trigger worker running"

cleanup() {
    local rc=$?
    rm -rf "${LOCK_DIR}"
    if [[ ${rc} -eq 0 ]]; then
        write_status completed "market sessions completed"
    else
        write_status failed "worker exited with status ${rc}"
    fi
}
trap cleanup EXIT INT TERM

session_epoch() {
    local clock="$1"
    TZ="${TZ_NAME}" "${PYTHON_BIN}" - "${TRADE_DATE}" "${clock}" <<'PY'
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo
value = dt.datetime.fromisoformat(f"{sys.argv[1]}T{sys.argv[2]}:00").replace(tzinfo=ZoneInfo(os.environ.get("TZ_NAME", "Asia/Shanghai")))
print(int(value.timestamp()))
PY
}

export TZ_NAME
morning_start="$(session_epoch '09:30')"
morning_end="$(session_epoch '11:30')"
afternoon_start="$(session_epoch '13:00')"
afternoon_end="$(session_epoch '15:00')"

run_session() {
    local start_epoch="$1"
    local end_epoch="$2"
    local now target http_code
    now="$(date '+%s')"
    if (( now < start_epoch )); then
        sleep "$((start_epoch - now))"
    fi
    target="${start_epoch}"
    now="$(date '+%s')"
    if (( now > target )); then
        target="$((now - (now % 10) + 10))"
    fi
    while (( target <= end_epoch )); do
        now="$(date '+%s')"
        if (( now < target )); then
            sleep "$((target - now))"
        fi
        http_code="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
            --connect-timeout 5 --max-time 9 \
            --request POST --url "${API_URL}" \
            --header "Authorization: Bearer ${SECTOR_FUND_FLOWS_TOKEN}" 2>>"${RUN_LOG}" || true)"
        if [[ "${http_code}" =~ ^2[0-9][0-9]$ ]]; then
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
            printf '%s success http=%s\n' "$(now_iso)" "${http_code}" >>"${RUN_LOG}"
        else
            FAILURE_COUNT=$((FAILURE_COUNT + 1))
            printf '%s failure http=%s\n' "$(now_iso)" "${http_code:-curl_error}" >>"${RUN_LOG}"
        fi
        write_status running "intraday trigger worker running"
        target=$((target + 10))
    done
}

current_epoch="$(date '+%s')"
if (( current_epoch <= morning_end )); then
    run_session "${morning_start}" "${morning_end}"
fi
current_epoch="$(date '+%s')"
if (( current_epoch <= afternoon_end )); then
    run_session "${afternoon_start}" "${afternoon_end}"
fi
