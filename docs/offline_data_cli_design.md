# 离线行情数据查询 CLI 技术设计文档

## 1. 背景与目标

当前 `investment_data` 项目负责从 MySQL 导出 A 股日线数据，转换为 Qlib binary provider 格式，并发布为 GitHub Release 资产 `qlib_bin.tar.gz`。为了让使用者可以在本地直接查询离线行情，需要新增一个面向终端的独立 CLI 工具。

CLI 项目名称和命令名统一为 `deep-alpha`。它应作为独立新仓库开发和发布，不放在当前 exporter 仓库中；当前仓库只继续负责生产和发布离线 Qlib 数据包。

CLI 的核心能力：

1. 默认从 Qlib 本地数据目录 `~/.qlib/qlib_data` 读取离线数据。
2. 通过 `qlib` Python 库访问数据，不直接解析 Qlib binary 文件。
3. 支持从 `https://github.com/xu-duqing/investment_data/releases` 下载离线数据包并解压到本地。
4. 支持更新本地离线数据到最新 Release。
5. 支持历史 K 线查询。
6. 当前数据源为 A 股日线，后续可扩展到美股日线、ETF、流动性指标等。

## 2. 非目标

首版不做以下能力：

1. 不做实时行情查询。
2. 不直接连接 MySQL、Tushare 或其他在线数据源。
3. 不实现复杂投研表达式引擎，只封装常用历史 K 线查询。
4. 不提供 GUI 或 Web 服务。
5. 不在 CLI 内生成 Qlib 数据，只负责下载、安装、更新和读取。

## 3. 用户场景

### 3.1 初始化本地离线数据

用户首次安装 CLI 后执行：

```bash
deep-alpha download
```

CLI 自动获取最新 GitHub Release，下载 `qlib_bin.tar.gz`，解压到：

```text
~/.qlib/qlib_data/cn_data
```

### 3.2 更新本地离线数据

```bash
deep-alpha update
```

CLI 检查当前本地数据版本与 GitHub 最新 Release。如果远端更新，则下载并原子替换本地数据目录。

### 3.3 查询历史 K 线

```bash
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-12-31
deep-alpha kline --symbol 600519.SH --start 2024-01-01 --end 2024-12-31
deep-alpha kline --symbol 600519 --start 2024-01-01 --end 2024-12-31
```

默认输出 CSV 到 stdout。
无论用户输入 `SH600519`、`600519.SH` 还是 `600519`，查询和输出中的 symbol 都统一为 Qlib 标准形式 `SH600519`。

也支持 JSON：

```bash
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-12-31 --format json
```

### 3.4 指定字段

```bash
deep-alpha kline \
  --symbol SH600519 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --fields open,high,low,close,volume,amount,vwap
```

### 3.5 指定数据目录

```bash
deep-alpha --provider-uri ~/.qlib/qlib_data/cn_data kline --symbol SH600519
```

也支持环境变量：

```bash
export DEEP_ALPHA_PROVIDER_URI=~/.qlib/qlib_data/cn_data
```

## 4. 命令设计

CLI 入口命令建议为：

```bash
deep-alpha
```

如果后续发布为 PyPI 包，`pyproject.toml` 中配置 console script：

```toml
[project.scripts]
deep-alpha = "deep_alpha.main:main"
```

### 4.1 全局参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--provider-uri` | `~/.qlib/qlib_data/cn_data` | Qlib provider 数据目录 |
| `--region` | `cn` | Qlib region，首版固定映射到 `REG_CN` |
| `--verbose` | false | 输出调试日志 |
| `--version` | - | 输出 CLI 版本 |

### 4.2 `download`

下载并安装离线数据。

