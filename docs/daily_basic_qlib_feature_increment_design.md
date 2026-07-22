# Daily Basic Qlib Feature 增量包技术设计

## 1. 背景与目标

当前项目将 MySQL 中的 A 股日线行情导出为 Qlib binary provider，并发布 `qlib_bin.tar.gz`。主数据安装在 `~/.qlib/qlib_data/cn_data`，包含交易日历、股票列表和行情 Feature。

项目已同步 Tushare `daily_basic` 到 MySQL `daily_basic` 表。为了让 Qlib 模型能在一次 `qlib.init(provider_uri=".../cn_data")` 后同时读取行情和每日指标，需要新增独立构建、独立发布、安装时安全合并的 Feature 增量包。

目标：

1. 保持现有 `qlib_bin.tar.gz` 的构建、内容和发布流程不变。
2. 独立生成 `daily_basic_qlib_features.tar.gz`。
3. 增量包只包含 `daily_basic` 指标 Feature，不包含 OHLCV 等行情字段。
4. 安装时将增量 Feature 合并到 `cn_data/features/`。
5. 使用 `cn_data` 的同一份交易日历生成二进制，保证日期索引完全一致。
6. 安装前检测 Feature key 和目标文件冲突，默认拒绝覆盖。
7. 安装完成后可通过一次 `qlib.init()` 联合查询行情和每日指标。

## 2. 已确认约束

1. `daily_basic` 是增量包，不是第二个独立运行的 Qlib provider。
2. 包可以独立下载和发布，但最终安装目标是现有 `cn_data`。
3. 不采用第二次 `qlib.init()` 切换 provider 的方式；Qlib provider 配置是进程级全局状态，第二次初始化会替换第一次配置。
4. 指标字段保持业务名称，不增加 `ts_` 字段前缀。
5. 增量包不导出 `close`，也不导出 `open/high/low/volume/amount/vwap/adjclose/factor/change`。
6. 不覆盖 `cn_data/calendars/` 和 `cn_data/instruments/`。
7. `daily_basic` 指标保持数据库原值，不做复权、不做首日归一化。

## 3. 非目标

首版不做以下能力：

1. 不修改现有行情包字段或归一化算法。
2. 不把 `daily_basic` 制作为可单独 `qlib.init()` 的完整 provider。
3. 不支持分钟级指标。
4. 不自动覆盖同名 Feature。
5. 不负责计算 Tushare 未提供的 `limit_status`；该字段只有在数据库中具有可信值时才发布。
6. 不在构建阶段填充缺失值，缺失数据保留为 `NaN`。

## 4. 数据源与字段

### 4.1 数据表关联

以 `stock_basic` 为证券主表，以 `daily_basic` 为指标表：

```sql
SELECT
    b.qlib_code AS symbol,
    DATE_FORMAT(d.trade_date, '%Y-%m-%d') AS tradedate,
    d.turnover_rate,
    d.turnover_rate_f,
    d.volume_ratio,
    d.pe,
    d.pe_ttm,
    d.pb,
    d.ps,
    d.ps_ttm,
    d.dv_ratio,
    d.dv_ttm,
    d.total_share,
    d.float_share,
    d.free_share,
    d.total_mv,
    d.circ_mv
FROM daily_basic d
JOIN stock_basic b ON b.ts_code = d.ts_code
WHERE b.qlib_code <> ''
ORDER BY b.qlib_code, d.trade_date;
```

关联键必须使用 `ts_code`，输出证券代码使用 `stock_basic.qlib_code`。不能在导出阶段自行推导交易所前缀。

### 4.2 Feature 白名单

首版允许发布：

