using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace MarketDash
{
    // The window owns this job: closing it also closes every collector process it started.
    public sealed class CollectorService : IDisposable
    {
        readonly string home = AppDomain.CurrentDomain.BaseDirectory;
        readonly string scratch;
        readonly object logLock = new object();
        Process host;
        SafeFileHandle job;
        DateTime stateWrite;
        Dictionary<string, object> state;
        public string Error { get; private set; }
        public string DataRoot { get { return Path.Combine(home, "collectors", "data"); } }
        public string StatePath { get { return Path.Combine(scratch, "collector-state.json"); } }
        string ControlPath { get { return Path.Combine(scratch, "collector-control.json"); } }
        public bool IsRunning
        {
            get { try { return host != null && !host.HasExited; } catch { return false; } }
        }

        public CollectorService()
        {
            scratch = Path.Combine(home, "work", "삭제");
        }

        public void Start()
        {
            if (IsRunning) return;
            Stop();
            string python = Path.Combine(home, "runtime", "python.exe");
            string runner = Path.Combine(home, "collectors", "collector_host.py");
            if (!File.Exists(python) || !File.Exists(runner))
                throw new FileNotFoundException("통합 패키지의 runtime 또는 collectors 폴더가 없습니다. ZIP 전체를 풀어 실행하세요.");
            Directory.CreateDirectory(scratch);
            Directory.CreateDirectory(DataRoot);
            // Replace a prior stop command so a new process can start immediately.
            Send("start");
            state = null;
            stateWrite = DateTime.MinValue;
            Error = null;
            job = CreateJobObject(IntPtr.Zero, null);
            if (job == null || job.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            var limit = new JobLimit();
            limit.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            int size = Marshal.SizeOf(typeof(JobLimit));
            IntPtr block = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(limit, block, false);
                if (!SetInformationJobObject(job, 9, block, (uint)size))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            finally { Marshal.FreeHGlobal(block); }
            try
            {
                host = new Process();
                host.StartInfo = new ProcessStartInfo(python, "-B -u " + Quote(runner) + " --state " + Quote(StatePath) +
                    " --control " + Quote(ControlPath) + " --parent-pid " + Process.GetCurrentProcess().Id.ToString(CultureInfo.InvariantCulture))
                {
                    WorkingDirectory = Path.Combine(home, "collectors"), UseShellExecute = false,
                    CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
                };
                host.StartInfo.EnvironmentVariables["PYTHONUTF8"] = "1";
                host.StartInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                host.StartInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                host.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
                host.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Log(e.Data); };
                host.Start();
                if (!AssignProcessToJobObject(job, host.Handle))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "수집기 프로세스 종료 관리를 설정할 수 없습니다.");
                host.BeginOutputReadLine();
                host.BeginErrorReadLine();
            }
            catch
            {
                Stop();
                throw;
            }
        }

        public void CollectNow()
        {
            if (!IsRunning) Start();
            Send("collect_now");
        }

        public Dictionary<string, object> ReadState()
        {
            try
            {
                var file = new FileInfo(StatePath);
                if (!file.Exists || file.LastWriteTimeUtc == stateWrite) return state;
                using (var stream = new FileStream(StatePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(stream, Encoding.UTF8))
                {
                    var parsed = Json.ParseObject(reader.ReadToEnd());
                    if (parsed != null) { state = parsed; stateWrite = file.LastWriteTimeUtc; }
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return state;
        }

        void Send(string command)
        {
            Directory.CreateDirectory(scratch);
            string temp = ControlPath + ".tmp";
            string json = "{\"command\":\"" + command + "\",\"id\":\"" + Guid.NewGuid().ToString("N") + "\"}";
            File.WriteAllText(temp, json, new UTF8Encoding(false));
            if (File.Exists(ControlPath)) File.Replace(temp, ControlPath, null);
            else File.Move(temp, ControlPath);
        }

        void Log(string line)
        {
            if (string.IsNullOrWhiteSpace(line)) return;
            try
            {
                lock (logLock)
                {
                    string file = Path.Combine(scratch, "collector-host.log");
                    if (File.Exists(file) && new FileInfo(file).Length > 1024 * 1024)
                        File.WriteAllText(file, "", new UTF8Encoding(false));
                    File.AppendAllText(file, DateTime.UtcNow.ToString("o") + " " + line + Environment.NewLine, new UTF8Encoding(false));
                }
            }
            catch { }
        }

        public void Stop()
        {
            if (host != null)
            {
                try { if (!host.HasExited) { Send("stop"); host.WaitForExit(800); } } catch { }
            }
            if (job != null) { job.Dispose(); job = null; }
            if (host != null)
            {
                try { if (!host.HasExited) host.Kill(); } catch { }
                host.Dispose(); host = null;
            }
        }

        public void Dispose() { Stop(); }
        static string Quote(string path) { return "\"" + path + "\""; }

        [StructLayout(LayoutKind.Sequential)]
        struct BasicLimit
        {
            public long ProcessTime, JobTime;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)]
        struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
        [StructLayout(LayoutKind.Sequential)]
        struct JobLimit
        {
            public BasicLimit Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetInformationJobObject(SafeFileHandle job, int infoClass, IntPtr info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AssignProcessToJobObject(SafeFileHandle job, IntPtr process);
    }
}
