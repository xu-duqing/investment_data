# Qlib 按交易日增量包改造方案

## 1. 背景

当前项目在每个交易日构建并发布两个全量资产：

1. `qlib_bin.tar.gz`：行情 Qlib Provider 全量包。
2. `daily_basic_qlib_features.tar.gz`：`daily_basic` Feature 全量历史包。

现有代码中的 `feature_increment` 表示“向主 Provider 增加一组 Feature 字段”，并不表示“仅包含新增交易日数据”。当前 `daily_basic_qlib_features.tar.gz` 默认不设置开始日期，因此包含截至发布日的完整历史数据。

本方案新增两个真正按交易日纵向追加的数据包，以减少日常更新的下载量和安装成本，同时保留全量包作为首次安装、版本断档、历史修正和故障恢复的可靠路径。

## 2. 目标发布资产

每个交易日的同一个 GitHub Release 发布四个相互匹配的资产：

1. `qlib_bin.tar.gz`：行情全量包。
2. `daily_basic_qlib_features.tar.gz`：`daily_basic` 全量 Feature 包。
3. `qlib_bin_delta.tar.gz`：行情按交易日增量包。
4. `daily_basic_qlib_features_delta.tar.gz`：`daily_basic` 按交易日增量包。

四个资产必须对应同一个目标 Release、同一份目标交易日历。两个增量包必须明确声明其基础 Release，不能脱离基线独立安装。

## 3. 范围与分期

### 3.1 第一期

- 支持“上一成功交易日 Release → 当前交易日 Release”的单步增量。
- 仅支持在交易日历尾部追加日期。
- 版本不连续、基线缺失、历史数据发生变化或本地文件被修改时，客户端自动回退全量更新。
- 行情和 `daily_basic` 作为一次配对事务更新。

### 3.2 第二期

- 支持遍历多个 GitHub Release，连续应用多日增量链。
- 在有明确需求后，再设计历史区间修正包。

第一期不应直接实现任意历史 patch，以免混淆“尾部追加”和“历史重写”两种不同的数据一致性模型。

## 4. 统一增量包协议

两个增量包使用统一 manifest 协议。建议 `manifest.json` 至少包含：

```json
{
  "schema_version": 1,
  "package_type": "qlib_temporal_delta",
  "dataset": "market",
  "frequency": "day",
  "release_tag": "2026-11-02",
  "base_release_tag": "2026-10-30",
  "calendar_before_sha256": "...",
  "calendar_after_sha256": "...",
  "calendar_before_count": 6500,
  "calendar_after_count": 6501,
  "calendar_append": ["2026-11-02"],
  "files": [
    {
      "path": "features/sh600000/close.day.bin",
      "operation": "append",
      "expected_old_size": 26004,
      "expected_old_sha256": "...",
      "append_value_count": 1,
      "new_size": 26008,
      "new_sha256": "..."
    }
  ]
}
```

### 4.1 核心约束

- `base_release_tag` 必须与本地已安装 Release 一致。
- `release_tag` 必须与当前 GitHub Release 标签一致。
- 日历只允许尾部追加，禁止插入、删除或重排历史日期。
- 已有 `.day.bin` 只允许尾部追加 little-endian float32 值。
- 新上市证券允许创建新的合法 `.day.bin`，文件仍需包含 Qlib 起始 offset。
- 对已有文件执行追加前，必须验证旧文件大小和 SHA-256。
- 应用后必须验证新文件大小和 SHA-256。
- 增量归档包含完整 `checksums.sha256`，并限制归档路径、文件类型、成员数及解压大小。
- 两个增量包的 `base_release_tag`、`release_tag` 和目标日历指纹必须一致。

### 4.2 历史修正

如果当前全量产物相对上一基线不仅发生尾部追加，还修改了历史值、删除了值或调整了历史日历，则本次不能生成普通 temporal delta。发布流程应：

1. 明确记录无法生成增量的原因；
2. 继续发布两个全量包；
3. 不上传无效增量包，或在发布元数据中明确标记增量不可用；
4. 客户端发现增量不可用后回退全量更新。

不得把历史重写伪装成尾部追加。

## 5. 行情增量包

### 5.1 目录结构

```text
market_delta/
├── manifest.json
├── checksums.sha256
├── calendars/
│   └── day.append.txt
├── instruments/
│   ├── all.txt
│   ├── csi300.txt
│   ├── csi500.txt
│   └── ...
└── features/
    ├── sh600000/
    │   ├── open.day.append.bin
    │   ├── close.day.append.bin
    │   └── ...
    └── ...
```

