using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

public partial class UdpListener : Node2D
{
	private enum SettingsModePage
	{
		Basic,
		Voice,
		Data,
		Advanced,
		Danger,
	}

	private SettingsModePage _settingsModePage = SettingsModePage.Basic;

	// Advanced mới chỉ phơi những lựa chọn người dùng thực sự cảm nhận được.
	// Pipeline, stage protocol, parser nội bộ... là invariant kiến trúc và không
	// còn bị biến thành công tắc trang trí nữa.
	private OptionButton _advancedPresenceProfileSelect;
	private OptionButton _advancedCommentaryCadenceSelect;
	private OptionButton _advancedResponseModeSelect;
	private OptionButton _advancedResponseLengthSelect;
	private OptionButton _advancedDirectnessSelect;
	private OptionButton _advancedMemoryProfileSelect;
	private OptionButton _advancedTutorModeSelect;
	private OptionButton _advancedProjectModeSelect;
	private OptionButton _advancedRepairAttemptsSelect;

	private TextureRect _advancedFallbackSwitch;
	private TextureRect _advancedDiscoveryLocalSignalSwitch;
	private TextureRect _advancedMemoryMaintenanceSwitch;
	private TextureRect _advancedTutorQuickSearchSwitch;
	private TextureRect _advancedTutorResearchSwitch;
	private TextureRect _advancedTutorPersistSwitch;
	private TextureRect _advancedProjectAskSwitch;
	private TextureRect _advancedProjectProceedSwitch;
	private TextureRect _advancedProjectRollbackSwitch;
	private TextureRect _advancedProjectPackageSwitch;

	private Label _advancedEffectiveSummaryLabel;
	private Label _advancedEffectiveDetailLabel;
	private Label _advancedApplyResultLabel;

	private string _advancedPresenceProfile = "Đồng hành";
	private string _advancedCommentaryCadence = "Vừa";
	private string _advancedResponseMode = "Cân bằng";
	private string _advancedResponseLength = "Vừa";
	private string _advancedDirectness = "Cân bằng";
	private string _advancedMemoryProfile = "Cân bằng";
	private string _advancedTutorMode = "Chỉ khi yêu cầu";
	private string _advancedProjectMode = "Tự làm an toàn";
	private int _advancedMaxRepairAttempts = 2;

	private bool _advancedAutoFallback = true;
	private bool _advancedDiscoveryLocalSignal = true;
	private bool _advancedMemoryMaintenance = true;
	private bool _advancedTutorQuickSearch = true;
	private bool _advancedTutorResearch = true;
	private bool _advancedTutorPersistResearchMemory;
	private bool _advancedProjectAskWhenUncertain = true;
	private bool _advancedProjectProceedWithoutReply = true;
	private bool _advancedProjectAutoRollback = true;
	private bool _advancedProjectAutoPackage = true;

	private void SwitchSettingsMode(SettingsModePage page)
	{
		if (_settingsModePage == page)
			return;
		_settingsModePage = page;
		BuildSettingsPanelUi();
		SetSettingsStatus(page switch
		{
			SettingsModePage.Voice => "Giọng nói chứa toàn bộ cấu hình TTS và nghe thử; Basic chỉ giữ điều khiển nhanh.",
			SettingsModePage.Data => "Kết nối & dữ liệu quản lý não chính, pool credential, backup và export.",
			SettingsModePage.Advanced => "Advanced điều khiển hành vi, memory, Tutor và quyền project. Một nút Áp dụng sẽ lưu lẫn cập nhật Brain.",
			SettingsModePage.Danger => "Danger Zone chỉ chứa thao tác reset/bảo trì thật. Mọi lệnh phá dữ liệu đều preview trước.",
			_ => "Đã quay lại Basic.",
		});
		if (page == SettingsModePage.Advanced)
			RequestAdvancedRuntimeSnapshot();
	}

	private void ResetSettingsUiReferencesForRebuild()
	{
		_settingsBrainStatusLabel = null;
		_settingsRuntimeDetailLabel = null;
		_settingsRuntimeProviderSummaryLabel = null;
		_settingsRuntimeModelSummaryLabel = null;
		_settingsMainBrainEndpointLabel = null;
		_settingsMainBrainKeyLabel = null;
		_settingsMainBrainLiveLabel = null;
		_settingsSystemAiHealthLabel = null;
		_settingsRuntimeStartButton = null;
		_settingsRuntimeStopButton = null;
		_settingsRuntimeRestartButton = null;
		_settingsConnectionOverlay = null;
		_settingsConnectionFeedbackLabel = null;
		_settingsMainPoolStatusLabel = null;
		_settingsPoolStatusLabel = null;
		_settingsFallbackStatusLabel = null;
		_settingsElevenLabsPoolStatusLabel = null;
		_settingsPoolHealthLabel = null;
		_settingsTtsEngineStatusLabel = null;
		_settingsStatusNoteLabel = null;
		_settingsTtsSwitch = null;
		_settingsAutoStartSwitch = null;
		_settingsAutoRestartSwitch = null;
		_settingsAlwaysOnTopSwitch = null;
		_settingsTransparentSwitch = null;
		_settingsPassthroughSwitch = null;
		_settingsPositionLockSwitch = null;
		_settingsProviderSelect = null;
		_settingsModelInput = null;
		_settingsBaseUrlInput = null;
		_settingsVoiceSelect = null;
		_settingsLanguageSelect = null;
		_settingsQualitySelect = null;
		_settingsTtsVolumeSlider = null;
		_settingsTtsSpeedSlider = null;
		_settingsTtsVolumeValueLabel = null;
		_settingsTtsSpeedValueLabel = null;
		_settingsImportSecretsDialog = null;
		_settingsKeyRowsBox = null;
		_settingsKeyNameInputs.Clear();
		_settingsKeyInputs.Clear();
		_settingsKeyStatusLabels.Clear();

		_advancedPresenceProfileSelect = null;
		_advancedCommentaryCadenceSelect = null;
		_advancedResponseModeSelect = null;
		_advancedResponseLengthSelect = null;
		_advancedDirectnessSelect = null;
		_advancedMemoryProfileSelect = null;
		_advancedTutorModeSelect = null;
		_advancedProjectModeSelect = null;
		_advancedRepairAttemptsSelect = null;
		_advancedFallbackSwitch = null;
		_advancedDiscoveryLocalSignalSwitch = null;
		_advancedMemoryMaintenanceSwitch = null;
		_advancedTutorQuickSearchSwitch = null;
		_advancedTutorResearchSwitch = null;
		_advancedTutorPersistSwitch = null;
		_advancedProjectAskSwitch = null;
		_advancedProjectProceedSwitch = null;
		_advancedProjectRollbackSwitch = null;
		_advancedProjectPackageSwitch = null;
		_advancedEffectiveSummaryLabel = null;
		_advancedEffectiveDetailLabel = null;
		_advancedApplyResultLabel = null;
		ResetRuntimeSchemaSettingsUiReferences();
		ResetDangerSettingsUiReferences();
	}

