---
name: ble-cli
description: 操作 ble-cli（BLE GATT 调试 CLI）——扫描、连接、发帧、收上行、用例执行、报告。Use for BLE debugging, GATT write/notify, and automated case execution with the ble-cli tool.
---

# ble-cli 操作手册（脱敏版）

## 1. 这是什么

`ble-cli` 是本仓库的 BLE GATT 调试 CLI（代码在 `ble-cli/`，Python ≥3.11，唯一运行时依赖 bleak）。两类用法：

- **人类模式**：交互 REPL、彩色表格、手动发帧调试
- **AI 模式**：`--json` 稳定信封 + 磁盘状态文件，每条命令原子化（独立连接→执行→断开）

何时用：用户要求扫描 BLE 设备、读写 GATT 特征、执行用例文档里的测试用例、生成测试报告时。

安装（已装可跳过）：`ble-cli/.venv/Scripts/pip install -e ble-cli`，之后 `ble-cli` 或 `ble-cli/.venv/Scripts/python -m blecli` 均可。运行设备命令需真机与蓝牙适配器；纯解析类命令（`cases list`）无需真机。

## 2. 先读敏感信息（动真机之前必做）

真实设备的广播名、服务/特征 UUID、帧字节值**不在仓库任何文件里**。操作真机前：

1. 读工作树根目录的 `ble-automation-prompt.md`（未跟踪文件，本机才有）拿真实值；
2. 该文件不存在 → 向用户索取所需值；
3. 真实值只允许写入 `.local/`（gitignore 内，永不推送）或本机会话记忆；
4. 仓库示例 `ble-cli/examples/demo_profile/` 的 UUID/帧值全部是虚构占位，仅演示格式。

## 3. 命令速查

| 命令 | 作用 |
|---|---|
| `scan [--timeout N] [--filter "Glob*"]` | 扫描附近设备（无需 profile） |
| `connect [--no-handshake]` | 连接并校验 profile 的 GATT 布局 |
| `init [--no-handshake]` | 连接 + 订阅通知 + 回放 profile 握手序列 |
| `gatt` | 打印设备 GATT 树 |
| `write HEX [--listen S]` | 写一个 hex 帧，可选监听 S 秒上行 |
| `sub --timeout S` | 纯订阅收上行 S 秒 |
| `disconnect` | 清除状态文件里记住的设备 |
| `confirm [--case-id ID] [--yes\|--no] [--note T]` | 回答用例执行器挂起的物理刺激确认 |
| `cases list --doc PATH [--validate] [--config P] [--group G]` | 解析用例文档并列出（可校验） |
| `cases run --doc PATH --rules DIR [--id C] [--group G] [--out P]` | 执行用例（需 profile），出报告 |
| `report [--path P]` | 汇总某次报告（默认上次） |
| `repl` | 交互式调试壳（持久连接） |

全局参数：`--json`（AI 模式）、`--profile PATH`（profile.toml 路径，或环境变量 `BLE_CLI_PROFILE`）、`--state-file PATH`（默认 `.local/runs/state.json`）、`--address MAC`（跳过发现直连）。

设备命令（connect/init/gatt/write/sub）每条都是独立连接：先按 显式 --address → 状态文件记住的地址 → 扫描过滤 的顺序定位设备，再连接、校验 GATT、订阅、回放握手。

## 4. --json 解读要点

- stdout 只有一个 JSON 信封；进度信息走 stderr，解析时忽略
- 信封恒含：`schema`("ble-cli/1")、`command`、`status`("ok"|"error")、`data`、`error`(null 或 `{"code","message"}`)、`warnings`、`state_file`、`elapsed_ms`
- `status=="error"` 时按 `error.code` 分支处理（见第 5 节）；用法错误同样进信封（exit 2）
- 设备地址、pending 确认、上次报告路径都经状态文件跨命令传递——别在脑内记
- 成功退出码 0 / 执行失败 1 / 用法错误 2

## 5. 退出码与 error.code 全枚举

