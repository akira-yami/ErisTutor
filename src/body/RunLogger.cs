using Godot;
using System;
using System.IO;
using System.Text;
using System.Text.Json;

/// <summary>
/// Ghi lai toan bo output (giong cua so Output cua Godot) ra mot file .txt
/// co timestamp, dat canh file .exe khi export (hoac project root khi chay editor).
/// Moi lan chay app tao mot file log moi: logs/eris_run_yyyyMMdd_HHmmss.txt
///
/// Muc dich: ban export double-click khong hien console, loi API/Python "im lang".
/// Logger nay giup tester gui lai file log de chan doan.
/// </summary>
public static class RunLogger
{
	private static readonly object _gate = new object();
	private static StreamWriter _writer;
	private static string _logFilePath;
	private static bool _initialized;

	public static string LogFilePath => _logFilePath;

	/// <summary>
	/// Khoi tao logger. Goi mot lan o dau _Ready cua node chinh.
	/// An toan khi goi nhieu lan (chi khoi tao lan dau).
	/// </summary>
	public static void Initialize()
	{
		lock (_gate)
		{
			if (_initialized)
				return;

			try
			{
				string baseDir = GetBaseDir();
				string logDir = Path.Combine(baseDir, "logs");
				Directory.CreateDirectory(logDir);

				string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
				_logFilePath = Path.Combine(logDir, $"eris_run_{stamp}.txt");

				_writer = new StreamWriter(_logFilePath, append: false, Encoding.UTF8)
				{
					AutoFlush = true,
				};

				_initialized = true;

				WriteHeader();
				// Cung in ra Output cua Godot de biet log dang nam o dau.
				GD.Print($"[RunLogger] Ghi log ra: {_logFilePath}");
			}
			catch (Exception ex)
			{
				// Khong duoc nem loi tu logger; chi bao ra Output.
				GD.PrintErr($"[RunLogger] Khong the khoi tao file log: {ex.Message}");
				_initialized = false;
				_writer = null;
			}
		}
	}

	/// <summary>Ghi mot dong thong tin (mirror ra Godot Output + file).</summary>
	public static void Log(string message)
	{
		GD.Print(message);
		WriteLine("INFO", message);
	}

	/// <summary>Ghi event JSON có cấu trúc nhưng đã loại field secret phổ biến.</summary>
	public static void LogStructured(string eventName, string rawJson)
	{
		try
		{
			using JsonDocument doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(rawJson) ? "{}" : rawJson);
			string safe = SanitizeJson(doc.RootElement);
			Log($"[{eventName}] {safe}");
		}
		catch (Exception ex)
		{
			Log($"[{eventName}] <invalid-json:{ex.Message}>");
		}
	}

	private static string SanitizeJson(JsonElement element)
	{
		if (element.ValueKind != JsonValueKind.Object)
			return element.GetRawText();
		var output = new System.Text.Json.Nodes.JsonObject();
		foreach (JsonProperty property in element.EnumerateObject())
		{
			string name = property.Name;
			if (name.Contains("api_key", StringComparison.OrdinalIgnoreCase)
				|| name.Equals("key", StringComparison.OrdinalIgnoreCase)
				|| name.Contains("secret", StringComparison.OrdinalIgnoreCase))
			{
				output[name] = "<redacted>";
				continue;
			}
			output[name] = System.Text.Json.Nodes.JsonNode.Parse(property.Value.GetRawText());
		}
		return output.ToJsonString();
	}

	/// <summary>Ghi mot dong loi (mirror ra Godot Output + file).</summary>
	public static void LogErr(string message)
	{
		GD.PrintErr(message);
		WriteLine("ERR ", message);
	}

	/// <summary>Dong file log (goi o _ExitTree).</summary>
	public static void Shutdown()
	{
		lock (_gate)
		{
			if (_writer == null)
				return;
			try
			{
				WriteLine("INFO", "[RunLogger] Ket thuc phien chay.");
				_writer.Flush();
				_writer.Dispose();
			}
			catch
			{
				// bo qua loi khi dong
			}
			finally
			{
				_writer = null;
				_initialized = false;
			}
		}
	}

	private static void WriteLine(string level, string message)
	{
		lock (_gate)
		{
			if (_writer == null)
				return;
			try
			{
				string ts = DateTime.Now.ToString("HH:mm:ss.fff");
				_writer.WriteLine($"{ts} [{level}] {message}");
			}
			catch
			{
				// khong de loi ghi log lam sap app
			}
		}
	}

	private static void WriteHeader()
	{
		try
		{
			_writer.WriteLine("==================================================");
			_writer.WriteLine($" Eris run log");
			_writer.WriteLine($" Bat dau : {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
			_writer.WriteLine($" Exe     : {OS.GetExecutablePath()}");
			_writer.WriteLine($" Editor  : {OS.HasFeature("editor")}");
			_writer.WriteLine($" OS      : {OS.GetName()}");
			_writer.WriteLine("==================================================");
		}
		catch
		{
			// bo qua
		}
	}

	// Cung logic voi ErisProcessManager.GetRuntimeBaseDir:
	// - Editor: project root (res://)
	// - Export: thu muc chua file .exe
	private static string GetBaseDir()
	{
		if (OS.HasFeature("editor"))
			return ProjectSettings.GlobalizePath("res://");

		string exePath = OS.GetExecutablePath();
		string exeDir = Path.GetDirectoryName(exePath);
		return string.IsNullOrWhiteSpace(exeDir)
			? ProjectSettings.GlobalizePath("res://")
			: exeDir;
	}
}