### 5.2 数据规则

- `calendars/day.append.txt` 只包含基础日历之后新增的交易日，第一期通常只有一个日期。
- 对已有证券，`*.day.append.bin` 只保存新增 float32 值，不重复保存 Qlib 起始 offset。
- 当证券在新增日期没有值时，必须显式追加 `NaN`，不能跳过，否则各 Feature 长度会错位。
- 对新上市证券，保存完整合法 `.day.bin`，首值为其在目标日历中的起始 offset。
- instruments 和指数成分文件体积相对较小，可在增量包中携带目标版本完整文件并原子替换，避免设计难以验证的文本差分协议。
- 退市证券仍需保持已有 Feature 文件；instruments 的有效区间按目标全量产物更新。

### 5.3 构建方式

推荐以“上一成功发布的 Provider 快照”和“本次全量 Provider”做结构化比较：

1. 校验两个日历具有严格前缀关系。
2. 比较每个已有 `.day.bin` 的起始 offset 和历史 value 区间。
3. 要求旧文件全部值与新文件相同，仅允许新文件尾部多出值。
4. 将新增值写入 append payload。
5. 将新出现的 Feature 文件按 create 操作加入增量。
6. 将删除、历史变化、offset 变化视为不可生成普通增量。

通过比较确定性全量产物生成 delta，比重新实现一套行情归一化逻辑更安全，也能检测历史源数据修正。

## 6. daily_basic 增量包

### 6.1 目录结构

```text
daily_basic_delta/
├── manifest.json
├── checksums.sha256
└── features/
    ├── sh600000/
    │   ├── pe.day.append.bin
    │   ├── pb.day.append.bin
    │   └── ...
    └── ...
```

### 6.2 构建规则

现有 `dump_daily_basic_source.py` 已支持 `--start-date` 和 `--end-date`，但现有 `dump_feature_increment.py` 生成的是可合并到 Provider 的完整 Feature 文件，不是时间增量 payload，不能直接作为 temporal delta 使用。

推荐与行情增量一致：

1. 正常构建 `daily_basic_qlib_features.tar.gz` 全量包。
2. 将上一成功发布的 daily_basic Feature 基线与本次全量 Feature 比较。
3. 验证旧区间没有变化。
4. 为已有文件提取尾部新增 float32 值。
5. 为新证券或新字段生成 create 操作。
6. 对缺失值显式追加 `NaN`。
7. 任何历史变化都使本次 temporal delta 不可生成。

SQL 日期范围可用于减少源数据导出成本，但不能代替与上一成功发布基线的历史一致性检查。

## 7. 发布基线与状态推进

增量构建必须保存上一成功发布状态，例如：

```text
release_state/
├── manifest.json
├── market_provider/
└── daily_basic/
    ├── manifest.json
    └── features/
```

`release_state/manifest.json` 至少记录：

- 已成功上传的 Release tag；
- 主日历 SHA-256、范围和数量；
- 两类数据文件清单及 SHA-256；
- 基线创建时间；
- 对应远端仓库。

状态推进必须满足：

1. 四个候选资产已完成本地构建和联合校验；
2. 需要发布的资产均已成功上传；
3. GitHub Release 中资产名称和大小已回读验证；
4. 发布成功后才原子更新 `release_state`。

上传失败、验证失败或进程中断时，绝对不能提前推进基线，否则下一次会基于未发布状态生成错误增量链。

## 8. 每日发布流程

交易日发布流程调整为：

1. 同步 `stock_daily` 与 `daily_basic`。
2. 等待两张表都达到目标交易日。
3. 读取并验证上一成功发布基线。
4. 构建 `qlib_bin.tar.gz`。
5. 基于该主 Provider 的确切日历构建 `daily_basic_qlib_features.tar.gz`。
6. 比较上一基线与当前全量产物，构建 `qlib_bin_delta.tar.gz`。
7. 比较上一基线与当前全量产物，构建 `daily_basic_qlib_features_delta.tar.gz`。
8. 联合校验两个全量包和两个增量包。
9. 上传同一个 GitHub Release。
10. 回读并验证远端资产。
11. 原子推进本地发布基线。
12. 发送成功通知；任一步失败则保留旧基线并发送失败通知。

非交易日继续跳过构建和发布。

## 9. 发布端代码改造

### 9.1 建议新增文件

