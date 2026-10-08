---
title: 当前项目状态
type: state
updated: 2026-10-08
---

# 当前状态

- 阶段：**1 — 实现完成 + D10 真机全量冒烟达成 + D11/D12 已决已实现**（见 decisions.md / design.md）
- 当前活动：D11（daemon 子命令）与 D12（cases run 单连接复用）已实现，73 项单测全绿、本机干跑 5/5（C1.2 已加 fresh_connection）。**待真机验证**：daemon start→透明路由→stop 全流程、批量用例单连接复用（需用户在场）。
- 待用户：真机验证时间；不握手会话支持（C1.1 类）；.gitignore 审阅。
- 上一步（2026-09-23）：
  - D10 一次性真机全量冒烟完成：P0 共 8 用例 **7 PASS / 1 MANUAL / 0 FAIL**（A1.1/C1.2/C2.1/C2.2/D1.1/D6.1/D6.2/D7.1）；
  - 冒烟验证：scan/init（握手）/gatt 树/write/物理刺激 confirm 全流程在真机可用；断言与 expect_not 判定准确；
  - 冒烟暴露 6 处框架问题已修复提交（99116fb）：bleak 3.x services dict 迭代、上行 t_ms 时间戳、connect 输出补 handshake 证据、--id 可重复、有规则无断言的纯物理用例按矩阵判 MANUAL、回归断言；
  - 两处设备行为与文档出入及一项新协议观测（按住按键注入配置帧后设备以固定间隔连续上报步进进度）——细节均记入本机 .local/README.md，仓库文本不涉帧值。
- 待用户：
  - 审阅 .gitignore 并按需手动修改（自 04b027c 起仍在待办）；
  - 决定 daemon 子命令（AI 模式长连接）是否立项。
- 阻塞项：无。
- 真机状态：冒烟结束已断开；设备 32s 无连接自动休眠，下次操作前需旅行锁唤醒。
