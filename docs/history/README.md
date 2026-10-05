# 历史记录

故障分析、迁移验证和设计计划的原始记录，仅供追溯。内容按当时代码撰写，部分提到的组件（如 C++ 桥接 `tianji_bridge`、`tj_hold`）已删除；当前行为以 [README](../../README.md) 和 `docs/` 顶层文档为准。证据 JSON 与引用它的报告放在同一目录。

## 遥操作 IK 与伺服（`ik_servo/`）

| 日期 | 记录 | 结论 |
| --- | --- | --- |
| 09-15 | [IK 回归排查](ik_servo/teleop_ik_analysis.md) | 撤回新增的连续性和单帧步长拒绝条件，加入 `[IK诊断]` 快照和离线分析工具 |
| 09-15 | [15:11 IK 暂停分析](ik_servo/teleop_ik_20260915_1511.md) | 7 次 IK 暂停中 1 次确实越限，6 次存在合法近邻解，原因是冗余姿态选解不当 |
| 09-15 | [核心控制修复](ik_servo/teleop_servo_20260915.md) | 逐帧精确 IK 改为受约束的笛卡尔速度求解，关节约束为硬约束、目标推进可缩小 |

## 控制超时与暂停（`control_timeout/`）

| 日期 | 记录 | 结论 |
| --- | --- | --- |
| 09-15 | [中途暂停分析](control_timeout/teleop_pause_20260915.md) | 7 次自动暂停；修复看门狗误判过期和状态查询等待手部求解锁两个并发问题 |
| 09-15 | [18:59 中断排查](control_timeout/teleop_pause_1859_20260915.md) | 13 次为上一条机械臂命令到期而新命令未提交，1 次为 Quest 坐标系切换 |
| 09-15 | [19:05 超时原因](control_timeout/teleop_timeout_1905_20260915.md) | 9 次超时均为下一条双臂命令提交前上一条已到期；建议手部降频并分进程 |
| 09-15 | [超时根因修复](control_timeout/teleop_timeout_fix_20260915.md) | 根因是 Wuji 收流与机械臂计算争用 GIL；Wuji 移入独立进程后，180 s 离线压测无超时（不含 Quest 输入和实际运动） |
| 09-16 | [新鲜度分层调研](control_timeout/teleop_freshness_layers_research_20260916.md) | 输入失效由控制层处理，目标期限逐层传递复查，断流最终保护应放在设备侧 |

## 天机设备与 SDK（`tianji/`）

| 日期 | 记录 | 结论 |
| --- | --- | --- |
| 09-16 | [Q 退出后左臂运动](tianji/tianji_exit_incident_20260916.md) | 退出未下伺服且停止结果未确认；改为退出时对受控臂下伺服并等待 IDLE 反馈 |
| 09-17 | [官方 SDK 迁移验证](tianji/tianji_official_sdk_migration.md) | 删除旧 C ABI 桥接，全部入口改用随包官方 SDK，测试与运动学对比通过 |
| 09-20 | [左臂位置模式失败](tianji/tianji_position_mode_failure_20260920.md) | 左臂 J1 内外编码器偏差接近控制器阈值，导致错误 4／6；09-21 复现 |
| 09-21 | [外编码器清零后失败](tianji/tianji_encoder_zero_failure_20260921.md) | 清零后驱动器报 0xFF35，J2 也报编码器检查错误，需厂家现场处理 |

## 其他

- [数据采集改进计划](data_collection_plan.md)：已实施，开头列出与实现的差异。
- [`validation/`](validation/)：未单独成文的现场验证数据。`quest_wakeup_*.json` 是 Quest 3S 唤醒实验，见 [Quest APK 验证记录](../../quest_app/artifacts/VERIFICATION.md)；`teleop_convenience_validation_20260916.json` 是回位、取消和预览的实机验证；`wuji_engagement_validation_20260916.json` 是右手 Hand2 种子保持测试。
