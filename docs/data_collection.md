# 原始采集与 Diffusion Policy 数据

## 使用

所有命令在项目根目录、Conda `bimanual-teleop` 环境中执行：

```bash
conda activate bimanual-teleop
PIP_USER=false python -m pip install -e '.[recording]'
python scripts/teleop_quest_tianji.py --record --viewer
```

正式采集使用三路 NVENC。程序会在机械臂运动前检查 NVIDIA 驱动并并发编码、解码三路测试视频；检查失败时直接退出，不自动退回三路 CPU 编码。简明操作步骤见[数据采集快速使用说明](recording_quickstart.md)。

程序启动时会在终端打印本次详细运行日志路径，默认位于 `logs/`。需要固定输出位置时使用
`--log-file PATH`。发生遥操作暂停后，请保留对应 `.jsonl` 文件；其中包含故障前约 2 秒的
逐控制周期时序、天机首次停机快照、Wuji 各数据流期限和录制进程状态，不需要从终端截取被折叠的诊断。

`configs/recording.yaml` 固定三台相机的序列号：第一台 D435 是主视角，默认 RGB＋深度；两台 D436 只启用 RGB。均为 640×480、30 Hz。`main_depth: false` 可关闭主视角深度。`state_hz` 默认 200，只限制记录频率，不修改 SDK 反馈、控制频率或保护逻辑。`output_dir` 默认 `recordings`，相对当前工作目录解析。可以使用 `--recording-config PATH` 指定其他配置。

尚未开始条目时，不启用预览则只检查相机时间戳和新鲜度，不复制像素；启用预览时仅复制三路 5 Hz RGB。按 C 后恢复三路 30 Hz RGB 和主视角 30 Hz 深度的完整采集。相机队列溢出错误会记录队列占用以及各路已接收、已消费帧数。

录制要求双臂双手联合运行。相机预检后仍沿用原有实机运动确认和回位流程。接合并就绪后：

| 操作 | 按键 |
| --- | --- |
| 开始一条演示 | C |
| 结束并保存原始条目；继续遥操作 | S |
| 作废当前条，保留原始文件 | X |
| 手动暂停并保存当前条 | Space 或原暂停手势 |
| 保存当前条并正常退出 | Q |

相机、编码或写盘故障会停止并作废当前条，但不会自动暂停遥操作。操作员应先主动脱离，排除故障后按 C 重启采集进程；待相机就绪，重新接合，再按 C 开新条。设备故障、安全保护和控制看门狗仍会立即暂停运动。如果采集进程被强制终止，需要退出并重新启动遥操作，避免复用可能损坏的通信状态。Ctrl+C、进程崩溃或未完成写入的条目不作为完整数据导出。

预览使用独立进程，只读取专用共享内存中的最新 RGB 图像，约 5 Hz 更新；它不持有正式录制帧。窗口卡顿或关闭不会阻塞编码与写盘，跳过预览更新不影响原始帧保存。正式共享帧池和低维共享队列均有容量限制，消费者跟不上时会明确作废当前条，不覆盖未消费数据。失败条目的 `episode.json.reason` 保留具体故障原因。

## 原始数据

每次运行生成一个独立会话目录。在线保存完成、尚未整理的条目为：

```text
recordings/<session>/episode_000000/
  episode.json              # status: captured
  camera_0.mp4
  camera_1.mp4
  camera_2.mp4
  raw_spool/
```

退出遥操作后执行：

```bash
python scripts/finalize_recording.py --input recordings/<session>
```

`--input` 下所有层级的条目都会被整理，条目内部和以 `.` 开头的临时目录不再向下搜索。单条失败不会中断其余条目。`discarded` 条目在这一步被删除整个目录。

整理完成后增加现有兼容格式，并把 `raw_spool/` 移出 `recordings/`：

```text
recordings/<session>/episode_000000/
  episode.json
  camera_0.mp4
  camera_1.mp4
  camera_2.mp4
  raw.zarr/                 # status: complete 后发布
  raw_spool -> <spool-archive>/<session>/episode_000000   # 符号链接
```

`--spool-archive` 默认是当前目录下的 `raw_spools/`，不得位于 `--input` 之内。转换只读 `raw.zarr` 和 MP4，不需要 `raw_spool`。跨文件系统时先完整复制、再删除原目录；中断后重新运行即可继续。已是 `complete` 但仍带真实 `raw_spool/` 目录的旧条目，再次运行时也会被归档。

需要重新生成 `raw.zarr` 时（例如运动学或整理逻辑有改动）：

