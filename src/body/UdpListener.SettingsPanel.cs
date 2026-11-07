using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

public partial class UdpListener : Node2D
{
	private sealed class SettingsKeyHealth
	{
		public int Index { get; init; } = -1;
		public string Fingerprint { get; init; } = "";
		public string Status { get; init; } = "unknown";
		public string Reason { get; init; } = "";
		public long CooldownMs { get; init; }
		public bool Active { get; init; }
	}

	private Panel _settingsPanel;
	private Label _settingsBrainStatusLabel;
	private Label _settingsRuntimeDetailLabel;
	private Label _settingsRuntimeProviderSummaryLabel;
	private Label _settingsRuntimeModelSummaryLabel;
	private Label _settingsMainBrainEndpointLabel;
	private Label _settingsMainBrainKeyLabel;
	private Label _settingsMainBrainLiveLabel;
	private Label _settingsSystemAiHealthLabel;
	private Button _settingsRuntimeStartButton;
	private Button _settingsRuntimeStopButton;
	private Button _settingsRuntimeRestartButton;
	private Control _settingsConnectionOverlay;
	private Label _settingsConnectionFeedbackLabel;
	private Button _settingsMainPoolStatusLabel;
	private Button _settingsPoolStatusLabel;
	private Button _settingsFallbackStatusLabel;
	private Button _settingsElevenLabsPoolStatusLabel;
	private Label _settingsPoolHealthLabel;
	private Label _settingsTtsEngineStatusLabel;
	private Label _settingsStatusNoteLabel;
	private TextureRect _settingsTtsSwitch;
	private TextureRect _settingsAutoStartSwitch;
	private TextureRect _settingsAutoRestartSwitch;
	private TextureRect _settingsAlwaysOnTopSwitch;
	private TextureRect _settingsTransparentSwitch;
	private TextureRect _settingsPassthroughSwitch;
	private TextureRect _settingsPositionLockSwitch;
	private OptionButton _settingsProviderSelect;
	private LineEdit _settingsModelInput;
	private LineEdit _settingsBaseUrlInput;
	private OptionButton _settingsVoiceSelect;
	private OptionButton _settingsLanguageSelect;
	private OptionButton _settingsQualitySelect;
	private HSlider _settingsTtsVolumeSlider;
	private HSlider _settingsTtsSpeedSlider;
	private Label _settingsTtsVolumeValueLabel;
	private Label _settingsTtsSpeedValueLabel;
	private FileDialog _settingsImportSecretsDialog;
	private VBoxContainer _settingsKeyRowsBox;
	private readonly List<LineEdit> _settingsKeyNameInputs = new();
	private readonly List<LineEdit> _settingsKeyInputs = new();
	private readonly List<Label> _settingsKeyStatusLabels = new();
	private readonly Dictionary<string, List<SettingsKeyHealth>> _settingsKeyHealth = new(StringComparer.OrdinalIgnoreCase);
	private readonly Dictionary<string, string> _settingsPoolHealthSummary = new(StringComparer.OrdinalIgnoreCase);

	// Giữ bản nháp riêng cho từng pool. Tên hiển thị không ghi vào secrets.json;
	// chúng được lưu an toàn trong user://eris_settings.json theo fingerprint của key.
	private readonly Dictionary<string, List<string>> _settingsKeyDrafts = new(StringComparer.OrdinalIgnoreCase);
	private readonly Dictionary<string, List<string>> _settingsKeyNameDrafts = new(StringComparer.OrdinalIgnoreCase);
	private readonly Dictionary<string, string> _settingsKeyAliases = new(StringComparer.OrdinalIgnoreCase);
	private bool _settingsSkipKeyDraftCaptureOnce;
	private string _settingsKeyPoolKind = "";
	private ulong _settingsNextRuntimeRefreshMs;
	private bool _settingsTtsEnabled = true;
	private string _settingsTtsProvider = "elevenlabs";
	private string _settingsTtsVoice = "Aoede";
	private string _settingsElevenLabsVoiceId = "fUjY9K2nAIwlALOwSiwc";
	private string _settingsElevenLabsModel = "eleven_v3";
	private string _settingsTtsLanguage = "ja";
	private int _settingsLoadedSchemaVersion = 0;
	private string _settingsTtsQuality = "balanced";
	private float _settingsTtsVolume = 1.00f;
	private float _settingsTtsSpeed = 1.04f;

	private const string SettingsStatePath = "user://eris_settings.json";
	private const string SettingsExportFileName = "eris_settings_export.json";

	private static readonly Color SettingsInk = new(0.42f, 0.07f, 0.27f);
	private static readonly Color SettingsInkSoft = new(0.63f, 0.32f, 0.48f);
	private static readonly Color SettingsPink = new(0.96f, 0.34f, 0.62f);
	private static readonly Color SettingsBorder = new(0.96f, 0.76f, 0.86f, 0.80f);
	private static readonly Color SettingsCard = new(1.00f, 0.965f, 0.982f, 0.70f);

	private void OnContextSettingsPressed()
	{
		ToggleSettingsPanel();
	}

	private void ToggleSettingsPanel()
	{
		if (_settingsPanel == null)
			return;
		if (_settingsPanel.Visible)
			CloseSettingsPanel();
		else
			ShowSettingsPanel();
	}

	private void ShowSettingsPanel()
	{
		if (_settingsPanel == null)
			return;
		CloseCharacterContextMenu();
		_settingsPanel.Visible = true;
		_settingsPanel.Size = SettingsPanelMinSize;
		Vector2 screenSize = GetViewportRect().Size;
		_settingsPanel.Position = new Vector2(
			Mathf.Max(0f, (screenSize.X - SettingsPanelMinSize.X) * 0.5f),
			Mathf.Max(0f, (screenSize.Y - SettingsPanelMinSize.Y) * 0.5f));
		RefreshSettingsPanel();
		if (_processes != null && _processes.IsVisionRunning)
			TestSettingsPool();
	}

	private void CloseSettingsPanel()
	{
		if (_settingsPanel != null)
			_settingsPanel.Visible = false;
	}

	private void BuildSettingsPanelUi()
	{
		if (_settingsPanel == null)
			return;

		// Rebuild panel không được nuốt mất key người dùng đang gõ dở.
		if (_settingsSkipKeyDraftCaptureOnce)
			_settingsSkipKeyDraftCaptureOnce = false;
		else
			CaptureCurrentSettingsKeyDraft();

		var bgTex = ResourceLoader.Exists("res://asset/Resource/SettingPanel.png")
			? GD.Load<Texture2D>("res://asset/Resource/SettingPanel.png")
			: null;
		if (bgTex != null)
		{
			_settingsPanel.AddThemeStyleboxOverride("panel", new StyleBoxTexture
			{
				Texture = bgTex,
				TextureMarginLeft = 0f,
				TextureMarginTop = 0f,
				TextureMarginRight = 0f,
				TextureMarginBottom = 0f,
				AxisStretchHorizontal = StyleBoxTexture.AxisStretchMode.Stretch,
				AxisStretchVertical = StyleBoxTexture.AxisStretchMode.Stretch,
			});
		}
		else
		{
			_settingsPanel.AddThemeStyleboxOverride("panel", new StyleBoxEmpty());
		}
		_settingsPanel.ClipContents = true;
		foreach (Node child in _settingsPanel.GetChildren())
		{
			_settingsPanel.RemoveChild(child);
			child.QueueFree();
		}
		ResetSettingsUiReferencesForRebuild();

		_switchOnTexture ??= GD.Load<Texture2D>("res://asset/Resource/on.png");
		_switchOffTexture ??= GD.Load<Texture2D>("res://asset/Resource/off.png");

		var root = new Control { MouseFilter = Control.MouseFilterEnum.Ignore };
		root.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		_settingsPanel.AddChild(root);

		var title = CreateUiLabel("Cài đặt", 34, SettingsInk);
		title.Position = new Vector2(54f, 34f);
		title.Size = new Vector2(260f, 52f);
		root.AddChild(title);

		var heart = CreateUiLabel("♡", 26, SettingsPink);
		heart.Position = new Vector2(222f, 42f);
		heart.Size = new Vector2(42f, 36f);
		root.AddChild(heart);

		var controls = CreateWindowControlCapsule(() => _settingsPanel.Visible = false, CloseSettingsPanel);
		controls.Position = new Vector2(1040f, 34f);
		root.AddChild(controls);

		AddSettingsMainTabs(root);
		BuildSettingsImportDialog();

		if (_settingsModePage == SettingsModePage.Voice)
		{
			BuildRuntimeVoiceSettingsPage(root);
			BuildSettingsConnectionOverlay(root);
			RefreshSettingsPanel();
			return;
		}

		if (_settingsModePage == SettingsModePage.Data)
		{
			BuildDataSettingsPage(root);
			BuildSettingsConnectionOverlay(root);
			RefreshSettingsPanel();
			if (_processes != null && _processes.IsVisionRunning)
				TestSettingsPool();
			return;
		}

		if (_settingsModePage == SettingsModePage.Advanced)
		{
			BuildRuntimeSchemaSettingsPage(root);
			RefreshSettingsPanel();
			return;
		}

		if (_settingsModePage == SettingsModePage.Danger)
		{
			BuildDangerSettingsPage(root);
			RefreshSettingsPanel();
			RequestMaintenanceCatalog();
			return;
		}

		var statusCard = AddSettingsCard(root, new Rect2(32f, 178f, 334f, 414f), "Trạng thái Eris");
		BuildSettingsStatusColumn(statusCard);

		var brainCard = AddSettingsCard(root, new Rect2(384f, 178f, 392f, 414f), "Não chính");
		BuildSettingsMainBrainColumn(brainCard);

		var quickCard = AddSettingsCard(root, new Rect2(794f, 178f, 354f, 414f), "Điều khiển nhanh");
		BuildSettingsQuickControlsColumn(quickCard);

		AddSettingsPageFooter(
			root,
			"Basic chỉ giữ trạng thái, não chính và thao tác dùng hằng ngày. Key phụ đã chuyển sang Kết nối & dữ liệu.",
			showBackupActions: false);

		BuildSettingsConnectionOverlay(root);
		RefreshSettingsPanel();
	}

	private void BuildSettingsImportDialog()
	{
		_settingsImportSecretsDialog = new FileDialog
		{
			Title = "Nhập secrets.json",
			Access = FileDialog.AccessEnum.Filesystem,
			FileMode = FileDialog.FileModeEnum.OpenFile,
			Filters = new[] { "*.json ; JSON secrets" },
			Size = new Vector2I(720, 520),
		};
		_settingsImportSecretsDialog.FileSelected += ImportSecretsFile;
		_settingsPanel.AddChild(_settingsImportSecretsDialog);
	}

	private void AddSettingsMainTabs(Control root)
	{
		// Một hàng điều hướng thật. Bản cũ có ba tab giả bên trên và ba tab thật
		// bên dưới, một nghi lễ thừa khá công phu chỉ để đổi một dòng status.
		AddSettingsTab(root, new Rect2(42f, 118f, 204f, 40f), "♡  Cơ bản", _settingsModePage == SettingsModePage.Basic, () => SwitchSettingsMode(SettingsModePage.Basic));
		AddSettingsTab(root, new Rect2(258f, 118f, 204f, 40f), "♬  Giọng nói", _settingsModePage == SettingsModePage.Voice, () => SwitchSettingsMode(SettingsModePage.Voice));
		AddSettingsTab(root, new Rect2(474f, 118f, 232f, 40f), "▤  Kết nối & dữ liệu", _settingsModePage == SettingsModePage.Data, () => SwitchSettingsMode(SettingsModePage.Data));
		AddSettingsTab(root, new Rect2(718f, 118f, 188f, 40f), "Advanced", _settingsModePage == SettingsModePage.Advanced, () => SwitchSettingsMode(SettingsModePage.Advanced));
		AddSettingsTab(root, new Rect2(918f, 118f, 220f, 40f), "△  Danger Zone", _settingsModePage == SettingsModePage.Danger, () => SwitchSettingsMode(SettingsModePage.Danger));
	}

	private Panel AddSettingsCard(Control parent, Rect2 rect, string title)
	{
		var panel = new Panel
		{
			Position = rect.Position,
			Size = rect.Size,
			ClipContents = true,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		panel.AddThemeStyleboxOverride("panel", SettingsCardStyle(SettingsCard, SettingsBorder, 16));
		parent.AddChild(panel);

		var titleLabel = CreateUiLabel(title, 20, SettingsInk);
		titleLabel.Position = new Vector2(18f, 14f);
		titleLabel.Size = new Vector2(rect.Size.X - 36f, 28f);
		panel.AddChild(titleLabel);
		return panel;
	}

	private void BuildSettingsStatusColumn(Control parent)
	{
		_settingsBrainStatusLabel = CreateSettingsChip("Brain", true);
		_settingsBrainStatusLabel.Position = new Vector2(18f, 48f);
		_settingsBrainStatusLabel.Size = new Vector2(parent.Size.X - 36f, 30f);
		parent.AddChild(_settingsBrainStatusLabel);

		_settingsRuntimeDetailLabel = CreateUiLabel("PID — · chưa chạy", 10, SettingsInkSoft);
		_settingsRuntimeDetailLabel.Position = new Vector2(20f, 80f);
		_settingsRuntimeDetailLabel.Size = new Vector2(parent.Size.X - 40f, 20f);
		StyleSettingsSingleLineLabel(_settingsRuntimeDetailLabel);
		parent.AddChild(_settingsRuntimeDetailLabel);

		AddSettingsCompactToggle(parent, 106f, "Tự khởi động Brain", AutoStartVision, ref _settingsAutoStartSwitch, () =>
		{
			AutoStartVision = !AutoStartVision;
			ApplySettingsSwitch(_settingsAutoStartSwitch, AutoStartVision);
			SetSettingsStatus(AutoStartVision
				? "Brain sẽ tự khởi động khi mở Eris."
				: "Đã tắt tự khởi động Brain khi mở Eris.");
		});
		AddSettingsCompactToggle(parent, 136f, "Tự phục hồi sau crash", AutoRestartVisionOnCrash, ref _settingsAutoRestartSwitch, () =>
		{
			AutoRestartVisionOnCrash = !AutoRestartVisionOnCrash;
			ApplySettingsSwitch(_settingsAutoRestartSwitch, AutoRestartVisionOnCrash);
			SetSettingsStatus(AutoRestartVisionOnCrash
				? "Đã bật tự phục hồi Brain sau crash."
				: "Đã tắt tự phục hồi Brain sau crash.");
		});

		_settingsRuntimeStartButton = CreateSettingsButton("▶  Khởi động Brain", () =>
		{
			RunLogger.Log("[Settings] User requested Brain start.");
			SyncSettingsInputsToFields();
			StartVisionProcess();
			SetSettingsStatus("Đang khởi động Brain...");
			RefreshSettingsRuntimeStatus();
		}, primary: true);
		_settingsRuntimeStartButton.Position = new Vector2(18f, 174f);
		_settingsRuntimeStartButton.Size = new Vector2(parent.Size.X - 36f, 36f);
		parent.AddChild(_settingsRuntimeStartButton);

		_settingsRuntimeStopButton = CreateSettingsButton("■  Dừng", () =>
		{
			RunLogger.Log("[Settings] User requested Brain stop.");
			StopVisionProcess();
			SetSettingsStatus("Đã yêu cầu dừng Brain.");
			RefreshSettingsRuntimeStatus();
		}, primary: false);
		_settingsRuntimeStopButton.Position = new Vector2(18f, 174f);
		_settingsRuntimeStopButton.Size = new Vector2(118f, 36f);
		parent.AddChild(_settingsRuntimeStopButton);

		_settingsRuntimeRestartButton = CreateSettingsButton("↻  Khởi động lại", () =>
		{
			RunLogger.Log("[Settings] User requested Brain restart.");
			SyncSettingsInputsToFields();
			RestartVisionProcess();
			SetSettingsStatus("Đang khởi động lại Brain với cấu hình hiện tại...");
			RefreshSettingsRuntimeStatus();
		}, primary: true);
		_settingsRuntimeRestartButton.Position = new Vector2(144f, 174f);
		_settingsRuntimeRestartButton.Size = new Vector2(parent.Size.X - 162f, 36f);
		parent.AddChild(_settingsRuntimeRestartButton);

		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.42f),
			Position = new Vector2(18f, 224f),
			Size = new Vector2(parent.Size.X - 36f, 1.2f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(divider);

		var healthTitle = CreateUiLabel("Tình trạng hệ thống", 11, SettingsInkSoft);
		healthTitle.Position = new Vector2(20f, 234f);
		healthTitle.Size = new Vector2(parent.Size.X - 40f, 20f);
		StyleSettingsSingleLineLabel(healthTitle);
		parent.AddChild(healthTitle);

		_settingsSystemAiHealthLabel = AddSettingsHealthRow(parent, 260f, "Kết nối AI", "Chưa kiểm tra");
		_settingsTtsEngineStatusLabel = AddSettingsHealthRow(parent, 290f, "TTS", "Chưa kiểm tra");
		AddSettingsHealthRow(parent, 320f, "UDP", _chatSend != null ? "Sẵn sàng" : "Chưa sẵn sàng");
		AddSettingsHealthRow(parent, 350f, "Memory", "Theo Brain");

		var test = CreateSettingsButton("Kiểm tra toàn bộ", TestSettingsPool, primary: false);
		test.Position = new Vector2(18f, 378f);
		test.Size = new Vector2(142f, 28f);
		parent.AddChild(test);

		var logs = CreateSettingsButton("Mở logs", OpenSettingsLogs, primary: false);
		logs.Position = new Vector2(168f, 378f);
		logs.Size = new Vector2(parent.Size.X - 186f, 28f);
		parent.AddChild(logs);
	}

	private Label AddSettingsHealthRow(Control parent, float y, string name, string status)
	{
		var row = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(parent.Size.X - 36f, 25f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1f, 0.985f, 0.995f, 0.58f),
			new Color(0.96f, 0.76f, 0.86f, 0.42f),
			10));
		parent.AddChild(row);

		var title = CreateUiLabel(name, 9, SettingsInkSoft);
		title.Position = new Vector2(10f, 3f);
		title.Size = new Vector2(112f, 18f);
		StyleSettingsSingleLineLabel(title);
		row.AddChild(title);

