#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/upload-release-test.XXXXXX")"
trap 'rm -rf "${TMP_ROOT}"' EXIT

MOCK_BIN="${TMP_ROOT}/bin"
mkdir -p "${MOCK_BIN}"
cat >"${MOCK_BIN}/python3" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == */qlib/validate_release_archives.py ]]; then
    printf '%s\n' "validated release archives" >&2
    exit 0
fi
exec /usr/bin/python3 "$@"
SH
chmod +x "${MOCK_BIN}/python3"
cat >"${MOCK_BIN}/curl" <<'PY'
#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import urllib.parse

args = sys.argv[1:]
out = pathlib.Path(args[args.index("-o") + 1])
url = args[-1]
state_path = pathlib.Path(os.environ["MOCK_CURL_STATE"])
log_path = pathlib.Path(os.environ["MOCK_CURL_LOG"])
state = json.loads(state_path.read_text()) if state_path.exists() else {"assets": []}
method = args[args.index("-X") + 1] if "-X" in args else "GET"
body = {}
status = "200"

with log_path.open("a") as log:
    log.write(f"{method} {url}\n")

if "/releases/tags/" in url:
    body = {"id": 101}
elif url.endswith("/releases/101/assets"):
    body = state["assets"]
elif "uploads.github.com" in url:
    name = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["name"][0]
    data_arg = args[args.index("--data-binary") + 1]
    file_path = pathlib.Path(data_arg.removeprefix("@"))
    asset = {"id": len(state["assets"]) + 1, "name": name, "size": file_path.stat().st_size}
    state["assets"] = [item for item in state["assets"] if item["name"] != name]
    state["assets"].append(asset)
    body = asset
    status = "201"
elif "/releases/assets/" in url and method == "DELETE":
    asset_id = int(url.rsplit("/", 1)[1])
    state["assets"] = [item for item in state["assets"] if item["id"] != asset_id]
    status = "204"
else:
    body = {"error": f"unexpected request: {method} {url}"}
    status = "500"

state_path.write_text(json.dumps(state))
out.write_text(json.dumps(body))
sys.stdout.write(status)
PY
chmod +x "${MOCK_BIN}/curl"

assert_contains() {
    local needle="$1"
    local file="$2"
    if ! grep -Fq -- "${needle}" "${file}"; then
        echo "Expected '${needle}' in ${file}" >&2
        cat "${file}" >&2
        exit 1
    fi
}

# Missing either archive must fail before any GitHub API request.
missing_output="${TMP_ROOT}/missing/output"
mkdir -p "${missing_output}"
printf 'main' >"${missing_output}/qlib_bin.tar.gz"
if PATH="${MOCK_BIN}:${PATH}" PYTHON_BIN="${MOCK_BIN}/python3" GITHUB_TOKEN=test \
    OUTPUT_DIR="${missing_output}" CLEAN_OUTPUT_AFTER_UPLOAD=0 \
    MOCK_CURL_STATE="${TMP_ROOT}/missing-state.json" \
    MOCK_CURL_LOG="${TMP_ROOT}/missing-curl.log" \
    bash "${REPO_DIR}/upload_release.sh" >"${TMP_ROOT}/missing.out" 2>&1; then
    echo "Expected upload to fail when daily-basic archive is missing" >&2
    exit 1
fi
assert_contains "archive not found or empty" "${TMP_ROOT}/missing.out"
if [[ -e "${TMP_ROOT}/missing-curl.log" ]]; then
    echo "GitHub API must not be called when an archive is missing" >&2
    exit 1
fi

# Both archives are uploaded, size-verified, and cleaned only after success.
success_output="${TMP_ROOT}/success/output"
mkdir -p "${success_output}"
printf 'main-archive' >"${success_output}/qlib_bin.tar.gz"
printf 'daily-basic-archive' >"${success_output}/daily_basic_qlib_features.tar.gz"
cat >"${TMP_ROOT}/success-state.json" <<'JSON'
{"assets":[{"id":41,"name":"qlib_bin.tar.gz","size":1},{"id":42,"name":"daily_basic_qlib_features.tar.gz","size":1}]}
JSON
PATH="${MOCK_BIN}:${PATH}" PYTHON_BIN="${MOCK_BIN}/python3" GITHUB_TOKEN=*** \
    OUTPUT_DIR="${success_output}" DATE=2026-07-22 \
    REQUEST_MAX_RETRIES=1 MAX_RETRIES=1 \
    MOCK_CURL_STATE="${TMP_ROOT}/success-state.json" \
    MOCK_CURL_LOG="${TMP_ROOT}/success-curl.log" \
    bash "${REPO_DIR}/upload_release.sh" >"${TMP_ROOT}/success.out" 2>&1

assert_contains "Verified qlib_bin.tar.gz" "${TMP_ROOT}/success.out"
assert_contains "Verified daily_basic_qlib_features.tar.gz" "${TMP_ROOT}/success.out"
assert_contains "name=qlib_bin.tar.gz" "${TMP_ROOT}/success-curl.log"
assert_contains "name=daily_basic_qlib_features.tar.gz" "${TMP_ROOT}/success-curl.log"
assert_contains "/releases/assets/41" "${TMP_ROOT}/success-curl.log"
assert_contains "/releases/assets/42" "${TMP_ROOT}/success-curl.log"
if [[ -e "${success_output}" ]]; then
    echo "Expected output directory cleanup after both assets were verified" >&2
    exit 1
fi

python3 - "${TMP_ROOT}/success-state.json" <<'PY'
import json
import pathlib
import sys
assets = json.loads(pathlib.Path(sys.argv[1]).read_text())["assets"]
assert {item["name"] for item in assets} == {
    "qlib_bin.tar.gz",
    "daily_basic_qlib_features.tar.gz",
}
assert all(item["size"] > 0 for item in assets)
PY

echo "upload_release.sh dual-asset tests passed"
