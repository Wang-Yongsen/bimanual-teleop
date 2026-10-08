# 导轨任务 zarr 生成

采集、原始记录整理和 zarr 生成均在 `bimanual-teleop` 中完成。转换配置位于 `configs/`，新数据建议输出到 `datasets/tianji/`；`diffusion_policy` 通过配置路径读取生成的数据。

天机模型集成流程现可在 `diffusion_policy` 的统一环境中直接引用本项目：创建与激活说明、项目内整理/转换入口及真机评估见[模型侧导轨说明](/home/jiyuchen/project/WYS/diffusion_policy/docs/tianji-rail/README.md)。该流程按用户要求只使用统一环境，不需要同时启动或切换两套 Conda。独立遥操作仍可使用下方原环境；同一设备的控制权只能由一个入口持有。

## 1．采集与整理

在项目根目录执行，采集按键与恢复方式见[采集快速说明](../recording_quickstart.md)。已有 `complete` 条目可跳过采集和整理。

```bash
cd /home/jiyuchen/project/WYS/bimanual-teleop
conda activate bimanual-teleop
python scripts/teleop_quest_tianji.py --record --viewer
python scripts/finalize_recording.py --input recordings/<session>
```

## 2．生成 zarr

将 `<session>` 换成实际会话名，输出路径必须尚不存在。

```bash
python scripts/convert_recording.py \
  --input recordings/<session> \
  --output datasets/tianji/episodes_eef_repaired.zarr \
  --action-space eef \
  --conversion-config configs/recording_conversion.yaml
```

[通用转换配置](../../configs/recording_conversion.yaml)的 `mode: repair` 启用 30 Hz 网格和短缺口修复；默认每路最多复用两个未匹配点，状态插值上限 100 ms，动作年龄上限 50 ms。各参数均有中文注释。不传 `--conversion-config` 使用严格模式，配置文件不会自动加载。

暂停接缝未经复核保留边界。旧会话 `20260924_204339_afdb5a7a` 的复核配置为[recording_conversion_tianji_rail_20260924.yaml](../../configs/recording_conversion_tianji_rail_20260924.yaml)，仅当 `--input recordings/20260924_204339_afdb5a7a` 时使用，替换上述 `--conversion-config` 路径即可；它保留主相机第 1341 帧处的暂停边界。新会话使用通用配置并重新复核，输入若为多个会话的上级目录，`pause_reviews` 的来源名须包含会话前缀。

## 3．核对与训练交接

终端会报告原始演示数、连续片段数和有效帧数。输出中 `meta.attrs['quality_report']['conversion_config']['mode']` 应为 `repair`，各条目 `camera_repairs` 与 `pauses` 分别记录图像复用和暂停处理；`episode_ends` 是来源边界，`segment_ends` 是训练连续片段边界。

训练时将 `task.dataset.dataset_path` 指向生成的绝对路径，例如 `/home/jiyuchen/project/WYS/bimanual-teleop/datasets/tianji/episodes_eef_repaired.zarr`。训练与评估见[导轨训练快速说明](/home/jiyuchen/project/WYS/diffusion_policy/docs/tianji-rail/README.md)。

转换从原始 MP4 和 `raw.zarr` 新建输出；本次文档与配置归属调整未移动、修改或重新生成已有数据集。