		var value = CreateUiLabel(status, 9, SettingsInk);
		value.Position = new Vector2(124f, 3f);
		value.Size = new Vector2(row.Size.X - 134f, 18f);
		StyleSettingsSingleLineLabel(value, HorizontalAlignment.Right);
		row.AddChild(value);
		return value;
	}

	private void BuildSettingsMainBrainColumn(Control parent)
	{
		var role = CreateUiLabel("MAIN COGNITION", 9, SettingsPink);
		role.Position = new Vector2(18f, 48f);
		role.Size = new Vector2(parent.Size.X - 36f, 18f);
		StyleSettingsSingleLineLabel(role);
		parent.AddChild(role);

		_settingsRuntimeModelSummaryLabel = CreateUiLabel(
			string.IsNullOrWhiteSpace(RemoteApiModelName) ? "Chưa chọn model" : FriendlyMainBrainModelName(RemoteApiModelName),
			22,
			SettingsInk);
		_settingsRuntimeModelSummaryLabel.Position = new Vector2(18f, 66f);
		_settingsRuntimeModelSummaryLabel.Size = new Vector2(parent.Size.X - 36f, 34f);
		_settingsRuntimeModelSummaryLabel.TooltipText = RemoteApiModelName ?? "";
		StyleSettingsSingleLineLabel(_settingsRuntimeModelSummaryLabel);
		parent.AddChild(_settingsRuntimeModelSummaryLabel);

		var modelId = CreateUiLabel(
			string.IsNullOrWhiteSpace(RemoteApiModelName) ? "Model ID: —" : $"Model ID: {RemoteApiModelName}",
			9,
			SettingsInkSoft);
		modelId.Position = new Vector2(20f, 100f);
		modelId.Size = new Vector2(parent.Size.X - 40f, 18f);
		StyleSettingsSingleLineLabel(modelId);
		modelId.TooltipText = RemoteApiModelName ?? "";
		parent.AddChild(modelId);

		var identityPanel = new Panel
		{
			Position = new Vector2(18f, 128f),
			Size = new Vector2(parent.Size.X - 36f, 116f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		identityPanel.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1f, 0.985f, 0.995f, 0.76f),
			SettingsBorder,
			14));
		parent.AddChild(identityPanel);

		_settingsRuntimeProviderSummaryLabel = AddSettingsBrainInfoLine(
			identityPanel, 10f, "Nhà cung cấp", DisplaySettingsProviderName(RemoteApiProvider));
		_settingsMainBrainEndpointLabel = AddSettingsBrainInfoLine(
			identityPanel, 38f, "Endpoint", CompactSettingsEndpoint(RemoteApiBaseUrl));
		_settingsMainBrainKeyLabel = AddSettingsBrainInfoLine(
			identityPanel, 66f, "Credential", MainBrainLocalCredentialSummary());
		AddSettingsBrainInfoLine(identityPanel, 94f, "Nguồn cấu hình", "remote_main");

		var test = CreateSettingsButton("Kiểm tra não chính", RequestMainBrainConnectionTest, primary: false);
		test.Position = new Vector2(18f, 256f);
		test.Size = new Vector2((parent.Size.X - 44f) * 0.52f, 34f);
		parent.AddChild(test);

		var configure = CreateSettingsButton("Cấu hình", ShowSettingsConnectionEditor, primary: true);
		configure.Position = new Vector2(test.Position.X + test.Size.X + 8f, 256f);
		configure.Size = new Vector2(parent.Size.X - configure.Position.X - 18f, 34f);
		parent.AddChild(configure);

		_settingsMainBrainLiveLabel = CreateUiLabel("Runtime: chưa đọc trạng thái hiệu lực", 10, SettingsInkSoft);
		_settingsMainBrainLiveLabel.Position = new Vector2(20f, 300f);
		_settingsMainBrainLiveLabel.Size = new Vector2(parent.Size.X - 40f, 40f);
		_settingsMainBrainLiveLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(_settingsMainBrainLiveLabel);

		var manage = CreateSettingsButton("Quản lý key não chính & key phụ", () => SwitchSettingsMode(SettingsModePage.Data), primary: false);
		manage.Position = new Vector2(18f, 350f);
		manage.Size = new Vector2(parent.Size.X - 36f, 34f);
		manage.AddThemeFontSizeOverride("font_size", 10);
		parent.AddChild(manage);
	}

	private Label AddSettingsBrainInfoLine(Control parent, float y, string name, string value)
	{
		var key = CreateUiLabel(name, 9, SettingsInkSoft);
		key.Position = new Vector2(12f, y);
		key.Size = new Vector2(112f, 20f);
		StyleSettingsSingleLineLabel(key);
		parent.AddChild(key);

		var label = CreateUiLabel(value, 9, SettingsInk);
		label.Position = new Vector2(126f, y);
		label.Size = new Vector2(parent.Size.X - 138f, 20f);
		StyleSettingsSingleLineLabel(label, HorizontalAlignment.Right);
		label.TooltipText = value ?? "";
		parent.AddChild(label);
		return label;
	}

	private static string FriendlyMainBrainModelName(string model)
	{
		string value = (model ?? "").Trim();
		string normalized = value.ToLowerInvariant();
		if (normalized.Contains("gpt-5.6-terra"))
			return "GPT-5.6 Terra";
		if (normalized.Contains("gpt-5.6"))
			return "GPT-5.6";
		if (normalized.Contains("gpt-5.5"))
			return "GPT-5.5";
		if (normalized.Contains("gemini"))
			return value.Replace("-", " ");
		return value;
	}

	private static string CompactSettingsEndpoint(string baseUrl)
	{
		string value = (baseUrl ?? "").Trim();
		if (string.IsNullOrWhiteSpace(value))
			return "Chưa đặt";
		try
		{
			var uri = new Uri(value);
			return uri.Host;
		}
		catch
		{
			return value.Length <= 32 ? value : value.Substring(0, 29) + "…";
		}
	}

	private string MainBrainLocalCredentialSummary()
	{
		int count = CountConfiguredMainKeys();
		if (count <= 0)
			return "Chưa có key";
		return count == 1 ? "Main Brain · 1 key" : $"Main Brain · {count} key";
	}

	private void BuildSettingsQuickControlsColumn(Control parent)
	{
		_settingsTtsSwitch = AddSettingsSwitch(parent, new Vector2(parent.Size.X - 64f, 46f), _settingsTtsEnabled, () =>
		{
			_settingsTtsEnabled = !_settingsTtsEnabled;
			ApplySettingsSwitch(_settingsTtsSwitch, _settingsTtsEnabled);
			SendTtsRuntimeSettingsUpdate();
			SetSettingsStatus(_settingsTtsEnabled ? "Đã bật TTS." : "Đã tắt TTS.");
		});
		var tts = CreateUiLabel("Giọng nói", 14, SettingsInk);
		tts.Position = new Vector2(18f, 50f);
		tts.Size = new Vector2(180f, 24f);
		parent.AddChild(tts);

		_settingsLanguageSelect = AddSettingsSpeechLanguageRow(parent, 78f);
		_settingsLanguageSelect.ItemSelected += _ => ApplyLiveTtsSettingsFromPanel("ngôn ngữ");

		_settingsTtsVolumeSlider = AddSettingsSliderRow(parent, 126f, "Âm lượng", 0d, 100d, 1d, _settingsTtsVolume * 100d,
			v => $"{Mathf.RoundToInt((float)v)}%", out _settingsTtsVolumeValueLabel);
		_settingsTtsVolumeSlider.ValueChanged += value =>
		{
			_settingsTtsVolume = Mathf.Clamp((float)value / 100f, 0f, 1f);
			_settingsTtsVolumeValueLabel.Text = $"{Mathf.RoundToInt(_settingsTtsVolume * 100f)}%";
			SendTtsRuntimeSettingsUpdate();
		};

		var voiceSettings = CreateSettingsButton("Mở cài đặt giọng nói", () => SwitchSettingsMode(SettingsModePage.Voice), primary: false);
		voiceSettings.Position = new Vector2(18f, 162f);
		voiceSettings.Size = new Vector2(parent.Size.X - 36f, 32f);
		parent.AddChild(voiceSettings);

		var line = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.42f),
			Position = new Vector2(18f, 206f),
			Size = new Vector2(parent.Size.X - 36f, 1.2f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(line);

		var windowTitle = CreateUiLabel("Cửa sổ nhân vật", 13, SettingsInk);
		windowTitle.Position = new Vector2(18f, 220f);
		windowTitle.Size = new Vector2(parent.Size.X - 36f, 24f);
		parent.AddChild(windowTitle);

		AddSettingsMiniToggle(parent, new Vector2(18f, 252f), "Luôn nổi", KeepWindowAlwaysOnTop, ref _settingsAlwaysOnTopSwitch, () =>
		{
			KeepWindowAlwaysOnTop = !KeepWindowAlwaysOnTop;
			ApplySettingsSwitch(_settingsAlwaysOnTopSwitch, KeepWindowAlwaysOnTop);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(172f, 252f), "Trong suốt", UseTransparentWindow, ref _settingsTransparentSwitch, () =>
		{
			UseTransparentWindow = !UseTransparentWindow;
			ApplySettingsSwitch(_settingsTransparentSwitch, UseTransparentWindow);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(18f, 288f), "Click xuyên", EnableMousePassthrough, ref _settingsPassthroughSwitch, () =>
		{
			EnableMousePassthrough = !EnableMousePassthrough;
			ApplySettingsSwitch(_settingsPassthroughSwitch, EnableMousePassthrough);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(172f, 288f), "Khóa vị trí", LockCharacterPosition, ref _settingsPositionLockSwitch, () =>
		{
			LockCharacterPosition = !LockCharacterPosition;
			ApplySettingsSwitch(_settingsPositionLockSwitch, LockCharacterPosition);
			if (LockCharacterPosition)
				CancelGrabMode();
		});

		var hint = CreateUiLabel("Ngôn ngữ và cửa sổ áp dụng ngay. Đổi ngôn ngữ sẽ dừng tiếng đang phát.", 9, SettingsInkSoft);
		hint.Position = new Vector2(18f, 326f);
		hint.Size = new Vector2(parent.Size.X - 36f, 30f);
		hint.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(hint);

		var apply = CreateSettingsButton("Lưu giao diện", SaveSettingsState, primary: true);
		apply.Position = new Vector2(18f, 364f);
		apply.Size = new Vector2(parent.Size.X - 36f, 34f);
		parent.AddChild(apply);
	}

	private void BuildVoiceSettingsPage(Control root)
	{
		var voiceCard = AddSettingsCard(root, new Rect2(118f, 178f, 338f, 414f), "Giọng nói");
		BuildSettingsDisplayColumn(voiceCard);

		var infoCard = AddSettingsCard(root, new Rect2(476f, 178f, 606f, 414f), "Trạng thái phát");
		var intro = CreateUiLabel(
			"Voice, ngôn ngữ và chất lượng được tách khỏi Basic để khỏi biến trang mở hằng ngày thành buồng lái TTS.",
			11,
			SettingsInkSoft);
		intro.Position = new Vector2(18f, 50f);
		intro.Size = new Vector2(infoCard.Size.X - 36f, 52f);
		intro.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		infoCard.AddChild(intro);

		_settingsTtsEngineStatusLabel = AddSettingsHealthRow(infoCard, 116f, "TTS engine", "Chưa kiểm tra");
		AddSettingsHealthRow(infoCard, 150f, "Voice", _settingsTtsVoice);
		AddSettingsHealthRow(infoCard, 184f, "Ngôn ngữ", NormalizeSettingsTtsLanguage(_settingsTtsLanguage));
		AddSettingsHealthRow(infoCard, 218f, "Chất lượng", _settingsTtsQuality);

		var preview = CreateSettingsButton("▶  Nghe thử", RequestTtsPreview, primary: true);
		preview.Position = new Vector2(18f, 270f);
		preview.Size = new Vector2(176f, 36f);
		infoCard.AddChild(preview);

		var refresh = CreateSettingsButton("Đọc trạng thái Brain", TestSettingsPool, primary: false);
		refresh.Position = new Vector2(204f, 270f);
		refresh.Size = new Vector2(190f, 36f);
		infoCard.AddChild(refresh);

		var note = CreateUiLabel("TTS hỗ trợ áp dụng live. Nếu worker không nạp lại được, UI sẽ báo cần restart thay vì giả vờ mọi thứ ổn.", 10, SettingsInkSoft);
		note.Position = new Vector2(18f, 326f);
		note.Size = new Vector2(infoCard.Size.X - 36f, 56f);
		note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		infoCard.AddChild(note);

		AddSettingsPageFooter(root, "Giọng nói áp dụng live; Đóng chỉ đóng panel, không giả danh là nút Hủy.", showBackupActions: false);
	}

	private void BuildDataSettingsPage(Control root)
	{
		var brainCard = AddSettingsCard(root, new Rect2(32f, 178f, 430f, 414f), "Não chính & kết nối");
		BuildSettingsMainBrainColumn(brainCard);

		var poolCard = AddSettingsCard(root, new Rect2(480f, 178f, 668f, 414f), "Credential pools");
		BuildSettingsKeyPoolColumn(poolCard);

		AddSettingsPageFooter(
			root,
			"Main Brain và ElevenLabs TTS. Các ô Gemini/Groq được giữ trống.",
			showBackupActions: true);
	}

	private void AddSettingsPageFooter(Control root, string noteText, bool showBackupActions)
	{
		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.38f),
			Position = new Vector2(32f, 604f),
			Size = new Vector2(1116f, 1.4f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		root.AddChild(divider);

		var noteHeart = CreateUiLabel("♡", 24, SettingsPink);
		noteHeart.Position = new Vector2(52f, 624f);
		noteHeart.Size = new Vector2(32f, 30f);
		root.AddChild(noteHeart);

		var note = CreateUiLabel(noteText, 11, SettingsInkSoft);
		note.Position = new Vector2(90f, 624f);
		note.Size = new Vector2(showBackupActions ? 560f : 720f, 22f);
		StyleSettingsSingleLineLabel(note);
		root.AddChild(note);

		_settingsStatusNoteLabel = CreateUiLabel("", 10, SettingsInk);
		_settingsStatusNoteLabel.Position = new Vector2(90f, 646f);
		_settingsStatusNoteLabel.Size = new Vector2(showBackupActions ? 560f : 720f, 16f);
		StyleSettingsSingleLineLabel(_settingsStatusNoteLabel);
		root.AddChild(_settingsStatusNoteLabel);

		if (showBackupActions)
		{
			var backup = CreateSettingsButton("Sao lưu", BackupSettingsAndSecrets, primary: false);
			backup.Position = new Vector2(650f, 622f);
			backup.Size = new Vector2(88f, 38f);
			root.AddChild(backup);

			var export = CreateSettingsButton("Xuất config", ExportSanitizedSettings, primary: false);
			export.Position = new Vector2(746f, 622f);
			export.Size = new Vector2(104f, 38f);
			root.AddChild(export);

			var close = CreateSettingsButton("Đóng", CloseSettingsPanel, primary: false);
			close.Position = new Vector2(858f, 622f);
			close.Size = new Vector2(76f, 38f);
			root.AddChild(close);

			var saveCredentials = CreateSettingsButton("Lưu credential", () => SaveSettingsState(true, writeSecrets: true), primary: false);
			saveCredentials.Position = new Vector2(942f, 620f);
			saveCredentials.Size = new Vector2(104f, 42f);
			saveCredentials.AddThemeFontSizeOverride("font_size", 9);
			root.AddChild(saveCredentials);

			var restart = CreateSettingsButton("Lưu & Restart", SaveSettingsAndRestart, primary: true);
			restart.Position = new Vector2(1054f, 620f);
			restart.Size = new Vector2(94f, 42f);
			restart.AddThemeFontSizeOverride("font_size", 8);
			root.AddChild(restart);
			return;
		}

		var closeLocal = CreateSettingsButton("Đóng", CloseSettingsPanel, primary: false);
		closeLocal.Position = new Vector2(970f, 622f);
		closeLocal.Size = new Vector2(76f, 38f);
		root.AddChild(closeLocal);

		var saveLocal = CreateSettingsButton("Lưu Body", SaveSettingsState, primary: true);
		saveLocal.Position = new Vector2(1054f, 620f);
		saveLocal.Size = new Vector2(94f, 42f);
		saveLocal.AddThemeFontSizeOverride("font_size", 9);
		root.AddChild(saveLocal);
	}

	private void BuildSettingsConnectionOverlay(Control root)
	{
		_settingsConnectionOverlay = new Control
		{
			Visible = false,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		_settingsConnectionOverlay.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		root.AddChild(_settingsConnectionOverlay);

		var dim = new ColorRect
		{
			Color = new Color(0.24f, 0.05f, 0.15f, 0.22f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		dim.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		dim.GuiInput += ev =>
		{
			if (ev is InputEventMouseButton mouse && mouse.ButtonIndex == MouseButton.Left && mouse.Pressed)
				HideSettingsConnectionEditor();
		};
		_settingsConnectionOverlay.AddChild(dim);

		var card = new Panel
		{
			Position = new Vector2(260f, 154f),
			Size = new Vector2(660f, 382f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		card.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1.00f, 0.97f, 0.985f, 0.985f),
			new Color(0.96f, 0.62f, 0.79f, 0.92f),
			20));
		_settingsConnectionOverlay.AddChild(card);

		var title = CreateUiLabel("Não chính & kết nối", 22, SettingsInk);
		title.Position = new Vector2(22f, 16f);
		title.Size = new Vector2(300f, 32f);
		card.AddChild(title);

		var close = CreateSettingsButton("×", HideSettingsConnectionEditor, primary: false);
		close.Position = new Vector2(608f, 14f);
		close.Size = new Vector2(34f, 30f);
		close.AddThemeFontSizeOverride("font_size", 16);
		card.AddChild(close);

		var hint = CreateUiLabel("Đây là route điều khiển toàn bộ hội thoại chính: provider + model + pool Main Brain.", 10, SettingsInkSoft);
		hint.Position = new Vector2(24f, 50f);
		hint.Size = new Vector2(590f, 22f);
		StyleSettingsSingleLineLabel(hint);
		card.AddChild(hint);

		_settingsProviderSelect = AddSettingsOptionRow(card, 80f, "Provider", new[] { "deepseek_web", "deepseek", "openai_compatible" }, RemoteApiProvider);
		_settingsModelInput = AddSettingsLineEditRow(card, 124f, "Model", RemoteApiModelName);
		_settingsBaseUrlInput = AddSettingsLineEditRow(card, 168f, "Base URL", RemoteApiBaseUrl);

		_settingsProviderSelect.ItemSelected += _ =>
		{
			RemoteApiProvider = SelectedOptionText(_settingsProviderSelect, "deepseek_web");
			ApplyProviderDefaults(RemoteApiProvider);
			RefreshSettingsProviderInputs();
			RefreshSettingsRuntimeModelSummary();
			SetSettingsConnectionStatus("Đã nạp preset provider. Bấm Áp dụng & Restart để Brain dùng cấu hình mới.");
		};
		_settingsModelInput.TextChanged += _ =>
		{
			SyncSettingsInputsToFields();
			RefreshSettingsRuntimeModelSummary();
			SetSettingsConnectionStatus("Model đã thay đổi, chưa áp dụng xuống Brain.");
		};
		_settingsBaseUrlInput.TextChanged += _ =>
		{
			SyncSettingsInputsToFields();
			RefreshSettingsRuntimeModelSummary();
			SetSettingsConnectionStatus("Base URL đã thay đổi, chưa áp dụng xuống Brain.");
		};

		var infoPanel = new Panel
		{
			Position = new Vector2(310f, 80f),
			Size = new Vector2(326f, 126f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		infoPanel.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1f, 0.985f, 0.995f, 0.82f), SettingsBorder, 14));
		card.AddChild(infoPanel);

		var infoTitle = CreateUiLabel("Trạng thái cấu hình", 12, SettingsInk);
		infoTitle.Position = new Vector2(14f, 10f);
		infoTitle.Size = new Vector2(250f, 22f);
		StyleSettingsSingleLineLabel(infoTitle);
		infoPanel.AddChild(infoTitle);

		_settingsConnectionFeedbackLabel = CreateUiLabel("Chưa có thay đổi.", 10, SettingsInkSoft);
		_settingsConnectionFeedbackLabel.Position = new Vector2(14f, 36f);
		_settingsConnectionFeedbackLabel.Size = new Vector2(298f, 72f);
		_settingsConnectionFeedbackLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		_settingsConnectionFeedbackLabel.VerticalAlignment = VerticalAlignment.Top;
		infoPanel.AddChild(_settingsConnectionFeedbackLabel);

		var validate = CreateSettingsButton("Kiểm tra cục bộ", TestSettingsConnectionEditor, primary: false);
		validate.Position = new Vector2(310f, 216f);
		validate.Size = new Vector2(150f, 32f);
		card.AddChild(validate);

		var liveTest = CreateSettingsButton("Kiểm tra request thật", RequestMainBrainConnectionTest, primary: true);
		liveTest.Position = new Vector2(468f, 216f);
		liveTest.Size = new Vector2(168f, 32f);
		liveTest.AddThemeFontSizeOverride("font_size", 9);
		card.AddChild(liveTest);

		var logs = CreateSettingsButton("Mở logs", OpenSettingsLogs, primary: false);
		logs.Position = new Vector2(310f, 258f);
		logs.Size = new Vector2(150f, 32f);
		card.AddChild(logs);

		var cancel = CreateSettingsButton("Đóng", HideSettingsConnectionEditor, primary: false);
		cancel.Position = new Vector2(468f, 258f);
		cancel.Size = new Vector2(80f, 32f);
		card.AddChild(cancel);

		var save = CreateSettingsButton("Lưu", () =>
		{
			SaveSettingsState(false, writeSecrets: true);
			RefreshSettingsRuntimeModelSummary();
			SetSettingsConnectionStatus("Đã lưu route não chính. Brain đang chạy vẫn dùng route cũ tới khi restart.");
		}, primary: false);
		save.Position = new Vector2(556f, 258f);
		save.Size = new Vector2(80f, 32f);
		card.AddChild(save);

		var apply = CreateSettingsButton("Lưu route & Restart Brain", () =>
		{
			SaveSettingsState(false, writeSecrets: true);
			RestartVisionProcess();
			RefreshSettingsRuntimeModelSummary();
			HideSettingsConnectionEditor();
			SetSettingsStatus("Đã lưu provider/model/pool Main Brain và khởi động lại Brain.");
		}, primary: true);
		apply.Position = new Vector2(310f, 304f);
		apply.Size = new Vector2(326f, 40f);
		apply.AddThemeFontSizeOverride("font_size", 10);
		card.AddChild(apply);
	}

	private void ShowSettingsConnectionEditor()
	{
		if (_settingsConnectionOverlay == null)
			return;
		RefreshSettingsProviderInputs();
		SetSettingsConnectionStatus(_processes != null && _processes.IsVisionRunning
			? "Brain đang chạy. Thay đổi provider/model cần Restart để áp dụng."
			: "Brain đang dừng. Lưu cấu hình rồi khởi động khi sẵn sàng.");
		_settingsConnectionOverlay.Visible = true;
		_settingsConnectionOverlay.MoveToFront();
	}

	private void HideSettingsConnectionEditor()
	{
		if (_settingsConnectionOverlay != null)
			_settingsConnectionOverlay.Visible = false;
	}

	private void SetSettingsConnectionStatus(string text)
	{
		if (_settingsConnectionFeedbackLabel != null)
			_settingsConnectionFeedbackLabel.Text = text ?? "";
	}

	private void TestSettingsConnectionEditor()
	{
		SyncSettingsInputsToFields();
		var problems = new List<string>();
		if (string.IsNullOrWhiteSpace(RemoteApiProvider))
			problems.Add("thiếu provider");
		if (string.IsNullOrWhiteSpace(RemoteApiModelName))
			problems.Add("thiếu model");
		if (!string.Equals(RemoteApiProvider, "gemini", StringComparison.OrdinalIgnoreCase)
			&& string.IsNullOrWhiteSpace(RemoteApiBaseUrl))
			problems.Add("thiếu Base URL");

		int keys = CountConfiguredMainKeys();
		if (keys <= 0)
			problems.Add("pool Main Brain chưa có key");

		if (problems.Count > 0)
		{
			SetSettingsConnectionStatus("Chưa ổn: " + string.Join(", ", problems) + ".");
			return;
		}

		string runtime = _processes != null && _processes.IsVisionRunning
			? $"Brain đang chạy PID {_processes.VisionProcessId}."
			: "Brain đang dừng.";
		SetSettingsConnectionStatus($"Cấu hình cục bộ hợp lệ: {DisplaySettingsProviderName(RemoteApiProvider)}, {FriendlyMainBrainModelName(RemoteApiModelName)}, Main Brain {keys} key. {runtime}");
	}

	private string DisplaySettingsProviderName(string provider)
	{
		string normalized = (provider ?? "").Trim().ToLowerInvariant();
		return normalized switch
		{
			"gemini" => "Gemini",
			"groq" => "Groq",
			"openai_compatible" => (RemoteApiBaseUrl ?? "").Contains("shopaikey", StringComparison.OrdinalIgnoreCase)
				? "ShopAIKey / OpenAI-compatible"
				: "OpenAI-compatible",
			_ => string.IsNullOrWhiteSpace(provider) ? "Chưa chọn" : provider,
		};
	}

	private void RequestMainBrainConnectionTest()
	{
		SyncSettingsInputsToFields();
		if (_processes == null || !_processes.IsVisionRunning)
		{
			SetSettingsConnectionStatus("Brain đang dừng. Lưu route rồi khởi động Brain trước khi gửi request thật.");
			SetSettingsStatus("Không thể kiểm tra não chính vì Brain đang dừng.");
			return;
		}
		if (_chatSend == null)
		{
			SetSettingsConnectionStatus("Kênh UDP chưa sẵn sàng.");
			return;
		}

		string requestId = $"settings_main_{Time.GetTicksMsec()}";
		var payload = new { type = "settings_connection_test", request_id = requestId };
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		if (error == Error.Ok)
		{
			SetSettingsConnectionStatus("Đang gửi request 8 token bằng đúng client Main Brain đang hiệu lực…");
			SetSettingsStatus("Đang kiểm tra não chính bằng request thật.");
		}
		else
		{
			SetSettingsConnectionStatus($"Không gửi được yêu cầu kiểm tra: {error}");
		}
	}

	private void ApplyMainBrainHealthSnapshot(JsonElement snapshot)
	{
		if (snapshot.ValueKind != JsonValueKind.Object)
			return;

		bool ready = snapshot.TryGetProperty("ready", out JsonElement readyEl) && readyEl.ValueKind == JsonValueKind.True;
		string provider = snapshot.TryGetProperty("requested_provider", out JsonElement requestedEl)
			? requestedEl.GetString() ?? ""
			: snapshot.TryGetProperty("provider", out JsonElement providerEl) ? providerEl.GetString() ?? "" : "";
		string model = snapshot.TryGetProperty("model", out JsonElement modelEl) ? modelEl.GetString() ?? "" : "";
		string baseUrl = snapshot.TryGetProperty("base_url", out JsonElement urlEl) ? urlEl.GetString() ?? "" : "";
		string source = snapshot.TryGetProperty("config_source", out JsonElement sourceEl) ? sourceEl.GetString() ?? "runtime" : "runtime";
		string fingerprint = snapshot.TryGetProperty("active_key_fingerprint", out JsonElement fpEl) ? fpEl.GetString() ?? "" : "";
		int configured = snapshot.TryGetProperty("configured_keys", out JsonElement configuredEl) && configuredEl.TryGetInt32(out int parsedConfigured) ? parsedConfigured : 0;
		int available = snapshot.TryGetProperty("available_keys", out JsonElement availableEl) && availableEl.TryGetInt32(out int parsedAvailable) ? parsedAvailable : 0;
		string fallback = snapshot.TryGetProperty("fallback_model", out JsonElement fallbackEl) ? fallbackEl.GetString() ?? "" : "";

		if (_settingsRuntimeProviderSummaryLabel != null)
			_settingsRuntimeProviderSummaryLabel.Text = DisplaySettingsProviderName(string.IsNullOrWhiteSpace(provider) ? RemoteApiProvider : provider);
		if (_settingsRuntimeModelSummaryLabel != null && !string.IsNullOrWhiteSpace(model))
		{
			_settingsRuntimeModelSummaryLabel.Text = FriendlyMainBrainModelName(model);
			_settingsRuntimeModelSummaryLabel.TooltipText = model;
		}
		if (_settingsMainBrainEndpointLabel != null)
			_settingsMainBrainEndpointLabel.Text = CompactSettingsEndpoint(string.IsNullOrWhiteSpace(baseUrl) ? RemoteApiBaseUrl : baseUrl);
		if (_settingsMainBrainKeyLabel != null)
		{
			string active = string.IsNullOrWhiteSpace(fingerprint) ? "chưa có key active" : $"active …{fingerprint.Substring(Math.Max(0, fingerprint.Length - 6))}";
			_settingsMainBrainKeyLabel.Text = $"{available}/{configured} sẵn sàng · {active}";
			_settingsMainBrainKeyLabel.TooltipText = $"Pool Main Brain · fingerprint {fingerprint}";
		}
		if (_settingsSystemAiHealthLabel != null)
			_settingsSystemAiHealthLabel.Text = ready ? "Sẵn sàng" : "Chưa sẵn sàng";
		if (_settingsMainBrainLiveLabel != null)
		{
			string fallbackText = string.IsNullOrWhiteSpace(fallback) ? "" : $" · fallback {fallback}";
			_settingsMainBrainLiveLabel.Text = ready
				? $"Runtime: {FriendlyMainBrainModelName(model)} · nguồn {source}{fallbackText}"
				: "Runtime: client não chính chưa sẵn sàng";
			_settingsMainBrainLiveLabel.TooltipText = string.IsNullOrWhiteSpace(baseUrl) ? source : $"{baseUrl}\n{source}";
		}
	}

	private static string FriendlyMainBrainConnectionError(string kind, string detail)
	{
		string message = kind switch
		{
			"invalid_key" => "Key Main Brain không hợp lệ hoặc không có quyền.",
			"model_or_endpoint_not_found" => "Không tìm thấy model hoặc endpoint.",
			"rate_limited" => "Provider đang giới hạn quota/rate limit.",
			"provider_unavailable" => "Provider đang lỗi phía máy chủ.",
			"timeout" => "Provider không phản hồi trước timeout.",
			_ => "Không kết nối được với não chính.",
		};
		return string.IsNullOrWhiteSpace(detail) ? message : $"{message} {detail}";
	}


	private void BuildSettingsKeyPoolColumn(Control parent)
	{
		if (string.IsNullOrWhiteSpace(_settingsKeyPoolKind))
			_settingsKeyPoolKind = "main";
		else
			_settingsKeyPoolKind = NormalizeSettingsPoolKind(_settingsKeyPoolKind);

		int mainCount = _settingsKeyDrafts.TryGetValue("main", out List<string> mainRows)
			? NormalizeSettingsKeyRows(mainRows).Count
			: ReadEditableKeysForPool("main").Count;
		int geminiCount = _settingsKeyDrafts.TryGetValue("gemini", out List<string> geminiRows)
			? NormalizeSettingsKeyRows(geminiRows).Count
			: ReadEditableKeysForPool("gemini").Count;
		int groqCount = _settingsKeyDrafts.TryGetValue("groq", out List<string> groqRows)
			? NormalizeSettingsKeyRows(groqRows).Count
			: ReadEditableKeysForPool("groq").Count;
		int elevenLabsCount = _settingsKeyDrafts.TryGetValue("elevenlabs", out List<string> elevenLabsRows)
			? NormalizeSettingsKeyRows(elevenLabsRows).Count
			: ReadEditableKeysForPool("elevenlabs").Count;

		float innerWidth = parent.Size.X - 36f;
		float tabGap = 6f;
		float tabWidth = (innerWidth - tabGap * 3f) / 4f;

		_settingsMainPoolStatusLabel = CreateSettingsButton($"Main · {mainCount}", () => SwitchSettingsKeyPool("main"), primary: _settingsKeyPoolKind == "main");
		_settingsMainPoolStatusLabel.Position = new Vector2(18f, 46f);
		_settingsMainPoolStatusLabel.Size = new Vector2(tabWidth, 30f);
		_settingsMainPoolStatusLabel.TooltipText = "Credential của Main Brain / remote_main";
		parent.AddChild(_settingsMainPoolStatusLabel);

		_settingsPoolStatusLabel = CreateSettingsButton($"Gemini · {geminiCount}", () => SwitchSettingsKeyPool("gemini"), primary: _settingsKeyPoolKind == "gemini");
		_settingsPoolStatusLabel.Position = new Vector2(18f + tabWidth + tabGap, 46f);
		_settingsPoolStatusLabel.Size = new Vector2(tabWidth, 30f);
		_settingsPoolStatusLabel.TooltipText = "Ô key trống; provider đã ngừng sử dụng.";
		_settingsPoolStatusLabel.Disabled = true;
		parent.AddChild(_settingsPoolStatusLabel);

		_settingsFallbackStatusLabel = CreateSettingsButton($"Groq · {groqCount}", () => SwitchSettingsKeyPool("groq"), primary: _settingsKeyPoolKind == "groq");
		_settingsFallbackStatusLabel.Position = new Vector2(18f + (tabWidth + tabGap) * 2f, 46f);
		_settingsFallbackStatusLabel.Size = new Vector2(tabWidth, 30f);
		_settingsFallbackStatusLabel.TooltipText = "Ô key trống; provider đã ngừng sử dụng.";
		_settingsFallbackStatusLabel.Disabled = true;
		parent.AddChild(_settingsFallbackStatusLabel);

		_settingsElevenLabsPoolStatusLabel = CreateSettingsButton($"11Labs · {elevenLabsCount}", () => SwitchSettingsKeyPool("elevenlabs"), primary: _settingsKeyPoolKind == "elevenlabs");
		_settingsElevenLabsPoolStatusLabel.Position = new Vector2(18f + (tabWidth + tabGap) * 3f, 46f);
		_settingsElevenLabsPoolStatusLabel.Size = new Vector2(tabWidth, 30f);
		_settingsElevenLabsPoolStatusLabel.TooltipText = "Credential riêng của provider TTS ElevenLabs";
		parent.AddChild(_settingsElevenLabsPoolStatusLabel);

		_settingsPoolHealthLabel = CreateUiLabel("Chưa đọc trạng thái runtime", 10, SettingsInkSoft);
		_settingsPoolHealthLabel.Position = new Vector2(20f, 80f);
		_settingsPoolHealthLabel.Size = new Vector2(innerWidth - 4f, 18f);
		StyleSettingsSingleLineLabel(_settingsPoolHealthLabel);
		parent.AddChild(_settingsPoolHealthLabel);

		var nameHeader = CreateUiLabel("Tên key", 9, SettingsInkSoft);
		nameHeader.Position = new Vector2(28f, 100f);
		nameHeader.Size = new Vector2(170f, 16f);
		StyleSettingsSingleLineLabel(nameHeader);
		parent.AddChild(nameHeader);

		var keyHeader = CreateUiLabel("API key", 9, SettingsInkSoft);
		keyHeader.Position = new Vector2(216f, 100f);
		keyHeader.Size = new Vector2(220f, 16f);
		StyleSettingsSingleLineLabel(keyHeader);
		parent.AddChild(keyHeader);

		var actionHeader = CreateUiLabel("Trạng thái / ưu tiên", 9, SettingsInkSoft);
		actionHeader.Position = new Vector2(parent.Size.X - 180f, 100f);
		actionHeader.Size = new Vector2(150f, 16f);
		StyleSettingsSingleLineLabel(actionHeader, HorizontalAlignment.Right);
		parent.AddChild(actionHeader);

		var list = new Panel
		{
			Position = new Vector2(18f, 118f),
			Size = new Vector2(innerWidth, 190f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		list.AddThemeStyleboxOverride("panel", SettingsCardStyle(new Color(1f, 0.98f, 0.99f, 0.72f), SettingsBorder, 14));
		parent.AddChild(list);

		_settingsKeyNameInputs.Clear();
		_settingsKeyInputs.Clear();
		_settingsKeyStatusLabels.Clear();
		var scroller = new ScrollContainer
		{
			Position = new Vector2(6f, 6f),
			Size = new Vector2(innerWidth - 12f, 178f),
			HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
			VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		list.AddChild(scroller);

		_settingsKeyRowsBox = new VBoxContainer
		{
			CustomMinimumSize = new Vector2(innerWidth - 26f, 0f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		_settingsKeyRowsBox.AddThemeConstantOverride("separation", 6);
		scroller.AddChild(_settingsKeyRowsBox);

		var keys = ReadSettingsKeysForEditor(_settingsKeyPoolKind);
		var names = ReadSettingsKeyNamesForEditor(_settingsKeyPoolKind, keys);
		for (int i = 0; i < keys.Count; i++)
			AddSettingsEditableKeyRow(keys[i], i < names.Count ? names[i] : "");

		if (keys.Count == 0)
			AddSettingsEditableKeyRow("", DefaultSettingsKeyName(_settingsKeyPoolKind, 1));

		float buttonY = 318f;
		var add = CreateSettingsButton("＋ Thêm key", () =>
		{
			int next = _settingsKeyInputs.Count + 1;
			AddSettingsEditableKeyRow("", DefaultSettingsKeyName(_settingsKeyPoolKind, next));
			if (_settingsKeyNameInputs.Count > 0)
			{
				LineEdit newestName = _settingsKeyNameInputs[_settingsKeyNameInputs.Count - 1];
				newestName.GrabFocus();
				newestName.SelectAll();
			}
			CaptureCurrentSettingsKeyDraft();
			RefreshSettingsPoolSelectorCounts();
			SetSettingsStatus("Đã thêm key vào bản nháp. Áp dụng & Restart để Brain nạp pool mới.");
		}, primary: true);
		add.Position = new Vector2(18f, buttonY);
		add.Size = new Vector2(132f, 30f);
		add.Disabled = _settingsKeyPoolKind == "elevenlabs" && _settingsKeyInputs.Count >= 1;
		add.TooltipText = _settingsKeyPoolKind == "elevenlabs"
			? "ElevenLabs hiện dùng một credential; sửa trực tiếp dòng đang có."
			: "Thêm credential fallback vào pool.";
		parent.AddChild(add);

		var import = CreateSettingsButton("Nhập secrets…", OpenImportSecretsDialog, primary: false);
		import.Position = new Vector2(158f, buttonY);
		import.Size = new Vector2(132f, 30f);
		import.TooltipText = "Nhập secrets.json; file cũ sẽ được sao lưu";
		parent.AddChild(import);

		var reload = CreateSettingsButton("↻ Bỏ bản nháp", () =>
		{
			_settingsKeyDrafts.Remove(_settingsKeyPoolKind);
			_settingsKeyNameDrafts.Remove(_settingsKeyPoolKind);
			_settingsSkipKeyDraftCaptureOnce = true;
			BuildSettingsPanelUi();
			SetSettingsStatus($"Đã đọc lại pool {DisplaySettingsPoolName(_settingsKeyPoolKind)} từ secrets.");
		}, primary: false);
		reload.Position = new Vector2(298f, buttonY);
		reload.Size = new Vector2(140f, 30f);
		reload.TooltipText = "Bỏ thay đổi chưa lưu của pool đang mở";
		parent.AddChild(reload);

		var test = CreateSettingsButton("Kiểm tra runtime", TestSettingsPool, primary: false);
		test.Position = new Vector2(parent.Size.X - 166f, buttonY);
		test.Size = new Vector2(148f, 30f);
		test.TooltipText = "Đọc key đang dùng, key sẵn sàng và cooldown từ Brain";
		parent.AddChild(test);

		var hint = CreateUiLabel(
			_settingsKeyPoolKind == "main"
				? "Pool Main Brain gắn trực tiếp với provider/model ở card bên trái."
				: _settingsKeyPoolKind == "elevenlabs"
					? "ElevenLabs sở hữu credential TTS riêng; chỉ key ưu tiên 1 được runtime sử dụng."
					: "Pool phụ không thay model chính; chúng chỉ phục vụ lane chuyên biệt.",
			9,
			SettingsInkSoft);
		hint.Position = new Vector2(20f, 356f);
		hint.Size = new Vector2(innerWidth - 4f, 36f);
		hint.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(hint);

		RefreshSettingsKeyRowHealthIndicators();
	}

	private void SwitchSettingsKeyPool(string requestedKind)
	{
		string next = NormalizeSettingsPoolKind(requestedKind);
		if (next == _settingsKeyPoolKind)
			return;

		CaptureCurrentSettingsKeyDraft();
		_settingsKeyPoolKind = next;
		_settingsSkipKeyDraftCaptureOnce = true;
		BuildSettingsPanelUi();
		SetSettingsStatus($"Đang chỉnh pool {DisplaySettingsPoolName(next)}. Bản nháp pool trước vẫn được giữ.");
	}

	private static string NormalizeSettingsPoolKind(string poolKind)
	{
		string kind = (poolKind ?? "").Trim().ToLowerInvariant();
		if (kind is "main" or "main_brain" or "remote_main" or "remote")
			return "main";
		if (kind is "elevenlabs" or "eleven" or "11labs" or "tts_elevenlabs")
			return "elevenlabs";
		return IsGroqPool(kind) ? "groq" : "gemini";
	}

	private static string DisplaySettingsPoolName(string poolKind)
	{
		return NormalizeSettingsPoolKind(poolKind) switch
		{
			"main" => "Main Brain",
			"groq" => "Groq phụ",
			"elevenlabs" => "ElevenLabs TTS",
			_ => "Gemini phụ",
		};
	}

	private void CaptureCurrentSettingsKeyDraft()
	{
		if (_settingsKeyInputs.Count == 0 || string.IsNullOrWhiteSpace(_settingsKeyPoolKind))
			return;

		var rows = new List<string>();
		var names = new List<string>();
		for (int i = 0; i < _settingsKeyInputs.Count; i++)
		{
			LineEdit input = _settingsKeyInputs[i];
			LineEdit nameInput = i < _settingsKeyNameInputs.Count ? _settingsKeyNameInputs[i] : null;
			rows.Add(input != null && IsInstanceValid(input) ? input.Text ?? "" : "");
			names.Add(nameInput != null && IsInstanceValid(nameInput) ? nameInput.Text ?? "" : "");
		}

		string kind = NormalizeSettingsPoolKind(_settingsKeyPoolKind);
		_settingsKeyDrafts[kind] = rows;
		_settingsKeyNameDrafts[kind] = names;
	}

	private List<string> ReadSettingsKeysForEditor(string poolKind)
	{
		string kind = NormalizeSettingsPoolKind(poolKind);
		if (_settingsKeyDrafts.TryGetValue(kind, out List<string> draft))
			return new List<string>(draft);

		var loaded = ReadEditableKeysForPool(kind);
		_settingsKeyDrafts[kind] = new List<string>(loaded);
		return loaded;
	}

	private List<string> ReadSettingsKeyNamesForEditor(string poolKind, List<string> keys)
	{
		string kind = NormalizeSettingsPoolKind(poolKind);
		if (_settingsKeyNameDrafts.TryGetValue(kind, out List<string> draft))
		{
			var copy = new List<string>(draft);
			while (copy.Count < keys.Count)
				copy.Add(DefaultSettingsKeyName(kind, copy.Count + 1));
			return copy;
		}

		var names = new List<string>();
		for (int i = 0; i < keys.Count; i++)
		{
			string fingerprint = SettingsKeyFingerprint(keys[i]);
			string aliasKey = SettingsKeyAliasMapKey(kind, fingerprint);
			string fallback = DefaultSettingsKeyName(kind, i + 1);
			names.Add(!string.IsNullOrWhiteSpace(fingerprint) && _settingsKeyAliases.TryGetValue(aliasKey, out string alias) && !string.IsNullOrWhiteSpace(alias)
				? alias.Trim()
				: fallback);
		}
		_settingsKeyNameDrafts[kind] = new List<string>(names);
		return names;
	}

	private static string DefaultSettingsKeyName(string poolKind, int rowNumber)
	{
		string provider = NormalizeSettingsPoolKind(poolKind) switch
		{
			"main" => "Main Brain",
			"groq" => "Groq",
			"elevenlabs" => "ElevenLabs",
			_ => "Gemini",
		};
		return $"{provider} {Math.Max(1, rowNumber):00}";
	}

	private static string SettingsKeyAliasMapKey(string poolKind, string fingerprint)
	{
		return $"{NormalizeSettingsPoolKind(poolKind)}:{(fingerprint ?? "").Trim().ToLowerInvariant()}";
	}

	private void RefreshSettingsPoolSelectorCounts()
	{
		int main = _settingsKeyDrafts.TryGetValue("main", out List<string> mainRows)
			? NormalizeSettingsKeyRows(mainRows).Count
			: ReadEditableKeysForPool("main").Count;
		int gemini = _settingsKeyDrafts.TryGetValue("gemini", out List<string> geminiRows)
			? NormalizeSettingsKeyRows(geminiRows).Count
			: ReadEditableKeysForPool("gemini").Count;
		int groq = _settingsKeyDrafts.TryGetValue("groq", out List<string> groqRows)
			? NormalizeSettingsKeyRows(groqRows).Count
			: ReadEditableKeysForPool("groq").Count;
		int elevenLabs = _settingsKeyDrafts.TryGetValue("elevenlabs", out List<string> elevenLabsRows)
			? NormalizeSettingsKeyRows(elevenLabsRows).Count
			: ReadEditableKeysForPool("elevenlabs").Count;

		if (_settingsMainPoolStatusLabel != null)
			_settingsMainPoolStatusLabel.Text = $"Main · {main}";
		if (_settingsPoolStatusLabel != null)
			_settingsPoolStatusLabel.Text = $"Gemini · {gemini}";
		if (_settingsFallbackStatusLabel != null)
			_settingsFallbackStatusLabel.Text = $"Groq · {groq}";
		if (_settingsElevenLabsPoolStatusLabel != null)
			_settingsElevenLabsPoolStatusLabel.Text = $"11Labs · {elevenLabs}";
	}


	private void BuildSettingsDisplayColumn(Control parent)
	{
		_settingsTtsSwitch = AddSettingsSwitch(parent, new Vector2(234f, 44f), _settingsTtsEnabled, () =>
		{
			_settingsTtsEnabled = !_settingsTtsEnabled;
			ApplySettingsSwitch(_settingsTtsSwitch, _settingsTtsEnabled);
			SendTtsRuntimeSettingsUpdate();
			SetSettingsStatus(_settingsTtsEnabled ? "Đã bật TTS." : "Đã tắt TTS.");
		});
		var tts = CreateUiLabel("Bật TTS", 14, SettingsInk);
		tts.Position = new Vector2(18f, 50f);
		tts.Size = new Vector2(160f, 24f);
		parent.AddChild(tts);

		_settingsVoiceSelect = AddSettingsOptionRow(parent, 78f, "Giọng", new[] { "ElevenLabs" }, _settingsTtsVoice);
		_settingsLanguageSelect = AddSettingsSpeechLanguageRow(parent, 118f);
		_settingsQualitySelect = AddSettingsOptionRow(parent, 158f, "Chất lượng", new[] { "balanced", "high" }, _settingsTtsQuality);
		_settingsVoiceSelect.ItemSelected += _ => ApplyLiveTtsSettingsFromPanel("giọng");
		_settingsLanguageSelect.ItemSelected += _ => ApplyLiveTtsSettingsFromPanel("ngôn ngữ");
		_settingsQualitySelect.ItemSelected += _ => ApplyLiveTtsSettingsFromPanel("chất lượng");

		var preview = CreateSettingsButton("▶ Nghe thử", RequestTtsPreview, primary: false);
		preview.Position = new Vector2(18f, 198f);
		preview.Size = new Vector2(98f, 28f);
		parent.AddChild(preview);
		_settingsTtsEngineStatusLabel = CreateUiLabel("Engine: chưa kiểm tra", 9, SettingsInkSoft);
		_settingsTtsEngineStatusLabel.Position = new Vector2(124f, 201f);
		_settingsTtsEngineStatusLabel.Size = new Vector2(158f, 22f);
		StyleSettingsSingleLineLabel(_settingsTtsEngineStatusLabel);
		parent.AddChild(_settingsTtsEngineStatusLabel);

		_settingsTtsVolumeSlider = AddSettingsSliderRow(parent, 228f, "Âm lượng", 0d, 100d, 1d, _settingsTtsVolume * 100d,
			v => $"{Mathf.RoundToInt((float)v)}%", out _settingsTtsVolumeValueLabel);
		_settingsTtsVolumeSlider.ValueChanged += value =>
		{
			_settingsTtsVolume = Mathf.Clamp((float)value / 100f, 0f, 1f);
			_settingsTtsVolumeValueLabel.Text = $"{Mathf.RoundToInt(_settingsTtsVolume * 100f)}%";
			SendTtsRuntimeSettingsUpdate();
		};

		_settingsTtsSpeedSlider = AddSettingsSliderRow(parent, 256f, "Tốc độ", 0.85d, 1.15d, 0.05d, _settingsTtsSpeed,
			v => $"{v:0.00}×", out _settingsTtsSpeedValueLabel);
		_settingsTtsSpeedSlider.ValueChanged += value =>
		{
			_settingsTtsSpeed = Mathf.Clamp((float)value, 0.85f, 1.15f);
			_settingsTtsSpeedValueLabel.Text = $"{_settingsTtsSpeed:0.00}×";
			SendTtsRuntimeSettingsUpdate();
		};

		var line = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.45f),
			Position = new Vector2(18f, 286f),
			Size = new Vector2(264f, 1.2f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(line);

		AddSettingsMiniToggle(parent, new Vector2(18f, 294f), "Luôn nổi", KeepWindowAlwaysOnTop, ref _settingsAlwaysOnTopSwitch, () =>
		{
			KeepWindowAlwaysOnTop = !KeepWindowAlwaysOnTop;
			ApplySettingsSwitch(_settingsAlwaysOnTopSwitch, KeepWindowAlwaysOnTop);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(150f, 294f), "Trong suốt", UseTransparentWindow, ref _settingsTransparentSwitch, () =>
		{
			UseTransparentWindow = !UseTransparentWindow;
			ApplySettingsSwitch(_settingsTransparentSwitch, UseTransparentWindow);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(18f, 318f), "Click xuyên", EnableMousePassthrough, ref _settingsPassthroughSwitch, () =>
		{
			EnableMousePassthrough = !EnableMousePassthrough;
			ApplySettingsSwitch(_settingsPassthroughSwitch, EnableMousePassthrough);
			ConfigureDesktopOverlayWindow();
		});
		AddSettingsMiniToggle(parent, new Vector2(150f, 318f), "Khóa vị trí", LockCharacterPosition, ref _settingsPositionLockSwitch, () =>
		{
			LockCharacterPosition = !LockCharacterPosition;
			ApplySettingsSwitch(_settingsPositionLockSwitch, LockCharacterPosition);
			if (LockCharacterPosition)
				CancelGrabMode();
		});
	}


	private void AddSettingsCompactToggle(Control parent, float y, string label, bool initial, ref TextureRect target, Action onToggle)
	{
		var lbl = CreateUiLabel(label, 10, SettingsInk);
		lbl.Position = new Vector2(18f, y + 1f);
		lbl.Size = new Vector2(178f, 22f);
		StyleSettingsSingleLineLabel(lbl);
		parent.AddChild(lbl);
		target = AddSettingsSwitch(parent, new Vector2(238f, y - 2f), initial, onToggle);
		target.Size = new Vector2(44f, 26f);
		target.CustomMinimumSize = target.Size;
	}

	private void AddSettingsMiniToggle(Control parent, Vector2 pos, string label, bool initial, ref TextureRect target, Action onToggle)
	{
		var lbl = CreateUiLabel(label, 9, SettingsInk);
		lbl.Position = pos;
		lbl.Size = new Vector2(82f, 20f);
		StyleSettingsSingleLineLabel(lbl);
		parent.AddChild(lbl);
		target = AddSettingsSwitch(parent, pos + new Vector2(88f, -2f), initial, onToggle);
		target.Size = new Vector2(38f, 22f);
		target.CustomMinimumSize = target.Size;
	}

	private HSlider AddSettingsSliderRow(
		Control parent, float y, string label, double min, double max, double step, double value,
		Func<double, string> formatter, out Label valueLabel)
	{
		var title = CreateUiLabel(label, 9, SettingsInk);
		title.Position = new Vector2(18f, y + 1f);
		title.Size = new Vector2(62f, 22f);
		StyleSettingsSingleLineLabel(title);
		parent.AddChild(title);

		var slider = new HSlider
		{
			Position = new Vector2(78f, y),
			Size = new Vector2(142f, 24f),
			MinValue = min,
			MaxValue = max,
			Step = step,
			Value = value,
			MouseFilter = Control.MouseFilterEnum.Stop,
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
		};
		parent.AddChild(slider);

		valueLabel = CreateUiLabel(formatter(value), 9, SettingsInkSoft);
		valueLabel.Position = new Vector2(224f, y + 1f);
		valueLabel.Size = new Vector2(58f, 22f);
		StyleSettingsSingleLineLabel(valueLabel, HorizontalAlignment.Right);
		parent.AddChild(valueLabel);
		return slider;
	}

	private LineEdit AddSettingsLineEditRow(Control parent, float y, string label, string value)
	{
		var row = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(264f, 38f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(new Color(1f, 0.98f, 0.99f, 0.62f), SettingsBorder, 12));
		parent.AddChild(row);

		var left = CreateUiLabel(label, 10, SettingsInkSoft);
		left.Position = new Vector2(12f, 8f);
		left.Size = new Vector2(76f, 22f);
		StyleSettingsSingleLineLabel(left);
		row.AddChild(left);

		var input = new LineEdit
		{
			Text = value ?? "",
			Position = new Vector2(90f, 5f),
			Size = new Vector2(162f, 28f),
			MouseFilter = Control.MouseFilterEnum.Stop,
			PlaceholderText = label,
			Flat = true,
		};
		input.AddThemeColorOverride("font_color", SettingsInk);
		input.AddThemeColorOverride("font_placeholder_color", SettingsInkSoft);
		input.AddThemeFontSizeOverride("font_size", 10);
		input.TextSubmitted += _ =>
		{
			SyncSettingsInputsToFields();
			SetSettingsStatus("Đã nhận giá trị. Bấm Áp dụng để ghi config.");
		};
		row.AddChild(input);
		return input;
	}

	private OptionButton AddSettingsSpeechLanguageRow(Control parent, float y)
	{
		string[] codes = { "vi", "ja", "en" };
		string[] labels = { "Tiếng Việt", "日本語", "English" };
		var option = AddSettingsOptionRow(parent, y, "Ngôn ngữ giọng", codes, NormalizeSettingsTtsLanguage(_settingsTtsLanguage));
		for (int i = 0; i < codes.Length; i++)
		{
			option.SetItemMetadata(i, codes[i]);
			option.SetItemText(i, labels[i]);
		}
		return option;
	}

	private OptionButton AddSettingsOptionRow(Control parent, float y, string label, string[] values, string selected)
	{
		var row = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(264f, 38f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(new Color(1f, 0.98f, 0.99f, 0.62f), SettingsBorder, 12));
		parent.AddChild(row);

		var left = CreateUiLabel(label, 10, SettingsInkSoft);
		left.Position = new Vector2(12f, 8f);
		left.Size = new Vector2(82f, 22f);
		StyleSettingsSingleLineLabel(left);
		row.AddChild(left);

		var option = new OptionButton
		{
			Position = new Vector2(96f, 5f),
			Size = new Vector2(156f, 28f),
			MouseFilter = Control.MouseFilterEnum.Stop,
			ClipText = true,
			TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
		};
		option.AddThemeFontSizeOverride("font_size", 10);
		option.AddThemeColorOverride("font_color", SettingsInk);
		for (int i = 0; i < values.Length; i++)
		{
			option.AddItem(values[i]);
			if (string.Equals(values[i], selected, StringComparison.OrdinalIgnoreCase))
				option.Select(i);
		}
		option.ItemSelected += _ => SyncSettingsInputsToFields();
		row.AddChild(option);
		return option;
	}

	private void AddSettingsEditableKeyRow(string value, string displayName = "")
	{
		if (_settingsKeyRowsBox == null)
			return;

		int rowNumber = _settingsKeyInputs.Count + 1;
		float rowWidth = Mathf.Max(356f, _settingsKeyRowsBox.CustomMinimumSize.X);
		var row = new Panel
		{
			CustomMinimumSize = new Vector2(rowWidth, 68f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(new Color(1f, 0.98f, 0.99f, 0.58f), new Color(0.96f, 0.76f, 0.86f, 0.45f), 10));
		_settingsKeyRowsBox.AddChild(row);

		var statusLabel = CreateUiLabel($"○ Chưa lưu · Ưu tiên {rowNumber}", 8, new Color(0.56f, 0.50f, 0.54f));
		statusLabel.Position = new Vector2(8f, 2f);
		statusLabel.Size = new Vector2(rowWidth - 104f, 18f);
		statusLabel.TooltipText = "Chưa có trạng thái runtime";
		StyleSettingsSingleLineLabel(statusLabel);
		row.AddChild(statusLabel);
		_settingsKeyStatusLabels.Add(statusLabel);

		var nameInput = new LineEdit
		{
			Text = string.IsNullOrWhiteSpace(displayName) ? DefaultSettingsKeyName(_settingsKeyPoolKind, rowNumber) : displayName.Trim(),
			PlaceholderText = "Tên key",
			Position = new Vector2(8f, 28f),
			Size = new Vector2(128f, 28f),
			Flat = false,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		nameInput.AddThemeFontSizeOverride("font_size", 9);
		nameInput.AddThemeColorOverride("font_color", SettingsInk);
		nameInput.AddThemeColorOverride("font_placeholder_color", SettingsInkSoft);
		nameInput.TooltipText = "Tên riêng để phân biệt key, ví dụ: Gemini chính, Groq dự phòng";
		nameInput.TextChanged += _ =>
		{
			CaptureCurrentSettingsKeyDraft();
			RefreshSettingsKeyRowHealthIndicators();
			SetSettingsStatus("Đã sửa tên key. Tên chỉ lưu cục bộ, không gửi xuống provider.");
		};
		row.AddChild(nameInput);
		_settingsKeyNameInputs.Add(nameInput);

		var input = new LineEdit
		{
			Text = value ?? "",
			PlaceholderText = "API key",
			Position = new Vector2(142f, 28f),
			Size = new Vector2(Mathf.Max(148f, rowWidth - 210f), 28f),
			Flat = false,
			Secret = true,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		input.AddThemeFontSizeOverride("font_size", 9);
		input.AddThemeColorOverride("font_color", SettingsInk);
		input.AddThemeColorOverride("font_placeholder_color", SettingsInkSoft);
		UpdateSettingsKeyInputTooltip(input, value);
		input.TextChanged += text =>
		{
			UpdateSettingsKeyInputTooltip(input, text);
			CaptureCurrentSettingsKeyDraft();
			RefreshSettingsPoolSelectorCounts();
			RefreshSettingsKeyRowHealthIndicators();
			SetSettingsStatus($"Đã sửa pool {DisplaySettingsPoolName(_settingsKeyPoolKind)}. Bấm Lưu & Restart để Brain nạp lại.");
		};
		row.AddChild(input);
		_settingsKeyInputs.Add(input);

		var moveUp = CreateSettingsCompactButton("↑", () => MoveSettingsKeyRow(input, row, -1), 10);
		moveUp.TooltipText = "Tăng ưu tiên key";
		moveUp.Position = new Vector2(rowWidth - 84f, 2f);
		moveUp.Size = new Vector2(24f, 18f);
		row.AddChild(moveUp);

		var moveDown = CreateSettingsCompactButton("↓", () => MoveSettingsKeyRow(input, row, 1), 10);
		moveDown.TooltipText = "Giảm ưu tiên key";
		moveDown.Position = new Vector2(rowWidth - 56f, 2f);
		moveDown.Size = new Vector2(24f, 18f);
		row.AddChild(moveDown);

		Button reveal = null;
		reveal = CreateSettingsCompactButton("Hiện", () =>
		{
			input.Secret = !input.Secret;
			reveal.Text = input.Secret ? "Hiện" : "Ẩn";
			SetSettingsStatus(input.Secret ? "Đã ẩn API key." : "Đang hiện API key; đừng chia sẻ màn hình lúc này.");
		}, 8);
		reveal.TooltipText = "Hiện/ẩn API key";
		reveal.Position = new Vector2(rowWidth - 60f, 28f);
		reveal.Size = new Vector2(52f, 28f);
		row.AddChild(reveal);

		var remove = CreateSettingsCompactButton("×", () =>
		{
			_settingsKeyNameInputs.Remove(nameInput);
			_settingsKeyInputs.Remove(input);
			_settingsKeyStatusLabels.Remove(statusLabel);
			row.QueueFree();
			CaptureCurrentSettingsKeyDraft();
			RefreshSettingsPoolSelectorCounts();
			RefreshSettingsKeyRowHealthIndicators();
			SetSettingsStatus("Đã bỏ key khỏi bản nháp. Bấm Áp dụng để ghi thay đổi vào secrets.");
		}, 11);
		remove.TooltipText = "Xóa key này";
		remove.Position = new Vector2(rowWidth - 28f, 2f);
		remove.Size = new Vector2(24f, 18f);
		row.AddChild(remove);

		RefreshSettingsKeyRowHealthIndicators();
	}

	private void MoveSettingsKeyRow(LineEdit input, Panel row, int delta)
	{
		if (input == null || row == null || _settingsKeyRowsBox == null)
			return;
		int index = _settingsKeyInputs.IndexOf(input);
		int target = index + delta;
		if (index < 0 || target < 0 || target >= _settingsKeyInputs.Count)
		{
			SetSettingsStatus(delta < 0 ? "Key này đã ở ưu tiên cao nhất." : "Key này đã ở cuối danh sách fallback.");
			return;
		}

		LineEdit inputSwap = _settingsKeyInputs[target];
		_settingsKeyInputs[target] = _settingsKeyInputs[index];
		_settingsKeyInputs[index] = inputSwap;
		LineEdit nameSwap = _settingsKeyNameInputs[target];
		_settingsKeyNameInputs[target] = _settingsKeyNameInputs[index];
		_settingsKeyNameInputs[index] = nameSwap;
		Label statusSwap = _settingsKeyStatusLabels[target];
		_settingsKeyStatusLabels[target] = _settingsKeyStatusLabels[index];
		_settingsKeyStatusLabels[index] = statusSwap;
		_settingsKeyRowsBox.MoveChild(row, target);

		CaptureCurrentSettingsKeyDraft();
		RefreshSettingsKeyRowHealthIndicators();
		SetSettingsStatus($"Đã đổi thứ tự ưu tiên key trong pool {DisplaySettingsPoolName(_settingsKeyPoolKind)}.");
	}

	private static void UpdateSettingsKeyInputTooltip(LineEdit input, string rawValue)
	{
		if (input == null)
			return;
		string value = (rawValue ?? "").Trim();
		string tail = value.Length == 0 ? "trống" : value.Length <= 6 ? value : value.Substring(value.Length - 6);
		input.TooltipText = value.Length == 0 ? "Dòng key trống" : $"API key kết thúc bằng …{tail}";
	}


	private void AddSettingsToggleRow(Control parent, float y, string label, bool initial, ref TextureRect target, Action onToggle)
	{
		var lbl = CreateUiLabel(label, 12, SettingsInk);
		lbl.Position = new Vector2(18f, y + 5f);
		lbl.Size = new Vector2(150f, 24f);
		StyleSettingsSingleLineLabel(lbl);
		parent.AddChild(lbl);
		target = AddSettingsSwitch(parent, new Vector2(234f, y), initial, onToggle);
	}

	private void StyleSettingsSingleLineLabel(Label label, HorizontalAlignment alignment = HorizontalAlignment.Left)
	{
		if (label == null)
			return;
		label.HorizontalAlignment = alignment;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.AutowrapMode = TextServer.AutowrapMode.Off;
		label.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
	}

	private TextureRect AddSettingsSwitch(Control parent, Vector2 pos, bool initial, Action onToggle)
	{
		var tex = new TextureRect
		{
			Texture = initial ? _switchOnTexture : _switchOffTexture,
			ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
			StretchMode = TextureRect.StretchModeEnum.Scale,
			Position = pos,
			Size = new Vector2(58f, 34f),
			CustomMinimumSize = new Vector2(58f, 34f),
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		tex.GuiInput += ev =>
		{
			if (ev is InputEventMouseButton mouse && mouse.ButtonIndex == MouseButton.Left && mouse.Pressed)
			{
				onToggle?.Invoke();
				tex.AcceptEvent();
			}
		};
		parent.AddChild(tex);
		return tex;
	}

	private void ApplySettingsSwitch(TextureRect tex, bool on)
	{
		if (tex != null)
			tex.Texture = on ? _switchOnTexture : _switchOffTexture;
	}

	private void AddSettingsTab(Control parent, Rect2 rect, string text, bool active, Action customAction = null)
	{
		var btn = CreateSettingsButton(text, customAction ?? (() => SetSettingsStatus(SettingsTabHint(text, active))), primary: active);
		btn.Position = rect.Position;
		btn.Size = rect.Size;
		parent.AddChild(btn);
	}

	private void AddSettingsSmallTab(Control parent, Rect2 rect, string text, bool active, Action customAction = null)
	{
		var btn = CreateSettingsButton(text, customAction ?? (() => SetSettingsStatus(SettingsTabHint(text, active))), primary: active);
		btn.Position = rect.Position;
		btn.Size = rect.Size;
		btn.AddThemeFontSizeOverride("font_size", 12);
		parent.AddChild(btn);
	}

	private static string SettingsTabHint(string text, bool active)
	{
		if (active)
			return "Đang chỉnh Basic: trạng thái, não chính, TTS và cửa sổ.";
		if (text.Contains("Giọng", StringComparison.OrdinalIgnoreCase))
			return "Giọng nói có trang riêng với voice, ngôn ngữ, chất lượng và nghe thử.";
		if (text.Contains("Dữ liệu", StringComparison.OrdinalIgnoreCase))
			return "Dữ liệu cơ bản: key pool, backup, export config.";
		if (text.Contains("Advanced", StringComparison.OrdinalIgnoreCase))
			return "Advanced điều khiển hiện diện, memory, Tutor và quyền thao tác project.";
		if (text.Contains("Danger", StringComparison.OrdinalIgnoreCase))
			return "Danger Zone: import secrets sẽ backup file cũ trước khi ghi đè.";
		return "Cấu hình nằm trong các card bên dưới.";
	}

	private Button CreateSettingsButton(string text, Action onPressed, bool primary)
	{
		var btn = new Button
		{
			Text = text,
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
			Alignment = HorizontalAlignment.Center,
			ClipText = true,
			TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
		};

		var normal = new StyleBoxFlat
		{
			BgColor = primary ? new Color(0.96f, 0.34f, 0.62f, 0.82f) : new Color(1f, 0.97f, 0.985f, 0.76f),
			BorderColor = primary ? new Color(1f, 0.66f, 0.80f, 0.95f) : SettingsBorder,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 13,
			CornerRadiusTopRight = 13,
			CornerRadiusBottomLeft = 13,
			CornerRadiusBottomRight = 13,
			ContentMarginLeft = 5f,
			ContentMarginRight = 5f,
			ContentMarginTop = 4f,
			ContentMarginBottom = 4f,
		};
		var hover = (StyleBoxFlat)normal.Duplicate();
		hover.BgColor = primary ? new Color(0.98f, 0.42f, 0.68f, 0.92f) : new Color(1f, 0.92f, 0.96f, 0.86f);
		var pressed = (StyleBoxFlat)hover.Duplicate();
		pressed.BgColor = primary ? new Color(0.92f, 0.26f, 0.55f, 0.92f) : new Color(0.99f, 0.86f, 0.92f, 0.90f);

		btn.AddThemeStyleboxOverride("normal", normal);
		btn.AddThemeStyleboxOverride("hover", hover);
		btn.AddThemeStyleboxOverride("pressed", pressed);
		btn.AddThemeStyleboxOverride("focus", hover);
		btn.AddThemeColorOverride("font_color", primary ? Colors.White : SettingsInk);
		btn.AddThemeColorOverride("font_hover_color", primary ? Colors.White : SettingsPink);
		btn.AddThemeColorOverride("font_pressed_color", primary ? Colors.White : SettingsInk);
		btn.AddThemeFontSizeOverride("font_size", 12);
		btn.Pressed += onPressed;
		return btn;
	}

	private Button CreateSettingsCompactButton(string text, Action onPressed, int fontSize = 9)
	{
		var btn = new Button
		{
			Text = text,
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
			Alignment = HorizontalAlignment.Center,
			ClipText = true,
			TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
		};
		var normal = new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.97f, 0.985f, 0.76f),
			BorderColor = SettingsBorder,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 8,
			CornerRadiusTopRight = 8,
			CornerRadiusBottomLeft = 8,
			CornerRadiusBottomRight = 8,
			ContentMarginLeft = 2f,
			ContentMarginRight = 2f,
			ContentMarginTop = 1f,
			ContentMarginBottom = 1f,
		};
		var hover = (StyleBoxFlat)normal.Duplicate();
		hover.BgColor = new Color(1f, 0.92f, 0.96f, 0.90f);
		var pressed = (StyleBoxFlat)hover.Duplicate();
		pressed.BgColor = new Color(0.99f, 0.86f, 0.92f, 0.94f);
		btn.AddThemeStyleboxOverride("normal", normal);
		btn.AddThemeStyleboxOverride("hover", hover);
		btn.AddThemeStyleboxOverride("pressed", pressed);
		btn.AddThemeStyleboxOverride("focus", hover);
		btn.AddThemeColorOverride("font_color", SettingsInk);
		btn.AddThemeColorOverride("font_hover_color", SettingsPink);
		btn.AddThemeFontSizeOverride("font_size", fontSize);
		btn.Pressed += onPressed;
		return btn;
	}

	private Label CreateSettingsChip(string text, bool good)
	{
		var chip = CreateUiLabel(text, 11, good ? new Color(0.10f, 0.42f, 0.14f) : new Color(0.64f, 0.34f, 0.08f));
		chip.HorizontalAlignment = HorizontalAlignment.Center;
		chip.VerticalAlignment = VerticalAlignment.Center;
		chip.AutowrapMode = TextServer.AutowrapMode.Off;
		chip.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		chip.AddThemeStyleboxOverride("normal", SettingsCardStyle(
			good ? new Color(0.84f, 1.00f, 0.86f, 0.88f) : new Color(1.00f, 0.94f, 0.80f, 0.88f),
			good ? new Color(0.40f, 0.84f, 0.48f, 0.80f) : new Color(1.00f, 0.62f, 0.24f, 0.75f),
			10));
		return chip;
	}

	private static StyleBoxFlat SettingsCardStyle(Color bg, Color border, int radius)
	{
		return new StyleBoxFlat
		{
			BgColor = bg,
			BorderColor = border,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = radius,
			CornerRadiusTopRight = radius,
			CornerRadiusBottomLeft = radius,
			CornerRadiusBottomRight = radius,
		};
	}

	private void LoadSettingsState()
	{
		LoadSettingsFromSecrets();
		try
		{
			string path = ProjectSettings.GlobalizePath(SettingsStatePath);
			if (!File.Exists(path))
				return;

			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return;

			_settingsLoadedSchemaVersion = root.TryGetProperty("version", out JsonElement versionEl) && versionEl.TryGetInt32(out int schemaVersion)
				? schemaVersion
				: 0;

			if (TryReadBool(root, "tts_enabled", out bool tts))
				_settingsTtsEnabled = tts;
			if (TryReadBool(root, "auto_start_brain", out bool autoStart))
				AutoStartVision = autoStart;
			if (TryReadBool(root, "auto_restart_brain", out bool autoRestart))
				AutoRestartVisionOnCrash = autoRestart;
			if (TryReadBool(root, "lock_character_position", out bool lockPosition))
				LockCharacterPosition = lockPosition;
			if (TryReadFloat(root, "tts_volume", out float ttsVolume))
				_settingsTtsVolume = Mathf.Clamp(ttsVolume, 0f, 1f);
			if (TryReadFloat(root, "tts_speed", out float ttsSpeed))
				_settingsTtsSpeed = Mathf.Clamp(ttsSpeed, 0.85f, 1.15f);
			if (TryReadBool(root, "always_on_top", out bool top))
				KeepWindowAlwaysOnTop = top;
			if (TryReadBool(root, "transparent_window", out bool transparent))
				UseTransparentWindow = transparent;
			if (TryReadBool(root, "mouse_passthrough", out bool pass))
				EnableMousePassthrough = pass;
			if (_settingsLoadedSchemaVersion >= 6)
			{
				RemoteApiProvider = ReadString(root, "remote_provider", RemoteApiProvider);
				RemoteApiBaseUrl = ReadString(root, "remote_base_url", RemoteApiBaseUrl);
				RemoteApiModelName = ReadString(root, "remote_model", RemoteApiModelName);
				NormalizeMainBrainSettingsAfterLoad();
			}
			_settingsTtsVoice = ReadString(root, "tts_voice", _settingsTtsVoice);
			_settingsTtsLanguage = ReadString(root, "tts_language", _settingsTtsLanguage);
			// Schema 4 từng ghép ngôn ngữ public và TTS nên đã ghi vi ngoài ý muốn.
			// Migrate một lần về voice Nhật; schema 5 trở đi tôn trọng lựa chọn thật.
			if (_settingsLoadedSchemaVersion < 5 && NormalizeSettingsTtsLanguage(_settingsTtsLanguage) == "vi")
				_settingsTtsLanguage = "ja";
			_settingsTtsQuality = ReadString(root, "tts_quality", _settingsTtsQuality);
			LoadSettingsKeyAliases(root);
			LoadAdvancedSettingsState(root);
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Settings] Khong doc duoc user settings: {ex.Message}");
		}
	}

	private void LoadSettingsFromSecrets()
	{
		try
		{
			string path = GetSecretsPath();
			if (!File.Exists(path))
				return;
			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return;

			RemoteApiProvider = ReadSecretString(root, "ERIS_REMOTE_PROVIDER", RemoteApiProvider);
			RemoteApiBaseUrl = ReadSecretString(root, "ERIS_REMOTE_BASE_URL", RemoteApiBaseUrl);
			RemoteApiModelName = ReadSecretString(root, "ERIS_REMOTE_MODEL", RemoteApiModelName);
			LoadMainBrainProviderProfile(root);
			string tts = ReadSecretString(root, "ERIS_TTS_ENABLED", _settingsTtsEnabled ? "1" : "0");
			_settingsTtsEnabled = IsTruthy(tts);
			_settingsTtsVoice = ReadSecretString(root, "ERIS_TTS_VOICE", _settingsTtsVoice);
			_settingsTtsLanguage = ReadSecretString(root, "ERIS_TTS_LANGUAGE", _settingsTtsLanguage);
			_settingsTtsQuality = ReadSecretString(root, "ERIS_TTS_QUALITY", _settingsTtsQuality);
			if (float.TryParse(ReadSecretString(root, "ERIS_TTS_VOLUME", _settingsTtsVolume.ToString(System.Globalization.CultureInfo.InvariantCulture)), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float volume))
				_settingsTtsVolume = Mathf.Clamp(volume, 0f, 1f);
			if (float.TryParse(ReadSecretString(root, "ERIS_TTS_SPEED", _settingsTtsSpeed.ToString(System.Globalization.CultureInfo.InvariantCulture)), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out float speed))
				_settingsTtsSpeed = Mathf.Clamp(speed, 0.85f, 1.15f);
			LoadAdvancedSettingsFromSecrets(root);
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Settings] Khong doc duoc config/secrets.json: {ex.Message}");
		}
	}

	private void LoadMainBrainProviderProfile(JsonElement root)
	{
		if (!root.TryGetProperty("providers", out JsonElement providers) || providers.ValueKind != JsonValueKind.Object)
			return;
		string provider = NormalizeProviderProfileKey(RemoteApiProvider);
		if (!providers.TryGetProperty(provider, out JsonElement providerNode) || providerNode.ValueKind != JsonValueKind.Object)
			return;
		if (!providerNode.TryGetProperty("remote_main", out JsonElement profile) || profile.ValueKind != JsonValueKind.Object)
			return;
		if (profile.TryGetProperty("base_url", out JsonElement baseUrl) && baseUrl.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(baseUrl.GetString()))
			RemoteApiBaseUrl = baseUrl.GetString() ?? RemoteApiBaseUrl;
		if (profile.TryGetProperty("model", out JsonElement model) && model.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(model.GetString()))
			RemoteApiModelName = model.GetString() ?? RemoteApiModelName;
	}

	private void NormalizeMainBrainSettingsAfterLoad()
	{
		string provider = NormalizeProviderProfileKey(RemoteApiProvider);
		string baseUrl = (RemoteApiBaseUrl ?? "").Trim();
		string model = (RemoteApiModelName ?? "").Trim();
		bool retired = provider is "gemini" or "groq" or "gemma"
			|| baseUrl.Contains("groq.com", StringComparison.OrdinalIgnoreCase)
			|| baseUrl.Contains("generativelanguage.googleapis.com", StringComparison.OrdinalIgnoreCase)
			|| baseUrl.Contains("shopaikey", StringComparison.OrdinalIgnoreCase)
			|| model.Contains("gemini", StringComparison.OrdinalIgnoreCase)
			|| model.Contains("gemma", StringComparison.OrdinalIgnoreCase);
		if (retired)
		{
			RemoteApiBaseUrl = "";
			RemoteApiModelName = "";
			ApplyProviderDefaults("deepseek_web");
		}
	}

	private void SaveSettingsState()
	{
		// Nút lưu chung chỉ sở hữu state cục bộ của Body. Runtime policy được
		// áp dụng qua settings_update; credential/bootstrap có nút riêng ở Data.
		SaveSettingsState(true, writeSecrets: false);
	}

	private void SaveSettingsAndRestart()
	{
		SaveSettingsState(false, writeSecrets: true);
		RestartVisionProcess();
		SetSettingsStatus("Đã lưu cấu hình và restart Brain.");
	}

	private void SaveSettingsState(bool showStatus, bool writeSecrets)
	{
		SyncSettingsInputsToFields();
		CaptureCurrentSettingsKeyDraft();
		try
		{
			string path = ProjectSettings.GlobalizePath(SettingsStatePath);
			string dir = Path.GetDirectoryName(path);
			if (!string.IsNullOrWhiteSpace(dir))
				Directory.CreateDirectory(dir);

			var state = BuildSanitizedSettingsNode();
			state["saved_at"] = DateTime.Now.ToString("O");
			File.WriteAllText(path, state.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);

			if (writeSecrets)
			{
				// Chỉ tab Kết nối & dữ liệu được ghi credential/bootstrap. Không
				// materialize tts.*, stage.*, personality.* hay policy runtime ra env.
				WriteRuntimeSettingsToSecrets(backup: true);
				WriteRuntimeSettingsToDotEnv();
			}

			if (showStatus)
				SetSettingsStatus(writeSecrets
					? "Đã lưu credential và route khởi động. Restart Brain để nạp thay đổi."
					: "Đã lưu trạng thái cục bộ của Body.");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Không lưu được settings: {ex.Message}");
		}
	}

	private void WriteRuntimeSettingsToSecrets(bool backup)
	{
		string path = GetSecretsPath();
		Directory.CreateDirectory(Path.GetDirectoryName(path) ?? GetRuntimeBaseDirForSettings());
		if (backup)
			BackupFileIfExists(path, "secrets_before_connection_save");

		JsonObject root = LoadSecretsRoot();
		JsonObject env = EnsureObject(root, "env");
		env["ERIS_REMOTE_API_ENABLED"] = EnableRemoteCognitionApi ? "1" : "0";
		env["ERIS_REMOTE_PROVIDER"] = RemoteApiProvider ?? "";
		env["ERIS_REMOTE_BASE_URL"] = RemoteApiBaseUrl ?? "";
		env["ERIS_REMOTE_MODEL"] = RemoteApiModelName ?? "";

		WriteMainBrainProviderProfile(root, ReadConfiguredMainKeysFromSecretsOnly());

		// Mỗi pool credential có owner rõ ràng. Runtime settings không được
		// lẫn vào đây chỉ vì cùng mang hình dạng key/value, một thói quen khá phá hoại.
		foreach (string kind in new[] { "main", "gemini", "groq", "elevenlabs" })
		{
			if (_settingsKeyDrafts.TryGetValue(kind, out List<string> draftRows))
				ReplaceEditableKeysForPool(root, kind, NormalizeSettingsKeyRows(draftRows));
		}

		File.WriteAllText(path, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
	}

	private void WriteRuntimeSettingsToDotEnv()
	{
		string path = Path.Combine(GetRuntimeBaseDirForSettings(), ".env");
		var updates = new Dictionary<string, string>(StringComparer.Ordinal)
		{
			["ERIS_REMOTE_API_ENABLED"] = EnableRemoteCognitionApi ? "1" : "0",
			["ERIS_REMOTE_PROVIDER"] = RemoteApiProvider ?? "",
			["ERIS_REMOTE_BASE_URL"] = RemoteApiBaseUrl ?? "",
			["ERIS_REMOTE_MODEL"] = RemoteApiModelName ?? "",
		};

		var lines = File.Exists(path)
			? new List<string>(File.ReadAllLines(path, Encoding.UTF8))
			: new List<string>();
		var written = new HashSet<string>(StringComparer.Ordinal);
		for (int i = 0; i < lines.Count; i++)
		{
			string trimmed = (lines[i] ?? "").Trim().TrimStart('\uFEFF');
			if (trimmed.StartsWith("export ", StringComparison.OrdinalIgnoreCase))
				trimmed = trimmed.Substring(7).Trim();
			int equals = trimmed.IndexOf('=');
			if (equals <= 0)
				continue;
			string key = trimmed.Substring(0, equals).Trim();
			if (!updates.TryGetValue(key, out string value))
				continue;
			lines[i] = $"{key}={value}";
			written.Add(key);
		}
		foreach (var pair in updates)
		{
			if (!written.Contains(pair.Key))
				lines.Add($"{pair.Key}={pair.Value}");
		}

		File.WriteAllLines(path, lines, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
	}

	private void ApplyLiveTtsSettingsFromPanel(string changedField)
	{
		SyncSettingsInputsToFields();
		SaveSettingsState(false, writeSecrets: false);
		SendTtsRuntimeSettingsUpdate(languageOnly: string.Equals(changedField, "ngôn ngữ", StringComparison.Ordinal));
		SetSettingsStatus($"Đã gửi {changedField} TTS xuống Brain runtime.");
	}

	private void SendTtsRuntimeSettingsUpdate(bool languageOnly = false)
	{
		if (_chatSend == null)
			return;

		var tts = languageOnly ? new JsonObject
		{
			["language"] = NormalizeSettingsTtsLanguage(_settingsTtsLanguage),
		} : new JsonObject
		{
			["enabled"] = _settingsTtsEnabled,
			["provider"] = string.IsNullOrWhiteSpace(_settingsTtsProvider) ? "elevenlabs" : _settingsTtsProvider.Trim().ToLowerInvariant(),
			["language"] = NormalizeSettingsTtsLanguage(_settingsTtsLanguage),
			["voice"] = string.IsNullOrWhiteSpace(_settingsTtsVoice) ? "Aoede" : _settingsTtsVoice.Trim(),
			["elevenlabs_voice_id"] = _settingsElevenLabsVoiceId ?? "",
			["elevenlabs_model"] = string.IsNullOrWhiteSpace(_settingsElevenLabsModel) ? "eleven_v3" : _settingsElevenLabsModel.Trim(),
			["quality"] = string.IsNullOrWhiteSpace(_settingsTtsQuality) ? "balanced" : _settingsTtsQuality.Trim().ToLowerInvariant(),
			["volume"] = Math.Round(_settingsTtsVolume, 2),
			["speed"] = Math.Round(_settingsTtsSpeed, 2),
		};
		var payload = new JsonObject
		{
			["type"] = "settings_update",
			["scope"] = "tts",
			["settings"] = new JsonObject { ["tts"] = tts },
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(payload.ToJsonString()));
		if (error != Error.Ok)
			RunLogger.LogErr($"[Settings] Khong gui duoc TTS settings_update: {error}");
		else
			RunLogger.Log($"[Settings] Da gui TTS settings_update provider={_settingsTtsProvider} language={_settingsTtsLanguage}");
	}


	private void TryApplySettingsUpdateAck(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw)
			|| (!raw.Contains("settings_update_ack", StringComparison.Ordinal)
				&& !raw.Contains("settings_health_ack", StringComparison.Ordinal)
				&& !raw.Contains("settings_connection_test_ack", StringComparison.Ordinal)
				&& !raw.Contains("tts_preview_ack", StringComparison.Ordinal)
				&& !raw.Contains("maintenance_", StringComparison.Ordinal)))
			return;

		if (raw.Contains("maintenance_", StringComparison.Ordinal))
			TryApplyMaintenanceMessage(raw);

		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;

			if (root.TryGetProperty("settings_update_ack", out JsonElement ack) && ack.ValueKind == JsonValueKind.Object)
			{
				string scope = ack.TryGetProperty("scope", out JsonElement scopeEl) ? scopeEl.GetString() ?? "tts" : "tts";
				if (string.Equals(scope, "advanced", StringComparison.OrdinalIgnoreCase))
				{
					ApplyAdvancedSettingsAck(ack);
				}
				else
				{
					ApplyRuntimeSettingsAck(ack);
					string provider = ack.TryGetProperty("provider", out JsonElement providerEl) ? providerEl.GetString() ?? _settingsTtsProvider : _settingsTtsProvider;
					string language = ack.TryGetProperty("language", out JsonElement langEl) ? langEl.GetString() ?? "" : "";
					string voice = ack.TryGetProperty("voice", out JsonElement voiceEl) ? voiceEl.GetString() ?? "" : "";
					bool rebuilt = ack.TryGetProperty("worker_rebuilt", out JsonElement rebuiltEl) && rebuiltEl.ValueKind == JsonValueKind.True;
					bool ready = ack.TryGetProperty("engine_ready", out JsonElement readyEl) && readyEl.ValueKind == JsonValueKind.True;
					float volume = ack.TryGetProperty("volume", out JsonElement volumeEl) && volumeEl.TryGetSingle(out float parsedVolume) ? parsedVolume : _settingsTtsVolume;
					float speed = ack.TryGetProperty("speed", out JsonElement speedEl) && speedEl.TryGetSingle(out float parsedSpeed) ? parsedSpeed : _settingsTtsSpeed;
					if (_settingsTtsEngineStatusLabel != null)
						_settingsTtsEngineStatusLabel.Text = ready ? $"{provider}: sẵn sàng · {volume * 100f:0}% · {speed:0.00}×" : $"{provider}: chưa sẵn sàng";
					SetSettingsStatus(ready
						? $"Brain đã nhận TTS {provider}: {language}, {voice}{(rebuilt ? " · worker đã nạp lại" : "")}"
						: $"Brain đã nhận setting {provider} nhưng engine chưa sẵn sàng; kiểm tra đúng credential owner.");
				}
			}

			if (root.TryGetProperty("settings_health_ack", out JsonElement healthAck) && healthAck.ValueKind == JsonValueKind.Object)
			{
				ApplyAdvancedHealthSnapshot(healthAck);
				if (healthAck.TryGetProperty("main_brain", out JsonElement mainBrain))
					ApplyMainBrainHealthSnapshot(mainBrain);
				if (healthAck.TryGetProperty("pools", out JsonElement pools) && pools.ValueKind == JsonValueKind.Object)
				{
					foreach (string kind in new[] { "main", "gemini", "groq", "elevenlabs" })
					{
						if (!pools.TryGetProperty(kind, out JsonElement pool) || pool.ValueKind != JsonValueKind.Object)
							continue;
						int size = pool.TryGetProperty("size", out JsonElement sizeEl) ? sizeEl.GetInt32() : 0;
						int available = pool.TryGetProperty("available", out JsonElement availableEl) ? availableEl.GetInt32() : 0;
						int dead = pool.TryGetProperty("dead", out JsonElement deadEl) ? deadEl.GetInt32() : 0;
						// Cooldown 401/quarantine có thể dài hơn 24.8 ngày, vượt Int32.
						// Đọc Int64 để Settings không nổ FormatException chỉ vì backend gửi
						// một mốc thời gian hoàn toàn hợp lệ nhưng quá lớn cho int 32-bit.
						long cooldownMs = ReadJsonInt64(pool, "global_cooldown_ms", 0L);
						_settingsPoolHealthSummary[kind] = cooldownMs > 0
							? $"{available}/{size} khả dụng · pool nghỉ {FormatCooldownDuration(cooldownMs)}"
							: $"{available}/{size} khả dụng · {dead} cooldown/lỗi";

						var keyHealth = new List<SettingsKeyHealth>();
						if (pool.TryGetProperty("keys", out JsonElement keys) && keys.ValueKind == JsonValueKind.Array)
						{
							foreach (JsonElement key in keys.EnumerateArray())
							{
								keyHealth.Add(new SettingsKeyHealth
								{
									Index = key.TryGetProperty("index", out JsonElement indexEl) && indexEl.TryGetInt32(out int parsedIndex) ? parsedIndex : -1,
									Fingerprint = key.TryGetProperty("fingerprint", out JsonElement fingerprintEl) ? fingerprintEl.GetString() ?? "" : "",
									Status = key.TryGetProperty("status", out JsonElement statusEl) ? statusEl.GetString() ?? "unknown" : "unknown",
									Reason = key.TryGetProperty("reason", out JsonElement reasonEl) ? reasonEl.GetString() ?? "" : "",
									CooldownMs = ReadJsonInt64(key, "cooldown_ms", 0L),
									Active = key.TryGetProperty("active", out JsonElement activeEl) && activeEl.ValueKind == JsonValueKind.True,
								});
							}
						}
						_settingsKeyHealth[kind] = keyHealth;
					}
				}
				if (healthAck.TryGetProperty("tts", out JsonElement tts) && tts.ValueKind == JsonValueKind.Object)
				{
					bool ready = tts.TryGetProperty("ready", out JsonElement readyEl) && readyEl.ValueKind == JsonValueKind.True;
					string provider = tts.TryGetProperty("provider", out JsonElement providerEl) ? providerEl.GetString() ?? _settingsTtsProvider : _settingsTtsProvider;
					string model = string.Equals(provider, "elevenlabs", StringComparison.OrdinalIgnoreCase)
						? (tts.TryGetProperty("elevenlabs_model", out JsonElement modelEl) ? modelEl.GetString() ?? _settingsElevenLabsModel : _settingsElevenLabsModel)
						: "ElevenLabs TTS";
					_settingsTtsProvider = provider;
					if (_settingsTtsEngineStatusLabel != null)
						_settingsTtsEngineStatusLabel.Text = ready ? $"{provider}: sẵn sàng · {model}" : $"{provider}: chưa sẵn sàng · {model}";
				}
				RefreshSettingsKeyRowHealthIndicators();
				SetSettingsStatus("Đã đọc trạng thái Main Brain, TTS và bốn credential owner từ Brain.");
			}

			if (root.TryGetProperty("settings_connection_test_ack", out JsonElement connectionAck) && connectionAck.ValueKind == JsonValueKind.Object)
			{
				bool ok = connectionAck.TryGetProperty("ok", out JsonElement okEl) && okEl.ValueKind == JsonValueKind.True;
				int latency = connectionAck.TryGetProperty("latency_ms", out JsonElement latencyEl) && latencyEl.TryGetInt32(out int parsedLatency) ? parsedLatency : 0;
				string kind = connectionAck.TryGetProperty("error_kind", out JsonElement kindEl) ? kindEl.GetString() ?? "" : "";
				string detail = connectionAck.TryGetProperty("error", out JsonElement detailEl) ? detailEl.GetString() ?? "" : "";
				if (connectionAck.TryGetProperty("main_brain", out JsonElement mainBrain))
					ApplyMainBrainHealthSnapshot(mainBrain);
				if (ok)
				{
					string message = $"Kết nối thật thành công · {latency} ms · {FriendlyMainBrainModelName(RemoteApiModelName)}";
					SetSettingsConnectionStatus(message);
					SetSettingsStatus(message);
					if (_settingsMainBrainLiveLabel != null)
						_settingsMainBrainLiveLabel.Text = $"Runtime: request thật thành công · {latency} ms";
				}
				else
				{
					string message = FriendlyMainBrainConnectionError(kind, detail);
					SetSettingsConnectionStatus(message);
					SetSettingsStatus(message);
					if (_settingsMainBrainLiveLabel != null)
						_settingsMainBrainLiveLabel.Text = $"Runtime: kiểm tra thất bại · {kind}";
				}
			}

			if (root.TryGetProperty("tts_preview_ack", out JsonElement previewAck) && previewAck.ValueKind == JsonValueKind.Object)
			{
				bool accepted = previewAck.TryGetProperty("accepted", out JsonElement acceptedEl) && acceptedEl.ValueKind == JsonValueKind.True;
				string reason = previewAck.TryGetProperty("reason", out JsonElement reasonEl) ? reasonEl.GetString() ?? "" : "";
				if (_settingsTtsEngineStatusLabel != null)
					_settingsTtsEngineStatusLabel.Text = accepted ? "Engine: đang phát mẫu" : "Engine: không phát được";
				SetSettingsStatus(accepted ? "Đã gửi câu nghe thử xuống TTS." : $"Không nghe thử được: {reason}");
			}
		}
		catch (Exception ex)
		{
			// Ghi cả loại exception để lần sau không phải đoán một câu FormatException
			// chung chung. ACK không chứa raw key nên log loại packet là an toàn.
			RunLogger.LogErr($"[Settings] ack lỗi {ex.GetType().Name}: {ex.Message}");
		}
	}


	private static long ReadJsonInt64(JsonElement root, string name, long fallback)
	{
		if (!root.TryGetProperty(name, out JsonElement item))
			return fallback;
		if (item.ValueKind == JsonValueKind.Number && item.TryGetInt64(out long number))
			return Math.Max(0L, number);
		if (item.ValueKind == JsonValueKind.String && long.TryParse(item.GetString(), out long parsed))
			return Math.Max(0L, parsed);
		return fallback;
	}

	private static string FormatCooldownDuration(long cooldownMs)
	{
		long totalSeconds = Math.Max(0L, (long)Math.Ceiling(cooldownMs / 1000d));
		if (totalSeconds >= 86400L)
			return $"{Math.Ceiling(totalSeconds / 86400d):0.#} ngày";
		if (totalSeconds >= 3600L)
			return $"{Math.Ceiling(totalSeconds / 3600d):0.#} giờ";
		if (totalSeconds >= 60L)
			return $"{Math.Ceiling(totalSeconds / 60d):0.#} phút";
		return $"{totalSeconds} giây";
	}

	private static string NormalizeSettingsTtsLanguage(string value)
	{
		string lang = (value ?? "").Trim().ToLowerInvariant();
		return lang switch
		{
			"" => "ja",
			"auto" => "ja",
			"jp" => "ja",
			"japanese" => "ja",
			"ja-jp" => "ja",
			_ => lang,
		};
	}

	private void LoadSettingsKeyAliases(JsonElement root)
	{
		_settingsKeyAliases.Clear();
		if (!root.TryGetProperty("key_aliases", out JsonElement aliasesRoot) || aliasesRoot.ValueKind != JsonValueKind.Object)
			return;

		foreach (JsonProperty poolProperty in aliasesRoot.EnumerateObject())
		{
			string kind = NormalizeSettingsPoolKind(poolProperty.Name);
			if (poolProperty.Value.ValueKind != JsonValueKind.Object)
				continue;
			foreach (JsonProperty aliasProperty in poolProperty.Value.EnumerateObject())
			{
				if (aliasProperty.Value.ValueKind != JsonValueKind.String)
					continue;
				string fingerprint = (aliasProperty.Name ?? "").Trim().ToLowerInvariant();
				string name = (aliasProperty.Value.GetString() ?? "").Trim();
				if (!string.IsNullOrWhiteSpace(fingerprint) && !string.IsNullOrWhiteSpace(name))
					_settingsKeyAliases[SettingsKeyAliasMapKey(kind, fingerprint)] = name;
			}
		}
	}

	private JsonObject BuildSettingsKeyAliasesNode()
	{
		var root = new JsonObject();
		foreach (string kind in new[] { "main", "gemini", "groq", "elevenlabs" })
		{
			List<string> values = _settingsKeyDrafts.TryGetValue(kind, out List<string> draftValues)
				? new List<string>(draftValues)
				: ReadEditableKeysForPool(kind);
			List<string> names = _settingsKeyNameDrafts.TryGetValue(kind, out List<string> draftNames)
				? new List<string>(draftNames)
				: ReadSettingsKeyNamesForEditor(kind, values);

			var pool = new JsonObject();
			for (int i = 0; i < values.Count; i++)
			{
				string rawKey = (values[i] ?? "").Trim();
				string fingerprint = SettingsKeyFingerprint(rawKey);
				if (string.IsNullOrWhiteSpace(fingerprint))
					continue;

				string name = i < names.Count ? (names[i] ?? "").Trim() : "";
				if (string.IsNullOrWhiteSpace(name))
					name = DefaultSettingsKeyName(kind, i + 1);
				pool[fingerprint] = name;
				_settingsKeyAliases[SettingsKeyAliasMapKey(kind, fingerprint)] = name;
			}
			root[kind] = pool;
		}
		return root;
	}

	private JsonObject BuildSanitizedSettingsNode()
	{
		var state = new JsonObject
		{
			["version"] = 6,
			["remote_api_enabled"] = EnableRemoteCognitionApi,
			["auto_start_brain"] = AutoStartVision,
			["auto_restart_brain"] = AutoRestartVisionOnCrash,
			["remote_provider"] = RemoteApiProvider ?? "",
			["remote_base_url"] = RemoteApiBaseUrl ?? "",
			["remote_model"] = RemoteApiModelName ?? "",
			["tts_enabled"] = _settingsTtsEnabled,
			["tts_voice"] = _settingsTtsVoice ?? "Aoede",
			["tts_language"] = _settingsTtsLanguage ?? "auto",
			["tts_quality"] = _settingsTtsQuality ?? "high",
			["tts_volume"] = Math.Round(_settingsTtsVolume, 2),
			["tts_speed"] = Math.Round(_settingsTtsSpeed, 2),
			["always_on_top"] = KeepWindowAlwaysOnTop,
			["transparent_window"] = UseTransparentWindow,
			["mouse_passthrough"] = EnableMousePassthrough,
			["lock_character_position"] = LockCharacterPosition,
			["editing_key_pool"] = _settingsKeyPoolKind ?? "",
			["editing_key_count"] = CollectEditedSettingsKeys().Count,
			["key_aliases"] = BuildSettingsKeyAliasesNode(),
		};
		AddAdvancedSettingsToState(state);
		return state;
	}

	private void SyncSettingsInputsToFields(bool applyProviderDefaults = false)
	{
		if (_settingsProviderSelect != null && _settingsProviderSelect.Selected >= 0)
			RemoteApiProvider = _settingsProviderSelect.GetItemText(_settingsProviderSelect.Selected);
		if (applyProviderDefaults)
			ApplyProviderDefaults(RemoteApiProvider);
		if (_settingsModelInput != null)
			RemoteApiModelName = _settingsModelInput.Text.Trim();
		if (_settingsBaseUrlInput != null)
			RemoteApiBaseUrl = _settingsBaseUrlInput.Text.Trim();
		if (_settingsVoiceSelect != null && _settingsVoiceSelect.Selected >= 0)
			_settingsTtsVoice = _settingsVoiceSelect.GetItemText(_settingsVoiceSelect.Selected);
		if (_settingsLanguageSelect != null && _settingsLanguageSelect.Selected >= 0)
			_settingsTtsLanguage = _settingsLanguageSelect.GetItemMetadata(_settingsLanguageSelect.Selected).AsString();
		if (_settingsQualitySelect != null && _settingsQualitySelect.Selected >= 0)
			_settingsTtsQuality = _settingsQualitySelect.GetItemText(_settingsQualitySelect.Selected);
	}

	private void ApplyProviderDefaults(string provider)
	{
		string normalized = (provider ?? "").Trim().ToLowerInvariant();
		if (normalized == "deepseek_web")
		{
			RemoteApiProvider = "deepseek_web";
			if (string.IsNullOrWhiteSpace(RemoteApiBaseUrl)) RemoteApiBaseUrl = "http://127.0.0.1:6011/v1";
			if (string.IsNullOrWhiteSpace(RemoteApiModelName)) RemoteApiModelName = "deepseek-flash";
			return;
		}
		if (normalized == "openai_compatible")
		{
			RemoteApiProvider = "openai_compatible";
			return;
		}
		RemoteApiProvider = "deepseek";
		RemoteApiBaseUrl = "https://api.deepseek.com";
		RemoteApiModelName = "deepseek-flash";
	}

	private void RefreshSettingsProviderInputs()
	{
		if (_settingsModelInput != null)
			_settingsModelInput.Text = RemoteApiModelName ?? "";
		if (_settingsBaseUrlInput != null)
			_settingsBaseUrlInput.Text = RemoteApiBaseUrl ?? "";
		if (_settingsProviderSelect != null)
			SelectOptionText(_settingsProviderSelect, RemoteApiProvider);
	}

	private void RestartVisionProcess()
	{
		StopVisionProcess();
		StartVisionProcess();
	}

	private void TestSettingsSystem()
	{
		SyncSettingsInputsToFields();
		int main = CountConfiguredMainKeys();
		int gemini = CountConfiguredGeminiKeys();
		int groq = CountConfiguredGroqKeys();
		string brain = _processes != null && _processes.IsVisionRunning
			? $"brain chạy PID {_processes.VisionProcessId}"
			: "brain dừng";
		string secrets = File.Exists(GetSecretsPath()) ? "có secrets" : "thiếu secrets";
		SetSettingsStatus($"OK: {brain}, main {FriendlyMainBrainModelName(RemoteApiModelName)} · {main} key, Gemini phụ {gemini}, Groq phụ {groq}, TTS {_settingsTtsEnabledText()}, {secrets}.");
	}

	private void TestSettingsPool()
	{
		if (_processes == null || !_processes.IsVisionRunning)
		{
			int main = CountConfiguredMainKeys();
			int gemini = CountConfiguredGeminiKeys();
			int groq = CountConfiguredGroqKeys();
			SetSettingsStatus($"Brain đang dừng; local: Main Brain {main}, Gemini phụ {gemini}, Groq phụ {groq}.");
			return;
		}
		if (_chatSend == null)
		{
			SetSettingsStatus("Kênh UDP chưa sẵn sàng nên chưa đọc được trạng thái key pool.");
			return;
		}

		if (_settingsPoolHealthLabel != null)
			_settingsPoolHealthLabel.Text = "Đang đọc trạng thái runtime…";
		var payload = new { type = "settings_health" };
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		SetSettingsStatus(error == Error.Ok ? "Đang đọc não chính và ba credential pool..." : $"Không gửi được yêu cầu trạng thái: {error}");
	}


	private void BackupSettingsAndSecrets()
	{
		try
		{
			string backupDir = GetSettingsBackupDir();
			Directory.CreateDirectory(backupDir);
			string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
			int copied = 0;

			string secrets = GetSecretsPath();
			if (File.Exists(secrets))
			{
				File.Copy(secrets, Path.Combine(backupDir, $"secrets_{stamp}.json"), overwrite: false);
				copied++;
			}

			string settings = ProjectSettings.GlobalizePath(SettingsStatePath);
			if (File.Exists(settings))
			{
				File.Copy(settings, Path.Combine(backupDir, $"eris_settings_{stamp}.json"), overwrite: false);
				copied++;
			}

			SetSettingsStatus(copied > 0 ? $"Đã sao lưu {copied} file vào {backupDir}" : "Chưa có secrets/settings để sao lưu.");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Sao lưu lỗi: {ex.Message}");
		}
	}

	private void ExportSanitizedSettings()
	{
		try
		{
			SyncSettingsInputsToFields();
			string exportPath = Path.Combine(GetRuntimeBaseDirForSettings(), "config", SettingsExportFileName);
			Directory.CreateDirectory(Path.GetDirectoryName(exportPath) ?? GetRuntimeBaseDirForSettings());

			var node = BuildSanitizedSettingsNode();
			node["exported_at"] = DateTime.Now.ToString("O");
			node["gemini_key_count"] = CountConfiguredGeminiKeys();
			node["groq_key_count"] = CountConfiguredGroqKeys();
			node["note"] = "No raw API keys are exported.";

			File.WriteAllText(exportPath, node.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Encoding.UTF8);
			SetSettingsStatus($"Đã xuất config sạch: {exportPath}");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Xuất config lỗi: {ex.Message}");
		}
	}

	private void OpenSettingsLogs()
	{
		try
		{
			string logPath = RunLogger.LogFilePath ?? "";
			string logDir = string.IsNullOrWhiteSpace(logPath)
				? Path.Combine(GetRuntimeBaseDirForSettings(), "logs")
				: Path.GetDirectoryName(logPath);
			if (string.IsNullOrWhiteSpace(logDir))
				logDir = Path.Combine(GetRuntimeBaseDirForSettings(), "logs");
			Directory.CreateDirectory(logDir);
			OS.ShellOpen(logDir);
			SetSettingsStatus($"Đã mở logs: {logDir}");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Không mở được logs: {ex.Message}");
		}
	}

	private void OpenImportSecretsDialog()
	{
		if (_settingsImportSecretsDialog == null)
		{
			SetSettingsStatus("FileDialog chưa sẵn sàng.");
			return;
		}
		_settingsImportSecretsDialog.PopupCentered(new Vector2I(720, 520));
	}

	private void ImportSecretsFile(string selectedPath)
	{
		try
		{
			if (string.IsNullOrWhiteSpace(selectedPath) || !File.Exists(selectedPath))
			{
				SetSettingsStatus("File secrets không tồn tại.");
				return;
			}

			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(selectedPath, Encoding.UTF8));
			if (doc.RootElement.ValueKind != JsonValueKind.Object)
			{
				SetSettingsStatus("secrets.json phải là JSON object.");
				return;
			}

			string target = GetSecretsPath();
			Directory.CreateDirectory(Path.GetDirectoryName(target) ?? GetRuntimeBaseDirForSettings());
			BackupFileIfExists(target, "secrets_before_import");
			File.Copy(selectedPath, target, overwrite: true);
			LoadSettingsFromSecrets();
			_settingsKeyDrafts.Clear();
			_settingsKeyNameDrafts.Clear();
			_settingsSkipKeyDraftCaptureOnce = true;
			BuildSettingsPanelUi();
			SetSettingsStatus($"Đã nhập secrets.json: {target}");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Nhập secrets lỗi: {ex.Message}");
		}
	}

	private void BackupFileIfExists(string path, string prefix)
	{
		if (!File.Exists(path))
			return;
		string backupDir = GetSettingsBackupDir();
		Directory.CreateDirectory(backupDir);
		string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
		File.Copy(path, Path.Combine(backupDir, $"{prefix}_{stamp}.json"), overwrite: false);
	}

	private string _settingsTtsEnabledText() => _settingsTtsEnabled ? "bật" : "tắt";

	private void SetSettingsStatus(string text)
	{
		if (_settingsStatusNoteLabel != null)
			_settingsStatusNoteLabel.Text = text ?? "";
		RunLogger.Log($"[Settings] {text}");
	}

	private void RefreshSettingsPanel()
	{
		if (_settingsPanel == null)
			return;

		RefreshSettingsRuntimeStatus();

		CaptureCurrentSettingsKeyDraft();
		RefreshSettingsPoolSelectorCounts();

		ApplySettingsSwitch(_settingsTtsSwitch, _settingsTtsEnabled);
		ApplySettingsSwitch(_settingsAutoStartSwitch, AutoStartVision);
		ApplySettingsSwitch(_settingsAutoRestartSwitch, AutoRestartVisionOnCrash);
		ApplySettingsSwitch(_settingsAlwaysOnTopSwitch, KeepWindowAlwaysOnTop);
		ApplySettingsSwitch(_settingsTransparentSwitch, UseTransparentWindow);
		ApplySettingsSwitch(_settingsPassthroughSwitch, EnableMousePassthrough);
		ApplySettingsSwitch(_settingsPositionLockSwitch, LockCharacterPosition);
		RefreshSettingsKeyRowHealthIndicators();
	}


	private string BuildHardeningRuntimeSummary()
	{
		if (string.IsNullOrWhiteSpace(_latestHardeningStatusJson))
			return "";
		try
		{
			using JsonDocument doc = JsonDocument.Parse(_latestHardeningStatusJson);
			JsonElement root = doc.RootElement;
			string releaseStatus = "";
			string phase7Status = "";
			int phase7Turns = 0;
			int phase7Target = 0;

			if (root.TryGetProperty("release", out JsonElement release)
				&& release.ValueKind == JsonValueKind.Object
				&& release.TryGetProperty("status", out JsonElement releaseNode))
				releaseStatus = releaseNode.GetString() ?? "";

			if (root.TryGetProperty("phase7", out JsonElement phase7)
				&& phase7.ValueKind == JsonValueKind.Object)
			{
				if (phase7.TryGetProperty("status", out JsonElement statusNode))
					phase7Status = statusNode.GetString() ?? "";
				if (phase7.TryGetProperty("public_turns", out JsonElement turnsNode)
					&& turnsNode.ValueKind == JsonValueKind.Number)
					turnsNode.TryGetInt32(out phase7Turns);
				if (phase7.TryGetProperty("target_turns", out JsonElement targetNode)
					&& targetNode.ValueKind == JsonValueKind.Number)
					targetNode.TryGetInt32(out phase7Target);
			}

			if (string.Equals(phase7Status, "running", StringComparison.OrdinalIgnoreCase))
				return $"Phase 7 {phase7Turns}/{Math.Max(phase7Target, 1)}";
			if (!string.IsNullOrWhiteSpace(releaseStatus))
				return $"Hardening {releaseStatus}";
			if (!string.IsNullOrWhiteSpace(phase7Status) && phase7Status != "disabled")
				return $"Phase 7 {phase7Status}";
		}
		catch (Exception)
		{
			// Status chỉ là phụ trợ UI; payload hỏng không được phép kéo sập Settings.
		}
		return "";
	}

	private void TickSettingsPanelRuntimeStatus()
	{
		if (_settingsPanel == null || !_settingsPanel.Visible)
			return;
		ulong now = Time.GetTicksMsec();
		if (now < _settingsNextRuntimeRefreshMs)
			return;
		_settingsNextRuntimeRefreshMs = now + 1000;
		RefreshSettingsRuntimeStatus();
	}

	private void RefreshSettingsRuntimeStatus()
	{
		bool running = _processes != null && _processes.IsVisionRunning;
		bool recovering = _visionRestartPending && !running;
		if (_settingsBrainStatusLabel != null)
		{
			_settingsBrainStatusLabel.Text = recovering
				? "●  Brain đang phục hồi..."
				: running ? "●  Brain đang chạy" : "●  Brain đã dừng";
			_settingsBrainStatusLabel.AddThemeStyleboxOverride("normal", SettingsCardStyle(
				running ? new Color(0.84f, 1.00f, 0.86f, 0.88f)
					: recovering ? new Color(1.00f, 0.96f, 0.82f, 0.90f)
					: new Color(1.00f, 0.94f, 0.80f, 0.88f),
				running ? new Color(0.40f, 0.84f, 0.48f, 0.80f)
					: recovering ? new Color(0.96f, 0.66f, 0.24f, 0.82f)
					: new Color(1.00f, 0.62f, 0.24f, 0.75f),
				10));
			_settingsBrainStatusLabel.AddThemeColorOverride(
				"font_color",
				running ? new Color(0.10f, 0.42f, 0.14f)
					: new Color(0.64f, 0.34f, 0.08f));
		}

		if (_settingsRuntimeDetailLabel != null)
		{
			if (running)
			{
				TimeSpan uptime = _processes.VisionUptime;
				string uptimeText = uptime.TotalHours >= 1d
					? $"{(int)uptime.TotalHours} giờ {uptime.Minutes:00} phút"
					: $"{uptime.Minutes:00} phút {uptime.Seconds:00} giây";
				string hardeningSummary = BuildHardeningRuntimeSummary();
				_settingsRuntimeDetailLabel.Text = $"PID {_processes.VisionProcessId} · Đã chạy {uptimeText}"
					+ (string.IsNullOrWhiteSpace(hardeningSummary) ? "" : $" · {hardeningSummary}");
				_settingsRuntimeDetailLabel.TooltipText = _processes.VisionStartedAtUtc.HasValue
					? $"Khởi động lúc {_processes.VisionStartedAtUtc.Value.ToLocalTime():HH:mm:ss}"
					: "Brain đang chạy";
			}
			else if (recovering)
			{
				_settingsRuntimeDetailLabel.Text = "Đang chờ 2 giây trước khi tự khởi động lại";
				_settingsRuntimeDetailLabel.TooltipText = "Tự phục hồi đang hoạt động";
			}
			else
			{
				_settingsRuntimeDetailLabel.Text = _processes?.LastVisionExitCode is int code
					? $"Lần thoát gần nhất · mã {code}"
					: "Chưa có tiến trình Brain đang chạy";
				_settingsRuntimeDetailLabel.TooltipText = _processes?.LastVisionExitCode is int lastCode
					? $"Exit code gần nhất: {lastCode}"
					: "Bấm Khởi động Brain để chạy runtime";
			}
		}

		if (_settingsRuntimeStartButton != null)
		{
			_settingsRuntimeStartButton.Visible = !running;
			_settingsRuntimeStartButton.Disabled = recovering;
			_settingsRuntimeStartButton.Text = recovering ? "Đang phục hồi..." : "▶  Khởi động Brain";
		}
		if (_settingsRuntimeStopButton != null)
		{
			_settingsRuntimeStopButton.Visible = running;
			_settingsRuntimeStopButton.Disabled = !running;
		}
		if (_settingsRuntimeRestartButton != null)
		{
			_settingsRuntimeRestartButton.Visible = running;
			_settingsRuntimeRestartButton.Disabled = !running;
		}

		RefreshSettingsRuntimeModelSummary();
	}

	private void RefreshSettingsRuntimeModelSummary()
	{
		if (_settingsRuntimeProviderSummaryLabel != null)
			_settingsRuntimeProviderSummaryLabel.Text = DisplaySettingsProviderName(RemoteApiProvider);
		if (_settingsRuntimeModelSummaryLabel != null)
		{
			string model = string.IsNullOrWhiteSpace(RemoteApiModelName) ? "Chưa chọn model" : RemoteApiModelName.Trim();
			_settingsRuntimeModelSummaryLabel.Text = FriendlyMainBrainModelName(model);
			_settingsRuntimeModelSummaryLabel.TooltipText = model;
		}
		if (_settingsMainBrainEndpointLabel != null)
			_settingsMainBrainEndpointLabel.Text = CompactSettingsEndpoint(RemoteApiBaseUrl);
		if (_settingsMainBrainKeyLabel != null)
			_settingsMainBrainKeyLabel.Text = MainBrainLocalCredentialSummary();
		if (_settingsMainBrainLiveLabel != null && (_processes == null || !_processes.IsVisionRunning))
			_settingsMainBrainLiveLabel.Text = "Runtime: Brain đang dừng; cấu hình trên là bản sẽ dùng khi khởi động.";
	}


	private void RefreshSettingsKeyRowHealthIndicators()
	{
		string kind = NormalizeSettingsPoolKind(_settingsKeyPoolKind);
		if (_settingsPoolHealthLabel != null)
			_settingsPoolHealthLabel.Text = _settingsPoolHealthSummary.TryGetValue(kind, out string summary) ? summary : "Chưa đọc trạng thái runtime";

		_settingsKeyHealth.TryGetValue(kind, out List<SettingsKeyHealth> health);
		for (int i = 0; i < _settingsKeyStatusLabels.Count; i++)
		{
			Label label = _settingsKeyStatusLabels[i];
			if (label == null || !IsInstanceValid(label))
				continue;

			int priority = i + 1;
			string rawKey = i < _settingsKeyInputs.Count && _settingsKeyInputs[i] != null ? (_settingsKeyInputs[i].Text ?? "").Trim() : "";
			string displayName = i < _settingsKeyNameInputs.Count && _settingsKeyNameInputs[i] != null
				? (_settingsKeyNameInputs[i].Text ?? "").Trim()
				: DefaultSettingsKeyName(kind, priority);
			if (string.IsNullOrWhiteSpace(displayName))
				displayName = DefaultSettingsKeyName(kind, priority);

			string fingerprint = SettingsKeyFingerprint(rawKey);
			SettingsKeyHealth item = health?.Find(candidate => string.Equals(candidate.Fingerprint, fingerprint, StringComparison.OrdinalIgnoreCase));

			if (string.IsNullOrWhiteSpace(rawKey))
			{
				label.Text = $"○ Trống · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.56f, 0.50f, 0.54f));
				label.TooltipText = $"{displayName}: chưa nhập API key; dòng này sẽ không được lưu";
				continue;
			}

			if (item == null)
			{
				label.Text = $"○ Chưa nạp · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.56f, 0.50f, 0.54f));
				label.TooltipText = $"{displayName}: chưa có trong runtime; Lưu & Restart để đồng bộ";
				continue;
			}

			if (string.Equals(item.Status, "quarantined", StringComparison.OrdinalIgnoreCase))
			{
				label.Text = $"● Cách ly · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.74f, 0.12f, 0.18f));
				label.TooltipText = $"{displayName}: key bị cách ly trong runtime{(string.IsNullOrWhiteSpace(item.Reason) ? "" : $" · {item.Reason}")}";
			}
			else if (string.Equals(item.Status, "cooldown", StringComparison.OrdinalIgnoreCase))
			{
				label.Text = $"● Cooldown · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.78f, 0.43f, 0.08f));
				label.TooltipText = $"{displayName}: đang cooldown, còn khoảng {FormatCooldownDuration(item.CooldownMs)}";
			}
			else if (string.Equals(item.Status, "error", StringComparison.OrdinalIgnoreCase)
				|| string.Equals(item.Status, "invalid", StringComparison.OrdinalIgnoreCase))
			{
				label.Text = $"● Lỗi · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.74f, 0.12f, 0.18f));
				label.TooltipText = $"{displayName}: runtime đánh dấu key lỗi hoặc không hợp lệ";
			}
			else if (item.Active)
			{
				label.Text = $"● Đang dùng · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.08f, 0.58f, 0.18f));
				label.TooltipText = $"{displayName}: runtime đang dùng cho request kế tiếp";
			}
			else
			{
				label.Text = $"● Sẵn sàng · Ưu tiên {priority}";
				label.AddThemeColorOverride("font_color", new Color(0.20f, 0.46f, 0.24f));
				label.TooltipText = $"{displayName}: key sẵn sàng trong pool";
			}
		}
	}


	private static string CompactSettingsKey(string value)
	{
		string text = (value ?? "").Trim();
		if (text.Length <= 12)
			return text;
		return text.Substring(0, 5) + "…" + text.Substring(text.Length - 4);
	}


	private static string SettingsKeyFingerprint(string rawKey)
	{
		if (string.IsNullOrWhiteSpace(rawKey))
			return "";
		byte[] hash = System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(rawKey.Trim()));
		return Convert.ToHexString(hash).Substring(0, 12).ToLowerInvariant();
	}


	private void RequestTtsPreview()
	{
		SyncSettingsInputsToFields();
		if (_processes == null || !_processes.IsVisionRunning)
		{
			SetSettingsStatus("Brain đang dừng nên chưa thể nghe thử TTS.");
			return;
		}
		SendTtsRuntimeSettingsUpdate();
		string language = NormalizeSettingsTtsLanguage(_settingsTtsLanguage);
		string sample = language switch
		{
			"ja" => "こんにちは、エリスです。音声設定を確認しています。",
			"en" => "Hello, this is Eris. I am checking the current voice settings.",
			_ => "Xin chào Ngài, tôi là Eris. Tôi đang kiểm tra thiết lập giọng nói.",
		};
		var payload = new { type = "tts_preview", text = sample };
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		SetSettingsStatus(error == Error.Ok ? "Đang tạo câu nghe thử..." : $"Không gửi được câu nghe thử: {error}");
	}

	private int CountConfiguredMainKeys()
	{
		return ReadConfiguredMainKeys().Count;
	}

	private int CountConfiguredGeminiKeys()
	{
		return ReadConfiguredGeminiKeys().Count;
	}

	private int CountConfiguredGroqKeys()
	{
		return ReadConfiguredGroqKeys().Count;
	}

	private List<string> ReadConfiguredMainKeys()
	{
		var keys = ReadConfiguredKeys("DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY", "ERIS_REMOTE_FALLBACK_API_KEYS");
		string profileKey = ReadMainBrainProfileApiKey();
		if (!string.IsNullOrWhiteSpace(profileKey))
		{
			keys.RemoveAll(value => string.Equals(value, profileKey, StringComparison.Ordinal));
			keys.Insert(0, profileKey);
		}
		return keys;
	}

	private List<string> ReadConfiguredGeminiKeys()
	{
		return new List<string>();
	}

	private List<string> ReadConfiguredGroqKeys()
	{
		return new List<string>();
	}

	private string ReadMainBrainProfileApiKey()
	{
		try
		{
			string path = GetSecretsPath();
			if (!File.Exists(path))
				return "";
			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
			JsonElement root = doc.RootElement;
			if (!root.TryGetProperty("providers", out JsonElement providers) || providers.ValueKind != JsonValueKind.Object)
				return "";
			string provider = NormalizeProviderProfileKey(RemoteApiProvider);
			if (!providers.TryGetProperty(provider, out JsonElement providerNode) || providerNode.ValueKind != JsonValueKind.Object)
				return "";
			if (!providerNode.TryGetProperty("remote_main", out JsonElement profile) || profile.ValueKind != JsonValueKind.Object)
				return "";
			return profile.TryGetProperty("api_key", out JsonElement key) && key.ValueKind == JsonValueKind.String
				? key.GetString() ?? ""
				: "";
		}
		catch
		{
			return "";
		}
	}

	private List<string> ReadEditableKeysForPool(string poolKind)
	{
		return NormalizeSettingsPoolKind(poolKind) switch
		{
			"main" => ReadConfiguredMainKeysFromSecretsOnly(),
			"groq" => ReadConfiguredKeysFromSecretsOnly("ERIS_GROQ_API_KEY", "ERIS_GROQ_FALLBACK_API_KEYS"),
			"elevenlabs" => ReadConfiguredKeysFromSecretsOnly("ELEVENLABS_API_KEY", "ERIS_ELEVENLABS_API_KEY"),
			_ => ReadConfiguredKeysFromSecretsOnly("GEMINI_API_KEY", "ERIS_PRIVATE_MIND_API_KEY", "ERIS_PRIVATE_MIND_FALLBACK_API_KEYS", "ERIS_GEMINI_FALLBACK_API_KEYS"),
		};
	}

	private List<string> ReadConfiguredMainKeysFromSecretsOnly()
	{
		var keys = ReadConfiguredKeysFromSecretsOnly("DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY", "ERIS_REMOTE_FALLBACK_API_KEYS");
		string profileKey = ReadMainBrainProfileApiKey();
		if (!string.IsNullOrWhiteSpace(profileKey))
		{
			keys.RemoveAll(value => string.Equals(value, profileKey, StringComparison.Ordinal));
			keys.Insert(0, profileKey);
		}
		return keys;
	}

	private List<string> CollectEditedSettingsKeys()
	{
		var rows = new List<string>();
		foreach (LineEdit input in _settingsKeyInputs)
		{
			if (input != null && IsInstanceValid(input))
				rows.Add(input.Text ?? "");
		}
		return NormalizeSettingsKeyRows(rows);
	}

	private static List<string> NormalizeSettingsKeyRows(IEnumerable<string> rows)
	{
		var keys = new List<string>();
		var unique = new HashSet<string>(StringComparer.Ordinal);
		if (rows == null)
			return keys;

		foreach (string raw in rows)
		{
			foreach (string key in ExtractClipboardKeys(raw ?? ""))
			{
				if (unique.Add(key))
					keys.Add(key);
			}
		}
		return keys;
	}


	private void ReplaceEditableKeysForPool(JsonObject root, string poolKind, List<string> keys)
	{
		string kind = NormalizeSettingsPoolKind(poolKind);
		if (kind is "gemini" or "groq")
		{
			JsonObject retiredSlots = EnsureObject(root, "api_keys");
			foreach (string name in new[] { "GEMINI_API_KEY", "ERIS_SPEECH_API_KEY", "ERIS_PRIVATE_MIND_API_KEY", "ERIS_GROQ_API_KEY" }) retiredSlots[name] = "";
			foreach (string name in new[] { "ERIS_GEMINI_FALLBACK_API_KEYS", "ERIS_PRIVATE_MIND_FALLBACK_API_KEYS", "ERIS_GROQ_FALLBACK_API_KEYS" }) retiredSlots[name] = new JsonArray();
			return;
		}
		string[] controlled = kind switch
		{
			"main" => new[] { "DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY", "ERIS_REMOTE_FALLBACK_API_KEYS" },
			"groq" => new[] { "ERIS_GROQ_API_KEY", "ERIS_GROQ_FALLBACK_API_KEYS" },
			"elevenlabs" => new[] { "ELEVENLABS_API_KEY", "ERIS_ELEVENLABS_API_KEY" },
			_ => new[] { "GEMINI_API_KEY", "ERIS_PRIVATE_MIND_API_KEY", "ERIS_PRIVATE_MIND_FALLBACK_API_KEYS", "ERIS_GEMINI_FALLBACK_API_KEYS" },
		};

		RemoveControlledKeys(root, controlled);
		foreach (string sectionName in new[] { "api_keys", "env", "secrets" })
		{
			if (root[sectionName] is JsonObject section)
				RemoveControlledKeys(section, controlled);
		}

		JsonObject apiKeys = EnsureObject(root, "api_keys");
		if (kind == "main")
		{
			WriteMainBrainProviderProfile(root, keys);
			if (keys.Count == 0)
				return;
			apiKeys["ERIS_REMOTE_API_KEY"] = keys[0];
			JsonArray fallback = new();
			for (int i = 1; i < keys.Count; i++)
				fallback.Add(keys[i]);
			apiKeys["ERIS_REMOTE_FALLBACK_API_KEYS"] = fallback;
			return;
		}

		if (keys.Count == 0)
			return;

		if (kind == "elevenlabs")
		{
			// Runtime ElevenLabs hiện nhận một credential. UI vẫn giữ contract pool
			// chung nhưng chỉ key ưu tiên 1 được materialize.
			apiKeys["ELEVENLABS_API_KEY"] = keys[0];
			return;
		}

		if (kind == "groq")
		{
			apiKeys["ERIS_GROQ_API_KEY"] = keys[0];
			JsonArray fallback = new();
			for (int i = 1; i < keys.Count; i++)
				fallback.Add(keys[i]);
			apiKeys["ERIS_GROQ_FALLBACK_API_KEYS"] = fallback;
			return;
		}

		apiKeys["GEMINI_API_KEY"] = keys[0];
		apiKeys["ERIS_PRIVATE_MIND_API_KEY"] = keys[0];
		JsonArray privateMindFallback = new();
		JsonArray geminiFallback = new();
		for (int i = 1; i < keys.Count; i++)
		{
			privateMindFallback.Add(keys[i]);
			geminiFallback.Add(keys[i]);
		}
		apiKeys["ERIS_PRIVATE_MIND_FALLBACK_API_KEYS"] = privateMindFallback;
		apiKeys["ERIS_GEMINI_FALLBACK_API_KEYS"] = geminiFallback;
	}

	private void WriteMainBrainProviderProfile(JsonObject root, List<string> keys)
	{
		JsonObject providers = EnsureObject(root, "providers");
		string provider = NormalizeProviderProfileKey(RemoteApiProvider);
		JsonObject providerNode = EnsureObject(providers, provider);
		JsonObject profile = EnsureObject(providerNode, "remote_main");
		profile["base_url"] = RemoteApiBaseUrl ?? "";
		profile["model"] = RemoteApiModelName ?? "";
		if (keys != null && keys.Count > 0)
			profile["api_key"] = keys[0];
		else
			profile.Remove("api_key");
	}

	private static string NormalizeProviderProfileKey(string provider)
	{
		string value = (provider ?? "").Trim().ToLowerInvariant();
		return value switch
		{
			"openai" => "openai_compatible",
			"compatible" => "openai_compatible",
			"shopaikey" => "openai_compatible",
			"" => "openai_compatible",
			_ => value,
		};
	}

	private static void RemoveControlledKeys(JsonObject obj, string[] names)
	{
		foreach (string name in names)
			obj.Remove(name);
	}

	private List<string> ReadConfiguredKeys(params string[] names)
	{
		var unique = new HashSet<string>(StringComparer.Ordinal);
		var keys = new List<string>();

		foreach (string name in names)
			AddSplitKeys(keys, unique, System.Environment.GetEnvironmentVariable(name) ?? "");

		try
		{
			string path = GetSecretsPath();
			if (!File.Exists(path))
				return keys;

			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return keys;

			CollectSecretsKeysFromObject(root, keys, unique, names);
			foreach (string sectionName in new[] { "api_keys", "env", "secrets" })
				if (root.TryGetProperty(sectionName, out JsonElement section) && section.ValueKind == JsonValueKind.Object)
					CollectSecretsKeysFromObject(section, keys, unique, names);
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Settings] Khong doc duoc key pool: {ex.Message}");
		}

		return keys;
	}

	private List<string> ReadConfiguredKeysFromSecretsOnly(params string[] names)
	{
		var unique = new HashSet<string>(StringComparer.Ordinal);
		var keys = new List<string>();
		try
		{
			string path = GetSecretsPath();
			if (!File.Exists(path))
				return keys;

			using JsonDocument doc = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return keys;

			CollectSecretsKeysFromObject(root, keys, unique, names);
			foreach (string sectionName in new[] { "api_keys", "env", "secrets" })
				if (root.TryGetProperty(sectionName, out JsonElement section) && section.ValueKind == JsonValueKind.Object)
					CollectSecretsKeysFromObject(section, keys, unique, names);
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Settings] Khong doc duoc editable key pool: {ex.Message}");
		}
		return keys;
	}

	private static void CollectSecretsKeysFromObject(JsonElement source, List<string> keys, HashSet<string> unique, string[] names)
	{
		foreach (string name in names)
		{
			if (!source.TryGetProperty(name, out JsonElement value))
				continue;
			if (value.ValueKind == JsonValueKind.String)
				AddSplitKeys(keys, unique, value.GetString() ?? "");
			else if (value.ValueKind == JsonValueKind.Array)
			{
				foreach (JsonElement item in value.EnumerateArray())
					if (item.ValueKind == JsonValueKind.String)
						AddSplitKeys(keys, unique, item.GetString() ?? "");
			}
		}
	}

	private static void AddSplitKeys(List<string> keys, HashSet<string> unique, string raw)
	{
		foreach (string part in (raw ?? "").Replace("\n", ",").Replace(";", ",").Split(','))
		{
			string key = part.Trim();
			if (key.Length > 0 && !key.StartsWith("PASTE_", StringComparison.OrdinalIgnoreCase) && unique.Add(key))
				keys.Add(key);
		}
	}

	private static List<string> ExtractClipboardKeys(string raw)
	{
		var keys = new List<string>();
		var unique = new HashSet<string>(StringComparer.Ordinal);
		foreach (string token in (raw ?? "").Split(new[] { '\r', '\n', '\t', ' ', ',', ';', '"', '\'', '[', ']' }, StringSplitOptions.RemoveEmptyEntries))
		{
			string key = token.Trim();
			if (key.Length < 20 || key.Contains('=') || key.StartsWith("PASTE_", StringComparison.OrdinalIgnoreCase))
				continue;
			bool validChars = true;
			foreach (char ch in key)
			{
				if (!(char.IsLetterOrDigit(ch) || ch == '_' || ch == '-' || ch == '.'))
				{
					validChars = false;
					break;
				}
			}
			if (validChars && unique.Add(key))
				keys.Add(key);
		}
		return keys;
	}

	private static bool IsGroqPool(string poolKind)
	{
		string kind = (poolKind ?? "").Trim().ToLowerInvariant();
		return kind == "groq" || kind == "openai" || kind == "openai_compatible" || kind == "compatible" || kind == "remote";
	}

	private static string KeyPoolKindFromProvider(string provider, string baseUrl)
	{
		string normalized = (provider ?? "").Trim().ToLowerInvariant();
		if (IsGroqPool(normalized))
			return "groq";
		string url = (baseUrl ?? "").Trim().ToLowerInvariant();
		return url.Contains("groq.com") || url.Contains("openai") || url.Contains("shopaikey") ? "groq" : "gemini";
	}

	private static JsonObject LoadSecretsRoot()
	{
		string path = GetSecretsPath();
		if (!File.Exists(path))
			return new JsonObject();
		JsonNode parsed = JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8));
		return parsed as JsonObject ?? new JsonObject();
	}

	private static JsonObject EnsureObject(JsonObject root, string name)
	{
		if (root[name] is JsonObject obj)
			return obj;
		obj = new JsonObject();
		root[name] = obj;
		return obj;
	}

	private static bool TryReadBool(JsonElement root, string name, out bool value)
	{
		value = false;
		if (!root.TryGetProperty(name, out JsonElement item))
			return false;
		if (item.ValueKind == JsonValueKind.True || item.ValueKind == JsonValueKind.False)
		{
			value = item.GetBoolean();
			return true;
		}
		if (item.ValueKind == JsonValueKind.String)
		{
			value = IsTruthy(item.GetString() ?? "");
			return true;
		}
		return false;
	}

	private static bool TryReadFloat(JsonElement root, string name, out float value)
	{
		value = 0f;
		if (!root.TryGetProperty(name, out JsonElement item))
			return false;
		if (item.ValueKind == JsonValueKind.Number && item.TryGetSingle(out value))
			return true;
		if (item.ValueKind == JsonValueKind.String)
			return float.TryParse(item.GetString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out value);
		return false;
	}

	private static string ReadString(JsonElement root, string name, string fallback)
	{
		return root.TryGetProperty(name, out JsonElement item) && item.ValueKind == JsonValueKind.String
			? item.GetString() ?? fallback
			: fallback;
	}

	private static string ReadSecretString(JsonElement root, string name, string fallback)
	{
		string direct = ReadString(root, name, "");
		if (!string.IsNullOrWhiteSpace(direct))
			return direct;
		foreach (string sectionName in new[] { "env", "api_keys", "secrets" })
		{
			if (root.TryGetProperty(sectionName, out JsonElement section) && section.ValueKind == JsonValueKind.Object)
			{
				string value = ReadString(section, name, "");
				if (!string.IsNullOrWhiteSpace(value))
					return value;
			}
		}
		return fallback;
	}

	private static bool IsTruthy(string value)
	{
		string v = (value ?? "").Trim().ToLowerInvariant();
		return v is "1" or "true" or "yes" or "on";
	}

	private static void SelectOptionText(OptionButton option, string value)
	{
		if (option == null)
			return;
		for (int i = 0; i < option.ItemCount; i++)
		{
			if (string.Equals(option.GetItemText(i), value, StringComparison.OrdinalIgnoreCase))
			{
				option.Select(i);
				return;
			}
		}
	}

	private static string SelectedOptionText(OptionButton option, string fallback)
	{
		return option != null && option.Selected >= 0 ? option.GetItemText(option.Selected) : fallback;
	}

	private static string GetRuntimeBaseDirForSettings()
	{
		if (OS.HasFeature("editor"))
			return ProjectSettings.GlobalizePath("res://");
		string exeDir = Path.GetDirectoryName(OS.GetExecutablePath());
		return string.IsNullOrWhiteSpace(exeDir) ? ProjectSettings.GlobalizePath("res://") : exeDir;
	}

	private static string GetSecretsPath()
	{
		return Path.Combine(GetRuntimeBaseDirForSettings(), "config", "secrets.json");
	}

	private static string GetRuntimeSettingsPath()
	{
		string configured = System.Environment.GetEnvironmentVariable("ERIS_RUNTIME_SETTINGS_PATH") ?? "";
		if (!string.IsNullOrWhiteSpace(configured))
		{
			string expanded = System.Environment.ExpandEnvironmentVariables(configured.Trim());
			return Path.IsPathRooted(expanded)
				? expanded
				: Path.Combine(GetRuntimeBaseDirForSettings(), expanded);
		}
		string documents = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments);
		return Path.Combine(documents, "ErisLive", "config", "runtime_settings.json");
	}

	private static string GetSettingsBackupDir()
	{
		return Path.Combine(GetRuntimeBaseDirForSettings(), "backups", "settings");
	}
}