```bash
deep-alpha download [options]
```

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--repo` | `xu-duqing/investment_data` | GitHub 仓库 |
| `--tag` | latest | 指定 Release tag；不传则取 latest |
| `--asset` | `qlib_bin.tar.gz` | Release asset 名称 |
| `--target-dir` | `~/.qlib/qlib_data/cn_data` | 解压目标目录 |
| `--force` | false | 本地已有数据时强制覆盖 |
| `--keep-archive` | false | 保留下载的 tar.gz |
| `--timeout` | 300 | HTTP 超时秒数 |

行为：

1. 调用 GitHub Releases API 获取 release 信息。
2. 找到名称为 `qlib_bin.tar.gz` 的 asset。
3. 下载到临时目录。
4. 校验文件非空，优先校验 GitHub API 返回的 size。
5. 解压到临时 staging 目录。
6. 对解压结果做目录兼容处理，自动识别 provider 根目录。
7. 校验并规范化 staging 目录，确保最终目标目录满足 Qlib provider 基本结构。
8. 原子替换目标目录。
9. 写入本地元数据文件 `.investment_data_meta.json`。

### 4.3 `update`

检查并更新本地离线数据。

```bash
deep-alpha update [options]
```

参数与 `download` 基本一致，额外行为：

1. 读取目标目录下 `.investment_data_meta.json`。
2. 获取远端 latest Release。
3. Release latest 约定总是指向最新可用交易日，同一天不会发布多个 Release。
4. 如果本地 `release_tag` 与远端 latest tag 相同，默认跳过。
5. 如果远端 latest tag 更新，执行 download 流程。
6. 支持 `--force` 忽略版本判断并重新安装。

### 4.4 `info`

查看本地数据集信息。

```bash
deep-alpha info
```

输出：

```text
provider_uri: /Users/<user>/.qlib/qlib_data/cn_data
release_tag: 2026-07-20
asset_name: qlib_bin.tar.gz
installed_at: 2026-07-21T08:10:00+08:00
calendar_start: 2000-01-04
calendar_end: 2026-07-20
instrument_count: 5400
fields: open, close, high, low, vwap, volume, amount
```

### 4.5 `kline`

查询历史 K 线。

```bash
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-12-31
```

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--symbol` | 必填 | 证券代码，兼容 `SH600519`、`600519.SH`、`600519` 等输入 |
| `--start` | 数据起始日 | 查询开始日期，格式 `YYYY-MM-DD` |
| `--end` | 数据结束日 | 查询结束日期，格式 `YYYY-MM-DD` |
| `--fields` | `open,high,low,close,volume,amount,vwap` | 输出字段 |
| `--format` | `csv` | 输出格式：`csv`、`json`、`table` |
| `--output` | stdout | 写入文件路径，不传则输出到 stdout |
| `--adjust` | `none` | 预留参数：`none`、`qfq`、`hfq`；首版只支持 `none` |

字段映射：

| CLI 字段 | Qlib 字段 |
| --- | --- |
| `open` | `$open` |
| `high` | `$high` |
| `low` | `$low` |
| `close` | `$close` |
| `volume` | `$volume` |
| `amount` | `$amount` |
| `vwap` | `$vwap` |

Qlib 读取逻辑：

```python
import qlib
from qlib.config import REG_CN
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)

df = D.features(
    instruments=["SH600519"],
    fields=["$open", "$high", "$low", "$close", "$volume", "$amount", "$vwap"],
    start_time="2024-01-01",
    end_time="2024-12-31",
    freq="day",
)
```

输出规范：

1. CSV 默认列顺序：`datetime,symbol,open,high,low,close,volume,amount,vwap`。
2. JSON 输出为 records 数组，每行一条记录。
3. table 输出用于人工查看，不保证稳定机器可读。
4. 缺失值保持为空字符串或 JSON null，不在 CLI 层填充。

### 4.6 `symbols`

列出或搜索本地可用标的。

```bash
deep-alpha symbols
deep-alpha symbols --prefix SH60
deep-alpha symbols --contains 600519
```

首版可以基于 Qlib instruments 文件或 `D.instruments()` 返回结果实现。

### 4.7 `calendar`

查看交易日历范围或列出交易日。

```bash
deep-alpha calendar --start 2024-01-01 --end 2024-01-31
```

用于辅助验证本地数据是否更新到预期日期。

## 5. 数据目录与安装布局

默认目录：

```text
~/.qlib/qlib_data/
└── cn_data/
    ├── calendars/
    │   └── day.txt
    ├── features/
    ├── instruments/
    └── .investment_data_meta.json
```

