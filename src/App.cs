using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Security.Cryptography;
using System.Windows;

[assembly: AssemblyTitle("시장 대시보드")]
[assembly: AssemblyDescription("102개 Python 수집기와 화면을 함께 실행하는 시장 관측실")]
[assembly: AssemblyProduct("Market Dashboard")]
[assembly: AssemblyVersion("1.1.0.0")]
[assembly: AssemblyFileVersion("1.1.0.0")]

namespace MarketDash
{
    public static class App
    {
        [STAThread]
        public static void Main()
        {
            string instance;
            using (var hash = SHA256.Create())
                instance = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(AppDomain.CurrentDomain.BaseDirectory.ToUpperInvariant()))).Replace("-", "");
            bool first;
            using (var mutex = new Mutex(true, "Local\\MarketDashboardUnified_" + instance, out first))
            {
                if (!first)
                {
                    MessageBox.Show("이 폴더의 시장 관측실이 이미 실행 중입니다.", "시장 관측실", MessageBoxButton.OK, MessageBoxImage.Information);
                    return;
                }
                Run();
            }
        }

        static void Run()
        {
            AppDomain.CurrentDomain.UnhandledException += delegate(object sender, UnhandledExceptionEventArgs e)
            {
                WriteError(e.ExceptionObject as Exception);
            };
            try
            {
                var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
                app.DispatcherUnhandledException += delegate(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
                {
                    ShowError(e.Exception);
                    e.Handled = true;
                };
                app.Run(new DashboardWindow());
            }
            catch (Exception ex) { ShowError(ex); }
        }

        static void ShowError(Exception ex)
        {
            string log = WriteError(ex);
            string message = "대시보드를 처리하는 중 오류가 발생했습니다.\n" + (ex == null ? "알 수 없는 오류" : ex.Message);
            if (log != null) message += "\n\n오류 기록: " + log;
            MessageBox.Show(message, "시장 대시보드", MessageBoxButton.OK, MessageBoxImage.Error);
        }

        static string WriteError(Exception ex)
        {
            try
            {
                string folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "work", "삭제");
                Directory.CreateDirectory(folder);
                string path = Path.Combine(folder, "errors.log");
                File.AppendAllText(path, DateTimeOffset.Now.ToString("o") + Environment.NewLine +
                    (ex == null ? "알 수 없는 오류" : ex.ToString()) + Environment.NewLine + Environment.NewLine,
                    new UTF8Encoding(false));
                return path;
            }
            catch { return null; }
        }
    }

    public class AppSettings
    {
        public static readonly string DefaultDataRoot = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "collectors", "data");
        public string DataRoot = DefaultDataRoot;
        public static AppSettings Load() { return new AppSettings(); }
    }
}
