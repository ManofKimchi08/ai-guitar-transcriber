using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class Program
{
    [STAThread]
    static void Main()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = Path.Combine(baseDir, @"venv\Scripts\pythonw.exe");
        string python = Path.Combine(baseDir, @"venv\Scripts\python.exe");
        string script = Path.Combine(baseDir, "app_gui.py");

        if (!File.Exists(script))
        {
            MessageBox.Show("app_gui.py 파일을 찾을 수 없습니다.\n경로: " + script, "AI Band Transcriber", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        string exeToRun = File.Exists(pythonw) ? pythonw : (File.Exists(python) ? python : "python.exe");

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = exeToRun;
        psi.Arguments = "\"" + script + "\"";
        psi.WorkingDirectory = baseDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        try
        {
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show("프로그램 실행 중 오류가 발생했습니다:\n" + ex.Message, "AI Band Transcriber", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
