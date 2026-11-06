using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher {
    // Windows argv quoting, including quotes and trailing backslashes.
    static string Quote(string value) {
        var output = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { output.Append('\\', slashes * 2 + 1); output.Append(c); }
            else { output.Append('\\', slashes); output.Append(c); }
            slashes = 0;
        }
        output.Append('\\', slashes * 2); output.Append('"'); return output.ToString();
    }
    [STAThread]
    static int Main(string[] args) {
        try {
            Mutex uninstall;
            bool maintenance = Array.IndexOf(args, "--uninstall") >= 0 ||
                               Array.IndexOf(args, "--remove-tasks") >= 0;
            if (!maintenance && Mutex.TryOpenExisting(@"Local\RedCometWatch.Uninstall", out uninstall)) {
                uninstall.Dispose();
                return 2;
            }
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
            string python = Path.Combine(root, "runtime", "pythonw.exe");
            var command = new StringBuilder("-I -B -m hguc_watch.desktop");
            foreach (string arg in args) command.Append(" " + Quote(arg));
            var info = new ProcessStartInfo(python, command.ToString());
            info.UseShellExecute = false; info.CreateNoWindow = true; info.WorkingDirectory = root;
            info.EnvironmentVariables["HGUC_WATCH_INSTALLED"] = "1";
            info.EnvironmentVariables["HGUC_WATCH_HOME"] = root;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            using (Process child = Process.Start(info)) { child.WaitForExit(); return child.ExitCode; }
        } catch (Exception error) {
            MessageBox.Show("程序启动失败，请重新安装。\n" + error.Message, "赤色彗星补给战情室");
            return 1;
        }
    }
}
