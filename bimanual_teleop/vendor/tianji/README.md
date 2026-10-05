# 官方天机 SDK

厂家原始运行文件，来源 [TJ_FX_ROBOT_CONTRL_SDK](https://github.com/cynthia-you/TJ_FX_ROBOT_CONTRL_SDK/tree/02440e886fb59095711eb9ec6dcbedd8be08922a)，控制库版本 `100343014`，适用 Linux x86_64。

- `SDK_PYTHON/`：原样的控制／运动学 Python 封装及 `libMarvinSDK.so`、`libKine.so`。
- `CommonConfig/ccs_m6_40.MvKDCfg`：M6 4.0 双臂名义几何、关节及耦合限位，不含设备个体标定。
- `LICENSE`：厂家原始许可证。
- `manifest.json`：来源提交、版本、目标平台和各文件 SHA-256。

所有天机入口默认使用本目录，路径按包位置解析，复制项目即可迁移，无需构建。外置 SDK 用 `--sdk-root /path/to/TJ_FX_ROBOT_CONTRL_SDK`，其 `SDK_PYTHON/` 内容须与清单一致；旧参数 `--library` 已移除。

更新厂家版本时，Python 文件、库和清单一起替换，不要单独替换某个 `.so`；替换后重新做兼容性测试、离线运动学测试和实机验收。本目录文件不做修改，官方封装的类型和精度问题由项目适配层修正，见[开发参考](../../../docs/development.md#天机官方-sdk)。
