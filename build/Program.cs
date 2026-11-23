using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;

class Program
{
    // 更新本版本号后，首次运行会重新解压
    const string Version = "2026.11.23.2";

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

    // 把旧版放在 app\ 里的记录和配置挪到 root\，升级时不再删除
    static void MigrateData(string appDir, string root)
    {
        try
        {
            string recSrc = Path.Combine(appDir, "记录");
            string recDst = Path.Combine(root, "记录");
            if (Directory.Exists(recSrc))
            {
                CopyTree(recSrc, recDst);
                try { Directory.Delete(recSrc, true); } catch { }
            }
            string cfgSrc = Path.Combine(appDir, "config.json");
            string cfgDst = Path.Combine(root, "config.json");
            if (File.Exists(cfgSrc) && !File.Exists(cfgDst))
            {
                try { File.Move(cfgSrc, cfgDst); }
                catch { try { File.Copy(cfgSrc, cfgDst, true); } catch { } }
            }
        }
        catch { }
    }

    static void CopyTree(string src, string dst)
    {
        Directory.CreateDirectory(dst);
        foreach (string dir in Directory.GetDirectories(src, "*", SearchOption.AllDirectories))
            Directory.CreateDirectory(dst + dir.Substring(src.Length));
        foreach (string file in Directory.GetFiles(src, "*", SearchOption.AllDirectories))
        {
            string target = dst + file.Substring(src.Length);
            if (File.Exists(target))
            {
                string folder = Path.GetDirectoryName(target);
                string name = Path.GetFileNameWithoutExtension(target);
                string ext = Path.GetExtension(target);
                int n = 1;
                string candidate;
                do { candidate = Path.Combine(folder, name + "_" + n + ext); n++; }
                while (File.Exists(candidate));
                target = candidate;
            }
            try { File.Copy(file, target, false); } catch { }
        }
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

        MigrateData(appDir, root);

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

        // 数据目录里没有配置时，用新解压的默认配置初始化一次
        string cfgNew = Path.Combine(appDir, "config.json");
        string cfgDst = Path.Combine(root, "config.json");
        if (File.Exists(cfgNew) && !File.Exists(cfgDst))
        {
            try { File.Copy(cfgNew, cfgDst); } catch { }
        }

        string pyName = "pythonw.exe";
        foreach (string a in args)
        {
            if (a == "--check-update") pyName = "python.exe";
        }
        string py = Path.Combine(appDir, "runtime", pyName);
        string script = Path.Combine(appDir, "app.py");
        if (!File.Exists(py) || !File.Exists(script)) return;

        string extra = args.Length > 0 ? " " + string.Join(" ", args) : "";
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = py;
        psi.Arguments = "\"" + script + "\"" + extra;
        psi.WorkingDirectory = appDir;
        psi.EnvironmentVariables["BMS_LAUNCHER_EXE"] = Assembly.GetExecutingAssembly().Location;
        psi.EnvironmentVariables["BMS_APP_DIR"] = appDir;
        psi.EnvironmentVariables["BMS_DATA_DIR"] = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        try { Process.Start(psi); } catch { }
    }
}
