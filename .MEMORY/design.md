---
title: 设计定稿
type: project
updated: 2026-09-22
---

# 设计定稿（需求第八节五问 + 11 项决策的产物）

本文件为设计唯一事实来源；决策过程见 `decisions.md`。**已脱敏**：真实设备名/UUID/帧值一律用占位符或「目标设备」表示，真实值见本机需求文档与 `.local/` 私有文件（见 `.MEMORY/README.md` 保密约定）。

## 1. 目录结构（第八节第 2 问，D6）

```
TweakTheBeat/
├── .MEMORY/                           # 协作记忆
├── .claude/skills/ble-cli/SKILL.md    # 脱敏版 SKILL（D9，gitignore 已豁免）
├── ble-cli/                           # ★ 开源框架包（可独立打包发布）
│   ├── pyproject.toml                 # 依赖仅 bleak；core 可单独 install
│   ├── README.md                      # 框架通用文档
│   ├── src/blecli/
│   │   ├── cli.py                     # argparse 入口 + 子命令分发
│   │   ├── transport/                 # ── 传输层 ──
│   │   │   ├── base.py                # Backend 抽象接口（未来 bumble/HCI 实现它）
│   │   │   └── bleak_backend.py       # 默认实现（bleak）
│   │   ├── core/                      # ── 核心层（协议无关，零专有常量）──
│   │   │   ├── discovery.py           # 扫描 + 名称过滤
│   │   │   ├── connection.py          # 连接管理（握手序列由 profile 驱动）
│   │   │   ├── gatt.py                # GATT 发现 + CRUD
│   │   │   ├── notify.py              # 通知订阅/转发（原始帧 hex+时间戳日志）
│   │   │   ├── output.py              # --json 结构化输出 + 退出码（D8）
│   │   │   └── state.py               # 状态落盘文件（AI 跨调用恢复）
│   │   ├── profiles/                  # ── 适配层（协议无关的装载器）──
│   │   │   ├── loader.py              # TOML 加载 + schema 校验（D5）
│   │   │   └── adapter_base.py        # 钩子接口定义（D2）
│   │   ├── cases/                     # ── 应用层·用例执行器（通用引擎）──
│   │   │   ├── parser.py              # markdown 用例表解析（D4）
│   │   │   ├── rules.py               # 断言规则引擎（D7）
│   │   │   ├── runner.py              # 状态机：注入/等待/confirm/超时（D3）
│   │   │   └── report.py              # PASS/FAIL/MANUAL markdown 报告
│   │   └── repl.py                    # 人类交互模式（彩色表格）
│   ├── examples/demo_profile/         # 示例 profile（占位符 UUID，公开）
│   │   └── profile.toml
│   └── tests/                         # 框架单测（mock 后端）
└── .local/                            # ★ 本地私有（gitignore，永不提交）
    ├── profiles/target/profile.toml   # 目标设备真实 profile
    ├── profiles/target/adapter.py     # 可选钩子（D2 保底）
    ├── cases/asserts/                 # 用例断言规则文件（真实帧）
    ├── cases/config.toml              # 用例文档路径 + 解析选项
    └── runs/                          # 运行时状态文件/日志/报告
```

设计要点：①提交边界与 D6 对齐；②依赖方向单向——core 不 import 任何 profile 目录，profile 是数据文件；③用例执行引擎协议无关公开、断言规则是数据放 `.local/`；④bumble/HCI 接入 = 实现 `transport/base.py` 的 `Backend` 接口。

## 2. profile TOML 格式（第八节第 3 问，D5/D2）

- 上行分类是声明式主战场；下行 v1 用字面帧（用例表注入帧本就是完整 hex）；复杂解码走 `adapter.py` 钩子。
- 通配符 `XX` 与用例文档标注习惯一致；断言规则共用同一套匹配语法。

