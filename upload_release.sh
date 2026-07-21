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

REPO="${REPO:-${GITHUB_REPOSITORY:-xu-duqing/investment_data}}"
RELEASE_TIMEZONE="${RELEASE_TIMEZONE:-Asia/Shanghai}"
DATE="${DATE:-$(TZ="${RELEASE_TIMEZONE}" date +%F)}"
ASSET_NAME="${ASSET_NAME:-qlib_bin.tar.gz}"
BODY="${BODY:-Daily Qlib data update}"
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/output}"
FILE_PATH="${FILE_PATH:-${OUTPUT_DIR}/${ASSET_NAME}}"
LOCK_FILE="${UPLOAD_RELEASE_LOCK_FILE:-/tmp/investment_data_upload_release.lock}"
CLEAN_OUTPUT_AFTER_UPLOAD="${CLEAN_OUTPUT_AFTER_UPLOAD:-1}"
REQUEST_MAX_RETRIES="${REQUEST_MAX_RETRIES:-8}"
REQUEST_RETRY_BASE_DELAY="${REQUEST_RETRY_BASE_DELAY:-15}"
REQUEST_RETRY_MAX_DELAY="${REQUEST_RETRY_MAX_DELAY:-180}"
RESPONSE_LOG_BYTES="${RESPONSE_LOG_BYTES:-4000}"

if [[ ! -s "${FILE_PATH}" ]]; then
    echo "Error: archive not found or empty: ${FILE_PATH}" >&2
    exit 1
fi

TOKEN="${GITHUB_PAT:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
if [[ -z "${TOKEN}" ]]; then
    echo "Error: GITHUB_PAT, GH_TOKEN, or GITHUB_TOKEN must be set." >&2
    exit 1
fi

for command_name in curl jq; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Error: ${command_name} is required but not installed." >&2
        exit 1
    fi
done

if command -v flock >/dev/null 2>&1; then
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        echo "Another upload_release.sh is running; skipping release ${DATE}." >&2
        exit 0
    fi
else
    echo "Warning: flock is unavailable; continuing without an upload lock." >&2
fi

RESPONSE_FILE="$(mktemp "${TMPDIR:-/tmp}/upload-release.XXXXXX")"
trap 'rm -f "${RESPONSE_FILE}"' EXIT

API="https://api.github.com/repos/${REPO}"
API_VERSION="2022-11-28"
AUTH_HEADER="Authorization: Bearer ${TOKEN}"
ACCEPT_HEADER="Accept: application/vnd.github+json"
VERSION_HEADER="X-GitHub-Api-Version: ${API_VERSION}"

print_response_excerpt() {
    if [[ ! -s "${RESPONSE_FILE}" ]]; then
        return 0
    fi
    python3 - "${RESPONSE_FILE}" "${RESPONSE_LOG_BYTES}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
limit = int(sys.argv[2])
data = path.read_bytes()
snippet = data[:limit]
sys.stderr.buffer.write(snippet)
if len(data) > limit:
    sys.stderr.write(f"\n... [truncated {len(data) - limit} bytes]\n")
elif data and not data.endswith(b"\n"):
    sys.stderr.write("\n")
PY
}

is_retryable_status() {
    local status="$1"
    case "${status}" in
        000|408|409|425|429|5??) return 0 ;;
        *) return 1 ;;
    esac
}

request_status_once() {
    local status
    if ! status="$(curl -sS --connect-timeout 30 --max-time 300 \
        -o "${RESPONSE_FILE}" -w "%{http_code}" \
        -H "${AUTH_HEADER}" \
        -H "${ACCEPT_HEADER}" \
        -H "${VERSION_HEADER}" \
        "$@")"; then
        status="000"
    fi
    printf '%s' "${status}"
}

request_status() {
    local attempt status delay
    for ((attempt = 1; attempt <= REQUEST_MAX_RETRIES; attempt++)); do
        status="$(request_status_once "$@")"
        if ! is_retryable_status "${status}" || ((attempt == REQUEST_MAX_RETRIES)); then
            printf '%s' "${status}"
            return 0
        fi

        delay=$((REQUEST_RETRY_BASE_DELAY * attempt))
        if ((delay > REQUEST_RETRY_MAX_DELAY)); then
            delay="${REQUEST_RETRY_MAX_DELAY}"
        fi
        echo "GitHub API request returned HTTP ${status}; retrying in ${delay}s (${attempt}/${REQUEST_MAX_RETRIES})..." >&2
        print_response_excerpt
        sleep "${delay}"
    done
}

HTTP_CODE="$(request_status "${API}/releases/tags/${DATE}")"
case "${HTTP_CODE}" in
    200)
        RELEASE_ID="$(jq -er '.id' "${RESPONSE_FILE}")"
        ;;
    404)
        PAYLOAD="$(jq -n \
            --arg tag "${DATE}" \
            --arg name "${DATE}" \
            --arg body "${BODY}" \
            '{tag_name: $tag, name: $name, body: $body, make_latest: "true"}')"
        HTTP_CODE="$(request_status \
            -X POST \
            -H "Content-Type: application/json" \
            -d "${PAYLOAD}" \
            "${API}/releases")"
        if [[ "${HTTP_CODE}" != "201" ]]; then
            echo "Error: unable to create release ${DATE} (HTTP ${HTTP_CODE})." >&2
            print_response_excerpt
            exit 1
        fi
        RELEASE_ID="$(jq -er '.id' "${RESPONSE_FILE}")"
        ;;
    *)
        echo "Error: unable to fetch release ${DATE} (HTTP ${HTTP_CODE})." >&2
        print_response_excerpt
        exit 1
        ;;
