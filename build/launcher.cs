using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class Launcher {
    [STAThread] static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string mode = Path.GetFileNameWithoutExtension(Application.ExecutablePath).Equals("Prepare", StringComparison.OrdinalIgnoreCase) ? "prepare" : "start";
        string script = Path.Combine(root, "build", "bootstrap.ps1");
        if (!File.Exists(script)) { MessageBox.Show("Keep this EXE inside the complete project folder.", "PYNQ CNN"); return 1; }
        if (args.Length == 1 && args[0] == "--self-test") { return 0; }
        try {
            var info = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Mode " + mode);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = mode == "start";
            using (var process = Process.Start(info)) { process.WaitForExit();
                if (process.ExitCode != 0) MessageBox.Show("Operation failed. Run " + (mode == "prepare" ? "Prepare.cmd" : "start.cmd") + " to view details.", "PYNQ CNN");
                return process.ExitCode;
            }
        } catch (Exception e) { MessageBox.Show(e.Message, "PYNQ CNN"); return 1; }
    }
}