| Qlib key | MySQL 字段 | 说明 |
| --- | --- | --- |
| `$turnover_rate` | `turnover_rate` | 换手率 |
| `$turnover_rate_f` | `turnover_rate_f` | 自由流通股换手率 |
| `$volume_ratio` | `volume_ratio` | 量比 |
| `$pe` | `pe` | 市盈率 |
| `$pe_ttm` | `pe_ttm` | 滚动市盈率 |
| `$pb` | `pb` | 市净率 |
| `$ps` | `ps` | 市销率 |
| `$ps_ttm` | `ps_ttm` | 滚动市销率 |
| `$dv_ratio` | `dv_ratio` | 股息率 |
| `$dv_ttm` | `dv_ttm` | 滚动股息率 |
| `$total_share` | `total_share` | 总股本，单位保持万元/万股口径中的原表定义 |
| `$float_share` | `float_share` | 流通股本 |
| `$free_share` | `free_share` | 自由流通股本 |
| `$total_mv` | `total_mv` | 总市值，单位保持原表定义 |
| `$circ_mv` | `circ_mv` | 流通市值，单位保持原表定义 |

`limit_status` 暂不进入首版白名单。当前 Tushare `daily_basic` 接口不返回该字段，数据库默认值 `0` 不能代表可信的真实涨跌停状态。后续有独立计算或可信来源后再通过设计变更加入。

## 5. 核心架构

```text
MySQL daily_basic + stock_basic
              |
              v
per-symbol source CSV
              |
              |  使用 cn_data/calendars/day.txt 对齐
              v
daily_basic feature binary staging
              |
              |  manifest + checksum + conflict inventory
              v
daily_basic_qlib_features.tar.gz
              |
              |  安装前校验并原子合并
              v
~/.qlib/qlib_data/cn_data/features/<symbol>/*.day.bin
```

构建和安装分离：构建器只生产增量归档；安装器负责检查目标 `cn_data`、校验日历指纹、检查冲突并合并。现有 `dump_qlib_bin.sh` 不调用该构建器。

## 6. 交易日历与二进制对齐

### 6.1 为什么必须复用 cn_data 日历

Qlib Feature `.bin` 文件的第一个 float32 值不是日期，而是该序列首条数据在 provider 全局日历中的起始下标；后续 float32 值按日历连续排列。Qlib `dump_bin.py` 的写入逻辑等价于：

```python
np.hstack([date_index, feature_values]).astype("<f").tofile(bin_path)
```

因此，如果用从 2020-01-02 开始的独立日历生成 `daily_basic` 二进制，再复制到从 2000 年开始的 `cn_data`，下标会被解释为错误日期，所有指标都会错位。

### 6.2 构建规则

构建器必须接收主 provider：

```text
--base-provider ~/.qlib/qlib_data/cn_data
```

并完成：

1. 读取 `<base-provider>/calendars/day.txt`，保持顺序不变。
2. 校验日历非空、严格递增、无重复。
3. 计算日历文件 SHA-256，写入 manifest。
4. 每只股票的数据按该日历 reindex。
5. 2020-01-02 之前及个股无数据日期保持 `NaN`。
6. 以该日历中的真实起始下标写入 `.day.bin`。
7. 不在增量包中携带或覆盖 `calendars/day.txt`。

为了让安装器能够验证兼容性，manifest 同时记录：

- `base_calendar_sha256`
- `base_calendar_start`
- `base_calendar_end`
- `base_calendar_count`

如果构建期间 MySQL 数据日期晚于主日历末日，默认构建失败，不能静默截断。应先更新主 `cn_data`，再重新构建增量包。

### 6.3 instruments 规则

增量包不携带 `instruments/all.txt`。只导出同时满足以下条件的证券：

1. `stock_basic.qlib_code` 非空。
2. 证券目录已存在于 `<base-provider>/features/`，或证券存在于主 provider 的 `instruments/all.txt`。
3. 至少存在一条 `daily_basic` 数据。

无法映射到主 provider 的证券写入构建报告并跳过；超过配置阈值时构建失败。

## 7. 源 CSV 与 binary 生成

### 7.1 临时目录

建议构建目录：

```text
<build-root>/
├── daily_basic_source/
├── daily_basic_bin/
│   └── features/
├── reports/
└── package/
```

每只股票一个 CSV：

```text
symbol,tradedate,turnover_rate,turnover_rate_f,volume_ratio,pe,...,circ_mv
SH600000,2020-01-02,0.43,0.39,0.91,6.21,...,11400000.00
```

只允许表 4.2 的字段进入 CSV。SQL 查询结果中的 `NULL` 写为空值。

### 7.2 不使用行情 Normalize

