using Godot;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

public sealed class ErisServiceConfig
{
	public string PythonExecutable { get; init; } = "python";
	public string VisionScriptPath { get; init; } = "src/vision.py";
	public bool EnableRemoteCognitionApi { get; init; } = true;
	public bool EnableTts { get; init; } = true;
	public string TtsVoice { get; init; } = "Aoede";
	public string TtsLanguage { get; init; } = "ja";
	public string TtsQuality { get; init; } = "high";
	public string DefaultRemoteApiKey { get; init; } = "";
	public string RemoteApiProvider { get; init; } = "deepseek";
	public string RemoteApiBaseUrl { get; init; } = "";
	public string RemoteApiModelName { get; init; } = "";
	public bool TutorEnabled { get; init; } = true;
	public bool TutorQuickSearchEnabled { get; init; } = true;
	public bool TutorResearchEnabled { get; init; } = true;
	public bool TutorSelfSearchEnabled { get; init; } = true;
	public bool TutorStaleGuardEnabled { get; init; } = true;
	public bool TutorPersistResearchMemory { get; init; } = false;
}

public sealed class ErisProcessManager
{
	private Process _visionProcess;
	private bool _visionTracebackActive;
	private bool _visionStopRequested;
	private DateTime? _visionStartedAtUtc;
	private int? _lastVisionExitCode;
	private readonly Action _onStatusChanged;
	private readonly Action _onUnexpectedExit;

	public ErisProcessManager(Action onStatusChanged = null, Action onUnexpectedExit = null)
	{
		_onStatusChanged = onStatusChanged ?? (() => { });
		_onUnexpectedExit = onUnexpectedExit ?? (() => { });
	}

	public bool IsVisionRunning => _visionProcess != null && !_visionProcess.HasExited;
	public int VisionProcessId => IsVisionRunning ? _visionProcess.Id : 0;
	public DateTime? VisionStartedAtUtc => IsVisionRunning ? _visionStartedAtUtc : null;
	public TimeSpan VisionUptime => VisionStartedAtUtc.HasValue ? DateTime.UtcNow - VisionStartedAtUtc.Value : TimeSpan.Zero;
	public int? LastVisionExitCode => _lastVisionExitCode;

