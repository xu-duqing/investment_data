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
python -m py_compile qlib/*.py tushare/*.py
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