当前 `qlib/normalize.py` 继承 `YahooNormalizeCN1d`，会按 `adjclose/close` 复权并按首日收盘价归一化，适用于行情，不适用于估值和市值指标。

增量流水线不得调用该 normalizer。指标必须原样进入 `dump_bin.py`。实现可以：

- 新增专用导出器直接生成已对齐 CSV，再调用 Qlib dump；或
- 新增专用 binary writer，复用 Qlib 的日历对齐和 float32 写入约定。

优先选择复用 `../qlib/scripts/dump_bin.py` 的实现或公开入口，避免手写格式漂移。但由于标准 `dump_all` 会从源 CSV 自行生成 calendar/instruments，首版需要新增一个显式使用基础 provider 日历的 wrapper，不能直接把独立 `dump_all` 产物复制进 `cn_data`。

### 7.3 精度

Qlib binary Feature 使用 little-endian float32。MySQL decimal 转换为 float32 会有预期精度损失。设计接受该限制，但构建验证需按字段设置合理容差，例如：

```text
abs(binary_value - source_value) <= max(1e-5, abs(source_value) * 1e-6)
```

## 8. 增量包格式

归档名：

```text
daily_basic_qlib_features.tar.gz
```

归档内容：

```text
daily_basic/
├── manifest.json
├── checksums.sha256
├── reports/
│   └── build_summary.json
└── features/
    ├── sh600000/
    │   ├── turnover_rate.day.bin
    │   ├── pe.day.bin
    │   └── ...
    └── sz000001/
        └── ...
```

归档严禁包含：

```text
calendars/
instruments/
features/*/open.day.bin
features/*/high.day.bin
features/*/low.day.bin
features/*/close.day.bin
features/*/volume.day.bin
features/*/amount.day.bin
features/*/vwap.day.bin
```

### 8.1 manifest.json

建议格式：

```json
{
  "schema_version": 1,
  "package_type": "qlib_feature_increment",
  "dataset": "daily_basic",
  "frequency": "day",
  "generated_at": "2026-07-21T18:30:00+08:00",
  "source_min_date": "2020-01-02",
  "source_max_date": "2026-07-21",
  "base_calendar_sha256": "<sha256>",
  "base_calendar_start": "2000-01-04",
  "base_calendar_end": "2026-07-21",
  "base_calendar_count": 6450,
  "features": ["turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share", "total_mv", "circ_mv"],
  "instrument_count": 5500,
  "file_count": 82500
}
```

`checksums.sha256` 覆盖 manifest、报告和每个 Feature 文件，安装前全部校验。

## 9. 安装与冲突防护

### 9.1 安装目标

默认目标：

```text
~/.qlib/qlib_data/cn_data
```

安装后示例：

```text
cn_data/
├── calendars/day.txt
├── instruments/all.txt
└── features/sh600000/
    ├── close.day.bin
    ├── volume.day.bin
    ├── pe.day.bin
    ├── pb.day.bin
    └── turnover_rate.day.bin
```

### 9.2 Feature key 定义

冲突 key 定义为：

```text
(<frequency>, <instrument>, <field>)
```

在文件系统中对应：

```text
features/<instrument>/<field>.<frequency>.bin
```

安装器必须同时执行两类检查：

1. 字段白名单检查：归档中的所有字段必须属于 manifest `features`，且不能属于行情禁用名单。
2. 目标文件检查：任何目标路径已存在即视为冲突，默认中止整个安装。

只检查字段名而不检查目标文件不够安全，因为同一字段可能来自之前安装的增量包。

### 9.3 默认拒绝覆盖

推荐命令：

```bash
python qlib/install_feature_increment.py \
  daily_basic_qlib_features.tar.gz \
  --target-dir ~/.qlib/qlib_data/cn_data
```

行为顺序：

