# TweakTheBeat

开源、可复用的 **BLE 调试器 CLI**，以及基于它构建的设备玩法测试自动化工作台。

被测设备的一切特征（广播名、GATT 布局、握手序列、帧格式、断言规则）都由 TOML 配置文件描述，核心框架**协议无关、设备无关**——换设备、换协议栈只改配置或加适配器，不改核心代码。

## 双接口

| 使用者 | 形态 | 特点 |
|---|---|---|
| 人类 | 交互式 REPL + 子命令 + 彩色表格 | 持久连接，替代手敲 hex 帧的日常调试 |
| AI / 脚本 | `--json` 稳定信封 + 确定性退出码 + 磁盘状态文件 | 单条命令原子、无交互、可被程序稳定解析 |

## 功能

- **扫描 / 连接 / GATT / 读写 / 通知订阅**：完整 BLE 调试基本操作
- **Profile 驱动**：一个 `profile.toml` 描述广播名过滤、服务/特征 UUID、握手序列、帧类型表；复杂解码用同目录 `adapter.py` 钩子保底
- **用例自动化**：Markdown 表格用例文档（文档即数据源）+ 每用例一个 TOML 断言规则（时序匹配、通配、expect_not），渐进覆盖，覆盖不足自动降级 `MANUAL` 不误判
- **物理刺激用例**：执行器打印操作指引 → 阻塞等待 → 测试者用 `confirm --yes|--no` 应答（跨进程经状态文件同步）
- **报告**：每用例 `PASS / FAIL / MANUAL`，FAIL/MANUAL 附完整上行日志与时间戳，`.md`（人读）+ `.json`（机读）成对产出
- **退出码三档**（0 成功 / 1 执行失败 / 2 用法错误）+ JSON `error.code` 字符串枚举，AI 可直接按码分支

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/pip install -e ble-cli        # Windows；Linux/macOS 用 .venv/bin/pip

ble-cli scan                                # 扫描附近 BLE 设备
ble-cli --profile ble-cli/examples/demo_profile/profile.toml init   # 连接 + 握手（示例 profile）
ble-cli --profile ble-cli/examples/demo_profile/profile.toml repl    # 交互式调试壳
```

完整命令速查、AI 工作流、profile 格式与用例规则写法见 **[ble-cli/README.md](ble-cli/README.md)**。

## 仓库布局

```
TweakTheBeat/
├── ble-cli/                       # 开源框架包（四层架构，可独立打包发布）
│   ├── src/blecli/
│   │   ├── transport/             # 传输层：Backend 抽象 + bleak 实现（预留 bumble/HCI）
│   │   ├── core/                  # 核心层：协议无关，零设备知识
│   │   ├── profiles/              # 适配层：profile TOML 加载器 + 钩子接口
│   │   └── cases/                 # 应用层：用例解析/断言规则/执行器/报告
│   ├── examples/demo_profile/     # 示例 profile（占位符 UUID，公开）
│   └── tests/                     # 单元测试（无需真机）
├── .claude/skills/ble-cli/        # AI 代理操作手册（SKILL）
├── .MEMORY/                       # 项目协作记忆（跨 agent 共享，随仓库推送）
└── .local/                        # 本机私有配置（gitignore，永不入库）
```

## 项目记忆

`.MEMORY/` 是本项目的**跨 agent 协作记忆**——任何参与本项目的 AI 会话都从这里恢复上下文，不依赖单个会话历史。入口 [.MEMORY/INDEX.md](.MEMORY/INDEX.md)；设计定稿、决策日志、需求迭代记录、当前状态分文件存放。

被测的真实设备在公开文本中一律以代号「**目标设备**」指代；其真实标识、协议细节、用例文档位于本机私有文件（不入库、不推送），供本机开发与测试使用。

## 当前状态

- 阶段 0（需求迭代）与阶段 1（框架实现）已完成：54 项单元测试全绿
- **真机全量冒烟达成**：P0 八用例 7 PASS / 1 MANUAL / 0 FAIL，人类与 AI 模式全流程验证通过
- 待定事项与后续计划见 [.MEMORY/state.md](.MEMORY/state.md)

## 开源形态

框架层（`ble-cli/`）与被测设备专有部分（profile、用例配置）代码分离，框架可独立开源（许可证待选）；预演标准：core 不 import 任何含设备专有常量的模块。