元数据文件示例：

```json
{
  "repo": "xu-duqing/investment_data",
  "release_tag": "2026-07-20",
  "asset_name": "qlib_bin.tar.gz",
  "asset_size": 123456789,
  "download_url": "https://github.com/xu-duqing/investment_data/releases/download/2026-07-20/qlib_bin.tar.gz",
  "installed_at": "2026-07-21T08:10:00+08:00",
  "dataset": "cn_stock_1d",
  "provider_uri": "/Users/appweb/.qlib/qlib_data/cn_data",
  "schema_version": 1
}
```

原子更新策略：

```text
~/.qlib/qlib_data/
├── cn_data/                      # 当前可用数据
├── .cn_data.tmp.<pid>/            # 解压 staging
└── .cn_data.backup.<timestamp>/   # 替换前备份，可选保留
```

解压目录兼容规则：

1. `qlib_bin.tar.gz` 解压后可能直接包含 `calendars/`、`features/`、`instruments/`。
2. 也可能包含一层根目录，例如 `qlib_bin/calendars/`、`qlib_bin/features/`、`qlib_bin/instruments/`。
3. installer 需要自动探测包含 `calendars/day.txt`、`features/`、`instruments/` 的目录，并把该目录作为 provider root。
4. 安装到目标目录前，应确保目标目录本身就是 provider root，而不是额外包一层 `qlib_bin/`。
5. 如果解压结果中找不到唯一 provider root，应失败并给出明确错误。

更新流程：

1. 下载 asset 到系统临时目录。
2. 解压到 `.cn_data.tmp.<pid>`。
3. 执行目录兼容处理，确定唯一 provider root。
4. 校验并规范化 staging。
5. 将当前 `cn_data` rename 为 backup。
6. 将规范化后的 staging rename 为 `cn_data`。
7. 成功后删除 backup；失败则回滚。

注意：跨文件系统 rename 不是原子操作，因此 staging 应创建在目标目录的父目录下。

## 6. GitHub Release 下载设计

### 6.1 API

获取 latest release：

```text
GET https://api.github.com/repos/xu-duqing/investment_data/releases/latest
```

约定：`latest` 总是指向最新可用交易日对应的 Release；同一天不会发布多个 Release，也不会通过同一个 tag 多次覆盖 asset。CLI 的 update 判断只需要比较本地 `release_tag` 与远端 latest tag。

获取指定 tag：

```text
GET https://api.github.com/repos/xu-duqing/investment_data/releases/tags/{tag}
```

从响应 `assets` 中选择：

```json
{
  "name": "qlib_bin.tar.gz",
  "size": 123456789,
  "browser_download_url": "https://github.com/.../qlib_bin.tar.gz"
}
```

### 6.2 认证

公开仓库下载不需要认证，但 GitHub API 有匿名限流。设计支持可选环境变量：

```text
GITHUB_TOKEN
GH_TOKEN
```

如果存在 token，则请求 header 加：

```text
Authorization: Bearer <token>
```

CLI 不打印 token。

### 6.3 重试

建议对以下情况重试：

1. 网络连接错误。
2. HTTP 408、409、425、429。
3. HTTP 5xx。

默认重试 3 次，指数退避或线性退避均可。下载大文件应支持流式写入，避免一次性读入内存。

## 7. Qlib 读取设计

### 7.1 初始化

封装 `QlibClient`，避免每个命令重复初始化：

```python
class QlibClient:
    def __init__(self, provider_uri: Path, region: str = "cn"):
        self.provider_uri = provider_uri
        self.region = region
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        import qlib
        from qlib.config import REG_CN
        qlib.init(provider_uri=str(self.provider_uri.expanduser()), region=REG_CN)
        self._initialized = True
```

首版只支持 `region=cn`。后续扩展美股时再增加 region/provider 映射。

### 7.2 历史 K 线查询

查询服务接口：

```python
@dataclass(frozen=True)
class KlineQuery:
    symbol: str
    start: str | None
    end: str | None
    fields: list[str]
    freq: str = "day"

class MarketDataService:
    def get_kline(self, query: KlineQuery) -> pandas.DataFrame:
        ...
```