| code | 含义 | 应对动作 |
|---|---|---|
| `usage_error` | 参数/用法错误（exit 2） | 读 message 修正参数重试 |
| `device_not_found` | 扫描/过滤无果 | 提示物理唤醒设备、放宽 filter、或给 --address |
| `connect_failed` | 连接失败 | 重试；确认无其他主机占连接；稍等再试 |
| `disconnected` | 执行中掉线 | 重试该命令 |
| `ble_os_error` | 操作系统蓝牙栈错误 | 建议重开蓝牙或重启蓝牙服务 |
| `service_not_found` / `char_not_found` | GATT 布局与 profile 不符 | 核对真实 UUID，修正 profile |
| `write_failed` / `notify_failed` | 写/订阅失败 | 检查特征属性与 write_type，重试 |
| `handshake_timeout` | 握手期望帧未齐 | 检查 sequence/expect 是否匹配真实协议；先唤醒设备 |
| `timeout` | 通用等待超时 | 加大超时或检查设备状态 |
| `profile_not_found` / `profile_invalid` / `profile_hook_error` | profile 文件问题 | 读 message 定位键/钩子错误并修正 |
| `cases_doc_not_found` / `cases_parse_failed` | 用例文档/规则文件问题 | 读 message 行号，修正后重跑 list --validate |
| `state_file_error` | 状态文件损坏/不可写 | 检查路径权限；必要时备份后删除重建 |
| `internal_error` | 兜底（含 Ctrl-C） | 报告给开发者，附 message |

## 6. Cookbook

**人类冒烟（交互）**
```
ble-cli --profile <P> repl
  scan
  connect
  init
  write "DE AD"            （示例占位帧，真机用真实帧）
  sub --timeout 5
  disconnect
  quit
```

**AI 冒烟（--json）**
```
ble-cli --json scan --timeout 5 --filter "<设备名Glob>"
ble-cli --json --profile <P> init
ble-cli --json --profile <P> write "<hex帧>" --listen 2
ble-cli --json --profile <P> sub --timeout 5
```

**单用例（含物理刺激）**：`ble-cli --json --profile <P> cases run --doc <D> --rules <R> --id X1.2`。执行到 physical 用例会阻塞等待确认——此时（另一个终端/进程）运行 `ble-cli --json confirm --yes --note "灯亮"`；执行器轮询状态文件，读到后继续。超时（默认 600s）则清标记并降级 MANUAL。

**全量 + 报告**：`ble-cli --json --profile <P> cases run --doc <D> --rules <R> --out report`。产出 `report_<时间戳>.md` + `.json` sidecar；`--out` 给无扩展名路径。汇总用 `ble-cli --json report --path report_xxx`。

**只发一帧**：`ble-cli --json --profile <P> write "AA BB CC" --listen 1`。hex 接受空格/冒号/连字符分隔或紧凑写法（`aabbcc`）。

## 7. 故障排查

| 症状 | 下一步 |
|---|---|
| 扫描不到设备 | 设备可能休眠——先物理唤醒（按唤醒键/摇动），再扫描；确认广播名 Glob 正确 |
| 连接被拒/占用 | 单连接设备被手机/nRF 占着——先断开其他连接方 |
| 握手超时 | 检查设备已唤醒；核对 profile 的 sequence/expect 与真实协议一致（读需求文档） |
| 写入无上行 | 确认已 init 订阅；加 `--listen`；检查上行帧 pattern 是否写错 |
| WinRT 偶发异常 | 重试一次；仍失败建议重开电脑蓝牙开关 |
| 扫描结果陈旧 | 重新 scan；`--address` 直连可绕过发现 |
| 命令间地址丢失 | 检查 `--state-file` 路径一致（默认 `.local/runs/state.json`） |

## 8. 资料位置指针

| 资料 | 位置 |
|---|---|
| 需求文档（本机，未跟踪，勿提交） | 工作树根 `ble-automation-prompt.md` |
| 设计定稿（profile/规则/信封 schema 全量规格） | `.MEMORY/design.md` |
| 决策/状态/环境/迭代 | `.MEMORY/` 下 decisions/state/environment/iteration |
| 真实 profile、断言规则、用例配置（本机，勿提交） | `.local/` |
| 示例 profile 与钩子 | `ble-cli/examples/demo_profile/` |
| 测试（无需真机） | `ble-cli/tests/`，`ble-cli/.venv/Scripts/python -m pytest ble-cli/tests -q` |

## 9. 保密红线（提交前自查）

1. 仓库文本（含本 SKILL、README、.MEMORY、代码注释）与 commit message 中**禁止出现**：外部数据源（商用闭源库）的名称/路径/内部结构、被测设备品牌/产品名/广播名、真实协议 UUID、真实帧字节值；
2. 被测设备在仓库文本中一律以「目标设备」指代；
3. 真实细节只存在于：本机 `ble-automation-prompt.md`、`.local/`、会话个人记忆——三者都永不推送；
4. 提交前 `git diff --cached` 自查，再 `git status` 确认无 `.local/`、需求文档等敏感路径；
5. 细则见 `.MEMORY/README.md` 保密约定节。
