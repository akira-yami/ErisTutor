using Godot;
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

/// <summary>
/// Sở hữu duy nhất process renderer Live2D. Cognition vẫn chạy trong vision.py;
/// body Godot chỉ bật/tắt renderer và forward event sân khấu sang UDP riêng.
/// </summary>
public sealed class Live2DBodyManager
{
	private readonly object _gate = new();
	private readonly Action<int> _onCurrentRendererExited;
	private Process _process;

	public Live2DBodyManager(Action<int> onCurrentRendererExited = null)
	{
		_onCurrentRendererExited = onCurrentRendererExited ?? (_ => { });
	}

	public bool IsRunning
	{
		get
		{
			lock (_gate)
				return IsAlive(_process);
		}
	}

	public int ProcessId
	{
		get
		{
			lock (_gate)
			{
				if (!IsAlive(_process))
					return 0;
				try { return _process.Id; } catch { return 0; }
			}
		}
	}

	public bool Start(string configuredPython, string modelRelativePath, int port, int windowX, int windowY, int windowWidth, int windowHeight)
	{
		lock (_gate)
		{
			if (IsAlive(_process))
				return true;
			_process = null;
		}

		string root = GetRuntimeBaseDir();
		string srcDir = Path.Combine(root, "src");
		string modelPath = ResolveRuntimePath(root, modelRelativePath);
		if (!File.Exists(modelPath))
		{
			RunLogger.LogErr($"[Live2D] Khong tim thay model3.json: {modelPath}");
			return false;
		}
		if (!Directory.Exists(Path.Combine(srcDir, "live2d_body")))
		{
			RunLogger.LogErr($"[Live2D] Khong thay module src/live2d_body trong: {srcDir}");
			return false;
		}

		string bundled = Path.Combine(root, "runtime", BundledLive2DFileName());
		bool useBundled = File.Exists(bundled);
		string fileName = useBundled ? bundled : ResolvePythonExecutable(configuredPython, root);
		RunLogger.Log($"[Live2D] Renderer launcher: {(useBundled ? "bundled" : "python")} -> {fileName}");
		int safePort = Math.Clamp(port, 1, 65535);
		int safeWidth = Math.Clamp(windowWidth, 320, 2400);
		int safeHeight = Math.Clamp(windowHeight, 480, 2400);
		int safeX = Math.Max(0, windowX);
		int safeY = Math.Max(0, windowY);
		string rendererArgs = $"--model \"{modelPath}\" --port {safePort} --width {safeWidth} --height {safeHeight} --x {safeX} --y {safeY}";
		string arguments = useBundled
			? rendererArgs
			: $"-m live2d_body.main {rendererArgs}";

		Process process = null;
		try
		{
			var startInfo = new ProcessStartInfo
			{
				FileName = fileName,
				Arguments = arguments,
				WorkingDirectory = useBundled ? root : srcDir,
				UseShellExecute = false,
				CreateNoWindow = true,
				RedirectStandardOutput = true,
				RedirectStandardError = true,
				StandardOutputEncoding = Encoding.UTF8,
				StandardErrorEncoding = Encoding.UTF8,
			};
			startInfo.Environment["PYTHONUTF8"] = "1";
			startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
			startInfo.Environment["PYTHONUNBUFFERED"] = "1";
			startInfo.Environment["ERIS_PROJECT_ROOT"] = root;
			startInfo.Environment["ERIS_LIVE2D_MODEL"] = modelPath;
			startInfo.Environment["ERIS_LIVE2D_PORT"] = safePort.ToString();
			startInfo.Environment["ERIS_GODOT_PID"] = Process.GetCurrentProcess().Id.ToString();

			process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
			process.OutputDataReceived += (_, e) =>
			{
				if (!string.IsNullOrWhiteSpace(e.Data))
					RunLogger.Log($"[live2d] {e.Data.Trim()}");
			};
			process.ErrorDataReceived += (_, e) =>
			{
				if (!string.IsNullOrWhiteSpace(e.Data))
					RunLogger.LogErr($"[live2d][ERR] {e.Data.Trim()}");
			};
			Process captured = process;
			process.Exited += (_, _) => HandleExited(captured);

			lock (_gate)
				_process = process;
			if (!process.Start())
				throw new InvalidOperationException("Process.Start trả về false.");

			process.BeginOutputReadLine();
			process.BeginErrorReadLine();
			RunLogger.Log($"[Live2D] Da khoi chay renderer PID={process.Id}, UDP={safePort}");
			return true;
		}
		catch (Exception ex)
		{
			lock (_gate)
			{
				if (ReferenceEquals(_process, process))
					_process = null;
			}
			try
			{
				if (process != null && IsAlive(process))
					process.Kill(true);
			}
			catch { }
			try { process?.Dispose(); } catch { }
			RunLogger.LogErr($"[Live2D] Loi khoi chay renderer: {ex.Message}");
			return false;
		}
	}