esac

get_asset_id() {
    local status
    status="$(request_status "${API}/releases/${RELEASE_ID}/assets")"
    if [[ "${status}" != "200" ]]; then
        echo "Error: unable to list release assets (HTTP ${status})." >&2
        print_response_excerpt
        return 1
    fi
    jq -r --arg name "${ASSET_NAME}" \
        'map(select(.name == $name))[0].id // empty' "${RESPONSE_FILE}"
}

delete_asset() {
    local asset_id="$1"
    [[ -z "${asset_id}" ]] && return 0
    local status
    status="$(request_status -X DELETE "${API}/releases/assets/${asset_id}")"
    if [[ "${status}" != "204" ]]; then
        echo "Error: unable to delete existing asset (HTTP ${status})." >&2
        print_response_excerpt
        return 1
    fi
}

cleanup_output_dir() {
    if [[ "${CLEAN_OUTPUT_AFTER_UPLOAD}" != "1" ]]; then
        return 0
    fi

    if [[ ! -d "${OUTPUT_DIR}" ]]; then
        return 0
    fi

    local output_parent output_name output_abs output_real file_parent
    output_parent="$(cd "$(dirname "${OUTPUT_DIR}")" && pwd -P)"
    output_name="$(basename "${OUTPUT_DIR}")"
    output_abs="${output_parent}/${output_name}"
    output_real="$(cd "${OUTPUT_DIR}" && pwd -P)"
    file_parent="$(cd "$(dirname "${FILE_PATH}")" && pwd -P)"

    if [[ "${file_parent}" != "${output_real}" ]]; then
        echo "Skipping output cleanup; uploaded file is outside OUTPUT_DIR: ${FILE_PATH}"
        return 0
    fi

    if [[ "${output_name}" != "output" ]]; then
        echo "Skipping output cleanup; unexpected OUTPUT_DIR name: ${output_abs}"
        return 0
    fi

    if [[ -z "${output_abs}" || "${output_abs}" == "/" || "${output_abs}" == "${SCRIPT_DIR}" ]]; then
        echo "Refusing to delete unsafe OUTPUT_DIR: ${output_abs}" >&2
        return 1
    fi

    rm -rf -- "${output_abs}"
    echo "Deleted output directory: ${output_abs}"
}

delete_asset "$(get_asset_id)"

ENCODED_ASSET_NAME="$(jq -rn --arg name "${ASSET_NAME}" '$name | @uri')"
UPLOAD_URL="https://uploads.github.com/repos/${REPO}/releases/${RELEASE_ID}/assets?name=${ENCODED_ASSET_NAME}"
MAX_RETRIES="${MAX_RETRIES:-3}"

for ((attempt = 1; attempt <= MAX_RETRIES; attempt++)); do
    echo "Upload attempt ${attempt}/${MAX_RETRIES}..."
    if ! HTTP_CODE="$(curl -sS --connect-timeout 30 --max-time 1800 \
        -o "${RESPONSE_FILE}" -w "%{http_code}" \
        -H "${AUTH_HEADER}" \
        -H "${ACCEPT_HEADER}" \
        -H "${VERSION_HEADER}" \
        -H "Content-Type: application/gzip" \
        --data-binary "@${FILE_PATH}" \
        "${UPLOAD_URL}")"; then
        HTTP_CODE="000"
    fi

    if [[ "${HTTP_CODE}" =~ ^2[0-9][0-9]$ ]]; then
        echo "Upload succeeded (HTTP ${HTTP_CODE})."
        break
    fi

    echo "Upload failed (HTTP ${HTTP_CODE})." >&2
    print_response_excerpt
    if ((attempt == MAX_RETRIES)); then
        echo "Error: upload failed after ${MAX_RETRIES} attempts." >&2
        exit 1
    fi

    delete_asset "$(get_asset_id)"
    sleep $((30 * attempt))
done

HTTP_CODE="$(request_status "${API}/releases/${RELEASE_ID}/assets")"
if [[ "${HTTP_CODE}" != "200" ]]; then
    echo "Error: unable to verify release assets (HTTP ${HTTP_CODE})." >&2
    print_response_excerpt
    exit 1
fi

UPLOADED_SIZE="$(jq -r --arg name "${ASSET_NAME}" \
    'map(select(.name == $name))[0].size // empty' "${RESPONSE_FILE}")"
LOCAL_SIZE="$(stat -c%s "${FILE_PATH}" 2>/dev/null || stat -f%z "${FILE_PATH}")"

if [[ -z "${UPLOADED_SIZE}" || "${UPLOADED_SIZE}" != "${LOCAL_SIZE}" ]]; then
    echo "Error: size mismatch; local=${LOCAL_SIZE}, uploaded=${UPLOADED_SIZE:-missing}." >&2
    exit 1
fi

echo "Verified ${ASSET_NAME} (${UPLOADED_SIZE} bytes) in release ${DATE}."
echo "https://github.com/${REPO}/releases/tag/${DATE}"
cleanup_output_dir