职责：

1. 校验 symbol 格式。
2. 校验日期格式。
3. 将 CLI 字段映射为 Qlib 字段。
4. 调用 `D.features`。
5. 将 Qlib MultiIndex DataFrame 整理为稳定输出结构。

### 7.3 Symbol 规范

当前导出脚本中 `stock_basic.qlib_code` 是最终 Qlib symbol。CLI 查询时必须将用户输入归一化为 provider 中存在的 Qlib symbol，并且输出中的 `symbol` 列统一使用归一化后的 Qlib symbol。

首版必须支持以下输入形式：

| 用户输入 | 归一化结果 | 说明 |
| --- | --- | --- |
| `SH600519` | `SH600519` | 已是 Qlib 标准形式 |
| `sh600519` | `SH600519` | 大小写归一化 |
| `600519.SH` | `SH600519` | 交易所后缀形式 |
| `000001.SZ` | `SZ000001` | 交易所后缀形式 |
| `600519` | `SH600519` | 无交易所输入，需在本地 symbol 索引中解析 |
| `000001` | `SZ000001` | 无交易所输入，需在本地 symbol 索引中解析 |

无交易所输入不能仅靠固定规则猜测，应基于 provider 中的 symbol 列表建立反向索引：

1. 扫描本地可用 symbol，例如 `SH600519`、`SZ000001`。
2. 对每个 symbol 取 6 位数字代码作为 key。
3. 如果 key 只匹配一个 symbol，则可解析。
4. 如果 key 匹配多个 symbol，返回歧义错误，并提示用户使用 `SH600519` 或 `600519.SH` 形式。
5. 如果转换后仍不存在，应返回明确错误。

## 8. 扩展性设计

后续数据源包括：

1. A 股日线，当前数据集。
2. 美股日线。
3. ETF。
4. 流动性指标。
5. 其他因子或基本面指标。

建议引入 dataset/profile 概念：

```yaml
cn_stock_1d:
  provider_uri: ~/.qlib/qlib_data/cn_data
  region: cn
  freq: day
  fields:
    - open
    - high
    - low
    - close
    - volume
    - amount
    - vwap

us_stock_1d:
  provider_uri: ~/.qlib/qlib_data/us_data
  region: us
  freq: day

cn_etf_1d:
  provider_uri: ~/.qlib/qlib_data/cn_etf_data
  region: cn
  freq: day

cn_liquidity_1d:
  provider_uri: ~/.qlib/qlib_data/cn_liquidity_data
  region: cn
  freq: day
```

CLI 参数预留：

```bash
deep-alpha --dataset cn_stock_1d kline --symbol SH600519
deep-alpha --dataset us_stock_1d kline --symbol AAPL
deep-alpha --dataset cn_etf_1d kline --symbol SH510300
```

首版可以只实现 `cn_stock_1d`，但配置和目录结构不要写死在业务逻辑中。

## 9. 推荐代码结构

`deep-alpha` 应作为独立新仓库实现。建议仓库结构：

```text
deep-alpha/
├── pyproject.toml
├── README.md
├── deep_alpha/
│   ├── __init__.py
│   ├── main.py              # argparse/typer 入口
│   ├── config.py            # 默认路径、dataset 配置、环境变量
│   ├── github_release.py    # GitHub Release API 与下载
│   ├── installer.py         # 解压、兼容目录规范化、校验、原子替换、元数据
│   ├── qlib_client.py       # qlib.init 与 D.features 封装
│   ├── services.py          # kline/symbol/calendar 服务
│   ├── symbols.py           # symbol 归一化与歧义处理
│   ├── output.py            # csv/json/table 输出
│   └── errors.py            # CLI 友好的异常类型
└── tests/
    ├── test_cli_args.py
    ├── test_github_release.py
    ├── test_installer.py
    ├── test_output.py
    └── test_symbol_normalize.py
```

核心 Python 包结构：