1. 检查目标 provider 结构。
2. 在临时目录安全解压，拒绝绝对路径、`..`、符号链接和硬链接穿越。
3. 校验 `checksums.sha256`。
4. 校验 manifest schema、包类型、数据集、频率和字段白名单。
5. 比较目标 `calendars/day.txt` 的 SHA-256、范围和数量。
6. 枚举全部目标路径并执行冲突预检。
7. 任何冲突默认不写入任何文件，输出冲突列表并返回非 0。
8. 无冲突时，将文件逐一写入同文件系统 staging 位置，再通过原子 rename 安装。
9. 写入安装记录 `.feature_increments/daily_basic.json`。
10. 重新扫描并验证已安装 Feature。

首版不建议提供通用 `--force`。需要升级已安装的 `daily_basic` 时，应提供受限的：

```text
--replace-same-dataset
```

它只允许替换 `.feature_increments/daily_basic.json` 中登记、且旧包也归属于 `daily_basic` 的文件，不能覆盖未知来源或行情字段。

### 9.4 失败与回滚

跨多个 Feature 文件无法依赖单个目录 rename 完成整体原子性，因此安装器应：

1. 预检后为将要替换的同数据集文件建立备份目录。
2. 记录安装 journal。
3. 写入失败时删除本次新增文件并恢复备份。
4. 成功后原子替换安装记录，再删除 journal 和备份。

进程异常退出后，下次启动先检测 journal，并要求自动恢复或显式 `--recover`，不能在未知状态继续安装。

## 10. 构建命令设计

建议新增独立入口：

```bash
./dump_daily_basic_qlib_features.sh
```

主要环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DAILY_BASIC_BASE_PROVIDER` | `~/.qlib/qlib_data/cn_data` | 用于日历和证券校验的主 provider |
| `DAILY_BASIC_BUILD_ROOT` | 临时目录 | 构建目录 |
| `DAILY_BASIC_OUTPUT_DIR` | `./output` | 产物目录 |
| `DAILY_BASIC_EXPORT_MAX_WORKERS` | `8` | MySQL 按证券导出并发数 |
| `DAILY_BASIC_DUMP_MAX_WORKERS` | `8` | binary 转换并发数 |
| `DAILY_BASIC_START_DATE` | 数据最早日期 | 可选起始日期 |
| `DAILY_BASIC_END_DATE` | 主日历末日 | 可选结束日期 |
| `CLEAN_DAILY_BASIC_BUILD_ROOT` | `1` | 成功后清理临时目录 |

构建脚本必须独立于 `dump_qlib_bin.sh`，不得复用或删除后者的 `BUILD_ROOT`、`OUTPUT_DIR/qlib_bin.tar.gz`。

建议内部步骤：

```text
preflight
  -> read and fingerprint base calendar
  -> export per-symbol CSV from MySQL
  -> validate fields and date bounds
  -> dump features against base calendar
  -> verify binary samples
  -> create manifest/checksums/report
  -> archive to output/daily_basic_qlib_features.tar.gz
```

## 11. 发布策略

现有 Release 资产继续保留：

```text
qlib_bin.tar.gz
```

新增资产：

```text
daily_basic_qlib_features.tar.gz
```

二者独立构建，但同一交易日 Release 中应满足：

```text
daily_basic manifest.base_calendar_sha256
== qlib_bin 中 calendars/day.txt 的 SHA-256
```

发布前必须解包主行情归档计算日历指纹，不能只依赖本机构建目录。若指纹不一致，拒绝上传增量包。

建议资产元数据还记录主包 Release tag，例如：

```json
{
  "compatible_base_release": "2026-07-21"
}
```

## 12. Qlib 使用方式

安装增量包后只初始化一次：

```python
import qlib
from qlib.config import REG_CN
from qlib.data import D

qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    region=REG_CN,
)