	private void BuildAdvancedSettingsPage(Control root)
	{
		var presenceCard = AddSettingsCard(root, new Rect2(32f, 205f, 350f, 380f), "Hiện diện & phản hồi");
		BuildAdvancedPresenceCard(presenceCard);

		var memoryCard = AddSettingsCard(root, new Rect2(398f, 205f, 350f, 380f), "Memory & Tutor");
		BuildAdvancedMemoryTutorCard(memoryCard);

		var projectCard = AddSettingsCard(root, new Rect2(764f, 205f, 384f, 380f), "Quyền project & trạng thái");
		BuildAdvancedProjectCard(projectCard);

		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.38f),
			Position = new Vector2(32f, 604f),
			Size = new Vector2(1116f, 1.4f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		root.AddChild(divider);

		var noteHeart = CreateUiLabel("♡", 24, SettingsPink);
		noteHeart.Position = new Vector2(72f, 624f);
		noteHeart.Size = new Vector2(32f, 30f);
		root.AddChild(noteHeart);

		var note = CreateUiLabel("Advanced chỉ giữ các núm thay đổi hành vi thật; cấu hình kỹ thuật được Brain tự quản.", 12, SettingsInkSoft);
		note.Position = new Vector2(112f, 626f);
		note.Size = new Vector2(530f, 22f);
		StyleSettingsSingleLineLabel(note);
		root.AddChild(note);

		_settingsStatusNoteLabel = CreateUiLabel("", 10, SettingsInk);
		_settingsStatusNoteLabel.Position = new Vector2(112f, 646f);
		_settingsStatusNoteLabel.Size = new Vector2(530f, 16f);
		StyleSettingsSingleLineLabel(_settingsStatusNoteLabel);
		root.AddChild(_settingsStatusNoteLabel);

		var defaults = CreateSettingsButton("Mặc định", ResetAdvancedSettingsDefaults, primary: false);
		defaults.Position = new Vector2(790f, 622f);
		defaults.Size = new Vector2(92f, 38f);
		root.AddChild(defaults);

		var close = CreateSettingsButton("Đóng", CloseSettingsPanel, primary: false);
		close.Position = new Vector2(890f, 622f);
		close.Size = new Vector2(78f, 38f);
		root.AddChild(close);

		var apply = CreateSettingsButton("Áp dụng", ApplyAdvancedSettingsLive, primary: true);
		apply.Position = new Vector2(976f, 620f);
		apply.Size = new Vector2(168f, 42f);
		apply.AddThemeFontSizeOverride("font_size", 12);
		root.AddChild(apply);
	}

	private void BuildAdvancedPresenceCard(Control parent)
	{
		_advancedPresenceProfileSelect = AddAdvancedOptionRow(parent, 48f, "Mức hiện diện", new[] { "Yên tĩnh", "Cân bằng", "Đồng hành" }, _advancedPresenceProfile, value =>
		{
			_advancedPresenceProfile = value;
			MarkAdvancedChanged("mức hiện diện");
		});

		_advancedCommentaryCadenceSelect = AddAdvancedOptionRow(parent, 90f, "Nhịp bình luận", new[] { "Thưa", "Vừa", "Nhiều" }, _advancedCommentaryCadence, value =>
		{
			_advancedCommentaryCadence = value;
			MarkAdvancedChanged("nhịp bình luận");
		});

		_advancedResponseModeSelect = AddAdvancedOptionRow(parent, 132f, "Chế độ phản hồi", new[] { "Nhanh", "Cân bằng", "Suy nghĩ kỹ" }, _advancedResponseMode, value =>
		{
			_advancedResponseMode = value;
			MarkAdvancedChanged("chế độ phản hồi");
		});

		_advancedResponseLengthSelect = AddAdvancedOptionRow(parent, 174f, "Độ dài trả lời", new[] { "Ngắn", "Vừa", "Chi tiết" }, _advancedResponseLength, value =>
		{
			_advancedResponseLength = value;
			MarkAdvancedChanged("độ dài trả lời");
		});

		_advancedDirectnessSelect = AddAdvancedOptionRow(parent, 216f, "Độ thẳng", new[] { "Mềm", "Cân bằng", "Sắc" }, _advancedDirectness, value =>
		{
			_advancedDirectness = value;
			MarkAdvancedChanged("độ thẳng");
		});

		_advancedFallbackSwitch = AddAdvancedToggleRow(parent, 262f, "Tự dùng model dự phòng", () => _advancedAutoFallback, value => _advancedAutoFallback = value, "model dự phòng");
		_advancedDiscoveryLocalSignalSwitch = AddAdvancedToggleRow(parent, 296f, "Khám phá tín hiệu ngoài phiên", () => _advancedDiscoveryLocalSignal, value => _advancedDiscoveryLocalSignal = value, "tín hiệu khám phá");

		var hint = CreateUiLabel("Yên tĩnh chỉ tắt lời chủ động; Eris vẫn trả lời bình thường khi bạn nói.", 9, SettingsInkSoft);
		hint.Position = new Vector2(18f, 330f);
		hint.Size = new Vector2(parent.Size.X - 36f, 34f);
		hint.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(hint);
	}

	private void BuildAdvancedMemoryTutorCard(Control parent)
	{
		_advancedMemoryProfileSelect = AddAdvancedOptionRow(parent, 48f, "Chế độ ghi nhớ", new[] { "Tối thiểu", "Cân bằng", "Sâu" }, _advancedMemoryProfile, value =>
		{
			_advancedMemoryProfile = value;
			MarkAdvancedChanged("chế độ ghi nhớ");
		});

		_advancedMemoryMaintenanceSwitch = AddAdvancedToggleRow(parent, 94f, "Hợp nhất memory khi rảnh", () => _advancedMemoryMaintenance, value => _advancedMemoryMaintenance = value, "hợp nhất memory");

		_advancedTutorModeSelect = AddAdvancedOptionRow(parent, 130f, "Quyền dùng Tutor", new[] { "Tắt", "Chỉ khi yêu cầu", "Có thể tự đề xuất" }, _advancedTutorMode, value =>
		{
			_advancedTutorMode = value;
			MarkAdvancedChanged("quyền Tutor");
		});

		_advancedTutorQuickSearchSwitch = AddAdvancedToggleRow(parent, 176f, "Cho phép tra cứu nhanh", () => _advancedTutorQuickSearch, value => _advancedTutorQuickSearch = value, "tra cứu nhanh");
		_advancedTutorResearchSwitch = AddAdvancedToggleRow(parent, 210f, "Cho phép nghiên cứu sâu", () => _advancedTutorResearch, value => _advancedTutorResearch = value, "nghiên cứu sâu");
		_advancedTutorPersistSwitch = AddAdvancedToggleRow(parent, 244f, "Lưu kết quả nghiên cứu vào memory", () => _advancedTutorPersistResearchMemory, value => _advancedTutorPersistResearchMemory = value, "lưu nghiên cứu");

		var openMemory = CreateSettingsButton("Mở trình quản lý memory", OpenAdvancedMemoryDirectory, primary: false);
		openMemory.Position = new Vector2(18f, 286f);
		openMemory.Size = new Vector2(parent.Size.X - 36f, 34f);
		openMemory.AddThemeFontSizeOverride("font_size", 10);
		parent.AddChild(openMemory);

		var hint = CreateUiLabel("Mặc định Tutor chỉ chạy khi bạn yêu cầu, không tự search chen vào chat thường.", 9, SettingsInkSoft);
		hint.Position = new Vector2(18f, 328f);
		hint.Size = new Vector2(parent.Size.X - 36f, 36f);
		hint.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(hint);
	}