```bash
python scripts/finalize_recording.py --input recordings/<session> --refinalize
```

`--refinalize` 先经符号链接找到 `raw_spool`；链接丢失或失效时，按 `<spool-archive>/<session>/<episode>` 查找并重建链接。归档目录被整体搬走后，用 `--spool-archive` 指向新位置即可。找不到 spool 的条目保持 `complete` 并报失败；找到后条目先改回 `captured` 再整理，旧 `raw.zarr` 在新结果发布前保持不变。整理中途失败时条目停在 `captured` 并记录 `finalize_error`，修复后不加 `--refinalize` 重新运行即可。

RGB 在线使用三路独立 NVENC H.264 编码；每个真实采集帧只编码一次，不复制帧凑 30 Hz。MP4 的播放时间不作为同步依据，真实时间先保存在原始分段，离线整理后写入对应 Zarr 表。深度在线顺序保存为原始 `uint16`，离线进行无损压缩；零值保持零值，乘相机 `depth_scale` 得米。深度不做空间重投影，内外参保存在 metadata。

`raw.zarr` 中每个流有独立 `time_ns`（主机单调时间，int64）、`sequence` 和以下数据字段：

| 流 | 字段与形状 |
| --- | --- |
| `arms/left`、`arms/right` | `joint_pos (N,7)`、`eef_pose (N,7)`、`wrench (N,6)` |
| `hands/left`、`hands/right` | `joint_pos (N,20)` |
| `arm_commands/left`、`arm_commands/right` | `joint_pos (N,7)`、`eef_pose (N,7)` |
| `hand_commands/left`、`hand_commands/right` | `joint_pos (N,20)` |
| `cameras/camera_0/rgb` 至 `camera_2/rgb` | `source_time_ms (N,)`；同序号对应视频解码后的帧 |
| `cameras/camera_0/depth`（启用时） | `source_time_ms (N,)`、`image (N,480,640)` |

末端采用各臂自身基座下的法兰，原始位姿为 `[x,y,z,qx,qy,qz,qw]`，位置米、关节角弧度。实际末端在离线整理时由实测关节计算 FK，并校验运动学模型摘要与采集时一致。力顺序为 `[Fx,Fy,Fz,Tx,Ty,Tz]`，单位 N / N·m，沿现有原生传感器轴，不额外去零、滤波或重力补偿。

机械臂目标按原有约 200 Hz、手目标按约 120 Hz 记录。末端目标是送入笛卡尔控制器、约束求解前的目标；关节目标是成功 SDK 提交的关节位置。两者用于不同动作空间实验，不能互相替代。SDK 成功返回不代表已经实机执行。

时间戳保留其真实含义：机械臂是主机读取 SDK 新反馈的时刻，手部是 SDK 出队时刻；相机采用 `GLOBAL_TIME` 帧时间并映射到主机单调时钟，不称作精确曝光时刻。三机无硬件同步；软件对齐不能消除曝光差和设备延迟。发现时钟域变化或时间倒退时会报错。

相机启动预热期间，时钟域切换、时间倒退或帧序号重置会重新开始该流的连续有效帧检查；预热帧不写入数据。三机全部就绪后，时钟域变化、时间或序号倒退仍会报错；倒退错误包含前后时间和序号以便诊断。

`episode.json` 的 `[start_ns,end_ns)` 定义有效窗口；异步收尾可能保留少量窗口外帧，转换时会排除。`captured` 表示原始采集已完成但尚未整理；只有 `complete` 可导出。`failed`、`discarded` 和未完成条目均跳过。配置、单位、相机标定及模型信息每条保存一次，不记录全套设备诊断。

## 离线转换

以下转换要求先完成 `finalize_recording.py`，输入中的条目状态必须为 `complete`。

```bash
python scripts/convert_recording.py \
  --input recordings/<session> \
  --output datasets/episodes_eef.zarr --action-space eef

python scripts/convert_recording.py \
  --input recordings/<session> \
  --output datasets/episodes_joint.zarr --action-space joint
```

输入也可以是一条 episode 或含多个会话的目录。输出路径必须尚不存在，转换失败不会发布半成品或覆盖旧数据。

默认只导出 RGB、机器人状态与动作：即使原始记录包含深度，也不读取、导出深度或因深度缺帧剔除 RGB 样本。需要深度数据时显式添加 `--include-depth`，此时保留原来的深度匹配检查。同一次转换在默认 RGB 模式下可混合有深度与无深度的原始记录；显式包含深度时，完整条目的深度开关必须一致。

