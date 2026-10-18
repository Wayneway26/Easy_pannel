# Tray Icon Helper · 托盘图标助手

一键把 Windows 任务栏右下角「倒三角」里的托盘图标全部展开到任务栏，也能一键恢复收纳。
不用再去「设置 → 个性化 → 任务栏」里一个个点开。

适用 Windows 11（22H2 及以上）与 Windows 10，绿色免安装。

---

## 功能

| 功能 | 说明 |
| --- | --- |
| 一键全部展开 | 把所有托盘图标（含溢出区）显示到任务栏 |
| 一键恢复收纳 | 全部收进右下角倒三角，恢复默认状态 |
| 实时状态 | 显示「显示 x / 收纳 y / 无值 z」的图标统计 |
| 自动重启 Explorer | 先结束 → 写注册表 → 再启动，保证设置不被覆盖 |
| 重启后二次校验 | 检测设置是否被 Explorer 改回，异常时给出提示 |
| 重置托盘缓存 | 清除 `TrayNotify\IconStreams`，用于排查图标状态异常 |
| 直达系统设置 | 一键打开系统「任务栏」设置页 |
| Win+X 菜单入口 | 右键开始按钮的菜单里直接点「一键展开托盘图标」（带确认弹窗） |

## 为什么不做进「设置」里？

Windows 11 的「设置」（`SystemSettings.exe`）是微软签名的 UWP 应用，**不开放任何第三方扩展点**：没有插件接口、没有注入 API，只能通过 `ms-settings:taskbar` 之类的深链接跳转过去。硬改其资源或替换 exe 会破坏系统签名，属于不推荐做法。

所以本工具补的是「入口」而不是「页面」——Win+X 菜单（右键开始按钮）是与系统自带项并列、最接近原生体验的位置。

> 补充：Win11 的设置里本来也没有 Win10 那个「始终显示所有图标」开关，Win11 只能逐个图标打开，这正是本工具要解决的问题。

## 原理

**Windows 11（build ≥ 22000）**：每个托盘图标的显示状态存在注册表

```
HKCU\Control Panel\NotifyIconSettings\<图标ID>\IsPromoted
    1 = 显示在任务栏
    0 = 收进溢出区（倒三角）
```

**Windows 10**：使用全局开关

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\EnableAutoTray
    0 = 关闭自动收纳（全部显示）   1 = 自动收纳
```

### 关键坑：执行顺序

Explorer 在退出时会把**内存中的托盘状态回写注册表**。因此必须按顺序执行：

```
1. taskkill /f /im explorer.exe     结束 Explorer
2. 写入 IsPromoted                  趁它不在时改注册表
3. 启动 explorer.exe                重新加载设置
```

如果先写注册表再重启 Explorer，改动会被退出时的回写覆盖 —— 表现为「点了没反应」。

## 使用方式

三种任选其一：

| 方式 | 操作 | 说明 |
| --- | --- | --- |
| GUI（推荐） | 双击 `启动托盘助手.bat` | 打开图形界面，按钮操作 + 实时日志 |
| Win+X 菜单 | 双击 `InstallWinX.pyw` 安装一次，之后右键开始按钮 → 「一键展开托盘图标」 | 带确认弹窗，最接近系统原生入口 |
| 快速脚本 | 双击 `一键展开托盘.bat` / `一键收起托盘.bat` | 无界面，命令行确认后执行 |
| C# 原生版 | 双击 `Build.bat` 编译出 `TrayAllShow.exe` | 调用系统自带 .NET Framework 编译器，生成独立 exe |

> GUI 需要带 `tkinter` 的 Python 3。`启动托盘助手.bat` 会自动在常见安装路径中寻找可用的解释器。
>
> Win+X 菜单项安装后可随时用 `从Win+X移除.bat` 卸载。若菜单里没有立即出现，重启一次 Explorer 即可。

## 目录说明

| 文件 | 作用 |
| --- | --- |
| `TrayGUI.pyw` | GUI 主程序（Python + tkinter，直接读写注册表） |
| `启动托盘助手.bat` | GUI 启动器，自动寻找带 tkinter 的 Python |
| `一键展开托盘.bat` | 无界面快速展开 |
| `一键收起托盘.bat` | 无界面快速收纳 |
| `TrayAllShow.cs` | C# WinForms 版源码（原生 exe 方案） |
| `Build.bat` | 编译 C# 源码为 `TrayAllShow.exe` |
| `TrayAction.pyw` | 供外部入口调用的动作脚本（`expand` / `collapse`，带确认弹窗） |
| `InstallWinX.pyw` | 安装 Win+X 菜单项；`--remove` 参数为卸载 |
| `从Win+X移除.bat` | 卸载 Win+X 菜单项 |
| `assets/tray-expand.ico` | Win+X 菜单项图标（程序化生成的 32×32 ICO） |

## 常见问题

**点了「展开」但任务栏没变化？**
确认勾选了「操作后重启 Explorer」。注册表修改必须由 Explorer 重新加载才生效。

**重启后图标又回到倒三角？**
点「重置托盘图标缓存」清除 `TrayNotify` 缓存，然后再操作一次。

**以后新装的软件图标又藏起来了？**
新图标会以「未设置」状态进入溢出区，再点一次「一键全部展开」即可。

**结束 Explorer 会丢失工作吗？**
不会。已打开的窗口和程序不受影响，只是任务栏会短暂消失并自动恢复。

**安全吗？**
只修改当前用户（HKCU）的托盘显示设置，不写系统级配置，也不会删除任何程序文件，随时可以反向操作恢复原状。

## 兼容性

| 系统 | 支持 | 机制 |
| --- | --- | --- |
| Windows 11 22H2+（含 24H2 / 25H2） | ✅ | `NotifyIconSettings\IsPromoted` |
| Windows 11 21H2 / 初始版 | ⚠️ 部分 | 视系统实现而定 |
| Windows 10 | ✅ | `EnableAutoTray` |

## 开发

- GUI：Python 3 + tkinter，单文件，仅用标准库（`winreg` / `subprocess` / `tkinter`）
- 原生版：C# 5（`csc.exe` `/target:winexe`），兼容 .NET Framework 4.x，无需安装 SDK
- 待办：用 PyInstaller 打包为单文件 exe；增加托盘常驻模式与开机自启

## 免责声明

本工具通过修改注册表实现托盘图标显示状态切换，属于系统设置层面的操作。请在使用前了解上述原理，作者不对因使用本工具造成的任何直接或间接损失负责。所有修改均可通过「一键恢复收纳」或系统设置还原。

## License

[MIT](LICENSE)