```text
qlib/temporal_delta_manifest.py
qlib/build_market_delta.py
qlib/build_daily_basic_delta.py
qlib/apply_temporal_delta.py
tests/test_temporal_delta_manifest.py
tests/test_build_market_delta.py
tests/test_build_daily_basic_delta.py
tests/test_apply_temporal_delta.py
```

`apply_temporal_delta.py` 可作为发布端测试工具和客户端实现的协议参考，但生产客户端仍应在 `deep-alpha` 内实现并测试自己的安全安装路径。

### 9.2 修改现有文件

#### `scripts/check_open_dump_upload_qlib.sh`

- 在两个全量包构建完成后构建两个 temporal delta。
- 读取上一成功发布基线。
- 区分“首次发布”“增量不可生成”和“正常单步增量”。
- 上传成功后才推进基线。
- 日志和通知中列出四个资产及增量是否可用。

#### `upload_release.sh`

- 从固定双资产扩展为四资产或显式资产清单。
- 上传前联合校验所有待发布资产。
- 上传所有资产后统一回读名称与大小。
- 只有所有要求的资产均验证成功才执行输出清理和基线推进。
- 保持现有认证、重试、资产替换和敏感信息保护逻辑。

#### `qlib/validate_release_archives.py`

扩展为联合验证：

- 两个全量包使用同一目标日历；
- 两个 delta 的基础和目标 Release 一致；
- market delta 的目标日历哈希等于主全量包日历哈希；
- daily_basic delta 的目标日历哈希等于 daily_basic 全量包所声明的基础日历哈希；
- 文件清单、校验和、操作类型和旧/新文件约束合法。

#### 文档与测试

- README 明确区分“Feature 扩展包”和“按日期增量包”。
- `tests/test_upload_release.sh` 覆盖四资产上传、缺失资产、失败不清理和大小回读验证。
- 定时任务文档说明基线状态、回退策略和通知语义。

## 10. deep-alpha 客户端改造

### 10.1 默认更新行为

`deep-alpha update` 应升级为配对更新流程：

1. 读取本地 market 与 daily_basic 元数据。
2. 获取目标最新 Release。
3. 查询两个 delta 资产及 manifest。
4. 若本地 Release 等于 delta 的 `base_release_tag`，下载并事务性应用两个 delta。
5. 若版本断档、增量缺失、校验失败或本地文件被修改，则下载并安装两个目标全量包。
6. 更新完成后，联合查询至少一个行情字段和一个 daily_basic 字段。
7. 最后写入配对 Release 元数据。

不建议继续要求用户依次执行：

```bash
deep-alpha update
deep-alpha update --dataset daily-basic
```

原因是 market 和 daily_basic 共享同一日历和 Release，分开更新容易产生中间不一致状态。

### 10.2 CLI 建议

```bash
deep-alpha update               # 优先增量，不可用时自动回退全量
deep-alpha update --full        # 强制下载并安装两个全量包
deep-alpha update --delta-only  # 只允许增量，不能应用时明确报错
```

为兼容现有用户，可暂时保留 `--dataset`，但配对默认更新应成为推荐路径，并明确单数据源更新的兼容性约束。

### 10.3 客户端主要改动位置

- `deep_alpha/config.py`：新增两个 delta 资产名及配对数据集配置。
- `deep_alpha/main.py`：新增自动选择 delta/full 的更新策略和 CLI 参数。
- `deep_alpha/github_release.py`：支持一次解析和获取同一 Release 的多个资产，避免重复请求和标签漂移。
- `deep_alpha/installer.py`：支持 market temporal delta 和配对事务。
- `deep_alpha/feature_increment.py`：将“全量 Feature 扩展安装”与“时间增量追加”概念明确分离。
- `deep_alpha/info`：分别展示 market、daily-basic 的 release、范围和更新模式，并报告是否配对。
- 对应 tests：覆盖单步 delta、断档回退、历史修改、损坏文件、缺失资产和崩溃恢复。

## 11. 原子安装与崩溃恢复

两个增量包必须作为一次事务应用。建议顺序：

1. 验证目标 Provider 和现有元数据。
2. 安全解包并校验两个增量归档。
3. 验证两个包的基础 Release、目标 Release 和目标日历一致。
4. 在任何写入前验证所有将修改文件的旧大小和旧 SHA-256。
5. 完整备份所有将修改、创建或替换的文件。
6. 备份完成后再发布恢复 journal。
7. 应用 market Feature 追加或创建。
8. 原子更新 calendar 和 instruments。
9. 应用 daily_basic Feature 追加或创建。
10. 验证全部新大小和新 SHA-256。
11. 用 Qlib 联合查询一个行情字段和一个指标字段。
12. 原子写入配对元数据。
13. 删除 journal。
14. 最后删除备份。