df = D.features(
    instruments=["SH600000"],
    fields=[
        "$close",
        "$volume",
        "$turnover_rate",
        "$pe",
        "$pb",
        "$total_mv",
    ],
    start_time="2020-01-01",
    end_time="2026-07-21",
    freq="day",
)
```

自定义指标和行情字段处于同一 provider，因此也可用于 Qlib 表达式：

```python
fields = [
    "$pe",
    "Ref($pe, 1)",
    "Mean($turnover_rate, 5)",
    "$total_mv / 10000",
]
```

训练配置中可直接加入 `QlibDataLoader`：

```python
config = {
    "feature": (
        ["$pe", "$pb", "$turnover_rate", "$volume_ratio"],
        ["PE", "PB", "TURNOVER", "VOLUME_RATIO"],
    ),
    "label": (
        ["Ref($close, -2) / Ref($close, -1) - 1"],
        ["LABEL0"],
    ),
}
```

## 13. 数据一致性与验证

### 13.1 构建前检查

- `daily_basic` 表存在且字段集合符合预期。
- `stock_basic` 代码映射可用。
- 主 provider 包含 `calendars/day.txt`、`instruments/all.txt`、`features/`。
- MySQL 最大指标日期不晚于主日历末日。
- Feature 白名单和禁用名单不相交。
- 构建目录和输出目录可写，剩余磁盘空间满足预估。

### 13.2 构建后检查

1. 归档中只有允许路径和普通文件。
2. 每个 `.bin` 文件大小是 4 字节的整数倍，且至少包含起始下标。
3. 起始下标是有限、非负、落在主日历范围内的整数值。
4. 每个文件的数据长度不越过日历末尾。
5. manifest 文件数、证券数、字段数与归档实际内容一致。
6. 随机抽样至少 10 只股票、5 个字段、3 个日期，与 MySQL 原值按 float32 容差比较。
7. 禁用行情字段文件数必须为 0。

### 13.3 安装后集成检查

在目标 `cn_data` 上执行一次 Qlib 初始化，并同时读取行情与增量字段：

```python
qlib.init(
    provider_uri=target_dir,
    region=REG_CN,
    expression_cache=None,
    dataset_cache=None,
)

sample = D.features(
    ["SH600000"],
    ["$close", "$pe", "$turnover_rate"],
    start_time="2020-01-02",
    end_time="2020-01-10",
    freq="day",
)
```

验收条件：

- `$close` 与安装前主 provider 查询结果一致。
- `$pe`、`$turnover_rate` 与 MySQL 同日期值一致。
- 2020-01-02 之前增量字段为 `NaN`，行情字段不受影响。
- 删除增量文件或执行卸载后，原行情 provider 仍可正常查询。

## 14. 更新与卸载

### 14.1 更新

每日增量包可以是完整快照，而不是仅包含最新一天。安装更新时使用 `--replace-same-dataset` 替换上一个 `daily_basic` 快照登记的文件，优势是恢复和校验简单。

更新要求：

1. 新旧包 `dataset` 都是 `daily_basic`。
2. 目标日历与新包 manifest 指纹一致。
3. 只替换旧安装记录中归属 `daily_basic` 的路径。
4. 新包字段集合缩小时，删除的旧字段文件必须在 journal 中记录并可恢复。
5. 更新后重新执行联合查询验证。

### 14.2 卸载

建议命令：

```bash
python qlib/install_feature_increment.py \
  --target-dir ~/.qlib/qlib_data/cn_data \
  --uninstall daily_basic
```

卸载器只删除安装记录列出的文件；如果文件 checksum 已被外部修改，默认拒绝删除并提示人工处理。空证券目录可以删除，但不得删除任何非空目录、calendar 或 instruments 文件。

## 15. 失败场景

| 场景 | 行为 |
| --- | --- |
| 主日历不存在或格式错误 | 构建/安装失败 |
| MySQL 最大日期晚于主日历 | 构建失败，要求先更新主包 |
| manifest 日历指纹与目标不一致 | 安装失败 |
| 归档含禁用行情字段 | 安装失败 |
| 目标存在未知同名 Feature | 安装失败且零写入 |
| checksum 不一致 | 安装失败 |
| 证券不在主 provider | 跳过并报告，超过阈值则失败 |
| 某指标全为空 | 报告并按配置跳过或失败 |
| 安装中断 | journal 驱动回滚/恢复 |
| Qlib 联合查询失败 | 安装视为失败并回滚 |

## 16. 安全要求

1. MySQL 凭据只从现有 `.env` 加载，不写入日志、manifest 或归档。
2. SQL 参数使用参数化查询；动态字段只能来自固定白名单。
3. 解压必须防止路径穿越和链接逃逸。
4. 拒绝归档中的设备文件、FIFO、socket、符号链接和硬链接。
5. 安装路径必须解析在目标 `features/` 目录内。
6. checksum 校验在任何目标写入之前完成。
7. 冲突检测必须覆盖完整文件清单，不能边检查边写入。
8. 日志不得输出 Tushare token 或数据库密码。

## 17. 建议代码结构

后续实现预计新增：

```text
investment_data/
├── dump_daily_basic_qlib_features.sh
├── qlib/
│   ├── dump_daily_basic_source.py
│   ├── dump_feature_increment.py
│   ├── feature_increment_manifest.py
│   └── install_feature_increment.py
├── tests/
│   ├── test_dump_daily_basic_source.py
│   ├── test_dump_feature_increment.py
│   ├── test_feature_increment_manifest.py
│   └── test_install_feature_increment.py
└── docs/
    └── daily_basic_qlib_feature_increment_design.md