```text
deep_alpha/
├── __init__.py
├── main.py              # argparse/typer 入口
├── config.py            # 默认路径、dataset 配置、环境变量
├── github_release.py    # GitHub Release API 与下载
├── installer.py         # 解压、兼容目录规范化、校验、原子替换、元数据
├── qlib_client.py       # qlib.init 与 D.features 封装
├── services.py          # kline/symbol/calendar 服务
├── symbols.py           # symbol 归一化与歧义处理
├── output.py            # csv/json/table 输出
└── errors.py            # CLI 友好的异常类型

tests/
├── test_cli_args.py
├── test_github_release.py
├── test_installer.py
├── test_output.py
└── test_symbol_normalize.py
```

### 9.1 依赖建议

当前 `requirements.txt` 只有：

```text
pandas
setuptools-scm
PyMySQL
```

CLI 新增依赖建议：

```text
requests
qlib
```

但 Qlib 的包名和安装方式需要单独验证。若 `pip install qlib` 与项目实际使用的 Microsoft Qlib 包不一致，则应在文档中要求用户安装 Microsoft Qlib，或继续使用本项目现有的 `../qlib` editable 安装方式。

为了减少依赖，首版 CLI 可以使用 Python 标准库：

1. `urllib.request` 下载 GitHub API 和 asset。
2. `tarfile` 解压。
3. `argparse` 实现命令。

如果追求更好的开发体验，可以使用：

1. `requests` 简化 HTTP。
2. `typer` 简化 CLI。
3. `rich` 美化表格输出。

首版建议优先使用标准库 + pandas + qlib，避免引入过多依赖。

## 10. 错误处理

常见错误与提示：

| 场景 | 错误提示 |
| --- | --- |
| 未安装 qlib | `Qlib is not installed. Please install Microsoft Qlib before querying data.` |
| 数据目录不存在 | `Provider directory does not exist. Run: deep-alpha download` |
| 数据目录结构不完整 | `Invalid Qlib provider directory: missing calendars/day.txt or features/` |
| Release 不存在 | `Release not found: <tag>` |
| Asset 不存在 | `Asset qlib_bin.tar.gz not found in release <tag>` |
| symbol 不存在 | `Symbol not found in local dataset: SH600519` |
| symbol 歧义 | `Ambiguous symbol: 600519. Please use SH600519 or 600519.SH` |
| 日期格式错误 | `Invalid date: expected YYYY-MM-DD` |
| 字段不存在 | `Unsupported field: turnover. Available fields: open, high, low, close, volume, amount, vwap` |

CLI 应使用非 0 exit code：

| Exit code | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 通用错误 |
| 2 | 参数错误 |
| 3 | 数据目录错误 |
| 4 | 下载/网络错误 |
| 5 | Qlib 查询错误 |

## 11. 安全与可靠性

1. 解压 tar.gz 时必须防止路径穿越，例如 asset 中包含 `../../evil`。
2. 下载文件先写临时文件，成功后再进入安装流程。
3. 更新数据时使用 staging + rename，避免中途失败破坏当前可用数据。
4. 不打印 token、完整下载 URL 中的敏感参数。
5. 默认不删除用户指定的非默认目录，除非 `--force` 明确启用。
6. 对 `target-dir` 做 expanduser 和 resolve，但不要跟随危险 symlink 删除目录。
7. 下载和解压过程输出进度，但不要刷屏。
8. 不支持断点续传；下载失败后删除临时文件，用户重新执行 `deep-alpha download` 或 `deep-alpha update` 即可。

## 12. 测试策略

### 12.1 单元测试

1. GitHub Release 响应解析。
2. asset 选择逻辑。
3. symbol 归一化。
4. 字段映射。
5. 日期校验。
6. 输出格式 CSV/JSON。
7. tar 解压路径穿越防护。
8. 元数据读写。

### 12.2 集成测试

使用临时目录构造最小 Qlib provider：

```text
calendars/day.txt
features/<symbol>/...
instruments/all.txt
```

如果构造 binary provider 成本高，可以将 Qlib 查询封装层 mock 掉，集成测试重点验证 CLI 参数、下载、安装和输出流程。

