---
title: 项目定位与边界
type: project
updated: 2026-09-23
---

# 项目定位

开源、可复用的 **BLE 调试器 CLI**（Python 优先），人类交互 + AI/脚本无交互（`--json`）双接口。核心框架**协议无关、设备无关**：换设备/换 GATT 协议栈/扩展 BT 时只改配置文件或加适配器，不改核心代码。

首个落地场景：**目标设备**（代号）BLE 玩法测试自动化（替代人工 nRF Connect 手发 hex 帧）。

需求原文：工作树根 `ble-automation-prompt.md`（**本地未跟踪文件，禁止提交/推送**；本文件为其摘要，冲突时以原文 + iteration.md 的最新澄清为准）。

# 交付物

1. BLE 调试器 CLI：人类交互模式（REPL/子命令、彩色表格）+ 无交互模式（`--json` 稳定 schema、确定性退出码、状态落盘文件、单条命令原子）。
2. Claude Code SKILL：`.claude/skills/ble-cli/SKILL.md`，**脱敏版提交仓库**（D9 已决，内容用代号与占位符）；让未来任意 AI 会话凭 SKILL + 本机需求文档正确使用 CLI（命令速查/JSON 解读/退出码表/cookbook/故障排查/文档位置指针）。
3. 目标设备 profile：第一个 profile 配置（TOML，D5 已决，格式见 design.md 第 2 节）。
4. 用例执行器：程序化解析 205 条用例文档（文档即数据源）；纯注入自动执行；物理刺激用例引导测试员；预期断言用规则文件（禁止字符串匹配），无法断言的标 MANUAL。
5. 报告：每用例 PASS/FAIL/MANUAL，FAIL/MANUAL 附完整上行日志+时间戳，汇总 markdown。

# 分层架构（需求要求）

1. 传输层：BLE 后端抽象接口（扫描/连接/读写/通知/断开），默认 bleak（WinRT），预留 bumble/HCI。
2. 核心层（协议无关，开源部分）：发现/连接/GATT CRUD/通知/日志/结构化输出/退出码。不含任何目标设备专有逻辑。
3. 适配层：每设备一个 profile 配置文件（设备过滤、UUID、握手序列、帧编解码、上行分类规则）。
4. 应用层：交互模式、无交互模式、用例执行器、报告。

# 边界（本轮不做）

BT Classic、2.4G 玩法、OTA、工厂模式、郊狼中继链路（P 组 P1.3/P1.4）；不修改固件；MCP server 为 phase 2 预留接口。

# 开源形态

框架层（core）与专有协议层（目标设备 profile/测试文档）代码分离；框架日后可独立开源（MIT/Apache-2.0 待选），目标设备部分留私有目录，是否随框架发布由用户决定。预演标准：core 不 import 任何含目标设备专有常量的模块。

# 参考项目结论（已调研）

- **blew**（stass/blew）：命令结构/AI 接口设计最佳参照（仅 macOS，代码不可直接用）。子命令：scan/gatt tree/read/write/sub/exec/REPL/mcp；`-o kv` 结构化输出、确定性退出码。
- **blescope**：输出格式（table/json/sarif）与退出码约定（0 干净/1 有发现/2 用法错误）参照。
- **bumble**：纯 Python 蓝牙栈（HCI），作为未来底层/抓包后端预留适配点，本轮不引入。
- **bleak**：默认传输后端（Windows WinRT / macOS CoreBluetooth / Linux BlueZ）。

# 验收标准（摘要，详见需求原文第七节；2026-09-23 D10 冒烟后状态标注）

| # | 状态 | 说明 |
|---|---|---|
| 1 | ✅ 达成 | 人类模式冒烟（scan→connect→init 握手→gatt→write）真机通过 |
| 2 | ✅ 达成 | AI 模式全 --json 冒烟同流程通过（含状态文件恢复） |
| 3 | ✅ 达成 | P0 八用例全量执行：7 PASS / 1 MANUAL（纯物理观察）/ 0 FAIL，报告含上行日志 |
| 4 | ⏳ 待验证 | 新会话仅凭 SKILL + 本机需求文档完成指定用例（冒烟后未演练） |
| 5 | ⏳ 部分 | 示例 profile 已随框架提供；bumble/HCI 接入说明待补 |

1. 人类模式冒烟：扫描发现目标设备→连接→使能上行通知→5s 内自动下发配置帧→收到握手/状态响应（具体标识见本地需求文档）。
2. AI 模式冒烟：全 `--json` 无交互完成同流程（含状态文件恢复），输出可被程序稳定解析。
3. 全部 P0 用例可执行（自动或交互引导），报告区分 PASS/FAIL/MANUAL，FAIL 日志齐全。
4. 新 Claude 会话仅凭 SKILL（仓库脱敏版）+ 本机需求文档完成"连接设备并执行指定用例（编号见本地文档）"。
5. 不改核心代码仅新增"示例 profile"证明配置驱动；给出 bumble/HCI 接入说明。