```toml
[meta]
name = "demo"
version = "1.0"
description = "示例 profile"

[device]
name_filter = "DemoDevice*"          # glob，可多个
# address = "AA:BB:..."              # 可选：直连固定 MAC
# wake_hint = "扫描不到请人工唤醒"     # 可选

[[gatt.services]]
service = "<service-uuid>"

[gatt.chars.write]                   # 指令下行特征
uuid = "<cmd-rx-uuid>"
write_type = "write_without_response"
max_packet = 20

[gatt.chars.notify]                  # 上报上行特征
uuid = "<cmd-tx-uuid>"
cccd = "0001"
max_packet = 16

[handshake]                          # 连接初始化握手（core 自动执行）
deadline_ms = 5000
sequence = [ { write = "<握手帧 hex>" } ]
expect = [                           # init 判定成功依据（顺序无关）
  { pattern = "BE EF XX", name = "ack" },
  { pattern = "CA FE +4B", name = "sn" },
]

[frames]
byteorder = "big"

[[frames.types]]
name = "ack"
match = { prefix = [0xBE, 0xEF], len = 3 }
fields = [ { name = "value", byte = 2 } ]

[[frames.types]]
name = "float_report"                # 声明式够不到 → 钩子
match = { prefix = [0xF1, 0x81], len = 10 }
hook = "decode_float"

[hooks]
module = "adapter"                   # profile 同目录 adapter.py（可选）
```

匹配/解码语法：`prefix`+`len` 精确匹配；`pattern` 支持 `XX` 通配与 `+4B` 后缀长度；`fields` 按字节偏移解码（大端）；`hook` 调 `adapter.py` 中 `decode_*(payload: bytes) -> dict`；未匹配包记 raw 日志标 `unknown` 永不丢数据。

loader 校验：TOML 语法/必填键/未知键/hex 合法性/长度一致性/UUID 格式/钩子签名 → `error.code = profile_invalid | profile_hook_error | profile_not_found`。

## 3. 断言规则格式（D7）

- 每用例一个 TOML 规则文件（`.local/cases/asserts/<用例ID>.toml`）；**渐进覆盖**：无规则文件的用例默认"注入+日志+MANUAL"。
- 禁止字符串匹配；自然语言部分转测试员指引/人工检查项。

```toml
case_id = "X1.1"
mode = "inject"                      # inject | physical | observe

[[inject]]
write = "<注入帧 hex>"               # 可多条按序写

[human]                              # physical/observe 模式
instruction = "操作指引，打印给测试员"
checks = [ { id = "led", prompt = "灯是否红快闪？" } ]   # 视觉检查项

[assert]
timeout_ms = 5000                    # 注入/刺激后判定窗口

[[assert.expect]]                    # 数组顺序 = 时序顺序
name = "状态帧"
pattern = "BE EF XX"                 # 或结构化: type+fields

[[assert.expect]]
name = "序列号帧"
pattern = "CA FE +4B"

[[assert.expect_not]]
pattern = "AC 01 01"

# [assert] expect_none = true        # 窗口内应无任何上行

# divergence = "固件现状与需求不符的备注"   # → 自动 MANUAL
```

expect 可选语义检查：`count = { min = 3 }`（周期上报类）、`check = "increasing"`（字段单调递增）、`check = "adapter:xxx"`（钩子）。

判定矩阵：全部命中+expect_not 未出现+人工检查项全过 → PASS；断言失败/窗口超时 → FAIL（附完整上行日志）；expect_none 出现上行/钩子缺失/人工项未答/解析失败/有 divergence → MANUAL（附原因）。

示例（demo 协议，真实规则文件在 .local/ 结构相同）：
- 纯注入（序列+通配）：inject `DE AD BE EF` → expect `BE EF XX` 后 `CA FE +4B`
- 物理刺激（时序）：inject 配置帧 → 指引"按下任一键后松开" → expect `<触发帧>` 后 `<取消帧>`
- 错误响应：inject `99` → expect `EE 99 01`（demo 错误头）

## 4. --json 输出 schema（第八节第 4 问，D8）

