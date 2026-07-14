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
mysql --version
git --version
```

On macOS, install the MySQL client if needed:

```bash
brew install mysql
```

Install Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If the virtual environment already exists, activate it and rerun
`pip install -r requirements.txt` after dependency changes.

When `.venv/bin/python` exists, `dump_qlib_bin.sh` uses it automatically. You
can override this with `PYTHON_BIN=/path/to/python`.

## Run

From the repository root:

```bash
./dump_qlib_bin.sh
```

The script clones Qlib into the parent working directory by default and writes
the generated archive to `./output/qlib_bin.tar.gz`.

Configure the MySQL connection in a local `.env` file before running the export.
The file is ignored by git; do not commit real credentials or internal endpoints.

```bash
MYSQL_HOST=<mysql-host>
MYSQL_PORT=<mysql-port>
MYSQL_USER=<mysql-user>
MYSQL_PASSWORD=<mysql-password>
MYSQL_DATABASE=<mysql-database>
MYSQL_EXCHANGE=SSE
```

Then run:

```bash
./dump_qlib_bin.sh
```

Useful runtime options:

```bash
TRACE=1 ./dump_qlib_bin.sh
CHECK_FRESHNESS=1 ./dump_qlib_bin.sh
CLEAN_QLIB_BUILD_ROOT=0 ./dump_qlib_bin.sh
DUMP_QLIB_MAX_WORKERS=4 ./dump_qlib_bin.sh
```

## Validation

Run syntax checks:

```bash
bash -n dump_qlib_bin.sh
python -m py_compile qlib/*.py tushare/*.py
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