	private void BuildAdvancedProjectCard(Control parent)
	{
		_advancedProjectModeSelect = AddAdvancedOptionRow(parent, 48f, "Quyền thao tác", new[] { "Tắt", "Chỉ đọc", "Tự làm an toàn", "Toàn quyền" }, _advancedProjectMode, value =>
		{
			_advancedProjectMode = value;
			MarkAdvancedChanged("quyền project");
		});

		_advancedRepairAttemptsSelect = AddAdvancedOptionRow(parent, 90f, "Vòng tự sửa", new[] { "0", "1", "2", "3" }, _advancedMaxRepairAttempts.ToString(), value =>
		{
			_advancedMaxRepairAttempts = ParseAdvancedInt(value, 2, 0, 5);
			MarkAdvancedChanged("vòng tự sửa");
		});

		_advancedProjectAskSwitch = AddAdvancedToggleRow(parent, 136f, "Hỏi khi thiếu dữ kiện quan trọng", () => _advancedProjectAskWhenUncertain, value => _advancedProjectAskWhenUncertain = value, "hỏi khi thiếu dữ kiện");
		_advancedProjectProceedSwitch = AddAdvancedToggleRow(parent, 168f, "Tiếp tục khi chỉ thiếu chi tiết nhỏ", () => _advancedProjectProceedWithoutReply, value => _advancedProjectProceedWithoutReply = value, "tiếp tục không chờ");
		_advancedProjectRollbackSwitch = AddAdvancedToggleRow(parent, 200f, "Tự rollback khi thất bại", () => _advancedProjectAutoRollback, value => _advancedProjectAutoRollback = value, "rollback tự động");
		_advancedProjectPackageSwitch = AddAdvancedToggleRow(parent, 232f, "Tự đóng gói patch sau khi xong", () => _advancedProjectAutoPackage, value => _advancedProjectAutoPackage = value, "đóng gói patch");

		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.32f),
			Position = new Vector2(18f, 266f),
			Size = new Vector2(parent.Size.X - 36f, 1f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(divider);

		_advancedEffectiveSummaryLabel = CreateUiLabel("Brain: chưa đọc cấu hình đang hiệu lực", 10, SettingsInk);
		_advancedEffectiveSummaryLabel.Position = new Vector2(18f, 274f);
		_advancedEffectiveSummaryLabel.Size = new Vector2(parent.Size.X - 36f, 22f);
		StyleSettingsSingleLineLabel(_advancedEffectiveSummaryLabel);
		parent.AddChild(_advancedEffectiveSummaryLabel);

		_advancedEffectiveDetailLabel = CreateUiLabel("Memory · Tutor · Project: —", 9, SettingsInkSoft);
		_advancedEffectiveDetailLabel.Position = new Vector2(18f, 298f);
		_advancedEffectiveDetailLabel.Size = new Vector2(parent.Size.X - 36f, 20f);
		StyleSettingsSingleLineLabel(_advancedEffectiveDetailLabel);
		parent.AddChild(_advancedEffectiveDetailLabel);

		_advancedApplyResultLabel = CreateUiLabel("Chưa có ACK từ Brain.", 8, SettingsInkSoft);
		_advancedApplyResultLabel.Position = new Vector2(18f, 320f);
		_advancedApplyResultLabel.Size = new Vector2(parent.Size.X - 36f, 16f);
		StyleSettingsSingleLineLabel(_advancedApplyResultLabel);
		parent.AddChild(_advancedApplyResultLabel);

		var refresh = CreateSettingsButton("Đọc cấu hình đang chạy", RequestAdvancedRuntimeSnapshot, primary: false);
		refresh.Position = new Vector2(18f, 340f);
		refresh.Size = new Vector2(parent.Size.X - 36f, 30f);
		refresh.AddThemeFontSizeOverride("font_size", 9);
		parent.AddChild(refresh);
	}

	private OptionButton AddAdvancedOptionRow(Control parent, float y, string label, string[] values, string selected, Action<string> onChanged)
	{
		float rowWidth = Mathf.Max(220f, parent.Size.X - 36f);
		var row = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(rowWidth, 34f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(new Color(1f, 0.98f, 0.99f, 0.62f), SettingsBorder, 11));
		parent.AddChild(row);

		var left = CreateUiLabel(label, 9, SettingsInkSoft);
		left.Position = new Vector2(10f, 6f);
		left.Size = new Vector2(130f, 22f);
		StyleSettingsSingleLineLabel(left);
		row.AddChild(left);

		var option = new OptionButton
		{
			Position = new Vector2(140f, 3f),
			Size = new Vector2(rowWidth - 150f, 28f),
			MouseFilter = Control.MouseFilterEnum.Stop,
			ClipText = true,
			TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
		};
		option.AddThemeFontSizeOverride("font_size", 9);
		option.AddThemeColorOverride("font_color", SettingsInk);
		int selectedIndex = 0;
		for (int i = 0; i < values.Length; i++)
		{
			option.AddItem(values[i]);
			if (string.Equals(values[i], selected, StringComparison.OrdinalIgnoreCase))
				selectedIndex = i;
		}
		option.Select(selectedIndex);
		option.ItemSelected += index => onChanged?.Invoke(option.GetItemText((int)index));
		row.AddChild(option);
		return option;
	}

	private TextureRect AddAdvancedToggleRow(Control parent, float y, string label, Func<bool> getter, Action<bool> setter, string changedName)
	{
		var text = CreateUiLabel(label, 9, SettingsInk);
		text.Position = new Vector2(18f, y + 2f);
		text.Size = new Vector2(parent.Size.X - 86f, 22f);
		StyleSettingsSingleLineLabel(text);
		parent.AddChild(text);

		TextureRect toggle = null;
		toggle = AddSettingsSwitch(parent, new Vector2(parent.Size.X - 58f, y - 2f), getter(), () =>
		{
			bool value = !getter();
			setter(value);
			ApplySettingsSwitch(toggle, value);
			MarkAdvancedChanged(changedName);
		});
		toggle.Size = new Vector2(40f, 24f);
		toggle.CustomMinimumSize = toggle.Size;
		return toggle;
	}