任何步骤失败都必须根据 journal 恢复到安装前状态，不能留下以下半成品：

- Feature 已追加但 calendar 未更新；
- market 已更新但 daily_basic 未更新；
- 文件已更新但 Release 元数据仍指向旧版本；
- journal 仍存在但备份已被删除。

恢复 journal 和元数据中的路径均视为不可信输入，必须限制在 Provider 允许的目录下。

## 12. 全量回退路径

以下任一情况应回退全量更新：

- 本地没有可靠的 Release 元数据；
- 本地 Release 与 delta 的 `base_release_tag` 不匹配；
- 中间 Release 缺失；
- 增量资产不存在或 manifest 无效；
- 本地旧文件大小或 SHA-256 不匹配；
- 日历不是严格尾部追加；
- 检测到历史值变化、文件删除或 offset 变化；
- journal 恢复后无法确认增量可安全重试。

全量回退也必须是配对操作：先准备并验证 market 与 daily_basic 两个全量包，再原子替换 Provider 并安装 daily_basic。不能只替换行情 Provider 后结束，因为这会删除或失配已安装的 daily_basic Feature。

## 13. 测试与验收

### 13.1 单元测试

- 日历严格前缀和尾部追加判断。
- manifest schema、基础/目标 Release 和日历指纹校验。
- 已有文件单值及多值追加。
- `NaN` 追加。
- 新证券 create 操作及 Qlib offset。
- 新字段 create 操作。
- 历史值变化、文件删除、offset 变化拒绝生成 delta。
- 路径穿越、链接、设备文件、重复成员和超大归档拒绝。
- 旧大小/哈希不匹配时零写入。
- 应用后大小/哈希校验。
- journal 恢复及重复执行幂等性。

### 13.2 集成测试

- 使用临时上一日 Provider 和当前日 Provider 构建两个 delta。
- 将 delta 应用到上一日 Provider 副本。
- 对应用结果与当前全量 Provider 做逐文件 SHA-256 比较。
- 使用一次 `qlib.init()` 联合查询 market 和 daily_basic 字段。
- 测试新上市、停牌、退市、缺值和指数成分变化。
- 模拟 market 完成而 daily_basic 失败，验证能完全回滚。
- 模拟客户端版本断档，验证自动回退两个全量包。

### 13.3 发布流程测试

- 四资产均成功上传后才清理本地输出。
- 任一资产缺失、上传失败或远端大小不一致时发布失败。
- 发布失败不推进 `release_state`。
- 再次运行同一目标 Release 不产生错误基线。
- 非交易日不构建、不上传、不推进状态。

### 13.4 最终验收标准

- 从上一交易日安装状态执行一次 `deep-alpha update`，实际只下载两个 delta 并更新到目标日。
- 更新后的 Provider 与同 Release 的两个全量包安装结果等价。
- 行情与 daily_basic 最后日期均等于目标交易日。
- 客户端元数据中的两个数据源 Release 完全一致。
- 增量不可用时能自动、安全地回退配对全量更新。
- 任意模拟中断后均可恢复到旧版本或完整新版本，不出现半更新状态。

## 14. 风险与设计取舍

### 14.1 为什么保留全量包

增量链依赖本地基线和连续 Release。全量包仍是首次安装、断档恢复、历史修正及灾难恢复的必要资产，不能被增量包替代。

### 14.2 为什么通过全量产物比较生成 delta

行情构建包含交易日历、复权/归一化、证券范围和指数成分逻辑。如果单独实现“仅导出当天”路径，很容易与全量构建产生语义漂移。比较上一全量基线与当前全量产物虽然构建端仍有全量成本，但能显著降低客户端下载和安装成本，并可可靠发现历史修正。

### 14.3 为什么 market 与 daily_basic 必须配对更新

两类 `.day.bin` 共享同一全局日历。分开推进会导致日历、文件长度、Release 元数据和可查询日期不一致，因此客户端需要把两个增量视为一个事务。

### 14.4 GitHub Release 原子性

GitHub Release 资产上传本身不是多资产原子事务。发布端必须先在本地完成全部校验，再上传，并在最后回读验证。客户端只应使用资产齐全且联合校验通过的 Release；如果看到部分上传状态，应等待或回退到上一完整 Release，而不是安装不完整资产。