	public void StartVision(ErisServiceConfig config)
	{
		if (_visionProcess != null)
		{
			if (!_visionProcess.HasExited)
			{
				RunLogger.Log($"[Vision] Da chay san, PID={_visionProcess.Id}");
				return;
			}
			try { _visionProcess.Dispose(); } catch { }
			_visionProcess = null;
		}

		_visionStopRequested = false;
		string baseDir = GetRuntimeBaseDir();

		// Ban export (packaged): uu tien runtime da dong goi bang PyInstaller,
		// nam o <thu muc chua .exe>/runtime/eris-runtime.exe. Tester khong can Python.
		string bundledRuntime = Path.Combine(baseDir, "runtime", BundledRuntimeFileName());
		bool useBundledRuntime = File.Exists(bundledRuntime);

		string fileName;
		string arguments;
		if (useBundledRuntime)
		{
			fileName = bundledRuntime;
			arguments = "";
		}
		else
		{
			// Che do dev / chua dong goi: chay truc tiep python src/vision.py (hoac duong dan dong phu hop).
			string visionScriptFullPath = ResolveVisionScriptPath(baseDir, config.VisionScriptPath);
			if (!File.Exists(visionScriptFullPath))
			{
				RunLogger.LogErr(
					$"[Vision] Khong tim thay runtime. Da thu:\n" +
					$"  - bundled: {bundledRuntime}\n" +
					$"  - script : {visionScriptFullPath}");
				return;
			}
			fileName = ResolvePythonExecutable(config.PythonExecutable, baseDir);
			arguments = $"\"{visionScriptFullPath}\"";
		}

		try
		{
			var startInfo = new ProcessStartInfo
			{
				FileName = fileName,
				Arguments = arguments,
				WorkingDirectory = baseDir,
				UseShellExecute = false,
				CreateNoWindow = true,
				RedirectStandardOutput = true,
				RedirectStandardError = true,
				StandardOutputEncoding = Encoding.UTF8,
				StandardErrorEncoding = Encoding.UTF8,
			};
			RunLogger.Log(useBundledRuntime
				? $"[Vision] Dung runtime dong goi: {bundledRuntime}"
				: $"[Vision] Dung Python dev: {fileName} {arguments}");
			PopulateVisionEnvironment(startInfo, config);

			var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
			process.OutputDataReceived += (_, e) =>
			{
				if (!string.IsNullOrWhiteSpace(e.Data) && ShouldLogVisionOutput(e.Data))
					RunLogger.Log($"[vision.py] {e.Data.Trim()}");
			};
			process.ErrorDataReceived += (_, e) => HandleVisionStderr(e.Data);
			process.Exited += (_, _) =>
			{
				int exitCode = 0;
				try { exitCode = process.ExitCode; } catch { }
				_lastVisionExitCode = exitCode;
				_visionStartedAtUtc = null;
				bool unexpected = !_visionStopRequested;
				RunLogger.Log($"[Vision] vision.py da thoat (PID={process.Id}, code={exitCode}, unexpected={unexpected}).");
				_onStatusChanged();
				if (unexpected)
					_onUnexpectedExit();
			};

			if (!process.Start())
			{
				RunLogger.LogErr("[Vision] Khong the khoi chay vision.py.");
				return;
			}

			_visionProcess = process;
			_visionStartedAtUtc = DateTime.UtcNow;
			_lastVisionExitCode = null;
			process.BeginOutputReadLine();
			process.BeginErrorReadLine();
			RunLogger.Log($"[Vision] Da khoi chay vision.py PID={process.Id}");
			_onStatusChanged();
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Vision] Loi khi chay vision.py: {ex.Message}");
		}
	}

	public void StopVision()
	{
		_visionStopRequested = true;
		StopProcess(ref _visionProcess, "Vision", "vision.py");
		_visionStartedAtUtc = null;
	}

	public void ToggleVision(ErisServiceConfig config)
	{
		if (_visionProcess != null && !_visionProcess.HasExited)
			StopVision();
		else
			StartVision(config);
	}

	public void StopAll()
	{
		StopVision();
	}

	private static void SetEnvIfAbsent(ProcessStartInfo startInfo, string key, string value)
	{
		// secrets.json duoc nap truoc; neu da co gia tri thi GIU, khong de len.
		if (startInfo.Environment.TryGetValue(key, out var existing) && !string.IsNullOrWhiteSpace(existing))
			return;
		if (!string.IsNullOrWhiteSpace(value))
			startInfo.Environment[key] = value;
	}

	private static string NormalizeTtsLanguage(string value)
	{
		string lang = (value ?? "").Trim().ToLowerInvariant();
		return lang switch
		{
			"" => "ja",
			"auto" => "ja",
			"jp" => "ja",
			"japanese" => "ja",
			_ => lang,
		};
	}



	private void PopulateVisionEnvironment(ProcessStartInfo startInfo, ErisServiceConfig config)
	{
		foreach (var secret in LoadProjectSecretsEnvironment())
		{
			if (!string.IsNullOrWhiteSpace(secret.Key) && !string.IsNullOrWhiteSpace(secret.Value))
				startInfo.Environment[secret.Key] = secret.Value;
		}

		startInfo.Environment["PYTHONUTF8"] = "1";
		startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
		startInfo.Environment["PYTHONUNBUFFERED"] = "1";
		// Cho phep Python tim base dir that (canh .exe khi da export) thay vi
		// suy ra tu __file__ (bi sai trong bundle PyInstaller).
		startInfo.Environment["ERIS_PROJECT_ROOT"] = GetRuntimeBaseDir();
		startInfo.Environment["ERIS_RUNTIME_SETTINGS_PATH"] = GetRuntimeSettingsPath();
		startInfo.Environment["ERIS_REMOTE_API_ENABLED"] = config.EnableRemoteCognitionApi ? "1" : "0";
		startInfo.Environment["ERIS_TTS_ENABLED"] = config.EnableTts ? "1" : "0";
		startInfo.Environment["ERIS_TTS_VOICE"] = string.IsNullOrWhiteSpace(config.TtsVoice) ? "Aoede" : config.TtsVoice.Trim();
		startInfo.Environment["ERIS_TTS_LANGUAGE"] = NormalizeTtsLanguage(config.TtsLanguage);
		startInfo.Environment["ERIS_TTS_QUALITY"] = string.IsNullOrWhiteSpace(config.TtsQuality) ? "high" : config.TtsQuality.Trim();
		startInfo.Environment["ERIS_TTS_EVENT_PAYLOAD"] = "1";
		// Settings panel da load secrets/user settings vao config truoc khi start.
		// Gan truc tiep de Restart ap dung ngay ca khi user chua bam Save.
		string effectiveProvider = string.IsNullOrWhiteSpace(config.RemoteApiProvider) ? "deepseek" : config.RemoteApiProvider.Trim();
		if (startInfo.Environment.TryGetValue("ERIS_REMOTE_PROVIDER", out var secretProvider)
			&& !string.IsNullOrWhiteSpace(secretProvider)
			&& effectiveProvider == "deepseek_web")
		{
			effectiveProvider = secretProvider.Trim();
		}
		startInfo.Environment["ERIS_REMOTE_PROVIDER"] = effectiveProvider;
		SetEnvIfAbsent(startInfo, "ERIS_REMOTE_BASE_URL", config.RemoteApiBaseUrl ?? "");
		SetEnvIfAbsent(startInfo, "ERIS_REMOTE_MODEL", config.RemoteApiModelName ?? "");
		startInfo.Environment["ERIS_LOCAL_STYLE_REWRITE_ENABLED"] = "0";
		startInfo.Environment["ERIS_LOCAL_VISION_FACTS"] = "0";
		startInfo.Environment["ERIS_TUTOR_ENABLED"] = config.TutorEnabled ? "1" : "0";
		startInfo.Environment["ERIS_TUTOR_QUICK_SEARCH_ENABLED"] = config.TutorQuickSearchEnabled ? "1" : "0";
		startInfo.Environment["ERIS_TUTOR_RESEARCH_ENABLED"] = config.TutorResearchEnabled ? "1" : "0";
		startInfo.Environment["ERIS_TUTOR_SELF_SEARCH_ENABLED"] = config.TutorSelfSearchEnabled ? "1" : "0";
		startInfo.Environment["ERIS_TUTOR_STALE_GUARD"] = config.TutorStaleGuardEnabled ? "1" : "0";
		startInfo.Environment["ERIS_TUTOR_RESEARCH_PERSIST"] = config.TutorPersistResearchMemory ? "1" : "0";
		if (!string.IsNullOrWhiteSpace(config.DefaultRemoteApiKey))
		{
			startInfo.Environment["ERIS_REMOTE_API_KEY"] = config.DefaultRemoteApiKey;

		}
	}

	private Dictionary<string, string> LoadProjectSecretsEnvironment()
	{
		var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
		string secretsPath = Path.Combine(GetRuntimeBaseDir(), "config", "secrets.json");
		if (!File.Exists(secretsPath))
			return values;

		try
		{
			using JsonDocument document = JsonDocument.Parse(File.ReadAllText(secretsPath, Encoding.UTF8));
			JsonElement root = document.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return values;

			AddSecretsFromObject(root, values);
			foreach (string sectionName in new[] { "api_keys", "env", "secrets" })
			{
				if (root.TryGetProperty(sectionName, out JsonElement section))
					AddSecretsFromObject(section, values);
			}
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Secrets] Khong doc duoc config/secrets.json: {ex.Message}");
		}

		return values;
	}

	private static string GetRuntimeSettingsPath()
	{
		string configured = System.Environment.GetEnvironmentVariable("ERIS_RUNTIME_SETTINGS_PATH") ?? "";
		if (!string.IsNullOrWhiteSpace(configured))
		{
			string expanded = System.Environment.ExpandEnvironmentVariables(configured.Trim());
			return Path.IsPathRooted(expanded)
				? expanded
				: Path.Combine(GetRuntimeBaseDir(), expanded);
		}
		string erisHome = System.Environment.GetEnvironmentVariable("ERIS_HOME");
		if (!string.IsNullOrWhiteSpace(erisHome))
			return Path.Combine(erisHome, "config", "runtime_settings.json");
		string localHome = Path.Combine(GetRuntimeBaseDir(), ".eris_home", "config", "runtime_settings.json");
		if (File.Exists(localHome))
			return localHome;
		string documents = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments);
		if (string.IsNullOrWhiteSpace(documents) || !Directory.Exists(documents))
		{
			string user = System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile);
			documents = Path.Combine(user, "Documents");
		}
		return Path.Combine(documents, "ErisLive", "config", "runtime_settings.json");
	}

	private void AddSecretsFromObject(JsonElement source, Dictionary<string, string> values)
	{
		if (source.ValueKind != JsonValueKind.Object)
			return;

		foreach (JsonProperty property in source.EnumerateObject())
		{
			if (string.IsNullOrWhiteSpace(property.Name))
				continue;
			if (property.Value.ValueKind == JsonValueKind.String)
			{
				string value = property.Value.GetString() ?? "";
				if (!string.IsNullOrWhiteSpace(value))
					values[property.Name] = value.Trim();
				continue;
			}
			if (property.Value.ValueKind == JsonValueKind.Array)
			{
				var parts = new List<string>();
				foreach (JsonElement item in property.Value.EnumerateArray())
				{
					if (item.ValueKind != JsonValueKind.String)
						continue;
					string value = item.GetString() ?? "";
					if (!string.IsNullOrWhiteSpace(value))
						parts.Add(value.Trim());
				}
				if (parts.Count > 0)
					values[property.Name] = string.Join(",", parts);
			}
		}
	}

	private void HandleVisionStderr(string data)
	{
		if (string.IsNullOrWhiteSpace(data))
			return;
		string rawLine = data;
		string line = rawLine.Trim();
		if (_visionTracebackActive)
		{
			if (string.IsNullOrWhiteSpace(line))
			{
				_visionTracebackActive = false;
			}
			else
			{
				RunLogger.LogErr($"[vision.py][ERR] {rawLine}");
				bool tracebackTail =
					rawLine.StartsWith(" ", StringComparison.Ordinal) ||
					rawLine.StartsWith("\t", StringComparison.Ordinal) ||
					line.StartsWith("File ", StringComparison.OrdinalIgnoreCase) ||
					line.StartsWith("During handling of the above exception", StringComparison.OrdinalIgnoreCase) ||
					line.Contains("Error:", StringComparison.OrdinalIgnoreCase) ||
					line.Contains("Exception:", StringComparison.OrdinalIgnoreCase);
				if (!tracebackTail)
					_visionTracebackActive = false;
				return;
			}
		}

		bool looksFatal =
			line.Contains("Traceback", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("Exception", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("No connection could be made", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("JSON khong hop le", StringComparison.OrdinalIgnoreCase);

		if (looksFatal)
		{
			if (line.Contains("Traceback", StringComparison.OrdinalIgnoreCase))
				_visionTracebackActive = true;
			RunLogger.LogErr($"[vision.py][ERR] {rawLine}");
		}
		else if (ShouldLogVisionStderr(line))
			RunLogger.Log($"[vision.py][stderr] {line}");
	}

	private void StopProcess(ref Process process, string label, string processName)
	{
		if (process == null)
		{
			_onStatusChanged();
			return;
		}
		try
		{
			if (!process.HasExited)
			{
				RunLogger.Log($"[{label}] Dang dung {processName} PID={process.Id}...");
				process.Kill(true);
				process.WaitForExit(3000);
			}
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[{label}] Khong the dung {processName} sach se: {ex.Message}");
		}
		finally
		{
			process.Dispose();
			process = null;
			_onStatusChanged();
		}
	}

	private static string GetProjectRoot()
	{
		return ProjectSettings.GlobalizePath("res://");
	}

	// Thu muc goc de tim runtime/config:
	// - Trong editor: dung res:// (project root tren o dia).
	// - Trong ban export: dung thu muc chua file .exe (vi res:// tro vao .pck).
	private static string GetRuntimeBaseDir()
	{
		if (OS.HasFeature("editor"))
			return GetProjectRoot();

		string exePath = OS.GetExecutablePath();
		string exeDir = Path.GetDirectoryName(exePath);
		return string.IsNullOrWhiteSpace(exeDir) ? GetProjectRoot() : exeDir;
	}

	/// <summary>
	/// Resolve Python executable theo thứ tự ưu tiên:
	/// 1. Nếu configuredExe là đường dẫn tuyệt đối và tồn tại → dùng thẳng.
	/// 2. .venv\Scripts\python.exe trong baseDir (venv local của project).
	/// 3. .venv\Scripts\python.exe trong thư mục gốc project (khi chạy từ editor sub-folder).
	/// 4. configuredExe gốc (fallback — dùng PATH hệ thống).
	/// </summary>
	private static string ResolveVisionScriptPath(string baseDir, string configuredPath)
	{
		string target = (configuredPath ?? "src/vision.py").Replace('/', Path.DirectorySeparatorChar);
		if (Path.IsPathRooted(target) && File.Exists(target))
			return target;

		string direct = Path.Combine(baseDir, target);
		if (File.Exists(direct))
			return direct;

		string[] candidates = new[]
		{
			Path.Combine(baseDir, "src", "vision.py"),
			Path.Combine(baseDir, "vision.py"),
			Path.Combine(baseDir, "src", "vision.pyc"),
			Path.Combine(baseDir, "vision.pyc"),
			Path.Combine(GetProjectRoot(), target),
			Path.Combine(GetProjectRoot(), "src", "vision.py"),
			Path.Combine(GetProjectRoot(), "vision.py")
		};
		foreach (string candidate in candidates)
		{
			if (File.Exists(candidate))
				return candidate;
		}
		return direct;
	}

	private static string ResolvePythonExecutable(string configuredExe, string baseDir)
	{
		// 0. Active VIRTUAL_ENV trong shell
		string virtualEnv = System.Environment.GetEnvironmentVariable("VIRTUAL_ENV");
		if (!string.IsNullOrWhiteSpace(virtualEnv))
		{
			string venvExe = Path.Combine(virtualEnv, "Scripts", "python.exe");
			if (File.Exists(venvExe))
			{
				RunLogger.Log($"[Vision] Python (active VIRTUAL_ENV): {venvExe}");
				return venvExe;
			}
		}

		// 1. Đường dẫn tuyệt đối có sẵn
		if (!string.IsNullOrWhiteSpace(configuredExe)
			&& Path.IsPathRooted(configuredExe)
			&& File.Exists(configuredExe))
		{
			RunLogger.Log($"[Vision] Python (absolute): {configuredExe}");
			return configuredExe;
		}

		// 2. .venv trong baseDir
		string venvInBase = Path.Combine(baseDir, ".venv", "Scripts", "python.exe");
		if (File.Exists(venvInBase))
		{
			RunLogger.Log($"[Vision] Python (venv baseDir): {venvInBase}");
			return venvInBase;
		}

		string portableInBase = Path.Combine(baseDir, "python", "python.exe");
		if (File.Exists(portableInBase))
		{
			RunLogger.Log($"[Vision] Python (portable baseDir): {portableInBase}");
			return portableInBase;
		}

		// 3. .venv trong project root (khi editor chạy từ sub-dir)
		string projectRoot = GetProjectRoot();
		if (!string.Equals(projectRoot, baseDir, StringComparison.OrdinalIgnoreCase))
		{
			string venvInRoot = Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
			if (File.Exists(venvInRoot))
			{
				RunLogger.Log($"[Vision] Python (venv projectRoot): {venvInRoot}");
				return venvInRoot;
			}
		}

		// 4. .venv ở thư mục cha
		string parentDir = Directory.GetParent(baseDir)?.FullName;
		if (!string.IsNullOrWhiteSpace(parentDir))
		{
			string venvInParent = Path.Combine(parentDir, ".venv", "Scripts", "python.exe");
			if (File.Exists(venvInParent))
			{
				RunLogger.Log($"[Vision] Python (venv parent): {venvInParent}");
				return venvInParent;
			}
		}

		// 5. Fallback: dùng nguyên configuredExe (hy vọng có trong PATH)
		string fallback = string.IsNullOrWhiteSpace(configuredExe) ? "python" : configuredExe;
		RunLogger.Log($"[Vision] Python (fallback PATH): {fallback}");
		return fallback;
	}

	private static string BundledRuntimeFileName()
	{
		return OS.HasFeature("windows") ? "eris-runtime.exe" : "eris-runtime";
	}

	private static string ResolveProjectPath(string path)
	{
		return Path.Combine(GetRuntimeBaseDir(), (path ?? "").Replace('/', Path.DirectorySeparatorChar));
	}

	private static bool ShouldLogVisionOutput(string line)
	{
		if (string.IsNullOrWhiteSpace(line))
			return false;
		string trimmed = line.Trim();
		return
			trimmed.Contains("[Init]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Shutdown]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Loop]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Parse]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[LLM]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Vision]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Chat]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[TTS]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[AI Decision]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[chat raw]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[directive raw]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[raw]", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Loi", StringComparison.OrdinalIgnoreCase) ||
			trimmed.Contains("[Error]", StringComparison.OrdinalIgnoreCase);
	}

	private static bool ShouldLogVisionStderr(string line)
	{
		if (string.IsNullOrWhiteSpace(line))
			return false;
		return
			line.Contains("Traceback", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("Exception", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("No connection could be made", StringComparison.OrdinalIgnoreCase) ||
			line.Contains("JSON khong hop le", StringComparison.OrdinalIgnoreCase);
	}
}