统一信封（所有命令共用）：

```json
{
  "schema": "ble-cli/1",
  "command": "init",
  "status": "ok",
  "data": {},
  "error": { "code": "handshake_timeout", "message": "..." },
  "warnings": [],
  "state_file": ".local/runs/state.json",
  "elapsed_ms": 3421
}
```

- `schema/command/status/error.code` 四字段永远存在；`status` 仅 ok/error。
- 时间戳 ISO8601 带毫秒；hex 一律 `"AA BB CC"` 大写空格分隔。
- **用法错误也 JSON 化**（argparse 错误被拦截重写为信封，`code="usage_error"` + 退出码 2）。
- 退出码：0 成功 / 1 执行失败 / 2 用法错误；细分靠 `error.code`。

各命令 data 形状：scan→devices[]；connect→connected/address/profile/services_found；init→handshake[]；gatt→services[]；write→written + 可选 uplinks[]（--listen N）；sub --timeout N→uplinks[]；cases list --validate→cases[]+stats；cases run→case_id/result(PASS|FAIL|MANUAL)/uplinks[]/assertion_results[]/human_checks[]；confirm→case_id/confirmed；report→path/summary{total,pass,fail,manual}；disconnect→disconnected。

error.code 全枚举：usage_error / device_not_found / connect_failed / service_not_found / char_not_found / write_failed / notify_failed / handshake_timeout / timeout / disconnected / profile_not_found / profile_invalid / profile_hook_error / cases_doc_not_found / cases_parse_failed / state_file_error / ble_os_error / internal_error。

状态文件（`.local/runs/state.json`）：记录逻辑状态与进度（device/last_action/pending_confirm/run_log），BLE 连接每次调用重建（连接→profile 握手自动重放）；`pending_confirm` 是 runner 阻塞等待时轮询的确认点（测试员跑 `confirm` 写入）。

## 5. SKILL 结构（第八节第 5 问，D9）

`.claude/skills/ble-cli/SKILL.md`，脱敏版单一文件提交仓库；frontmatter（name: ble-cli + 中英文触发描述）+ 九节：

1. 这是什么（定位/入口/何时用）
2. **先读敏感信息**（读工作树 `ble-automation-prompt.md` 拿真实值；不存在→向用户索取；真实值绝不写入推送文本）
3. 命令速查（scan/connect/init/gatt/write/sub/disconnect/cases/confirm/report/repl 一行一表）
4. --json 解读要点（信封/分支/warnings/state_file/用法错误也是 JSON）
5. 退出码表（三档 + error.code 全枚举 + 每码应对动作）
6. Cookbook（人类冒烟/AI 冒烟/单用例含物理刺激节奏/全量+报告/发单帧）
7. 故障排查（休眠/单连接被占/握手超时/WinRT 偶发/扫描缓存，每项带下一步动作）
8. 资料位置指针（需求文档/.MEMORY/.local/profile/断言规则）
9. 保密红线（提交前自查；细则引用 .MEMORY/README.md）

## 6. 决策速查（详见 decisions.md）

| # | 结论 |
|---|---|
| D0 | 项目记忆在仓库 `.MEMORY/` |
| D1 | 数据源仅直接引用+保密 |
| D2 | 配置为主+adapter.py 钩子保底 |
| D3 | 物理刺激=指引+阻塞等待+confirm |
| D4 | 解析校验报告+降级 MANUAL |
| D5 | argparse + TOML，Python≥3.11，唯一运行时依赖 bleak |
| D6 | 框架公开+示例 profile；专有内容 .local/ |
| D7 | 断言规则=每用例 TOML，渐进覆盖 |
| D8 | 退出码三档 + JSON error.code |
| D9 | SKILL 仅仓库脱敏版（单文件） |
| D10 | 真机一次性全量冒烟 |
| 脱敏 | 代号「目标设备」，真实标识不入仓库 |
| GIT | gitignore 由 AI 维护、用户审阅 |