	private void MarkAdvancedChanged(string field)
	{
		SetSettingsStatus($"Đã đổi {field}. Bấm Áp dụng để lưu và cập nhật Brain.");
		if (_advancedApplyResultLabel != null)
			_advancedApplyResultLabel.Text = "Có thay đổi chưa áp dụng.";
	}

	private void ResetAdvancedSettingsDefaults()
	{
		_advancedPresenceProfile = "Đồng hành";
		_advancedCommentaryCadence = "Vừa";
		_advancedResponseMode = "Cân bằng";
		_advancedResponseLength = "Vừa";
		_advancedDirectness = "Cân bằng";
		_advancedMemoryProfile = "Cân bằng";
		_advancedTutorMode = "Chỉ khi yêu cầu";
		_advancedProjectMode = "Tự làm an toàn";
		_advancedMaxRepairAttempts = 2;
		_advancedAutoFallback = true;
		_advancedDiscoveryLocalSignal = true;
		_advancedMemoryMaintenance = true;
		_advancedTutorQuickSearch = true;
		_advancedTutorResearch = true;
		_advancedTutorPersistResearchMemory = false;
		_advancedProjectAskWhenUncertain = true;
		_advancedProjectProceedWithoutReply = true;
		_advancedProjectAutoRollback = true;
		_advancedProjectAutoPackage = true;
		BuildSettingsPanelUi();
		SetSettingsStatus("Đã khôi phục mặc định Advanced trong bản nháp. Bấm Áp dụng để ghi thật.");
	}

	private void SyncAdvancedSettingsInputsToFields()
	{
		_advancedPresenceProfile = ReadSelected(_advancedPresenceProfileSelect, _advancedPresenceProfile);
		_advancedCommentaryCadence = ReadSelected(_advancedCommentaryCadenceSelect, _advancedCommentaryCadence);
		_advancedResponseMode = ReadSelected(_advancedResponseModeSelect, _advancedResponseMode);
		_advancedResponseLength = ReadSelected(_advancedResponseLengthSelect, _advancedResponseLength);
		_advancedDirectness = ReadSelected(_advancedDirectnessSelect, _advancedDirectness);
		_advancedMemoryProfile = ReadSelected(_advancedMemoryProfileSelect, _advancedMemoryProfile);
		_advancedTutorMode = ReadSelected(_advancedTutorModeSelect, _advancedTutorMode);
		_advancedProjectMode = ReadSelected(_advancedProjectModeSelect, _advancedProjectMode);
		if (_advancedRepairAttemptsSelect != null && _advancedRepairAttemptsSelect.Selected >= 0)
			_advancedMaxRepairAttempts = ParseAdvancedInt(_advancedRepairAttemptsSelect.GetItemText(_advancedRepairAttemptsSelect.Selected), _advancedMaxRepairAttempts, 0, 5);
	}

	private static string ReadSelected(OptionButton option, string fallback)
	{
		return option != null && option.Selected >= 0 ? option.GetItemText(option.Selected) : fallback;
	}

	private void LoadAdvancedSettingsState(JsonElement root)
	{
		_advancedPresenceProfile = ReadString(root, "advanced_presence_profile", _advancedPresenceProfile);
		_advancedCommentaryCadence = ReadString(root, "advanced_commentary_cadence", _advancedCommentaryCadence);
		_advancedResponseMode = ReadString(root, "advanced_response_mode", _advancedResponseMode);
		_advancedResponseLength = ReadString(root, "advanced_response_length", _advancedResponseLength);
		_advancedDirectness = ReadString(root, "advanced_directness", _advancedDirectness);
		_advancedMemoryProfile = ReadString(root, "advanced_memory_profile", _advancedMemoryProfile);
		_advancedTutorMode = ReadString(root, "advanced_tutor_mode", _advancedTutorMode);
		_advancedProjectMode = ReadString(root, "advanced_project_mode", _advancedProjectMode);
		_advancedMaxRepairAttempts = ReadAdvancedInt(root, "advanced_project_repair_attempts", _advancedMaxRepairAttempts, 0, 5);

		if (TryReadBool(root, "advanced_auto_fallback", out bool fallback)) _advancedAutoFallback = fallback;
		if (TryReadBool(root, "advanced_discovery_local_signal", out bool localSignal)) _advancedDiscoveryLocalSignal = localSignal;
		if (TryReadBool(root, "advanced_memory_maintenance", out bool maintenance)) _advancedMemoryMaintenance = maintenance;
		if (TryReadBool(root, "advanced_tutor_quick_search", out bool quick)) _advancedTutorQuickSearch = quick;
		if (TryReadBool(root, "advanced_tutor_research", out bool research)) _advancedTutorResearch = research;
		if (TryReadBool(root, "advanced_tutor_persist", out bool persist)) _advancedTutorPersistResearchMemory = persist;
		if (TryReadBool(root, "advanced_project_ask", out bool ask)) _advancedProjectAskWhenUncertain = ask;
		if (TryReadBool(root, "advanced_project_proceed", out bool proceed)) _advancedProjectProceedWithoutReply = proceed;
		if (TryReadBool(root, "advanced_project_rollback", out bool rollback)) _advancedProjectAutoRollback = rollback;
		if (TryReadBool(root, "advanced_project_package", out bool package)) _advancedProjectAutoPackage = package;

		// Migration mềm từ UI Advanced cũ. Chỉ dùng khi schema mới chưa từng được lưu.
		if (!root.TryGetProperty("advanced_response_mode", out _))
		{
			string oldReasoning = ReadString(root, "reasoning_effort", "medium");
			_advancedResponseMode = oldReasoning == "high" ? "Suy nghĩ kỹ" : oldReasoning == "low" ? "Nhanh" : "Cân bằng";
			int oldTokens = ReadAdvancedInt(root, "completion_chat_tokens", 1280, 384, 4096);
			_advancedResponseLength = oldTokens <= 900 ? "Ngắn" : oldTokens >= 1800 ? "Chi tiết" : "Vừa";
			string oldRetort = ReadString(root, "retort_mode", "balanced");
			_advancedDirectness = oldRetort == "sharp" ? "Sắc" : oldRetort == "restrained" || oldRetort == "off" ? "Mềm" : "Cân bằng";
		}
	}

