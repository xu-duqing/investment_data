#!/usr/bin/env bash
set -euo pipefail

INVESTMENT_DATA_DIR="/Users/appweb/investment_data"
STATE_DIR="${INVESTMENT_DATA_DIR}/.qlib_release_state"
STATUS_FILE="${STATE_DIR}/daily-qlib-release.status"
NOTIFIED_FILE="${STATE_DIR}/daily-qlib-release.email.notified"
EMAIL_TO="${QLIB_RELEASE_NOTIFY_EMAIL:-ahjd114@163.com}"
BODY_FILE_REL=".qlib_release_state/daily-qlib-release-email-body.txt"
BODY_FILE="${INVESTMENT_DATA_DIR}/${BODY_FILE_REL}"
SEND_STDERR_FILE="${STATE_DIR}/daily-qlib-release-email-send.stderr"

cd "${INVESTMENT_DATA_DIR}"
[ -f "${STATUS_FILE}" ] || exit 0
mkdir -p "${STATE_DIR}"

get_value() {
    local key="$1"
    awk -F= -v k="${key}" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "${STATUS_FILE}"
}

parse_confirmation_token() {
    python3 -c '
import json
import sys

text = sys.stdin.read().strip()
if "\ntip:" in text:
    text = text.split("\ntip:", 1)[0].strip()
try:
    data = json.loads(text)
except Exception as exc:
    print(f"failed to parse agently-cli JSON: {exc}", file=sys.stderr)
    sys.exit(2)

token = (data.get("data") or {}).get("confirmation_token") or data.get("confirmation_token")
if not token:
    print("confirmation_token not found in agently-cli response", file=sys.stderr)
    sys.exit(3)
print(token)
'
}

sanitize_cli_stderr() {
    if [ -f "${SEND_STDERR_FILE}" ]; then
        sed -E 's/ctk_[A-Za-z0-9-]+/ctk_***/g' "${SEND_STDERR_FILE}" >&2 || true
    fi
}

state="$(get_value state)"
case "${state}" in
    completed|failed) ;;
    *) exit 0 ;;
esac

updated_at="$(get_value updated_at)"
trade_date="$(get_value trade_date)"
if [ -z "${trade_date}" ]; then
    trade_date="$(get_value previous_day)"
fi
exchange="$(get_value exchange)"
run_log="$(get_value run_log)"
message="$(get_value message)"

notification_key="${EMAIL_TO}|${trade_date}|${exchange}|${state}|${updated_at}|${message}"
if [ -f "${NOTIFIED_FILE}" ] && [ "$(cat "${NOTIFIED_FILE}")" = "${notification_key}" ]; then
    exit 0
fi

release_url=""
if [ -f "${run_log}" ]; then
    release_url="$(grep -Eo 'https://github.com/[^[:space:]]+/releases/tag/[^[:space:]]+' "${run_log}" | tail -n 1 || true)"
fi
if [ -z "${release_url}" ] && [ "${state}" = "completed" ] && [ -n "${trade_date}" ]; then
    release_url="https://github.com/xu-duqing/investment_data/releases/tag/${trade_date}"
fi

if [ "${state}" = "completed" ]; then
    subject="Qlib Release 成功 - ${trade_date}"
    {
        printf '✅ daily-qlib-release-after-close 成功\n'
        printf '交易日: %s (%s)\n' "${trade_date}" "${exchange}"
        printf '状态: %s\n' "${message:-completed}"
        [ -n "${release_url}" ] && printf 'Release: %s\n' "${release_url}"
        printf '日志: %s\n' "${run_log}"
    } >"${BODY_FILE}"
else
    subject="Qlib Release 失败 - ${trade_date}"
    {
        printf '❌ daily-qlib-release-after-close 失败\n'
        printf '交易日: %s (%s)\n' "${trade_date}" "${exchange}"
        printf '状态: %s\n' "${message:-failed}"
        printf '日志: %s\n' "${run_log}"
        if [ -f "${run_log}" ]; then
            printf '\n最近日志（每行最多 2000 字节）:\n'
            tail -n 30 "${run_log}" | cut -c 1-2000 | sed -e 's/[[:cntrl:]]\[[0-9;]*m//g'
        fi
    } >"${BODY_FILE}"
fi

if ! send_output="$(agently-cli message +send --to "${EMAIL_TO}" --subject "${subject}" --body-file "${BODY_FILE_REL}" 2>"${SEND_STDERR_FILE}")"; then
    echo "Failed to request Agent Mail send confirmation." >&2
    sanitize_cli_stderr
    exit 1
fi
confirmation_token="$(printf '%s' "${send_output}" | parse_confirmation_token)"
if ! agently-cli message +send --to "${EMAIL_TO}" --subject "${subject}" --body-file "${BODY_FILE_REL}" --confirmation-token "${confirmation_token}" > /dev/null 2>"${SEND_STDERR_FILE}"; then
    echo "Failed to send Agent Mail notification after confirmation." >&2
    sanitize_cli_stderr
    exit 1
fi

printf '%s' "${notification_key}" >"${NOTIFIED_FILE}"
printf '已发送 qlib release %s 邮件通知到 %s：%s (%s)\n' "${state}" "${EMAIL_TO}" "${trade_date}" "${exchange}"