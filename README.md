# investment_data：Qlib 中国市场数据导出与发布工具

[![ch](https://img.shields.io/badge/lang-ch-yellow.svg)](README.md)
[![en](https://img.shields.io/badge/lang-en-red.svg)](docs/README-en.md)

本项目从配置好的 MySQL 数据库读取中国市场行情、复权因子、交易日历、证券信息、指数成分权重和 Tushare `daily_basic` 指标，生成可供 Microsoft Qlib 使用的二进制数据。

## 使用 deep-alpha 下载和更新数据

[deep-alpha](https://github.com/xu-duqing/deep-alpha) 是配套的 Qlib 离线数据命令行工具，可用于下载、检查和更新本项目发布的数据。

### 下载日线行情

首次使用时，先安装基础行情数据：

```bash
deep-alpha download
```

### 下载每日指标

基础行情安装完成后，再安装 `daily-basic` 增量包：

```bash
deep-alpha download --dataset daily-basic
```

### 检查本地数据

```bash
deep-alpha info
```

### 更新数据

更新基础行情：

```bash
deep-alpha update
```

更新 `daily-basic` 指标：

```bash
deep-alpha update --dataset daily-basic
```

每次正式发布包含两个相互兼容的文件：

- `qlib_bin.tar.gz`：主 Qlib 数据包，包含交易日历、证券列表、行情 Feature 和指数成分。
- `daily_basic_qlib_features.tar.gz`：`daily_basic` Feature 增量包，安装到主 Provider 的 `features/` 目录中。

两个包使用完全相同的 Qlib 交易日历。发布脚本会在上传前验证日历 SHA-256，避免 Feature 日期偏移。

## 目录

- [使用 deep-alpha 下载和更新数据](#使用-deep-alpha-下载和更新数据)
  - [下载日线行情](#下载日线行情)
  - [下载每日指标](#下载每日指标)
  - [检查本地数据](#检查本地数据)
  - [更新数据](#更新数据)
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

## 行情复权与归一化

主行情构建不是简单保存原始价格：

1. 导出阶段计算 `adjclose = close × adj_factor`。
2. Qlib 标准化阶段根据 `adjclose / close` 处理 OHLC、VWAP 和成交量。
3. 价格序列按每只证券首个有效复权收盘价进一步归一化。

因此 `$open`、`$high`、`$low`、`$close`、`$vwap` 是适合 Qlib 研究使用的复权归一化序列，不应直接视为人民币原始成交价。

`daily_basic` 指标不进入行情标准化流程，保持数据库原值。

## daily_basic 字段

当前增量包包含以下 15 个 Feature：


字段名 | 中文名称 | 详细解释
-- | -- | --
turnover_rate | 换手率 (日) | 当日股票成交股本 / 流通股本 ×100%，衡量个股当日交易活跃度；数值越高筹码交换越频繁，短期波动通常更大
turnover_rate_f | 自由流通换手率 | 当日成交量 / 自由流通股本（剔除大股东锁定、长期持股股份）计算的换手率，更真实反映散户 / 短线资金实际交易热度
volume_ratio | 量比 | 当前累计成交量 / 过去 5 日同一时段平均成交量；>1 代表放量，<1 代表缩量，用于判断当日资金相较近期是否异动
pe | 静态市盈率 | 总市值 ÷ 上一完整会计年度归母净利润；依赖去年全年业绩，若企业当期利润大幅波动会失真
pe_ttm | 滚动市盈率 (TTM) | 总市值 ÷ 过去连续 12 个月归母净利润，市场最常用估值指标，平滑季度业绩波动，适合绝大多数盈利企业估值对比
pb | 市净率 | 总市值 ÷ 归属母公司股东净资产；重资产、银行、周期股核心估值指标，PB<1 俗称破净，代表股价低于账面净资产
ps | 静态市销率 | 总市值 ÷ 上一年度营业收入；适用于尚未盈利、亏损但营收增长的企业，避开净利润为负无法用 PE 估值的场景
ps_ttm | 滚动市销率 (TTM) | 总市值 ÷ 过去 12 个月累计营业收入，动态反映营收估值，比静态 PS 时效性更强
dv_ratio | 静态股息率 | 上一完整年度现金分红总额 / 当前总市值 ×100%，衡量去年全年分红带来的现金回报
dv_ttm | 滚动股息率 (TTM) | 过去 12 个月实际现金分红总额 / 当前总市值，动态股息收益指标，不受财年划分影响
total_share | 总股本 | 上市公司全部发行股份总数，包含限售股 + 流通股，计算总市值基础：total_share * 收盘价 = total_mv
float_share | 流通股本 | 二级市场可正常交易的股份（不含首发锁定、定增锁定、大股东限售股份），用于计算常规换手率
free_share | 自由流通股本 | 在流通股本基础上，剔除持股 5% 以上大股东、战略投资者长期持有的股份；仅统计真正可自由买卖的筹码，对应自由流通换手率
total_mv | 总市值 | 总股本 × 当前股票收盘价，代表公司整体市场价值，大盘 / 小盘划分核心依据
circ_mv | 流通市值 | 流通股本 × 当前股票收盘价，仅统计二级市场可交易股份的市值，短线资金博弈主要参考规模)

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