	private void LoadAdvancedSettingsFromSecrets(JsonElement root)
	{
		// Env chỉ còn là migration/bootstrap. runtime_settings.json mới là authority.
		string reasoning = ReadSecretString(root, "ERIS_REASONING_EFFORT", "").Trim().ToLowerInvariant();
		if (reasoning.Length > 0)
			_advancedResponseMode = reasoning == "high" ? "Suy nghĩ kỹ" : reasoning == "low" ? "Nhanh" : "Cân bằng";

		int tokens = ParseAdvancedInt(ReadSecretString(root, "ERIS_LLM_COMPLETION_CHAT_TOKENS", "1280"), 1280, 384, 4096);
		_advancedResponseLength = tokens <= 900 ? "Ngắn" : tokens >= 1800 ? "Chi tiết" : "Vừa";
		_advancedAutoFallback = !IsTruthy(ReadSecretString(root, "ERIS_NO_FALLBACK_MODE", _advancedAutoFallback ? "0" : "1"));

		string retort = ReadSecretString(root, "ERIS_RETORT_MODE", "balanced").Trim().ToLowerInvariant();
		_advancedDirectness = retort == "sharp" ? "Sắc" : retort == "restrained" || retort == "off" ? "Mềm" : "Cân bằng";

		bool commentary = IsTruthy(ReadSecretString(root, "ERIS_COMMENTARY_ENABLED", "1"));
		bool initiative = IsTruthy(ReadSecretString(root, "ERIS_INITIATIVE_ENABLED", "1"));
		bool discovery = IsTruthy(ReadSecretString(root, "ERIS_DISCOVERY_ENABLED", "1"));
		_advancedPresenceProfile = !commentary && !initiative ? "Yên tĩnh" : discovery ? "Đồng hành" : "Cân bằng";
		int desired = ParseAdvancedInt(ReadSecretString(root, "ERIS_COMMENTARY_DESIRED_INTERVAL_SECONDS", "60"), 60, 20, 1800);
		_advancedCommentaryCadence = desired <= 45 ? "Nhiều" : desired <= 100 ? "Vừa" : "Thưa";
		_advancedDiscoveryLocalSignal = IsTruthy(ReadSecretString(root, "ERIS_DISCOVERY_LOCAL_SIGNAL_ENABLED", _advancedDiscoveryLocalSignal ? "1" : "0"));

		bool backgroundDigest = IsTruthy(ReadSecretString(root, "ERIS_MEMORY_BACKGROUND_DIGEST", "1"));
		_advancedMemoryMaintenance = IsTruthy(ReadSecretString(root, "ERIS_MEMORY_DIGEST_MAINTENANCE", _advancedMemoryMaintenance ? "1" : "0"));
		string memoryBudget = ReadSecretString(root, "ERIS_MEMORY_MAINTENANCE_BUDGET", "small").Trim().ToLowerInvariant();
		_advancedMemoryProfile = !backgroundDigest && !_advancedMemoryMaintenance ? "Tối thiểu" : memoryBudget == "large" || memoryBudget == "medium" ? "Sâu" : "Cân bằng";

		bool tutorEnabled = IsTruthy(ReadSecretString(root, "ERIS_TUTOR_ENABLED", "1"));
		bool tutorSelf = IsTruthy(ReadSecretString(root, "ERIS_TUTOR_SELF_SEARCH_ENABLED", "0"));
		_advancedTutorMode = !tutorEnabled ? "Tắt" : tutorSelf ? "Có thể tự đề xuất" : "Chỉ khi yêu cầu";
		_advancedTutorQuickSearch = IsTruthy(ReadSecretString(root, "ERIS_TUTOR_QUICK_SEARCH_ENABLED", _advancedTutorQuickSearch ? "1" : "0"));
		_advancedTutorResearch = IsTruthy(ReadSecretString(root, "ERIS_TUTOR_RESEARCH_ENABLED", _advancedTutorResearch ? "1" : "0"));
		_advancedTutorPersistResearchMemory = IsTruthy(ReadSecretString(root, "ERIS_TUTOR_RESEARCH_PERSIST", _advancedTutorPersistResearchMemory ? "1" : "0"));

		bool projectEnabled = IsTruthy(ReadSecretString(root, "ERIS_PROJECT_OPS_ENABLED", "1"));
		string autonomy = ReadSecretString(root, "ERIS_PROJECT_AUTONOMY_MODE", "guarded").Trim().ToLowerInvariant();
		_advancedProjectMode = !projectEnabled || autonomy == "off" ? "Tắt" : autonomy == "read_only" ? "Chỉ đọc" : autonomy == "full" ? "Toàn quyền" : "Tự làm an toàn";
		_advancedProjectAskWhenUncertain = IsTruthy(ReadSecretString(root, "ERIS_PROJECT_ASK_WHEN_UNCERTAIN", _advancedProjectAskWhenUncertain ? "1" : "0"));
		_advancedProjectProceedWithoutReply = IsTruthy(ReadSecretString(root, "ERIS_PROJECT_PROCEED_WITHOUT_REPLY", _advancedProjectProceedWithoutReply ? "1" : "0"));
		_advancedProjectAutoRollback = IsTruthy(ReadSecretString(root, "ERIS_PROJECT_AUTO_ROLLBACK", _advancedProjectAutoRollback ? "1" : "0"));
		_advancedProjectAutoPackage = IsTruthy(ReadSecretString(root, "ERIS_PROJECT_AUTO_PACKAGE", _advancedProjectAutoPackage ? "1" : "0"));
		_advancedMaxRepairAttempts = ParseAdvancedInt(ReadSecretString(root, "ERIS_PROJECT_MAX_REPAIR_ATTEMPTS", _advancedMaxRepairAttempts.ToString()), _advancedMaxRepairAttempts, 0, 5);
	}

	private void AddAdvancedSettingsToState(JsonObject state)
	{
		state["advanced_presence_profile"] = _advancedPresenceProfile;
		state["advanced_commentary_cadence"] = _advancedCommentaryCadence;
		state["advanced_response_mode"] = _advancedResponseMode;
		state["advanced_response_length"] = _advancedResponseLength;
		state["advanced_directness"] = _advancedDirectness;
		state["advanced_memory_profile"] = _advancedMemoryProfile;
		state["advanced_tutor_mode"] = _advancedTutorMode;
		state["advanced_project_mode"] = _advancedProjectMode;
		state["advanced_project_repair_attempts"] = _advancedMaxRepairAttempts;
		state["advanced_auto_fallback"] = _advancedAutoFallback;
		state["advanced_discovery_local_signal"] = _advancedDiscoveryLocalSignal;
		state["advanced_memory_maintenance"] = _advancedMemoryMaintenance;
		state["advanced_tutor_quick_search"] = _advancedTutorQuickSearch;
		state["advanced_tutor_research"] = _advancedTutorResearch;
		state["advanced_tutor_persist"] = _advancedTutorPersistResearchMemory;
		state["advanced_project_ask"] = _advancedProjectAskWhenUncertain;
		state["advanced_project_proceed"] = _advancedProjectProceedWithoutReply;
		state["advanced_project_rollback"] = _advancedProjectAutoRollback;
		state["advanced_project_package"] = _advancedProjectAutoPackage;
	}

