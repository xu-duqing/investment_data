# investment_data：Qlib 中国市场数据导出与发布工具

[![ch](https://img.shields.io/badge/lang-ch-yellow.svg)](README.md)
[![en](https://img.shields.io/badge/lang-en-red.svg)](docs/README-en.md)

本项目从配置好的 MySQL 数据库读取中国市场行情、复权因子、交易日历、证券信息、指数成分权重和 Tushare `daily_basic` 指标，生成可供 Microsoft Qlib 使用的二进制数据。

每次正式发布包含两个相互兼容的文件：

- `qlib_bin.tar.gz`：主 Qlib 数据包，包含交易日历、证券列表、行情 Feature 和指数成分。
- `daily_basic_qlib_features.tar.gz`：`daily_basic` Feature 增量包，安装到主 Provider 的 `features/` 目录中。

两个包使用完全相同的 Qlib 交易日历。发布脚本会在上传前验证日历 SHA-256，避免 Feature 日期偏移。

> 本项目已经不再使用旧版 Dolt 数据源。当前导出流程以 MySQL 为数据源。

## 目录

- [快速使用](#快速使用)
  - [下载发布文件](#下载发布文件)
  - [安装主数据包](#安装主数据包)
  - [安装 daily_basic 增量包](#安装-daily_basic-增量包)
  - [在 Qlib 中查询](#在-qlib-中查询)
  - [更新和卸载增量包](#更新和卸载增量包)
- [开发环境](#开发环境)
  - [安装依赖](#安装依赖)
  - [配置环境变量](#配置环境变量)
  - [准备 Qlib 源码环境](#准备-qlib-源码环境)
- [数据同步](#数据同步)
  - [同步当天 daily_basic](#同步当天-daily_basic)
  - [历史回填](#历史回填)
- [构建 Qlib 数据](#构建-qlib-数据)
  - [构建主数据包](#构建主数据包)
  - [构建 daily_basic 增量包](#构建-daily_basic-增量包)
  - [联合校验和上传](#联合校验和上传)
- [自动化定时任务](#自动化定时任务)
- [数据结构与处理规则](#数据结构与处理规则)
  - [数据表](#数据表)
  - [行情复权与归一化](#行情复权与归一化)
  - [daily_basic 字段](#daily_basic-字段)
  - [日历对齐](#日历对齐)
- [安全和故障恢复](#安全和故障恢复)
- [验证与测试](#验证与测试)
- [贡献指南](#贡献指南)

# 快速使用

## 下载发布文件

请从同一个 GitHub Release 下载主数据包和 `daily_basic` 增量包。两个文件必须来自同一发布标签，不要混用不同日期的资产。

```bash
RELEASE_TAG=<release-tag>
REPO=xu-duqing/investment_data

wget "https://github.com/${REPO}/releases/download/${RELEASE_TAG}/qlib_bin.tar.gz"
wget "https://github.com/${REPO}/releases/download/${RELEASE_TAG}/daily_basic_qlib_features.tar.gz"
```

也可以使用 GitHub CLI：

```bash
gh release download "${RELEASE_TAG}" \
  --repo xu-duqing/investment_data \
  --pattern 'qlib_bin.tar.gz' \
  --pattern 'daily_basic_qlib_features.tar.gz'
```

下载后可先做基础完整性检查：

```bash
test -s qlib_bin.tar.gz
test -s daily_basic_qlib_features.tar.gz
tar -tzf qlib_bin.tar.gz >/dev/null
tar -tzf daily_basic_qlib_features.tar.gz >/dev/null
```

如果已经克隆本仓库，可进一步验证两个发布包是否使用相同日历：

```bash
python qlib/validate_release_archives.py \
  qlib_bin.tar.gz \
  daily_basic_qlib_features.tar.gz
```

校验成功时会输出两个包共享的日历 SHA-256。

## 安装主数据包

建议先备份现有 Provider，再将主包解压到 Qlib 默认目录：

```bash
mkdir -p ~/.qlib/qlib_data/cn_data
tar -zxvf qlib_bin.tar.gz \
  -C ~/.qlib/qlib_data/cn_data \
  --strip-components=1
```

主包顶层目录为 `qlib_bin/`，因此解压时需要 `--strip-components=1`。

## 安装 daily_basic 增量包

不要直接用 `tar` 将增量包覆盖到 Provider。请使用项目提供的安全安装器：

```bash
python qlib/install_feature_increment.py \
  daily_basic_qlib_features.tar.gz \
  --target-dir ~/.qlib/qlib_data/cn_data
```

安装器会执行以下检查：

1. 校验归档路径和成员类型，拒绝路径穿越、符号链接和异常归档。
2. 校验 manifest、文件清单和 SHA-256。
3. 校验目标 Provider 的 `calendars/day.txt` 与增量包声明的日历完全兼容。
4. 拒绝覆盖来源不明的同名 Feature。
5. 写入安装记录，支持后续安全更新、恢复和卸载。

建议第一次安装时先复制一份 `cn_data` 做验证，不要直接操作唯一的生产数据目录。

## 在 Qlib 中查询

安装完成后，行情和 `daily_basic` 指标位于同一个 Provider，可以通过一次 `qlib.init()` 联合查询：

```python
import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

features = D.features(
    ["SH600000"],
    ["$close", "$pe", "$pb", "$turnover_rate", "$total_mv"],
    start_time="2026-07-01",
    end_time="2026-07-23",
    freq="day",
)
print(features.tail())
```

主行情字段经过 Qlib 行情标准化流程；`daily_basic` 指标保持数据库原值，不复权、不归一化。

## 更新和卸载增量包

如果目标 Provider 已经通过本安装器安装过 `daily_basic`，更新时使用：

```bash
python qlib/install_feature_increment.py \
  daily_basic_qlib_features.tar.gz \
  --target-dir ~/.qlib/qlib_data/cn_data \
  --replace-same-dataset
```

安装中断后可执行恢复：

```bash
python qlib/install_feature_increment.py \
  --target-dir ~/.qlib/qlib_data/cn_data \
  --recover
```

卸载 `daily_basic`：

```bash
python qlib/install_feature_increment.py \
  --target-dir ~/.qlib/qlib_data/cn_data \
  --uninstall daily_basic
```

如果已安装文件被其他程序修改，安装器会拒绝更新或卸载，避免误删用户数据。

# 开发环境

## 安装依赖

系统需要 Git、Python 3 和常见的 POSIX 命令行工具。项目通过 PyMySQL 访问数据库，不要求安装 MySQL 命令行客户端。

```bash
git clone git@github.com:xu-duqing/investment_data.git
cd investment_data

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

主要 Python 依赖包括：

- `pandas`
- `PyMySQL`
- `requests`
- `setuptools-scm`

## 配置环境变量

在仓库根目录创建本地 `.env`。该文件已被 Git 忽略，禁止提交真实凭据或内部地址。

```bash
MYSQL_HOST=<mysql-host>
MYSQL_PORT=<mysql-port>
MYSQL_USER=<mysql-user>
MYSQL_PASSWORD=<mysql-password>
MYSQL_DATABASE=<mysql-database>
MYSQL_EXCHANGE=SSE
TUSHARE_TOKEN=<tushare-token>
GITHUB_PAT=<github-token>
```

说明：

- `MYSQL_EXCHANGE` 默认是 `SSE`。
- `TUSHARE_TOKEN` 用于同步 Tushare `daily_basic`。
- `GITHUB_PAT`、`GH_TOKEN` 或 `GITHUB_TOKEN` 任意一个可用于发布脚本。
- 不要在日志、文档或提交中输出密钥。

## 准备 Qlib 源码环境

`dump_qlib_bin.sh` 默认使用相邻目录中的 `../qlib` 源码，并需要编译 Qlib 的 Cython 扩展：

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

Python 解释器选择顺序为：

1. `PYTHON_BIN` 显式指定的解释器。
2. `../qlib/.venv/bin/python`。
3. 本仓库 `.venv/bin/python`。
4. 系统 `python3`。

# 数据同步

## 同步当天 daily_basic

在收盘后运行以下命令：

```bash
source .venv/bin/activate
python tushare/sync_daily_basic.py
```

默认目标是交易日历中“小于或等于今天”的最近开盘日。因此：

- 当天是交易日且已收盘时，同步当天数据。
- 周末或休市日运行时，定位到最近一个交易日。
- 写入使用 `(ts_code, trade_date)` 唯一键进行幂等 upsert，可安全重跑。

同步指定交易日：

```bash
python tushare/sync_daily_basic.py --trade-date 2026-07-23
```

同步过程会通过 `stock_basic` 将 Tushare `ts_code` 映射为 `qlib_code` 和 `stock_code`。无法映射的证券会被记录并跳过；如果所有证券都无法映射，任务会失败。

## 历史回填

按交易日历回填日期范围：

```bash
python tushare/sync_daily_basic.py \
  --start-date 2020-01-02 \
  --end-date 2026-07-23 \
  --skip-existing
```

常用参数：

- `--skip-existing`：跳过数据库中已经存在的交易日。
- `--request-interval`：API 请求间隔，默认 0.15 秒。
- `--batch-size`：MySQL 批量 upsert 大小，默认 1000。
- `--timeout`：单次 Tushare 请求超时，默认 60 秒。
- `--exchange`：交易所日历，默认读取 `MYSQL_EXCHANGE` 或使用 `SSE`。

# 构建 Qlib 数据

## 构建主数据包

在仓库根目录运行：

```bash
./dump_qlib_bin.sh
```

构建流程：

1. 从 MySQL 导出每只证券的行情 CSV。
2. 结合 `stock_adj_factor` 计算复权价格。
3. 使用 Qlib Yahoo CN 标准化逻辑处理行情。
4. 生成 Qlib `.day.bin` Feature。
5. 写入交易日历和指数成分文件。
6. 打包为 `./output/qlib_bin.tar.gz`。

常用选项：

```bash
TRACE=1 ./dump_qlib_bin.sh
CHECK_FRESHNESS=1 ./dump_qlib_bin.sh
CLEAN_QLIB_BUILD_ROOT=0 ./dump_qlib_bin.sh
QLIB_EXPORT_MAX_WORKERS=8 ./dump_qlib_bin.sh
DUMP_QLIB_MAX_WORKERS=4 ./dump_qlib_bin.sh
```

- `QLIB_EXPORT_MAX_WORKERS` 控制 MySQL 按证券导出的并发数。
- `DUMP_QLIB_MAX_WORKERS` 控制 Qlib CSV 标准化并发数。
- 数据库连接或磁盘 I/O 压力较高时应降低并发。
- 正式发布使用 `CHECK_FRESHNESS=1`，要求 `stock_daily`、交易日历和产物日期一致。

## 构建 daily_basic 增量包

增量包必须基于主包的确切日历构建。使用本地 Provider 时：

```bash
DAILY_BASIC_BASE_PROVIDER=~/.qlib/qlib_data/cn_data \
./dump_daily_basic_qlib_features.sh
```

输出：

```text
./output/daily_basic_qlib_features.tar.gz
```

归档中只包含：

- 白名单 `daily_basic` Feature 二进制文件。
- `manifest.json`。
- `checksums.sha256`。
- `reports/build_summary.json`。

归档不会包含或覆盖：

- `calendars/`
- `instruments/`
- OHLCV、`amount`、`vwap` 等行情字段

若 `daily_basic` 的最大日期与目标基础日历结束日期不一致，构建会失败，不会静默生成缺少当天数据的包。

## 联合校验和上传

上传前可手工校验两个包：

```bash
python qlib/validate_release_archives.py \
  output/qlib_bin.tar.gz \
  output/daily_basic_qlib_features.tar.gz
```

发布：

```bash
./upload_release.sh
```

发布脚本会：

1. 检查两个文件均存在且非空。
2. 验证主包交易日历与增量包 manifest 的日历 SHA-256 一致。
3. 创建或复用当天 GitHub Release。
4. 替换同名资产。
5. 校验远端两个资产的大小。
6. 全部成功后默认清理 `output/`。

保留本地产物：

```bash
CLEAN_OUTPUT_AFTER_UPLOAD=0 ./upload_release.sh
```

使用明确的目标交易日发布时：

```bash
QLIB_RELEASE_TRADE_DATE=2026-07-23 ./upload_release.sh
```

如果同时设置了 `DATE`，它必须与 `QLIB_RELEASE_TRADE_DATE` 一致，否则脚本会拒绝发布，防止资产上传到错误标签。

# 自动化定时任务

当前生产自动化使用 Hermes cron：

1. `tushare-daily-basic-trading-day-sync-email`
   - 每天 18:00（Asia/Shanghai）运行。
   - 检查当天是否为 SSE 交易日。
   - 交易日同步当天 `daily_basic`，并发送成功或失败邮件。
   - 非交易日跳过。

2. `daily-qlib-release-after-close`
   - 每天 19:35（Asia/Shanghai）运行。
   - 仅在当天为交易日时启动。
   - 等待 `stock_daily` 和 `daily_basic` 的最大交易日期都达到当天，默认最长等待 30 分钟。
   - 完成主包和增量包构建、联合校验及上传。

3. `daily-qlib-release-status-notify`
   - 每 10 分钟检查后台发布状态。
   - 对真实发布成功或失败发送通知。
   - 非交易日和 dry-run 不发送成功通知。

版本管理中的生产包装器：

```text
scripts/check_open_dump_upload_qlib.sh
```

Hermes 实际运行的安装副本：

```text
~/.hermes/scripts/check_open_dump_upload_qlib.sh
```

发布任务使用原子目录锁防止重复运行。运行状态与日志分别位于：

```text
.qlib_release_state/daily-qlib-release.status
logs/qlib_release_<trade-date>_<timestamp>.log
logs/qlib_release_handoff.log
```

# 数据结构与处理规则

## 数据表

主数据包读取以下 MySQL 表：

- `stock_daily`：日行情。
- `stock_adj_factor`：复权因子。
- `stock_basic`：证券代码与 Qlib 代码映射。
- `trade_calendar`：交易日历。
- `index_weight`：指数成分权重。

增量包读取：

- `daily_basic`：换手率、估值、股本、市值和股息率等日频指标。

## 行情复权与归一化

主行情构建不是简单保存原始价格：

1. 导出阶段计算 `adjclose = close × adj_factor`。
2. Qlib 标准化阶段根据 `adjclose / close` 处理 OHLC、VWAP 和成交量。
3. 价格序列按每只证券首个有效复权收盘价进一步归一化。

因此 `$open`、`$high`、`$low`、`$close`、`$vwap` 是适合 Qlib 研究使用的复权归一化序列，不应直接视为人民币原始成交价。

`daily_basic` 指标不进入行情标准化流程，保持数据库原值。

## daily_basic 字段

当前增量包包含以下 15 个 Feature：

- `turnover_rate`
- `turnover_rate_f`
- `volume_ratio`
- `pe`
- `pe_ttm`
- `pb`
- `ps`
- `ps_ttm`
- `dv_ratio`
- `dv_ttm`
- `total_share`
- `float_share`
- `free_share`
- `total_mv`
- `circ_mv`

这些字段使用原字段名，在 Qlib 中通过 `$pe`、`$pb`、`$turnover_rate` 等方式查询。

Tushare `daily_basic` 接口不提供 `limit_status`，因此该字段不包含在当前发布增量包中。

## 日历对齐

Qlib `.day.bin` 的第一个 float32 数值是数据在 Provider 全局交易日历中的起始下标。若主包和增量包使用不同日历，即使文件名和日期看起来正确，查询结果也会整体错位。

因此本项目执行以下约束：

- 增量包构建时读取基础 Provider 的 `calendars/day.txt`。
- manifest 保存日历起止日期、行数和 SHA-256。
- 上传前验证主包日历 SHA-256。
- 安装前再次验证目标 Provider 日历。
- 增量包自身不携带或替换日历。

# 安全和故障恢复

- `.env`、数据库密码、Tushare Token 和 GitHub Token 不得提交。
- 发布前必须同时验证主包和增量包，不能只验证其中一个。
- 不要手工覆盖已有同名 Feature；使用安全安装器。
- 安装器发现未知冲突或已安装文件被修改时会拒绝操作。
- 更新过程中会创建备份和恢复 journal；发生异常时执行 `--recover`。
- 定时发布采用锁保护，已有存活进程时不会启动第二个任务。
- 后台任务通过状态文件区分 `starting`、`running`、`completed`、`failed` 和 `skipped`。
- `dry-run` 最终记录为 `skipped`，不会被通知器误报为正式发布成功。

# 验证与测试

运行完整单元测试：

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

运行双资产发布模拟测试：

```bash
bash tests/test_upload_release.sh
```

Shell 和 Python 语法检查：

```bash
bash -n dump_qlib_bin.sh
bash -n dump_daily_basic_qlib_features.sh upload_release.sh
bash -n scripts/check_open_dump_upload_qlib.sh
python -m py_compile qlib/*.py tushare/*.py tests/*.py
```

小规模 MySQL 行情导出：

```bash
mkdir -p /tmp/qlib_mysql_test/source
QLIB_SOURCE_DIR=/tmp/qlib_mysql_test/source \
QLIB_EXPORT_SYMBOL_LIMIT=2 \
python qlib/dump_all_to_qlib_source.py
```

小规模指数成分导出：

```bash
mkdir -p /tmp/qlib_mysql_test/index
QLIB_INDEX_DIR=/tmp/qlib_mysql_test/index \
QLIB_INDEX_MARKETS=csi300 \
python qlib/dump_index_weight.py
```

正式发布前至少确认：

1. `stock_daily` 和 `daily_basic` 最大日期均为目标交易日。
2. 主包日历最后日期与目标交易日一致。
3. 两个归档日历 SHA-256 一致。
4. 增量包不包含行情字段、日历或证券列表。
5. 在临时 Provider 中安装后，可以通过一次 `qlib.init()` 同时查询 `$close` 和 `$pe`。

# 贡献指南

## 增加指数

增加新的指数通常需要：

1. 扩展指数权重采集或数据库写入逻辑。
2. 确保 `index_weight` 中存在完整、可验证的历史记录。
3. 修改 `qlib/dump_index_weight.py` 的市场配置和输出命名。
4. 增加测试，验证成分区间的起止日期和最终 Provider 中的 instruments 文件。

## 增加数据源或字段

建议先创建 Issue 讨论，至少说明：

1. 为什么需要这组数据，它解决什么研究问题？
2. 数据源是什么，授权和稳定性如何？
3. 历史数据如何回填？
4. 每日更新时间是什么，如何判断更新完成？
5. 如何验证完整性、准确性和证券代码映射？
6. 出现异常数据时如何阻止发布和恢复？
7. 是修改已有表，还是增加新表？
8. 新字段是否与现有 Qlib Feature 重名？
9. 新增二进制 Feature 如何复用并验证主 Provider 日历？

新增 Feature 应使用显式白名单，禁止通过“排除已知行情字段”的方式推断字段归属。涉及第三方数据源时，应保留清晰的来源信息和更新边界。

高质量、可验证、可恢复的数据比单纯增加数据量更重要。错误或错位的数据会产生看似合理但实际错误的研究结论。
