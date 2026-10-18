# 托盘图标助手 · Tray Icon Helper

Windows 11 把「始终显示所有图标」这个开关去掉了，想让右下角的图标全部露出来，只能去设置里一个个点。
这个小工具把这件事变回一次点击：**一键展开全部托盘图标，也能一键恢复收纳**。

绿色免安装，不联网，只改当前用户的显示设置，随时可还原。

```
Windows 11 22H2 / 23H2 / 24H2 / 25H2   （主程序）
Windows 10                              （C# 原生版，见「兼容性」）
```

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [界面](#界面)
- [快速开始](#快速开始)
- [三个入口](#三个入口)
- [功能清单](#功能清单)
- [原理](#原理)
- [两个必须知道的坑](#两个必须知道的坑)
- [文件说明](#文件说明)
- [安装与卸载](#安装与卸载)
- [常见问题](#常见问题)
- [兼容性与已知限制](#兼容性与已知限制)
- [安全性与可逆性](#安全性与可逆性)
- [开发与构建](#开发与构建)
- [版本历史](#版本历史)
- [License](#license)

---

## 它解决什么问题

| 场景 | 系统原生 | 本工具 |
| --- | --- | --- |
| 让所有托盘图标显示在任务栏 | Win11 只能逐个图标打开 | 一次点击全部展开 |
| 新装了软件，图标又躲进倒三角 | 再去设置里找一个 | 再点一次即可 |
| 想恢复默认收纳状态 | 逐个关闭 | 一键恢复收纳 |
| 找到这个设置 | 设置 → 个性化 → 任务栏 → 逐个展开 | 右键开始按钮 / 桌面右键直接点 |

Win10 有 `EnableAutoTray` 全局开关，Win11 改成了**逐图标**存储，
所以 Win11 用户实际面对的是「N 个图标点 N 次」——这就是本工具存在的理由。

---

## 界面

主界面是一个 560 × 470 的窗口（Python + tkinter，不依赖任何第三方库），从上到下分五块：

| 区域 | 内容 |
| --- | --- |
| 状态栏 | `托盘图标记录 89 个：85 个显示在任务栏，0 个收在溢出区，4 个暂无设置值` |
| 主操作 | **一键全部展开** / **一键恢复收纳** 两个大按钮 |
| 选项 | 复选框「操作后重启 Explorer 使设置生效」，默认勾选 |
| 辅助按钮 | 刷新状态 / 打开系统托盘设置 / 仅重启 Explorer / 重置托盘图标缓存 |
| 日志区 | 逐步输出执行过程、写入数量，以及重启前后的两次校验结果 |

窗口里的每个操作都会写进日志，出问题时能直接看到卡在哪一步：
是 Explorer 没结束、注册表没写进去，还是写进去又被改回了。

---

## 快速开始

1. 解压/拷贝整个文件夹到任意位置（不要单独拷某一个文件，脚本之间互相引用）；
2. 双击 **`启动托盘助手.bat`**；
3. 点 **「一键全部展开」** → 确认 → 任务栏闪一下 → 图标全部出现。

就这三步。不想开窗口的话，直接双击 `一键展开托盘.bat` 也行。

> 需要 Python 3（含 tkinter）。`启动托盘助手.bat` 会依次检查
> `C:\MiniConda\python.exe` → `C:\Windows\py.exe` → 托管 Python，找到能用的就启动。
> 如果三个都没有，用 `Build.bat` 编译 C# 原生版，完全不需要 Python。

---

## 三个入口

除了主界面，还提供了三个「不用打开程序」的入口：

| 入口 | 装上后怎么用 | 生效时机 |
| --- | --- | --- |
| **Win+X 菜单** | 右键「开始」按钮 → 一键展开托盘图标 | 装完需**注销或重启**才出现（系统缓存只在登录时重建） |
| **桌面右键菜单** | 桌面空白处右键 → 显示更多选项 → 一键展开 / 一键收起 | **立即生效** |
| **桌面快捷方式** | 桌面上双击，或右键「固定到任务栏」后单击 | 立即生效 |

三个入口点下去都会弹一个确认框（防止误触），确认后执行，完成再给结果提示。

安装它们（各执行一次）：

```bat
:: Win+X 菜单
python InstallWinX.pyw

:: 桌面右键菜单 + 桌面快捷方式
python InstallContextMenu.pyw
```

卸载同样简单：

```bat
从Win+X移除.bat         :: 移除 Win+X 菜单项
移除桌面菜单.bat         :: 移除桌面右键菜单项
```

> ⚠️ 入口记录的是**工具的当前路径**。如果移动或重命名了 `tray-helper` 文件夹，
> 需要重新执行一次安装，否则点下去会找不到文件。

---

## 功能清单

| 功能 | 说明 |
| --- | --- |
| 一键全部展开 | 把所有托盘图标（含溢出区）显示到任务栏 |
| 一键恢复收纳 | 全部收进右下角倒三角，恢复默认状态 |
| 实时统计 | 显示「显示 x / 收纳 y / 无值 z」 |
| 自动重启 Explorer | 先结束 → 写注册表 → 再启动，保证修改不被覆盖 |
| 重启后二次校验 | 检测设置是否被 Explorer 改回，异常时提示 |
| 重置托盘缓存 | 清除 `TrayNotify\IconStreams`，排查图标状态异常 |
| 看门狗兜底 | 中途崩溃导致 Explorer 没起来时，自动把它拉回来 |
| 紧急恢复脚本 | 桌面黑屏 / 任务栏消失时，双击即可恢复 |

---

## 原理

### Windows 11

每个托盘图标的显示状态，存在注册表里按图标 ID 分开的子键中：

```
HKCU\Control Panel\NotifyIconSettings\<图标ID>\IsPromoted
    1 = 显示在任务栏
    0 = 收进溢出区（倒三角）
    值不存在 = 尚未被用户设置过，通常表现为收在溢出区
```

工具做的事就是：遍历所有子键，把 `IsPromoted` 统一写成 `1`（展开）或 `0`（收纳）。

### Windows 10

Win10 用的是全局开关，不是逐图标：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\EnableAutoTray
    0 = 关闭自动收纳（全部显示）
    1 = 自动收纳
```

> 主程序（Python 版）只实现了 Win11 机制；Win10 请用 C# 原生版（`Build.bat` 编译）。

---

## 两个必须知道的坑

这两个坑都是本项目踩出来的真实故障，改代码时务必保留。

### 坑一：执行顺序必须是「先结束 → 再写 → 再启动」

Explorer 退出时会把**内存中的托盘状态回写注册表**。所以：

```
1. taskkill /f /im explorer.exe     结束 Explorer
2. 写入 IsPromoted                  趁它不在时改
3. 启动 explorer.exe                重新加载设置
```

如果先写再重启，你写的值会被退出时的回写冲掉——表现就是「点了没反应」。

### 坑二：Explorer 必须「脱离父进程」启动

用普通的 `subprocess.Popen(["explorer.exe"])` 启动，Explorer 会成为调用脚本的子进程。
脚本一退出，Explorer 可能被连带结束——**桌面全黑、任务栏消失**。

正确做法是同时带上三个标志位：

| 标志 | 值 | 作用 |
| --- | --- | --- |
| `DETACHED_PROCESS` | `0x00000008` | 不继承控制台，进程独立存活 |
| `CREATE_NEW_PROCESS_GROUP` | `0x00000200` | 独立进程组，不随父进程终止 |
| `CREATE_BREAKAWAY_FROM_JOB` | `0x01000000` | 从父进程的 Job 对象脱离 |

启动后还要**轮询确认** `explorer.exe` 真的在跑，失败则重试，最后降级到 `cmd /c start` 兜底。

再加两道保险：

- **`ExplorerWatchdog.pyw`**：在结束 Explorer 之前先启动它。宽限期（默认 10 秒）内
  若 Explorer 正常恢复就静默退出；超时还没有，就强制拉起并写 `watchdog.log`。
- **`finally` 兜底**：任何异常分支都检查一次 Explorer 是否在运行，不在就恢复。

---

## 文件说明

```
tray-helper/
├── 启动托盘助手.bat          GUI 启动器（自动找可用的 Python）
├── TrayGUI.pyw               主程序：界面 + 注册表读写 + 重启逻辑
├── TrayAction.pyw            动作脚本：给菜单入口调用（expand / collapse）
├── ExplorerWatchdog.pyw      看门狗：Explorer 没起来时兜底
│
├── 一键展开托盘.bat          无界面快速展开
├── 一键收起托盘.bat          无界面快速收纳
├── 恢复桌面.bat              紧急恢复：黑屏 / 任务栏消失时双击
├── 重启资源管理器.bat        手动重启 Explorer
│
├── InstallWinX.pyw           安装 Win+X 菜单项（--remove 卸载）
├── 从Win+X移除.bat           卸载 Win+X 菜单项
├── InstallContextMenu.pyw    安装桌面右键菜单 + 桌面快捷方式（--remove 卸载）
├── 移除桌面菜单.bat          卸载桌面右键菜单
│
├── TrayAllShow.cs            C# WinForms 原生版源码（含 Win10 支持）
├── Build.bat                 用系统自带编译器生成 TrayAllShow.exe
│
├── assets/tray-expand.ico    菜单图标（程序化生成的 32×32 ICO）
├── README.md
├── LICENSE
├── .gitignore
└── .gitattributes
```

| 文件 | 作用 |
| --- | --- |
| `TrayGUI.pyw` | GUI 主程序，单文件，只用标准库 `winreg` / `subprocess` / `tkinter` / `ctypes` |
| `TrayAction.pyw` | 无窗口动作入口，带确认弹窗和结果校验，供菜单项调用 |
| `ExplorerWatchdog.pyw` | 看门狗，参数为宽限秒数，如 `pythonw ExplorerWatchdog.pyw 10` |
| `InstallWinX.pyw` | 手写 MS-SHLLINK 生成 `.lnk` 写入 WinX 目录，不依赖 COM / pywin32 |
| `InstallContextMenu.pyw` | 写 `HKCU\Software\Classes\DesktopBackground\shell`，并生成桌面快捷方式 |
| `TrayAllShow.cs` | C# 5 + WinForms，兼容 .NET Framework 4.x，同时处理 Win10 / Win11 |

---

## 安装与卸载

| 操作 | 命令 |
| --- | --- |
| 打开界面 | 双击 `启动托盘助手.bat` |
| 装 Win+X 菜单 | `python InstallWinX.pyw` |
| 卸 Win+X 菜单 | `从Win+X移除.bat` |
| 装桌面右键菜单 | `python InstallContextMenu.pyw` |
| 卸桌面右键菜单 | `移除桌面菜单.bat` |
| 编译原生 exe | 双击 `Build.bat` → 生成 `TrayAllShow.exe` |

所有安装都只写 `HKCU`（当前用户），不碰 `HKLM`，不需要管理员权限。

---

## 常见问题

**桌面全黑、任务栏不见了？**
说明 `explorer.exe` 没在运行。任选一种恢复：

| 方式 | 操作 |
| --- | --- |
| 最快 | 双击 `恢复桌面.bat` |
| 手动 | `Ctrl+Shift+Esc` → 文件 → 运行新任务 → 输入 `explorer.exe` → 回车 |
| 彻底 | `Ctrl+Alt+Del` → 注销 → 重新登录 |

壁纸、已打开的窗口和文件都不会丢，只是外壳进程没起来。
当前版本已修复此问题（见「坑二」），正常使用不应再出现。

**点了「展开」但任务栏没变化？**
确认勾选了「操作后重启 Explorer」。注册表改了必须由 Explorer 重新加载才生效。

**重启后图标又回到倒三角？**
点「重置托盘图标缓存」清掉 `TrayNotify` 缓存，再操作一次。

**以后新装的软件图标又藏起来了？**
新图标会以「无值」状态进入溢出区，再点一次「一键全部展开」即可。

**Win+X 菜单里找不到这个选项？**
该菜单的条目缓存**只在登录时重建**，装完需注销重新登录或重启（仅重启 Explorer 不一定够）。
急用就用桌面右键菜单，它是立即生效的。

**结束 Explorer 会丢失工作吗？**
不会。已开窗口、程序、未保存的文件都不受影响，任务栏会消失一两秒后自动恢复。

**为什么不做进 Windows「设置」里？**
`SystemSettings.exe` 是微软签名的 UWP 应用，不开放任何第三方扩展点：没有插件接口、没有注入 API，
只能通过 `ms-settings:taskbar` 这类深链接跳过去。硬改资源或替换 exe 会破坏系统签名，不推荐。
所以本工具补的是「入口」，不是「页面」。

**安全吗？**
只改 `HKCU` 下的托盘显示设置，不写系统级配置，不联网，不删除任何文件，
随时可以用「一键恢复收纳」或系统设置还原。

---

## 兼容性与已知限制

| 系统 | 支持 | 机制 |
| --- | --- | --- |
| Windows 11 22H2+（含 24H2 / 25H2） | 支持 | `NotifyIconSettings\IsPromoted` |
| Windows 11 21H2 / 初始版 | 部分 | 视系统实现而定 |
| Windows 10 | 支持（C# 原生版） | `EnableAutoTray` |
| Windows 8 / 7 | 不支持 | 机制不同 |

已知限制：

- **Python 版不支持 Win10**，Win10 请用 `Build.bat` 编译的 C# 版；
- **Win+X 菜单需要注销或重启**后才出现，属系统限制；
- 尚未提供单文件 exe（需要 PyInstaller），目前是 `pyw + 启动器`；
- 没有托盘常驻模式，不会后台自动监控新图标；
- 移动文件夹后需重新安装菜单入口。

---

## 安全性与可逆性

| 操作 | 影响范围 | 是否可逆 |
| --- | --- | --- |
| 展开 / 收纳图标 | `HKCU\Control Panel\NotifyIconSettings\*\IsPromoted` | 可逆，反向再点一次 |
| 重置托盘缓存 | `HKCU\...\TrayNotify\IconStreams` | Explorer 会自动重建 |
| 安装菜单入口 | `HKCU\Software\Classes\DesktopBackground\shell`、WinX 目录、桌面 `.lnk` | 可逆，有对应卸载脚本 |
| 重启 Explorer | 结束并重新启动外壳进程 | 窗口不受影响 |

不写 `HKLM`，不需要管理员权限，不联网，不收集任何数据。

---

## 开发与构建

**GUI 版**

```
Python 3.8+，仅标准库：tkinter / winreg / subprocess / ctypes
直接运行：pythonw TrayGUI.pyw
```

**原生版**

```bat
Build.bat
:: 内部调用 C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe
:: 输出 TrayAllShow.exe，无需安装 .NET SDK
```

**调试技巧**

- 验证子进程是否真的脱离父进程：启动一个 detached 的 `notepad.exe`，
  父进程退出后它还在，就说明脱离生效——比拿 explorer 直接试风险低得多；
- 目录里出现 `watchdog.log` 说明看门狗执行过强制恢复，可以据此判断是否发生过失败；
- `explorer.exe` 死活起不来时，先看 `tasklist /fi "imagename eq explorer.exe"`，
  再看 `Shell_TrayWnd` 窗口是否存在。

待办：

- [ ] 用 PyInstaller 打包单文件 exe
- [ ] 全局热键（如 `Ctrl+Alt+E`）
- [ ] 托盘常驻 + 新图标自动展开
- [ ] Python 版补齐 Win10 的 `EnableAutoTray` 支持

---

## 版本历史

| 提交 | 日期 | 内容 |
| --- | --- | --- |
| `7747533` | 2026-10-18 | 修复重启 Explorer 后桌面黑屏：脱离父进程启动 + 看门狗 + 紧急恢复脚本 |
| `1321813` | 2026-10-18 | 新增桌面右键菜单与桌面快捷方式入口 |
| `d5125dd` | 2026-10-18 | 文档：说明移动目录后需重装 Win+X 菜单项 |
| `4154942` | 2026-10-18 | 新增 Win+X 菜单一键展开入口 |
| `fce2f3e` | 2026-10-18 | 初始版本：GUI、无界面脚本、C# 原生方案 |

---

## License

[MIT](LICENSE) © 2026 Wayneway26

使用本工具即表示你了解：它会结束并重启 Explorer 进程，并修改当前用户的托盘显示设置。
作者不对因使用本工具造成的任何直接或间接损失负责。