```

职责：

- `dump_daily_basic_source.py`：按证券导出白名单指标 CSV。
- `dump_feature_increment.py`：读取主日历、对齐数据并生成 Feature binary。
- `feature_increment_manifest.py`：生成/校验 manifest、文件清单和 checksum。
- `install_feature_increment.py`：安全解压、兼容性检查、冲突检查、安装、更新、卸载和恢复。
- Shell 脚本：环境检查、目录管理、调用步骤和归档。

## 18. 测试策略

### 18.1 单元测试

- 日历严格递增和重复日期校验。
- 起始下标和 float32 文件布局。
- 缺失日期 reindex 为 `NaN`。
- Feature 白名单/禁用名单。
- manifest schema 和 checksum。
- tar 路径穿越、绝对路径、符号链接拒绝。
- 同名目标冲突零写入。
- `--replace-same-dataset` 不能覆盖未知文件。
- journal 回滚和恢复。
- 卸载不删除非本数据集文件。

### 18.2 集成测试

用小型 fixture provider：

```text
calendar: 2019-12-30, 2019-12-31, 2020-01-02, 2020-01-03
instrument: SH600000
base feature: close
increment feature: pe
```

验证：

1. 构建出的 `$pe` 在 2020-01-02 对齐正确。
2. 安装前 `$close` 可读，安装后值完全不变。
3. 安装后一次 `qlib.init()` 可同时读取 `$close` 和 `$pe`。
4. 重复安装默认因冲突失败。
5. 同数据集升级成功，模拟中断后可恢复。

### 18.3 真实数据冒烟测试

- 限制 2 只证券和短日期范围生成测试包。
- 对 MySQL、binary、Qlib API 三方值进行抽样比较。
- 在临时复制的 `cn_data` 上安装，禁止直接修改生产数据目录。
- 成功后再运行全量构建。

## 19. 验收标准

1. 现有 `dump_qlib_bin.sh` 和 `qlib_bin.tar.gz` 未发生行为变化。
2. 能独立生成 `daily_basic_qlib_features.tar.gz`。
3. 归档只含 manifest、checksum、报告和白名单 Feature 文件。
4. 增量二进制使用目标 `cn_data` 的交易日历下标。
5. 默认安装遇到任意同名目标文件时零写入失败。
6. 安装不修改 calendar、instruments 和任何行情 Feature。
7. 安装后一次 `qlib.init(cn_data)` 能联合读取 `$close` 与 daily_basic 字段。
8. 抽样数值和日期与 MySQL 一致。
9. 更新、失败回滚和卸载均只影响登记为 `daily_basic` 的文件。
10. 构建和安装测试全部通过，并保留机器可读报告。

## 20. 实施顺序

1. 固化 manifest schema、字段白名单和禁用名单。
2. 为 calendar fingerprint、binary 对齐和安全安装编写失败测试。
3. 实现 MySQL 源 CSV 导出器。
4. 实现复用主日历的 Feature binary 构建器。
5. 实现 manifest、checksum 和构建报告。
6. 实现安全安装、冲突预检和联合查询验证。
7. 实现同数据集更新、journal 回滚和卸载。
8. 添加小型 fixture provider 集成测试。
9. 运行 2 只证券真实数据冒烟测试。
10. 全量构建并验证归档，再接入独立 Release 资产发布流程。
