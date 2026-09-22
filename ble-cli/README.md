# ble-cli — 协议无关的 BLE GATT 调试 CLI

一个开源、可复用的 BLE 调试工具。面向两类使用者：

- **人类**：交互式 REPL 与彩色表格，替代手敲 hex 帧的日常调试；
- **AI 代理**：`--json` 稳定信封输出 + 磁盘状态文件 + 单命令原子语义，适合 LLM 驱动自动化测试。

核心框架**协议无关、设备无关**：被测设备的一切特征（广播名、GATT 布局、握手序列、帧格式、断言规则）都由 TOML profile 与断言规则文件描述，不写死在代码里。

## 特性

- 四层架构：传输后端（bleak，预留 bumble/HCI）→ 核心层（无任何设备知识）→ 适配层（TOML profile + Python 钩子）→ 应用层（CLI / 用例执行器 / 报告）
- 人类模式：持久连接 REPL、ANSI 彩色表格（自动检测 isatty）
- AI 模式：单条命令 = 单次连接（BLE 连接无法跨进程共享），状态通过磁盘文件在命令间传递
- 用例执行：Markdown 表格用例文档 + 每用例一个 TOML 断言规则（渐进覆盖），自动校验报告，覆盖不足自动降级 MANUAL
- 三档退出码（0/1/2）+ JSON `error.code` 字符串枚举，AI 可直接按码分支

## 安装

Python ≥ 3.11（依赖 `tomllib`），唯一第三方运行时依赖是 [bleak](https://github.com/hbldh/bleak)：

```bash
python -m venv .venv
.venv/Scripts/pip install -e .        # Windows；Linux/macOS 用 .venv/bin/pip
```

安装后获得 `ble-cli` 命令（等价于 `python -m blecli`）。

## 快速上手（人类模式）

```bash
ble-cli scan                                # 扫描附近设备
ble-cli --profile examples/demo_profile/profile.toml connect
ble-cli --profile examples/demo_profile/profile.toml init     # 连接 + 握手
ble-cli --profile examples/demo_profile/profile.toml gatt     # GATT 树
ble-cli --profile examples/demo_profile/profile.toml write "DE AD BE EF" --listen 3
ble-cli --profile examples/demo_profile/profile.toml repl      # 交互式调试壳
```

进入 `repl` 后连接保持，可用 `scan / connect / init / gatt / write / sub / disconnect / help / quit`。

## 快速上手（AI 模式）

所有命令加 `--json`，stdout 只输出一个 JSON 信封：

```json
{
  "schema": "ble-cli/1",
  "command": "write",
  "status": "ok",
  "data": {"written": ["DE AD BE EF"], "uplinks": [{"t_ms": 123, "hex": "BE EF 01"}]},
  "warnings": [],
  "error": null,
  "state_file": ".local/runs/state.json",
  "elapsed_ms": 812
}
```

- `status` 为 `ok | error`；出错时 `error = {"code": "...", "message": "..."}`，`code` 是稳定字符串枚举（见下）
- 退出码：成功 0；执行失败 1；用法错误 2（`usage_error`）
- 状态文件（默认 `.local/runs/state.json`，可用 `--state-file` 指定）在命令间传递已记住的设备地址、pending 确认、上次报告路径等

### 典型 AI 工作流

```bash
ble-cli --json scan --timeout 5 --filter "Demo*"
ble-cli --json --profile P init                      # 重连 + 握手（每命令独立连接）
ble-cli --json --profile P write "DE AD" --listen 2
ble-cli --json --profile P sub --timeout 5
ble-cli --json confirm                                # 回答用例执行器的物理刺激确认
```

## error.code 全枚举

| 类别 | 枚举值 |
|---|---|
| 用法 | `usage_error` |
| 设备/连接 | `device_not_found` `connect_failed` `disconnected` `ble_os_error` |
| GATT | `service_not_found` `char_not_found` `write_failed` `notify_failed` |
| 会话 | `handshake_timeout` `timeout` |
| 配置 | `profile_not_found` `profile_invalid` `profile_hook_error` |
| 用例 | `cases_doc_not_found` `cases_parse_failed` |
| 状态 | `state_file_error` |
| 兜底 | `internal_error` |

## Profile 文件

设备的一切知识都在一个 `profile.toml` 里：广播名过滤、服务/特征 UUID、下行帧、上行通知、连接握手序列（写 sequence + 期望 pattern）、帧类型表（prefix+len 或 XX 通配 pattern 匹配、字节字段解码）、可选 Python 钩子模块（同目录 `adapter.py`，用 `AdapterBase` 解码自定义帧）。完整格式见根目录 `.MEMORY/design.md` 与 `examples/demo_profile/`。

## 用例执行

用例文档是一个 Markdown 表格（列：用例 | 注入帧 | 物理刺激 | 预期 | 备注），支持 `XX` 通配、`->` 帧序列、`//` 多选、`×N` 重复等记号。断言规则是每用例一个 `<case_id>.toml`：

```toml
case_id = "X1.1"
mode = "inject"                 # inject | physical | observe
[[inject]]
write = "DE AD"
[assert]
timeout_ms = 5000
[[assert.expect]]
name = "ack"
pattern = "BE EF XX"            # 顺序期望，游标推进
[[assert.expect_not]]
pattern = "AC 01 01"
[human]
instruction = "按下物理按键"     # physical 用例：指引 + 阻塞等待确认
[[human.checks]]
id = "led"
prompt = "LED 是否点亮？"
```

执行与报告：

```bash
ble-cli --json cases list --doc cases.md --validate          # 解析 + 校验
ble-cli --json --profile P cases run --doc cases.md --rules rules/ --out report
ble-cli --json report --path report_20260922_103000          # 汇总
```

判定矩阵：断言全过 + 人工确认齐备 → `PASS`；断言失败 → `FAIL`；无规则 / 未确认 / 覆盖不足 / 声明 divergence → `MANUAL`。报告为一对 `.md`（人类阅读）+ `.json`（机器读取）文件，含每个用例的上行日志表。

物理刺激用例执行时会向状态文件写入 `pending_confirm` 并阻塞轮询；测试者（人）用 `ble-cli confirm --yes|--no --note ...` 应答，执行器读到后继续。

## 目录结构

```
ble-cli/
  pyproject.toml
  src/blecli/
    transport/   # Backend 抽象 + bleak 实现
    core/        # 协议无关核心：frames/discovery/connection/gatt/state
    profiles/    # profile TOML 加载器（含钩子）
    cases/       # 用例解析/断言规则/执行器/报告
    cli.py repl.py output.py errors.py util.py
  examples/demo_profile/   # 示例 profile + 钩子
  tests/                   # pytest（53 项，无需真机）
```

仓库根目录另有 `.MEMORY/design.md`——五份设计定稿的唯一事实来源（决策速查表、profile/规则/信封 schema 全量规格）。

## 开发

```bash
.venv/Scripts/python -m pytest tests/ -q
```

## 本地约定

- `.local/` 与需求文档等本机私有文件被 gitignore，永不入库；机器特定的 profile、断言规则、用例配置放这里
- `.claude/skills/ble-cli/SKILL.md` 是供 Claude Code 等 AI 代理使用的操作手册
