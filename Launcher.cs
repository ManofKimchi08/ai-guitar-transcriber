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
        string startBat = Path.Combine(baseDir, "start.bat");

        if (!File.Exists(script))
        {
            MessageBox.Show(
                "app_gui.py 파일을 찾을 수 없습니다.\n경로: " + script,
                "AI Band Transcriber",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return;
        }

        bool venvExists = File.Exists(pythonw) || File.Exists(python);

        if (!venvExists)
        {
            DialogResult result = MessageBox.Show(
                "AI Band Transcriber를 실행하기 위한 필수 AI 가상환경(venv)이 아직 설치되지 않았습니다.\n\n" +
                "[확인]을 누르시면 터미널 창이 열리며 최초 1회 자동 설치(start.bat)를 시작합니다.\n" +
                "설치가 끝나면 프로그램이 자동으로 실행됩니다.\n\n" +
                "자동 설치를 진행하시겠습니까?",
                "AI Band Transcriber - 최초 설정 안내",
                MessageBoxButtons.OKCancel,
                MessageBoxIcon.Information
            );

            if (result == DialogResult.OK)
            {
                if (File.Exists(startBat))
                {
                    ProcessStartInfo psiSetup = new ProcessStartInfo();
                    psiSetup.FileName = "cmd.exe";
                    psiSetup.Arguments = "/c \"" + startBat + "\"";
                    psiSetup.WorkingDirectory = baseDir;
                    psiSetup.UseShellExecute = true;
                    Process.Start(psiSetup);
                }
                else
                {
                    MessageBox.Show(
                        "start.bat 파일을 찾을 수 없습니다.",
                        "오류",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                }
            }
            return;
        }

        string exeToRun = File.Exists(pythonw) ? pythonw : python;

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
            MessageBox.Show(
                "프로그램 실행 중 오류가 발생했습니다:\n" + ex.Message,
                "AI Band Transcriber",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
