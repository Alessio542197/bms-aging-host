using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;

class Program
{
    // 更新本版本号后，首次运行会重新解压
    const string Version = "2026.11.20.1";

    static bool Writable(string dir)
    {
        try
        {
            Directory.CreateDirectory(dir);
            string probe = Path.Combine(dir, ".probe");
            File.WriteAllText(probe, "1");
            File.Delete(probe);
            return true;
        }
        catch { return false; }
    }

    static void Main(string[] args)
    {
        try { Run(args); } catch { }
    }

    static void Run(string[] args)
    {
        string root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BmsAgingHost");
        if (!Writable(root))
        {
            root = Path.Combine(Path.GetTempPath(), "BmsAgingHost");
            if (!Writable(root)) return;
        }

        string appDir = Path.Combine(root, "app");
        string marker = Path.Combine(root, "version.txt");
        bool fresh;
        try
        {
            fresh = !File.Exists(marker)
                    || File.ReadAllText(marker).Trim() != Version
                    || !File.Exists(Path.Combine(appDir, "app.py"));
        }
        catch { fresh = true; }

        if (fresh)
        {
            try
            {
                string tmp = Path.Combine(root, "payload.tmp");
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
                {
                    if (s == null) throw new InvalidOperationException("payload missing");
                    using (FileStream f = File.Create(tmp)) s.CopyTo(f);
                }
                if (Directory.Exists(appDir)) Directory.Delete(appDir, true);
                ZipFile.ExtractToDirectory(tmp, appDir);
                File.Delete(tmp);
                File.WriteAllText(marker, Version);
            }
            catch (Exception ex)
            {
                try { File.WriteAllText(Path.Combine(root, "error.txt"), ex.ToString()); } catch { }
            }
        }

        string py = Path.Combine(appDir, "runtime", "pythonw.exe");
        string script = Path.Combine(appDir, "app.py");
        if (!File.Exists(py) || !File.Exists(script)) return;

        string extra = args.Length > 0 ? " " + string.Join(" ", args) : "";
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = py;
        psi.Arguments = "\"" + script + "\"" + extra;
        psi.WorkingDirectory = appDir;
        psi.EnvironmentVariables["BMS_LAUNCHER_EXE"] = Assembly.GetExecutingAssembly().Location;
        psi.EnvironmentVariables["BMS_APP_DIR"] = appDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        try { Process.Start(psi); } catch { }
    }
}