	private Dictionary<string, string> BuildAdvancedEnvironmentValues()
	{
		GetResponseSettings(out string reasoning, out int timeout, out int tokens);
		GetPresenceSettings(out bool commentary, out bool initiative, out bool discovery, out int desired, out int maximumQuiet, out int minimumSilence, out int initiativeInterval, out int initiativeBudget);
		GetDirectnessSettings(out string retort, out bool allowSharp, out bool allowCold, out string styleRepair);
		GetMemorySettings(out bool backgroundDigest, out bool maintenance, out string maintenanceBudget);
		GetTutorSettings(out bool tutorEnabled, out bool tutorSelfSearch);
		GetProjectSettings(out bool projectEnabled, out string autonomyMode);

		return new Dictionary<string, string>(StringComparer.Ordinal)
		{
			["ERIS_REASONING_EFFORT"] = reasoning,
			["ERIS_LLM_COMPLETION_CHAT_TOKENS"] = tokens.ToString(),
			["ERIS_LLM_TIMEOUT_CHAT_S"] = timeout.ToString(),
			["ERIS_NO_FALLBACK_MODE"] = _advancedAutoFallback ? "0" : "1",
			["ERIS_RETORT_MODE"] = retort,
			["ERIS_ALLOW_SHARP_RETORT"] = allowSharp ? "1" : "0",
			["ERIS_ALLOW_COLD_BOUNDARY"] = allowCold ? "1" : "0",
			["ERIS_STYLE_REPAIR_MODE"] = styleRepair,
			["ERIS_COMMENTARY_ENABLED"] = commentary ? "1" : "0",
			["ERIS_COMMENTARY_DESIRED_INTERVAL_SECONDS"] = desired.ToString(),
			["ERIS_COMMENTARY_MAXIMUM_QUIET_SECONDS"] = maximumQuiet.ToString(),
			["ERIS_COMMENTARY_MINIMUM_USER_SILENCE_SECONDS"] = minimumSilence.ToString(),
			["ERIS_INITIATIVE_ENABLED"] = initiative ? "1" : "0",
			["ERIS_INITIATIVE_MINIMUM_INTERVAL_SECONDS"] = initiativeInterval.ToString(),
			["ERIS_INITIATIVE_MINIMUM_USER_SILENCE_SECONDS"] = minimumSilence.ToString(),
			["ERIS_INITIATIVE_GLOBAL_HOURLY_BUDGET"] = initiativeBudget.ToString(),
			["ERIS_DISCOVERY_ENABLED"] = discovery ? "1" : "0",
			["ERIS_DISCOVERY_LOCAL_SIGNAL_ENABLED"] = discovery && _advancedDiscoveryLocalSignal ? "1" : "0",
			["ERIS_MEMORY_BACKGROUND_DIGEST"] = backgroundDigest ? "1" : "0",
			["ERIS_MEMORY_DIGEST_MAINTENANCE"] = maintenance ? "1" : "0",
			["ERIS_MEMORY_MAINTENANCE_BUDGET"] = maintenanceBudget,
			["ERIS_TUTOR_ENABLED"] = tutorEnabled ? "1" : "0",
			["ERIS_TUTOR_QUICK_SEARCH_ENABLED"] = tutorEnabled && _advancedTutorQuickSearch ? "1" : "0",
			["ERIS_TUTOR_RESEARCH_ENABLED"] = tutorEnabled && _advancedTutorResearch ? "1" : "0",
			["ERIS_TUTOR_SELF_SEARCH_ENABLED"] = tutorSelfSearch ? "1" : "0",
			["ERIS_TUTOR_STALE_GUARD"] = "1",
			["ERIS_TUTOR_RESEARCH_PERSIST"] = tutorEnabled && _advancedTutorPersistResearchMemory ? "1" : "0",
			["ERIS_PROJECT_OPS_ENABLED"] = projectEnabled ? "1" : "0",
			["ERIS_PROJECT_AUTONOMY_MODE"] = autonomyMode,
			["ERIS_PROJECT_ASK_WHEN_UNCERTAIN"] = _advancedProjectAskWhenUncertain ? "1" : "0",
			["ERIS_PROJECT_PROCEED_WITHOUT_REPLY"] = _advancedProjectProceedWithoutReply ? "1" : "0",
			["ERIS_PROJECT_AUTO_ROLLBACK"] = _advancedProjectAutoRollback ? "1" : "0",
			["ERIS_PROJECT_AUTO_PACKAGE"] = _advancedProjectAutoPackage ? "1" : "0",
			["ERIS_PROJECT_MAX_REPAIR_ATTEMPTS"] = _advancedMaxRepairAttempts.ToString(),
		};
	}

	private void WriteAdvancedSettingsToSecrets(JsonObject env)
	{
		// Legacy no-op. Runtime policy thuộc Brain runtime store và chỉ được
		// thay đổi qua settings_update, không ghi lén vào secrets.json.
	}

	private void AppendAdvancedSettingsToDotEnv(Dictionary<string, string> updates)
	{
		// Legacy no-op. .env chỉ giữ bootstrap route, không phải mirror schema runtime.
	}

	private void ApplyAdvancedSettingsLive()
	{
		SyncAdvancedSettingsInputsToFields();
		SaveSettingsState(false, writeSecrets: false);
		if (_advancedApplyResultLabel != null)
			_advancedApplyResultLabel.Text = "Đang chờ Brain xác nhận…";
		SendAdvancedRuntimeSettingsUpdate();
	}

