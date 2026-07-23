# investment_data qlib exporter

This repository exports market data from a configured MySQL database into Qlib
binary data and packages the result as `qlib_bin.tar.gz`.

## Data Source

The export scripts read these tables:

- `stock_daily`
- `stock_adj_factor`
- `stock_basic`
- `trade_calendar`
- `index_weight`

The old Dolt data source is no longer used.

## Requirements

Install system tools:

```bash
git --version
```

The exporter uses `PyMySQL`, so a system `mysql` client is not required. This
keeps the dump script runnable on older macOS versions where the Homebrew MySQL
client is not available.

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If the virtual environment already exists, activate it and rerun
`pip install -r requirements.txt` after dependency changes.

Prepare Qlib in its own virtual environment. The dump script uses Qlib source
from `../qlib` by default and needs its Cython extensions compiled in place:

```bash
cd ../qlib
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r ../investment_data/requirements.txt
python -m pip install -e ".[test]"
python -m pip install -r scripts/data_collector/yahoo/requirements.txt
python setup.py build_ext --inplace
```

When `../qlib/.venv/bin/python` exists, `dump_qlib_bin.sh` uses it first. It
falls back to this repository's `.venv/bin/python`, then `python3`. You can
override this with `PYTHON_BIN=/path/to/python`.

## Run

From the repository root:

```bash
./dump_qlib_bin.sh
```

The script clones Qlib into the parent working directory by default and writes
the generated archive to `./output/qlib_bin.tar.gz`.

`upload_release.sh` publishes both `./output/qlib_bin.tar.gz` and
`./output/daily_basic_qlib_features.tar.gz` to the same GitHub Release. It
checks that both archives exist and are non-empty before creating or changing a
release, replaces same-named assets, and verifies both uploaded sizes. After
both assets pass verification, it deletes `./output` by default. Set
`CLEAN_OUTPUT_AFTER_UPLOAD=0` to keep it.

The upload paths and names can be overridden independently:

```bash
MAIN_ASSET_NAME=qlib_bin.tar.gz
MAIN_FILE_PATH=./output/qlib_bin.tar.gz
DAILY_BASIC_ASSET_NAME=daily_basic_qlib_features.tar.gz
DAILY_BASIC_FILE_PATH=./output/daily_basic_qlib_features.tar.gz
./upload_release.sh
```

The legacy `ASSET_NAME` and `FILE_PATH` variables remain aliases for the main
archive only. Supplying them does not disable the required daily-basic asset.

Configure the MySQL connection in a local `.env` file before running the export.
The file is ignored by git; do not commit real credentials or internal endpoints.

```bash
MYSQL_HOST=<mysql-host>
MYSQL_PORT=<mysql-port>
MYSQL_USER=<mysql-user>
MYSQL_PASSWORD=<mysql-password>
MYSQL_DATABASE=<mysql-database>
MYSQL_EXCHANGE=SSE
TUSHARE_TOKEN=<tushare-token>
```

Then run:

```bash
./dump_qlib_bin.sh
```

## Sync Tushare daily indicators

Sync Tushare's `daily_basic` endpoint into the MySQL `daily_basic` table:

```bash
python tushare/sync_daily_basic.py
```

By default, the script uses the latest open trading day on or before today from
`trade_calendar`, so an after-close run syncs the current trading day. To
backfill or rerun a specific day, pass an explicit date;
the write is idempotent through the table's `(ts_code, trade_date)` unique key:

```bash
python tushare/sync_daily_basic.py --trade-date 2026-07-20
```

Symbols are mapped to `qlib_code` and `stock_code` through `stock_basic`.
`limit_status` is not returned by Tushare's `daily_basic` endpoint and remains
at the table default (or its existing value on an update).

## Build and install daily-basic Qlib features

Build the independently published Feature increment against the exact calendar
of an existing `cn_data` provider:

```bash
DAILY_BASIC_BASE_PROVIDER=~/.qlib/qlib_data/cn_data \
./dump_daily_basic_qlib_features.sh
```

This creates `./output/daily_basic_qlib_features.tar.gz`. The archive contains
only the 15 approved daily-basic fields, a manifest, checksums, and a build
report. It never contains calendars, instruments, OHLCV, amount, or vwap. The
builder fails instead of truncating if MySQL `daily_basic` is newer than the
base provider calendar.

Install into a copy of `cn_data` first:

```bash
python qlib/install_feature_increment.py \
  output/daily_basic_qlib_features.tar.gz \
  --target-dir ~/.qlib/qlib_data/cn_data
```

Unknown same-name Feature files are never overwritten. To update a package
previously installed by this installer, use `--replace-same-dataset`. Recovery
and uninstall commands are:

```bash
python qlib/install_feature_increment.py --target-dir ~/.qlib/qlib_data/cn_data --recover
python qlib/install_feature_increment.py --target-dir ~/.qlib/qlib_data/cn_data --uninstall daily_basic
```

`upload_release.sh` verifies that the main archive calendar SHA-256 equals the
daily-basic manifest calendar SHA-256 before making any GitHub API request.

The scheduled release wrapper is versioned at
`scripts/check_open_dump_upload_qlib.sh`; Hermes runs its installed copy from
`~/.hermes/scripts/`. It runs after the same-day indicator sync. On an open
trading day it builds the main archive first,
derives daily-basic features from that exact provider/calendar, then validates
and uploads both assets as one release transaction. Freshness checks require
both `stock_daily` and `daily_basic` to reach that same trading date.

Useful runtime options:

```bash
TRACE=1 ./dump_qlib_bin.sh
CHECK_FRESHNESS=1 ./dump_qlib_bin.sh
CLEAN_QLIB_BUILD_ROOT=0 ./dump_qlib_bin.sh
QLIB_EXPORT_MAX_WORKERS=8 ./dump_qlib_bin.sh
DUMP_QLIB_MAX_WORKERS=4 ./dump_qlib_bin.sh
```

`QLIB_EXPORT_MAX_WORKERS` controls concurrent MySQL symbol exports. Start with
8 and reduce it if the database server reaches its connection or I/O limits.
`DUMP_QLIB_MAX_WORKERS` separately controls CSV normalization workers.

## Validation

Run syntax checks:

```bash
bash -n dump_qlib_bin.sh
bash -n dump_daily_basic_qlib_features.sh upload_release.sh
python -m py_compile qlib/*.py tushare/*.py
python -m unittest discover -s tests -v
python -c "import pymysql, setuptools_scm"
PYTHONPATH=../qlib:../qlib/scripts ../qlib/.venv/bin/python -c "import qlib; from qlib.data._libs.rolling import rolling_slope; from qlib.data._libs.expanding import expanding_slope; from data_collector.yahoo import collector"
```

Run a small MySQL export sample:

```bash
mkdir -p /tmp/qlib_mysql_test/source
QLIB_SOURCE_DIR=/tmp/qlib_mysql_test/source \
QLIB_EXPORT_SYMBOL_LIMIT=2 \
python qlib/dump_all_to_qlib_source.py
```

Run a small index constituent export sample:

```bash
mkdir -p /tmp/qlib_mysql_test/index
QLIB_INDEX_DIR=/tmp/qlib_mysql_test/index \
QLIB_INDEX_MARKETS=csi300 \
python qlib/dump_index_weight.py
```

## Output Layout

During a build, the script creates a temporary build directory containing:

- `qlib_source/`: raw per-symbol CSV files read from MySQL.
- `qlib_normalize/`: normalized CSV files.
- `qlib_index/`: index constituent instrument files.
- `qlib_bin/`: final Qlib binary provider directory.

Only `qlib_bin.tar.gz` is kept after a successful run when
`CLEAN_QLIB_BUILD_ROOT=1`.
