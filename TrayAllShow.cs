// 托盘图标助手 - 一键全部展开 / 收起
// 适用 Windows 10 / Windows 11（自动检测版本，走不同注册表机制）
// 编译: Build.bat （调用 Windows 自带的 .NET Framework 编译器，无需安装任何 SDK）
using System;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace TrayAllShow
{
    public class MainForm : Form
    {
        private const string NotifyKey = @"Control Panel\NotifyIconSettings";

        private Button btnExpand;
        private Button btnCollapse;
        private CheckBox chkRestart;
        private TextBox txtLog;
        private Label lblInfo;

        // 广播系统设置变更消息（部分情况下可免重启 Explorer 生效）
        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr SendMessageTimeout(IntPtr hWnd, int Msg, IntPtr wParam,
            string lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);

        public MainForm()
        {
            InitUi();
        }

        private void InitUi()
        {
            this.Text = "托盘图标助手 - 一键全部展开";
            this.Size = new Size(490, 410);
            this.FormBorderStyle = FormBorderStyle.FixedSingle;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.Font = new Font("Microsoft YaHei UI", 9F);

            lblInfo = new Label();
            lblInfo.Location = new Point(16, 15);
            lblInfo.Size = new Size(448, 46);
            lblInfo.Text = GetInfoText();
            this.Controls.Add(lblInfo);

            btnExpand = new Button();
            btnExpand.Text = "一键全部展开";
            btnExpand.Location = new Point(16, 66);
            btnExpand.Size = new Size(218, 48);
            btnExpand.BackColor = Color.FromArgb(230, 6, 81);
            btnExpand.ForeColor = Color.White;
            btnExpand.FlatStyle = FlatStyle.Flat;
            btnExpand.Font = new Font("Microsoft YaHei UI", 11F, FontStyle.Bold);
            btnExpand.Cursor = Cursors.Hand;
            btnExpand.Click += new EventHandler(OnExpand);
            this.Controls.Add(btnExpand);

            btnCollapse = new Button();
            btnCollapse.Text = "恢复收纳到倒三角";
            btnCollapse.Location = new Point(246, 66);
            btnCollapse.Size = new Size(218, 48);
            btnCollapse.BackColor = Color.FromArgb(100, 100, 100);
            btnCollapse.ForeColor = Color.White;
            btnCollapse.FlatStyle = FlatStyle.Flat;
            btnCollapse.Font = new Font("Microsoft YaHei UI", 11F, FontStyle.Bold);
            btnCollapse.Cursor = Cursors.Hand;
            btnCollapse.Click += new EventHandler(OnCollapse);
            this.Controls.Add(btnCollapse);

            chkRestart = new CheckBox();
            chkRestart.Text = "完成后自动重启 Explorer 使设置立即生效（任务栏闪一下，已开窗口不受影响）";
            chkRestart.Location = new Point(16, 124);
            chkRestart.Size = new Size(448, 30);
            chkRestart.Checked = true;
            this.Controls.Add(chkRestart);

            txtLog = new TextBox();
            txtLog.Multiline = true;
            txtLog.ReadOnly = true;
            txtLog.ScrollBars = ScrollBars.Vertical;
            txtLog.Location = new Point(16, 162);
            txtLog.Size = new Size(448, 200);
            txtLog.BackColor = Color.White;
            this.Controls.Add(txtLog);

            Log("就绪。点击「一键全部展开」把所有托盘图标（含倒三角里的）显示到任务栏。");
            Log("提示：以后新装软件的图标出现后，再运行一次即可全部展开。");
        }

        private int GetBuild()
        {
            using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                if (k != null)
                {
                    object v = k.GetValue("CurrentBuild");
                    if (v != null)
                    {
                        int b;
                        if (int.TryParse(v.ToString(), out b)) return b;
                    }
                }
            }
            return 0;
        }

        private string GetInfoText()
        {
            int build = GetBuild();
            if (build >= 22000)
            {
                int total = 0, shown = 0;
                using (RegistryKey root = Registry.CurrentUser.OpenSubKey(NotifyKey))
                {
                    if (root != null)
                    {
                        foreach (string sub in root.GetSubKeyNames())
                        {
                            total++;
                            using (RegistryKey sk = root.OpenSubKey(sub))
                            {
                                if (sk != null)
                                {
                                    object v = sk.GetValue("IsPromoted");
                                    if (v is int && (int)v == 1) shown++;
                                }
                            }
                        }
                    }
                }
                return string.Format("系统：Windows 11 (build {0})\r\n托盘图标记录 {1} 个：{2} 个显示在任务栏，{3} 个收在溢出区。",
                    build, total, shown, total - shown);
            }
            return string.Format("系统：Windows 10 (build {0})\r\n将通过 EnableAutoTray 开关控制是否收纳图标。", build);
        }

        private void UpdateInfo()
        {
            lblInfo.Text = GetInfoText();
        }

        private void OnExpand(object sender, EventArgs e)
        {
            int build = GetBuild();
            if (build >= 22000)
            {
                int n = SetAllPromoted(1);
                Log(string.Format("已将 {0} 个托盘图标设置为「显示在任务栏」。", n));
            }
            else
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Explorer", true))
                {
                    if (k != null) k.SetValue("EnableAutoTray", 0, RegistryValueKind.DWord);
                }
                Log("已关闭自动收纳（EnableAutoTray = 0）。");
            }
            RefreshIfRequested();
            UpdateInfo();
        }

        private void OnCollapse(object sender, EventArgs e)
        {
            DialogResult r = MessageBox.Show(
                "将把所有托盘图标收进右下角倒三角（恢复默认收纳状态），确定吗？",
                "确认", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (r != DialogResult.Yes) return;

            int build = GetBuild();
            if (build >= 22000)
            {
                int n = SetAllPromoted(0);
                Log(string.Format("已将 {0} 个托盘图标收进溢出区。", n));
            }
            else
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Explorer", true))
                {
                    if (k != null) k.SetValue("EnableAutoTray", 1, RegistryValueKind.DWord);
                }
                Log("已恢复自动收纳（EnableAutoTray = 1）。");
            }
            RefreshIfRequested();
            UpdateInfo();
        }

        private int SetAllPromoted(int value)
        {
            int n = 0;
            using (RegistryKey root = Registry.CurrentUser.OpenSubKey(NotifyKey, true))
            {
                if (root == null)
                {
                    Log("未找到注册表项 NotifyIconSettings（系统可能不支持）。");
                    return 0;
                }
                foreach (string sub in root.GetSubKeyNames())
                {
                    using (RegistryKey sk = root.OpenSubKey(sub, true))
                    {
                        if (sk == null) continue;
                        try
                        {
                            sk.SetValue("IsPromoted", value, RegistryValueKind.DWord);
                            n++;
                        }
                        catch (Exception ex)
                        {
                            Log("写入失败 " + sub + " : " + ex.Message);
                        }
                    }
                }
            }
            return n;
        }

        private void RefreshIfRequested()
        {
            // 先尝试广播设置变更消息
            try
            {
                IntPtr res;
                SendMessageTimeout((IntPtr)0xffff, 0x001A, IntPtr.Zero, "TraySettings", 0x0002, 1000, out res);
            }
            catch { }

            if (chkRestart.Checked)
            {
                DialogResult r = MessageBox.Show(
                    "即将重启 Explorer：任务栏会短暂消失后自动恢复，已打开的窗口不受影响。继续吗？",
                    "确认重启 Explorer", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (r != DialogResult.Yes)
                {
                    Log("已跳过重启，设置将在下次重启 Explorer 后生效。");
                    return;
                }
                foreach (Process p in Process.GetProcessesByName("explorer"))
                {
                    try { p.Kill(); } catch { }
                }
                Thread.Sleep(800);
                Process.Start("explorer.exe");
                Log("Explorer 已重启，设置已生效。");
            }
            else
            {
                Log("未重启 Explorer。若任务栏未变化，请在任务管理器中重启「Windows 资源管理器」。");
            }
        }

        private void Log(string msg)
        {
            txtLog.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + msg + "\r\n");
        }

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}