	private JsonObject BuildAdvancedRuntimeSettingsPatch()
	{
		GetResponseSettings(out string reasoning, out int timeout, out int tokens);
		GetPresenceSettings(out bool commentary, out bool initiative, out bool discovery, out int desired, out int maximumQuiet, out int minimumSilence, out int initiativeInterval, out int initiativeBudget);
		GetDirectnessSettings(out string retort, out bool allowSharp, out bool allowCold, out string styleRepair);
		GetMemorySettings(out bool backgroundDigest, out bool maintenance, out string maintenanceBudget);
		GetTutorSettings(out bool tutorEnabled, out bool tutorSelfSearch);
		GetProjectSettings(out bool projectEnabled, out string autonomyMode);

		return new JsonObject
		{
			["chat"] = new JsonObject
			{
				["reasoning_effort"] = reasoning,
				["completion_chat_tokens"] = tokens,
				["chat_timeout_seconds"] = timeout,
				["auto_fallback"] = _advancedAutoFallback,
			},
			["personality"] = new JsonObject
			{
				["retort_mode"] = retort,
				["allow_sharp_retort"] = allowSharp,
				["allow_cold_boundary"] = allowCold,
				["style_repair_mode"] = styleRepair,
			},
			["commentary"] = new JsonObject
			{
				["enabled"] = commentary,
				["desired_interval_seconds"] = desired,
				["maximum_quiet_seconds"] = maximumQuiet,
				["startup_delay_seconds"] = commentary ? 8 : 20,
				["minimum_user_silence_seconds"] = minimumSilence,
			},
			["initiative"] = new JsonObject
			{
				["enabled"] = initiative,
				["global_hourly_budget"] = initiativeBudget,
				["minimum_interval_seconds"] = initiativeInterval,
				["minimum_user_silence_seconds"] = minimumSilence,
			},
			["discovery"] = new JsonObject
			{
				["enabled"] = discovery,
				["local_signal_enabled"] = discovery && _advancedDiscoveryLocalSignal,
			},
			["memory"] = new JsonObject
			{
				["background_digest"] = backgroundDigest,
				["maintenance_enabled"] = maintenance,
				["maintenance_budget"] = maintenanceBudget,
			},
			["tutor"] = new JsonObject
			{
				["enabled"] = tutorEnabled,
				["quick_search_enabled"] = tutorEnabled && _advancedTutorQuickSearch,
				["research_enabled"] = tutorEnabled && _advancedTutorResearch,
				["self_search_enabled"] = tutorSelfSearch,
				["stale_result_guard_enabled"] = true,
				["persist_research_memory"] = tutorEnabled && _advancedTutorPersistResearchMemory,
			},
			["project_ops"] = new JsonObject
			{
				["enabled"] = projectEnabled,
				["autonomy_mode"] = autonomyMode,
				["ask_when_uncertain"] = _advancedProjectAskWhenUncertain,
				["proceed_without_user_reply"] = _advancedProjectProceedWithoutReply,
				["auto_rollback_on_failure"] = _advancedProjectAutoRollback,
				["auto_package_patch"] = _advancedProjectAutoPackage,
				["max_repair_attempts"] = _advancedMaxRepairAttempts,
			},
		};
	}

	private void PersistRuntimeSettingsJson()
	{
		// Legacy no-op. Body không sở hữu runtime_settings.json.
	}

	private static void MergeAdvancedRuntimePatch(JsonObject target, JsonObject patch)
	{
		foreach (var pair in patch)
		{
			if (pair.Value is JsonObject patchObject)
			{
				var targetObject = target[pair.Key] as JsonObject ?? new JsonObject();
				MergeAdvancedRuntimePatch(targetObject, patchObject);
				target[pair.Key] = targetObject;
			}
			else
			{
				target[pair.Key] = pair.Value?.DeepClone();
			}
		}
	}