	public void Stop()
	{
		Process process;
		lock (_gate)
		{
			process = _process;
			_process = null;
		}
		if (process == null)
			return;
		try
		{
			if (IsAlive(process))
			{
				RunLogger.Log($"[Live2D] Dang dung renderer PID={process.Id}...");
				process.Kill(true);
				process.WaitForExit(2000);
			}
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Live2D] Khong dung duoc renderer sach se: {ex.Message}");
		}
		finally
		{
			try { process.Dispose(); } catch { }
		}
	}

	private void HandleExited(Process process)
	{
		int exitCode = 0;
		try { exitCode = process.ExitCode; } catch { }
		bool wasCurrent;
		lock (_gate)
		{
			wasCurrent = ReferenceEquals(_process, process);
			if (wasCurrent)
				_process = null;
		}
		RunLogger.Log($"[Live2D] Renderer da thoat (code={exitCode}, current={wasCurrent}).");
		if (wasCurrent)
		{
			try { process.Dispose(); } catch { }
			_onCurrentRendererExited(exitCode);
		}
	}

	private static bool IsAlive(Process process)
	{
		if (process == null)
			return false;
		try { return !process.HasExited; }
		catch { return false; }
	}

	private static string ResolveRuntimePath(string root, string path)
	{
		string value = string.IsNullOrWhiteSpace(path)
			? "src/assets/live2d/infj/infj.model3.json"
			: path.Trim();
		return Path.IsPathRooted(value)
			? value
			: Path.Combine(root, value.Replace('/', Path.DirectorySeparatorChar));
	}

	private static string GetProjectRoot() => ProjectSettings.GlobalizePath("res://");

	private static string GetRuntimeBaseDir()
	{
		if (OS.HasFeature("editor"))
			return GetProjectRoot();
		string exeDir = Path.GetDirectoryName(OS.GetExecutablePath());
		return string.IsNullOrWhiteSpace(exeDir) ? GetProjectRoot() : exeDir;
	}

	private static string ResolvePythonExecutable(string configured, string root)
	{
		if (!string.IsNullOrWhiteSpace(configured) && Path.IsPathRooted(configured) && File.Exists(configured))
		{
			RunLogger.Log($"[Live2D] Python (absolute): {configured}");
			return configured;
		}
		string portable = Path.Combine(root, "python", "python.exe");
		if (!OS.HasFeature("editor") && File.Exists(portable))
		{
			RunLogger.Log($"[Live2D] Python (portable runtime): {portable}");
			return portable;
		}
		string venv = Path.Combine(root, ".venv", "Scripts", "python.exe");
		if (File.Exists(venv))
		{
			RunLogger.Log($"[Live2D] Python (venv): {venv}");
			return venv;
		}
		if (File.Exists(portable))
		{
			RunLogger.Log($"[Live2D] Python (portable): {portable}");
			return portable;
		}
		string fallback = string.IsNullOrWhiteSpace(configured) ? "python" : configured;
		RunLogger.Log($"[Live2D] Python (fallback PATH): {fallback}");
		return fallback;
	}

	private static string BundledLive2DFileName()
	{
		return OS.HasFeature("windows") ? "eris-live2d.exe" : "eris-live2d";
	}
}