### 12.3 手工验收

```bash
python -m deep_alpha.main download --target-dir /tmp/qlib_data/cn_data
python -m deep_alpha.main info --provider-uri /tmp/qlib_data/cn_data
python -m deep_alpha.main kline --provider-uri /tmp/qlib_data/cn_data --symbol SH600519 --start 2024-01-01 --end 2024-01-10
python -m deep_alpha.main update --provider-uri /tmp/qlib_data/cn_data
```

验收标准：

1. `download` 能成功下载并解压最新 Release。
2. `info` 能输出本地数据版本、日期范围和字段。
3. `kline` 能输出指定 symbol 的历史日线。
4. `update` 在本地已是最新版本时能正确跳过。
5. 所有错误路径都有清晰提示和非 0 exit code。

## 13. 版本与兼容性

### 13.1 本地数据 schema version

`.investment_data_meta.json` 使用 `schema_version` 标记 CLI 能理解的本地安装元数据结构。

Qlib provider 本身的 schema 由 Qlib 管理，CLI 不修改 provider 内部结构。

### 13.2 Release tag 约定

当前 Release tag 使用日期，例如：

```text
2026-07-20
```

CLI 将 tag 作为数据版本。Release latest 总是指向最新可用交易日；同一天不会发布多个 Release，也不会通过同 tag 覆盖 asset。因此 `update` 只比较本地 `release_tag` 与远端 latest tag，不需要额外比较 asset size 或 `updated_at`。

## 14. 实施计划

### Phase 1：最小可用版本

1. 新建独立仓库 `deep-alpha`，并新增 Python 包 `deep_alpha`。
2. 实现 `download`、`update`、`info`。
3. 实现安全解压和原子替换。
4. 实现 `.investment_data_meta.json`。
5. 加单元测试覆盖下载响应解析、安装校验、元数据。

### Phase 2：历史 K 线查询

1. 引入 `qlib_client.py`。
2. 实现 `kline`。
3. 支持 CSV/JSON 输出。
4. 支持字段选择和日期范围。
5. 加 Qlib 查询封装层测试。

### Phase 3：本地数据探索

1. 实现 `symbols`。
2. 实现 `calendar`。
3. `info` 增加 instrument count、calendar range、field list。

### Phase 4：多数据集扩展

1. 增加 dataset 配置。
2. 支持 `--dataset cn_stock_1d`。
3. 预留 `us_stock_1d`、`cn_etf_1d`、`cn_liquidity_1d`。
4. Release asset 命名扩展，例如：
   - `cn_stock_1d_qlib_bin.tar.gz`
   - `us_stock_1d_qlib_bin.tar.gz`
   - `cn_etf_1d_qlib_bin.tar.gz`

## 15. 已确认约束

1. CLI 命令名为 `deep-alpha`，例如 `deep-alpha info`。
2. Symbol 输入必须兼容 `SH600519`、`600519.SH`、`600519` 等形式，查询和输出统一使用 Qlib 标准形式 `SH600519`。
3. `qlib_bin.tar.gz` 解压结果需要做目录兼容处理，最终安装目录必须满足 Qlib provider 规范。
4. Release latest 总是指向最新可用交易日。
5. 同一天不会发布多个 Release，也不会通过同 tag 覆盖 asset。
6. CLI 不支持断点续传。
7. CLI 作为独立新仓库实现，不放在当前 exporter 仓库内。

## 16. 首版验收标准

首版完成后，用户应能完成以下完整流程：

```bash
python -m pip install -e .
deep-alpha download
deep-alpha info
deep-alpha kline --symbol SH600519 --start 2024-01-01 --end 2024-01-10 --format csv
deep-alpha update
```

并满足：

1. 默认数据目录为 `~/.qlib/qlib_data/cn_data`。
2. 数据下载来源为 `https://github.com/xu-duqing/investment_data/releases`。
3. 查询通过 `qlib` 库完成。
4. 历史 K 线能稳定输出 CSV/JSON。
5. 后续新增美股日线、ETF、流动性指标时，不需要重写 CLI 主流程，只新增 dataset 配置和字段映射。
