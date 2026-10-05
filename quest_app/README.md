# Quest OpenXR 客户端

采集头显位姿和左右 Touch 手柄的 grip 位姿，通过 USB ADB 传给主机。支持 Quest 3 / 3S，请求 90 Hz，头显内显示空白 XR 场景。安装 APK、查看器和遥操作命令见 [README 快速开始](../README.md#快速开始)；多台 USB 设备时用 `--serial SERIAL` 选择头显。

## 连接与追踪

1. 开启开发者模式，连接 USB 并授权调试；`adb devices -l` 应显示 `device`。
2. 唤醒头显和双手柄，手柄保持在头显摄像头视野内，周围环境应能正常追踪。
3. 关闭系统菜单、边界确认或权限窗口，回到采集应用；查看器报告有效追踪后再遥操作。

主机程序运行期间会让头显亮屏并保持活跃，退出后恢复休眠检测；应用可先于手柄唤醒启动并等待。Quest 3S 真正休眠后，可能需要短按实体电源键解除传感器锁，亮屏不代表 XR 已恢复焦点。休眠、追踪丢失或重新定位后须重新接合。

主机异常退出或 USB 中断后，若应用残留或休眠检测未恢复：

```bash
adb shell am force-stop org.bimanual.questcapture
adb shell am broadcast -a com.oculus.vrpowermanager.automation_disable
```

嵌入其他程序时，`QuestSource(keep_awake=False)` 保留系统原有电源行为。

## 构建

设置 JDK 17 的 `JAVA_HOME` 和 Android SDK 的 `ANDROID_HOME`，在 `quest_app/` 目录执行：

```bash
sdkmanager 'platform-tools' 'platforms;android-32' 'build-tools;33.0.1' 'ndk;27.0.12077973' 'cmake;3.22.1'
./gradlew assembleDebug
adb install -r build/outputs/apk/debug/QuestCapture-debug.apk
```

固定 Gradle 8.5、Android Gradle Plugin 8.1.4、OpenXR loader 1.1.53，目标 arm64-v8a，最低 API 26。首次构建会下载并校验 Meta OpenXR SDK 提交 `bbed2f20e38a5df7113630771c83cb8279e4fc26`，可用 `-PmetaSdkSource=/path/to/meta-openxr-sdk-v85` 指向同一提交的本地源码。交付 APK 为 `artifacts/quest-capture-debug.apk`，验证记录见 [VERIFICATION.md](artifacts/VERIFICATION.md)。

## 独立检查

不经主机程序直接查看输出（`SERIAL` 换成设备序列号）。一个终端读日志：

```bash
adb -s SERIAL logcat -v raw -T 0 QuestCapture:I '*:S'
```

另一个终端用新会话 ID 启动应用：

```bash
SESSION_ID=$(tr -d '-' < /proc/sys/kernel/random/uuid)
adb -s SERIAL shell am force-stop org.bimanual.questcapture
adb -s SERIAL shell am start -n org.bimanual.questcapture/.MainActivity --es session_id "$SESSION_ID"
```

这种方式不含主机的电源管理。每行输出一个 JSON，格式见[协议参考](../docs/development.md#quest-协议)。重点检查双手柄位置和旋转、遮挡、重新定位、系统菜单和 USB 断开。构建通过不代表追踪正常，须在目标头显上实测。