	private void SendAdvancedRuntimeSettingsUpdate()
	{
		if (_chatSend == null)
		{
			SetSettingsStatus("Chưa có UDP transport; đã lưu file nhưng chưa áp dụng live.");
			if (_advancedApplyResultLabel != null)
				_advancedApplyResultLabel.Text = "Đã lưu file · Brain chưa kết nối.";
			return;
		}
		var payload = new
		{
			type = "settings_update",
			scope = "advanced",
			settings = BuildAdvancedRuntimeSettingsPatch(),
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		SetSettingsStatus(error == Error.Ok
			? "Đã lưu và gửi Advanced xuống Brain. Đang chờ ACK chi tiết."
			: $"Không gửi được Advanced live: {error}");
	}

	private void RequestAdvancedRuntimeSnapshot()
	{
		if (_chatSend == null)
		{
			if (_advancedApplyResultLabel != null)
				_advancedApplyResultLabel.Text = "UDP chưa sẵn sàng; chưa đọc được Brain.";
			return;
		}
		var payload = new { type = "settings_health" };
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		if (_advancedApplyResultLabel != null)
			_advancedApplyResultLabel.Text = error == Error.Ok ? "Đang đọc cấu hình hiệu lực…" : $"Không gửi được health request: {error}";
	}

	private void ApplyAdvancedSettingsAck(JsonElement ack)
	{
		ApplyRuntimeSettingsAck(ack);
	}

	private void ApplyAdvancedHealthSnapshot(JsonElement healthAck)
	{
		ApplyRuntimeSettingsHealthSnapshot(healthAck);
	}

	private void ApplyAdvancedEffectiveSnapshot(JsonElement effective)
	{
		if (!effective.TryGetProperty("settings", out JsonElement settings) || settings.ValueKind != JsonValueKind.Object)
			return;

		string presence = DerivePresenceProfile(settings);
		string response = DeriveResponseMode(settings);
		string length = DeriveResponseLength(settings);
		string memory = DeriveMemoryProfile(settings);
		string tutor = DeriveTutorMode(settings);
		string project = DeriveProjectMode(settings);

		if (_advancedEffectiveSummaryLabel != null)
			_advancedEffectiveSummaryLabel.Text = $"Đang chạy: {presence} · {response}/{length}";
		if (_advancedEffectiveDetailLabel != null)
			_advancedEffectiveDetailLabel.Text = $"Memory {memory} · Tutor {tutor} · Project {project}";

		int revision = effective.TryGetProperty("revision", out JsonElement revisionEl) && revisionEl.TryGetInt32(out int parsedRevision) ? parsedRevision : 0;
		if (_advancedEffectiveSummaryLabel != null && revision > 0)
			_advancedEffectiveSummaryLabel.TooltipText = $"Runtime settings revision {revision}";
	}

	private void OpenAdvancedMemoryDirectory()
	{
		try
		{
			string memoryDir = Path.Combine(GetRuntimeBaseDirForSettings(), "memory_core");
			Directory.CreateDirectory(memoryDir);
			OS.ShellOpen(memoryDir);
			SetSettingsStatus($"Đã mở memory: {memoryDir}");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Không mở được memory: {ex.Message}");
		}
	}

	private void GetResponseSettings(out string reasoning, out int timeout, out int tokens)
	{
		reasoning = _advancedResponseMode == "Nhanh" ? "low" : _advancedResponseMode == "Suy nghĩ kỹ" ? "high" : "medium";
		timeout = _advancedResponseMode == "Nhanh" ? 20 : _advancedResponseMode == "Suy nghĩ kỹ" ? 45 : 30;
		tokens = _advancedResponseLength == "Ngắn" ? 768 : _advancedResponseLength == "Chi tiết" ? 2048 : 1280;
	}

	private void GetPresenceSettings(out bool commentary, out bool initiative, out bool discovery, out int desired, out int maximumQuiet, out int minimumSilence, out int initiativeInterval, out int initiativeBudget)
	{
		commentary = _advancedPresenceProfile != "Yên tĩnh";
		initiative = _advancedPresenceProfile != "Yên tĩnh";
		discovery = _advancedPresenceProfile == "Đồng hành";
		switch (_advancedCommentaryCadence)
		{
			case "Thưa":
				desired = 180;
				maximumQuiet = 360;
				minimumSilence = 18;
				initiativeInterval = 120;
				initiativeBudget = 20;
				break;
			case "Nhiều":
				desired = 35;
				maximumQuiet = 75;
				minimumSilence = 10;
				initiativeInterval = 40;
				initiativeBudget = 60;
				break;
			default:
				desired = 60;
				maximumQuiet = 120;
				minimumSilence = 12;
				initiativeInterval = 60;
				initiativeBudget = 40;
				break;
		}
	}

	private void GetDirectnessSettings(out string retort, out bool allowSharp, out bool allowCold, out string styleRepair)
	{
		if (_advancedDirectness == "Mềm")
		{
			retort = "restrained";
			allowSharp = false;
			allowCold = false;
			styleRepair = "soft";
			return;
		}
		if (_advancedDirectness == "Sắc")
		{
			retort = "sharp";
			allowSharp = true;
			allowCold = true;
			styleRepair = "direct";
			return;
		}
		retort = "balanced";
		allowSharp = true;
		allowCold = true;
		styleRepair = "self_aware_dry";
	}

	private void GetMemorySettings(out bool backgroundDigest, out bool maintenance, out string maintenanceBudget)
	{
		if (_advancedMemoryProfile == "Tối thiểu")
		{
			backgroundDigest = false;
			maintenance = false;
			maintenanceBudget = "small";
			return;
		}
		backgroundDigest = true;
		maintenance = _advancedMemoryProfile == "Sâu" || _advancedMemoryMaintenance;
		maintenanceBudget = _advancedMemoryProfile == "Sâu" ? "large" : "small";
	}

	private void GetTutorSettings(out bool enabled, out bool selfSearch)
	{
		enabled = _advancedTutorMode != "Tắt";
		selfSearch = enabled && _advancedTutorMode == "Có thể tự đề xuất";
	}

	private void GetProjectSettings(out bool enabled, out string autonomyMode)
	{
		enabled = _advancedProjectMode != "Tắt";
		autonomyMode = _advancedProjectMode switch
		{
			"Chỉ đọc" => "read_only",
			"Toàn quyền" => "full",
			"Tự làm an toàn" => "guarded",
			_ => "off",
		};
	}

	private static string DerivePresenceProfile(JsonElement settings)
	{
		bool commentary = ReadNestedBool(settings, "commentary", "enabled", true);
		bool initiative = ReadNestedBool(settings, "initiative", "enabled", true);
		bool discovery = ReadNestedBool(settings, "discovery", "enabled", false);
		return !commentary && !initiative ? "Yên tĩnh" : discovery ? "Đồng hành" : "Cân bằng";
	}

	private static string DeriveResponseMode(JsonElement settings)
	{
		string reasoning = ReadNestedString(settings, "chat", "reasoning_effort", "medium");
		return reasoning == "low" ? "Nhanh" : reasoning == "high" ? "Suy nghĩ kỹ" : "Cân bằng";
	}

	private static string DeriveResponseLength(JsonElement settings)
	{
		int tokens = ReadNestedInt(settings, "chat", "completion_chat_tokens", 1280);
		return tokens <= 900 ? "Ngắn" : tokens >= 1800 ? "Chi tiết" : "Vừa";
	}

	private static string DeriveMemoryProfile(JsonElement settings)
	{
		bool digest = ReadNestedBool(settings, "memory", "background_digest", true);
		bool maintenance = ReadNestedBool(settings, "memory", "maintenance_enabled", true);
		string budget = ReadNestedString(settings, "memory", "maintenance_budget", "small");
		return !digest && !maintenance ? "Tối thiểu" : budget == "large" || budget == "medium" ? "Sâu" : "Cân bằng";
	}

	private static string DeriveTutorMode(JsonElement settings)
	{
		bool enabled = ReadNestedBool(settings, "tutor", "enabled", true);
		bool selfSearch = ReadNestedBool(settings, "tutor", "self_search_enabled", false);
		return !enabled ? "Tắt" : selfSearch ? "Tự đề xuất" : "Theo yêu cầu";
	}

	private static string DeriveProjectMode(JsonElement settings)
	{
		bool enabled = ReadNestedBool(settings, "project_ops", "enabled", true);
		string mode = ReadNestedString(settings, "project_ops", "autonomy_mode", "guarded");
		if (!enabled || mode == "off") return "Tắt";
		if (mode == "read_only") return "Chỉ đọc";
		if (mode == "full") return "Toàn quyền";
		return "An toàn";
	}

	private static bool ReadNestedBool(JsonElement root, string section, string name, bool fallback)
	{
		if (!root.TryGetProperty(section, out JsonElement group) || group.ValueKind != JsonValueKind.Object || !group.TryGetProperty(name, out JsonElement item))
			return fallback;
		if (item.ValueKind == JsonValueKind.True) return true;
		if (item.ValueKind == JsonValueKind.False) return false;
		if (item.ValueKind == JsonValueKind.String) return IsTruthy(item.GetString());
		return fallback;
	}

	private static int ReadNestedInt(JsonElement root, string section, string name, int fallback)
	{
		if (!root.TryGetProperty(section, out JsonElement group) || group.ValueKind != JsonValueKind.Object || !group.TryGetProperty(name, out JsonElement item))
			return fallback;
		if (item.ValueKind == JsonValueKind.Number && item.TryGetInt32(out int value)) return value;
		return item.ValueKind == JsonValueKind.String ? ParseAdvancedInt(item.GetString(), fallback, int.MinValue, int.MaxValue) : fallback;
	}

	private static string ReadNestedString(JsonElement root, string section, string name, string fallback)
	{
		if (!root.TryGetProperty(section, out JsonElement group) || group.ValueKind != JsonValueKind.Object || !group.TryGetProperty(name, out JsonElement item))
			return fallback;
		return item.ValueKind == JsonValueKind.String ? item.GetString() ?? fallback : fallback;
	}

	private static int JsonArrayCount(JsonElement root, string name)
	{
		return root.TryGetProperty(name, out JsonElement item) && item.ValueKind == JsonValueKind.Array ? item.GetArrayLength() : 0;
	}

	private static string FirstJsonArrayText(JsonElement root, string name)
	{
		if (!root.TryGetProperty(name, out JsonElement item) || item.ValueKind != JsonValueKind.Array)
			return "—";
		foreach (JsonElement value in item.EnumerateArray())
			return value.ToString();
		return "—";
	}

	private static int ReadAdvancedInt(JsonElement root, string name, int fallback, int min, int max)
	{
		if (!root.TryGetProperty(name, out JsonElement item))
			return fallback;
		if (item.ValueKind == JsonValueKind.Number && item.TryGetInt32(out int number))
			return Math.Clamp(number, min, max);
		if (item.ValueKind == JsonValueKind.String)
			return ParseAdvancedInt(item.GetString(), fallback, min, max);
		return fallback;
	}

	private static int ParseAdvancedInt(string value, int fallback, int min, int max)
	{
		return int.TryParse(value, out int parsed) ? Math.Clamp(parsed, min, max) : fallback;
	}
}