以主 RGB 的真实帧时间为基准，其他 RGB 选最近帧，最大时间差 20 ms；显式包含深度时，深度也采用同一匹配规则。实际关节、位置和力线性插值，姿态用 SLERP；状态插值间隔不得超过 50 ms，不在观测边界外补值。action 取该时刻之前最近一次成功提交的目标，最大命令龄 50 ms，不插值控制命令、不人为移动一帧。

缺失、无效数据和主相机超过 50 ms 的帧间隔会拆成连续片段；训练采样不会跨缺口。首尾不满足对齐条件的帧被裁掉。原始时间轴是名义 30 Hz，而非人为生成的严格等间隔网格。

输出 schema v2 使用 DP ReplayBuffer 的 `data/*`，并区分两类累计边界：

- `meta/episode_ends`：每条保留了有效帧的原始演示占一个元素，一条原始演示对应 `[T]`。失败或没有有效帧的条目不占元素。
- `meta/segment_ends`：每个连续有效片段占一个元素，用于约束训练窗口不跨缺帧或时间缺口。每个原始演示边界也是片段边界。

例如一条演示保留 100 帧，中间在第 40 帧后存在缺口，则 `episode_ends=[100]`、`segment_ends=[40,100]`。切片段不增加独立演示数。CLI 和质检报告分别显示 `output_episodes` 与 `output_segments`。

直接使用只读取 `episode_ends` 的通用 DP loader 会忽略内部缺口；本项目适配器会使用 `segment_ends` 采样。自定义 loader 也必须遵循该约定。

| 字段 | 形状 / 类型 |
| --- | --- |
| `camera_0`、`camera_1`、`camera_2` | `(T,480,640,3)`，RGB uint8 |
| `camera_0_depth`（显式 `--include-depth` 时且原始记录有深度） | `(T,480,640)`，uint16 |
| `robot_eef_pose` | `(T,12)`，每臂 xyz＋旋转向量 |
| `robot_joint` | `(T,14)` |
| `hand_joint` | `(T,40)` |
| `wrench` | `(T,12)` |
| `action` | eef 模式 `(T,52)`；joint 模式 `(T,54)` |
| `timestamp` | `(T,)`，相对原始 episode 开始的秒数，float64 |

所有低维训练值为 float32，各组内部先左后右；action 为左臂、右臂、左手、右手。质检结果和片段来源保存在 `meta.attrs['quality_report']` 与 `meta.attrs['segments']`，标定参数保留在对应源条目的 metadata。`segments` 与 `segment_ends` 一一对应，包含原始演示来源和片段输出范围。

旧 schema v1 将连续片段的结束位置写入 `episode_ends`，所以一次采集可能出现多个元素。适配器仍支持这类旧数据，通过 `segments.source_episode` 分组。旧数据中已丢弃的 RGB 帧必须从原始记录重新转换才能恢复，修改旧 zarr 的边界数组不能恢复帧。

## DP 训练接入

官方 PushT loader 限制了动作维度，本项目提供薄适配器：

```text
bimanual_teleop.recording.dataset.BimanualImageDataset
```

`configs/dp_eef.yaml`、`configs/dp_joint.yaml` 是两种实验的数据读取配置片段，不是完整训练配置。两者分别选择末端观测或关节观测，并使用三 RGB、手关节及六维力。只有 `shape_meta.obs` 中选择的字段进入模型。默认转换不保存深度；显式保存的深度也不会自动送入 RGB 编码器，深度网络不在本次实现范围。

在已有官方 DP 训练环境中安装本项目及 recording 依赖，再使用该 dataset target 和匹配的 `shape_meta`。采集环境不安装 PyTorch、训练框架或模型。适配器复用 DP 的序列采样和归一化接口，以只读视图向采样器提供片段边界，保留数据集自身的原始演示边界。同一原始演示拆出的片段保持在同一训练/验证分区；只有一条演示时没有独立验证集。

设计依据：[DP 实机代码](https://github.com/real-stanford/diffusion_policy/blob/main/diffusion_policy/real_world/real_env.py)、[DP 双臂论文 §7.1](https://arxiv.org/html/2303.04137v5#S7.SS1)、[UMI 双臂对齐](https://github.com/real-stanford/universal_manipulation_interface/blob/main/umi/real_world/bimanual_umi_env.py)、[ALOHA 录制](https://github.com/tonyzhaozh/aloha/blob/main/aloha_scripts/record_episodes.py)、[TeleVision 后处理](https://github.com/OpenTeleVision/TeleVision/blob/main/scripts/post_process.py)。仅借鉴本项目需要的部分；未照搬其平台专属字段或固定延迟。
