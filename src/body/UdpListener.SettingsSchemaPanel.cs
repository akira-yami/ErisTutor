using Godot;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

public partial class UdpListener : Node2D
{
	private OptionButton _runtimeSettingsSectionSelect;
	private ScrollContainer _runtimeSettingsScroll;
	private VBoxContainer _runtimeSettingsRows;
	private Label _runtimeSettingsOwnerLabel;
	private Label _runtimeSettingsRevisionLabel;
	private Label _runtimeSettingsDirtyLabel;
	private Label _runtimeSettingsApplyLabel;
	private Button _runtimeSettingsApplyButton;
	private Button _runtimeSettingsResetSectionButton;
	private string _runtimeSettingsSelectedSection = "chat";
	private int _runtimeSettingsRevision;
	private JsonObject _runtimeSettingsEffective = new();
	private JsonObject _runtimeSettingsCatalog = new();
	private readonly Dictionary<string, JsonNode> _runtimeSettingsDraft = new(StringComparer.Ordinal);
	private readonly Dictionary<string, JsonObject> _runtimeSettingsFieldMeta = new(StringComparer.Ordinal);
	private readonly List<string> _runtimeSettingsSectionIds = new();
	private bool _runtimeSettingsRowsBuilding;
	private bool _runtimeSettingsSnapshotRequested;

	private static readonly Dictionary<string, string> RuntimeSettingsSectionFallbackLabels = new(StringComparer.Ordinal)
	{
		["architecture"] = "Kiến trúc khởi động",
		["chat"] = "Hội thoại & định tuyến model",
		["personality"] = "Tính cách & ranh giới",
		["prompt"] = "Prompt & grounding",
		["social_observer"] = "Não phụ / Social observer",
		["commentary"] = "Bình luận chủ động",
		["initiative"] = "Chủ động mở lời",
		["discovery"] = "Khám phá",
		["memory"] = "Memory runtime",
		["tutor"] = "Tutor / nghiên cứu",
		["stage"] = "Stage & chia đoạn",
		["tts"] = "TTS & diễn giọng",
		["capabilities"] = "Khả năng hệ thống",
		["project_ops"] = "Project operations",
		["chess_bridge"] = "Chess bridge",
		["ai_runtime"] = "AI runtime",
		["public_turn"] = "Public turn timing",
		["engagement"] = "Engagement queue",
		["observability"] = "Observability / SLO",
		["health"] = "Health history",
		["protocol"] = "UDP protocol guard",
		["run_loop"] = "Run-loop recovery",
		["hardening"] = "Hardening & release guard",
		["danger_zone"] = "Chính sách Danger Zone",
	};

	private void ResetRuntimeSchemaSettingsUiReferences()
	{
		_runtimeSettingsSectionSelect = null;
		_runtimeSettingsScroll = null;
		_runtimeSettingsRows = null;
		_runtimeSettingsOwnerLabel = null;
		_runtimeSettingsRevisionLabel = null;
		_runtimeSettingsDirtyLabel = null;
		_runtimeSettingsApplyLabel = null;
		_runtimeSettingsApplyButton = null;
		_runtimeSettingsResetSectionButton = null;
	}

	private void BuildRuntimeSchemaSettingsPage(Control root)
	{
		BuildRuntimeSettingsPage(root, voiceOnly: false);
	}

	private void BuildRuntimeVoiceSettingsPage(Control root)
	{
		BuildRuntimeSettingsPage(root, voiceOnly: true);
	}

	private void BuildRuntimeSettingsPage(Control root, bool voiceOnly)
	{
		var left = AddSettingsCard(
			root,
			voiceOnly ? new Rect2(32f, 178f, 250f, 414f) : new Rect2(32f, 178f, 270f, 414f),
			voiceOnly ? "Quyền sở hữu" : "Nhóm cấu hình");
		BuildRuntimeSettingsNavigation(left, voiceOnly);

		var editor = AddSettingsCard(
			root,
			voiceOnly ? new Rect2(300f, 178f, 576f, 414f) : new Rect2(320f, 178f, 560f, 414f),
			voiceOnly ? "TTS runtime" : "Cấu hình đang hiệu lực");
		BuildRuntimeSettingsEditor(editor, voiceOnly);

		var owner = AddSettingsCard(
			root,
			voiceOnly ? new Rect2(894f, 178f, 254f, 414f) : new Rect2(898f, 178f, 250f, 414f),
			"Owner & áp dụng");
		BuildRuntimeSettingsOwnerCard(owner, voiceOnly);

		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.38f),
			Position = new Vector2(32f, 604f),
			Size = new Vector2(1116f, 1.4f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		root.AddChild(divider);

		_settingsStatusNoteLabel = CreateUiLabel("", 10, SettingsInk);
		_settingsStatusNoteLabel.Position = new Vector2(52f, 636f);
		_settingsStatusNoteLabel.Size = new Vector2(650f, 20f);
		StyleSettingsSingleLineLabel(_settingsStatusNoteLabel);
		root.AddChild(_settingsStatusNoteLabel);

		var reload = CreateSettingsButton("Đọc lại từ Brain", RequestAdvancedRuntimeSnapshot, primary: false);
		reload.Position = new Vector2(724f, 620f);
		reload.Size = new Vector2(138f, 42f);
		reload.AddThemeFontSizeOverride("font_size", 9);
		root.AddChild(reload);

		var close = CreateSettingsButton("Đóng", CloseSettingsPanel, primary: false);
		close.Position = new Vector2(870f, 620f);
		close.Size = new Vector2(78f, 42f);
		root.AddChild(close);

		_runtimeSettingsApplyButton = CreateSettingsButton(
			voiceOnly ? "Áp dụng TTS" : "Áp dụng runtime",
			() => ApplyRuntimeSettingsDraft(voiceOnly ? "tts" : "advanced"),
			primary: true);
		_runtimeSettingsApplyButton.Position = new Vector2(956f, 620f);
		_runtimeSettingsApplyButton.Size = new Vector2(188f, 42f);
		_runtimeSettingsApplyButton.AddThemeFontSizeOverride("font_size", 10);
		root.AddChild(_runtimeSettingsApplyButton);

		if (!_runtimeSettingsSnapshotRequested || _runtimeSettingsEffective.Count == 0)
		{
			_runtimeSettingsSnapshotRequested = true;
			RequestAdvancedRuntimeSnapshot();
		}
		RefreshRuntimeSettingsNavigation(voiceOnly);
		RebuildRuntimeSettingsRows(voiceOnly);
		RefreshRuntimeSettingsOwnerCard(voiceOnly);
	}

	private void BuildRuntimeSettingsNavigation(Control parent, bool voiceOnly)
	{
		var authority = CreateUiLabel(
			voiceOnly ? "VOICE RUNTIME" : "BRAIN RUNTIME",
			9,
			SettingsPink);
		authority.Position = new Vector2(18f, 48f);
		authority.Size = new Vector2(parent.Size.X - 36f, 18f);
		StyleSettingsSingleLineLabel(authority);
		parent.AddChild(authority);

		var explanation = CreateUiLabel(
			voiceOnly
				? "Tab này chỉ sửa tts.* trong runtime store. API key ElevenLabs nằm ở Kết nối & dữ liệu."
				: "Mỗi dòng là key thật trong schema Brain. Không dùng preset gom nhiều trách nhiệm vào một nút nữa.",
			10,
			SettingsInkSoft);
		explanation.Position = new Vector2(18f, 72f);
		explanation.Size = new Vector2(parent.Size.X - 36f, voiceOnly ? 76f : 70f);
		explanation.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(explanation);

		_runtimeSettingsSectionSelect = new OptionButton
		{
			Position = new Vector2(18f, voiceOnly ? 162f : 154f),
			Size = new Vector2(parent.Size.X - 36f, 34f),
			ClipText = true,
			TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
		};
		_runtimeSettingsSectionSelect.AddThemeFontSizeOverride("font_size", 10);
		_runtimeSettingsSectionSelect.ItemSelected += index =>
		{
			int i = (int)index;
			if (_runtimeSettingsRowsBuilding || i < 0 || i >= _runtimeSettingsSectionIds.Count)
				return;
			_runtimeSettingsSelectedSection = _runtimeSettingsSectionIds[i];
			RebuildRuntimeSettingsRows(voiceOnly);
			RefreshRuntimeSettingsOwnerCard(voiceOnly);
		};
		parent.AddChild(_runtimeSettingsSectionSelect);

		var legend = new Panel
		{
			Position = new Vector2(18f, voiceOnly ? 212f : 204f),
			Size = new Vector2(parent.Size.X - 36f, 154f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		legend.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1f, 0.985f, 0.995f, 0.70f), SettingsBorder, 12));
		parent.AddChild(legend);

		var legendText = CreateUiLabel(
			"Body local\n  user://eris_settings.json\n\nBrain runtime\n  settings_update / settings_health\n\nCredential/bootstrap\n  config/secrets.json + restart",
			9,
			SettingsInkSoft);
		legendText.Position = new Vector2(12f, 10f);
		legendText.Size = new Vector2(legend.Size.X - 24f, 136f);
		legendText.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		legend.AddChild(legendText);

		_runtimeSettingsRevisionLabel = CreateUiLabel("Schema: chưa đọc", 9, SettingsInk);
		_runtimeSettingsRevisionLabel.Position = new Vector2(18f, 376f);
		_runtimeSettingsRevisionLabel.Size = new Vector2(parent.Size.X - 36f, 20f);
		StyleSettingsSingleLineLabel(_runtimeSettingsRevisionLabel);
		parent.AddChild(_runtimeSettingsRevisionLabel);
	}

	private void BuildRuntimeSettingsEditor(Control parent, bool voiceOnly)
	{
		_runtimeSettingsScroll = new ScrollContainer
		{
			Position = new Vector2(14f, 44f),
			Size = new Vector2(parent.Size.X - 28f, 352f),
			HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
			VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		parent.AddChild(_runtimeSettingsScroll);

		_runtimeSettingsRows = new VBoxContainer
		{
			CustomMinimumSize = new Vector2(parent.Size.X - 48f, 0f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		_runtimeSettingsRows.AddThemeConstantOverride("separation", 7);
		_runtimeSettingsScroll.AddChild(_runtimeSettingsRows);
	}

	private void BuildRuntimeSettingsOwnerCard(Control parent, bool voiceOnly)
	{
		_runtimeSettingsOwnerLabel = CreateUiLabel("Đang chờ catalog từ Brain.", 10, SettingsInkSoft);
		_runtimeSettingsOwnerLabel.Position = new Vector2(18f, 48f);
		_runtimeSettingsOwnerLabel.Size = new Vector2(parent.Size.X - 36f, 120f);
		_runtimeSettingsOwnerLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		_runtimeSettingsOwnerLabel.VerticalAlignment = VerticalAlignment.Top;
		parent.AddChild(_runtimeSettingsOwnerLabel);

		var divider = new ColorRect
		{
			Color = new Color(0.96f, 0.72f, 0.84f, 0.36f),
			Position = new Vector2(18f, 178f),
			Size = new Vector2(parent.Size.X - 36f, 1f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(divider);

		_runtimeSettingsDirtyLabel = CreateUiLabel("0 thay đổi chưa áp dụng", 11, SettingsInk);
		_runtimeSettingsDirtyLabel.Position = new Vector2(18f, 194f);
		_runtimeSettingsDirtyLabel.Size = new Vector2(parent.Size.X - 36f, 24f);
		StyleSettingsSingleLineLabel(_runtimeSettingsDirtyLabel);
		parent.AddChild(_runtimeSettingsDirtyLabel);

		_runtimeSettingsApplyLabel = CreateUiLabel(
			"Brain sẽ trả applied / rejected / restart_required cho từng path.",
			9,
			SettingsInkSoft);
		_runtimeSettingsApplyLabel.Position = new Vector2(18f, 226f);
		_runtimeSettingsApplyLabel.Size = new Vector2(parent.Size.X - 36f, 72f);
		_runtimeSettingsApplyLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(_runtimeSettingsApplyLabel);

		_runtimeSettingsResetSectionButton = CreateSettingsButton("Hoàn tác nhóm đang mở", () =>
		{
			DiscardRuntimeSettingsSectionDraft(_runtimeSettingsSelectedSection);
			RebuildRuntimeSettingsRows(voiceOnly);
			RefreshRuntimeSettingsOwnerCard(voiceOnly);
			SetSettingsStatus("Đã bỏ thay đổi chưa áp dụng của nhóm đang mở.");
		}, primary: false);
		_runtimeSettingsResetSectionButton.Position = new Vector2(18f, 314f);
		_runtimeSettingsResetSectionButton.Size = new Vector2(parent.Size.X - 36f, 32f);
		_runtimeSettingsResetSectionButton.AddThemeFontSizeOverride("font_size", 9);
		parent.AddChild(_runtimeSettingsResetSectionButton);

		var warning = CreateUiLabel(
			voiceOnly
				? "Đổi provider/voice/model TTS có thể rebuild worker live; ACK sẽ nói rõ."
				: "Mục có dấu ↻ cần restart. Panel không tự ghi file Brain sau lưng runtime.",
			8,
			SettingsInkSoft);
		warning.Position = new Vector2(18f, 354f);
		warning.Size = new Vector2(parent.Size.X - 36f, 42f);
		warning.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		parent.AddChild(warning);
	}

	private void RefreshRuntimeSettingsNavigation(bool voiceOnly)
	{
		if (_runtimeSettingsSectionSelect == null)
			return;

		_runtimeSettingsRowsBuilding = true;
		_runtimeSettingsSectionSelect.Clear();
		_runtimeSettingsSectionIds.Clear();

		foreach (string section in EnumerateRuntimeSettingsSections(voiceOnly))
		{
			_runtimeSettingsSectionIds.Add(section);
			_runtimeSettingsSectionSelect.AddItem(RuntimeSettingsSectionLabel(section));
		}

		if (voiceOnly)
			_runtimeSettingsSelectedSection = "tts";
		if (!_runtimeSettingsSectionIds.Contains(_runtimeSettingsSelectedSection, StringComparer.Ordinal))
			_runtimeSettingsSelectedSection = _runtimeSettingsSectionIds.Count > 0 ? _runtimeSettingsSectionIds[0] : (voiceOnly ? "tts" : "chat");

		int selected = Math.Max(0, _runtimeSettingsSectionIds.FindIndex(value => string.Equals(value, _runtimeSettingsSelectedSection, StringComparison.Ordinal)));
		if (_runtimeSettingsSectionIds.Count > 0)
			_runtimeSettingsSectionSelect.Select(selected);
		_runtimeSettingsSectionSelect.Disabled = voiceOnly || _runtimeSettingsSectionIds.Count <= 1;
		_runtimeSettingsRowsBuilding = false;

		if (_runtimeSettingsRevisionLabel != null)
		{
			int schema = ReadCatalogSchemaVersion();
			_runtimeSettingsRevisionLabel.Text = _runtimeSettingsEffective.Count == 0
				? "Schema: đang chờ Brain"
				: $"Schema v{schema} · revision {_runtimeSettingsRevision}";
		}
	}

	private IEnumerable<string> EnumerateRuntimeSettingsSections(bool voiceOnly)
	{
		var sections = new List<string>();
		if (_runtimeSettingsCatalog["sections"] is JsonObject catalogSections)
		{
			foreach (var pair in catalogSections)
			{
				if (pair.Value is not JsonObject meta)
					continue;
				string surface = ReadJsonNodeString(meta["surface"], "advanced");
				if ((voiceOnly && surface == "voice") || (!voiceOnly && surface == "advanced"))
					sections.Add(pair.Key);
			}
		}
		else
		{
			foreach (var pair in _runtimeSettingsEffective)
			{
				if (voiceOnly ? pair.Key == "tts" : pair.Key != "tts")
					sections.Add(pair.Key);
			}
		}

		string[] preferred =
		{
			"architecture", "chat", "personality", "prompt", "social_observer",
			"commentary", "initiative", "discovery", "memory", "tutor", "stage",
			"tts", "capabilities", "project_ops", "chess_bridge", "ai_runtime",
			"public_turn", "engagement", "observability", "health", "protocol",
			"run_loop", "hardening", "danger_zone",
		};
		return sections
			.Distinct(StringComparer.Ordinal)
			.OrderBy(value => Array.IndexOf(preferred, value) is int index && index >= 0 ? index : 999)
			.ThenBy(value => value, StringComparer.Ordinal);
	}

	private string RuntimeSettingsSectionLabel(string section)
	{
		if (_runtimeSettingsCatalog["sections"] is JsonObject sections
			&& sections[section] is JsonObject meta)
		{
			string label = ReadJsonNodeString(meta["label"], "");
			if (!string.IsNullOrWhiteSpace(label))
				return label;
		}
		return RuntimeSettingsSectionFallbackLabels.TryGetValue(section, out string fallback)
			? fallback
			: HumanizeRuntimeSettingsKey(section);
	}

	private void RebuildRuntimeSettingsRows(bool voiceOnly)
	{
		if (_runtimeSettingsRows == null)
			return;

		_runtimeSettingsRowsBuilding = true;
		foreach (Node child in _runtimeSettingsRows.GetChildren())
		{
			_runtimeSettingsRows.RemoveChild(child);
			child.QueueFree();
		}
		_runtimeSettingsFieldMeta.Clear();

		if (_runtimeSettingsEffective.Count == 0)
		{
			var waiting = CreateUiLabel("Đang chờ settings_health từ Brain…", 11, SettingsInkSoft);
			waiting.CustomMinimumSize = new Vector2(0f, 48f);
			waiting.HorizontalAlignment = HorizontalAlignment.Center;
			waiting.VerticalAlignment = VerticalAlignment.Center;
			_runtimeSettingsRows.AddChild(waiting);
			_runtimeSettingsRowsBuilding = false;
			return;
		}

		List<JsonObject> fields = RuntimeSettingsFieldsForSection(_runtimeSettingsSelectedSection, voiceOnly);
		if (fields.Count == 0 && _runtimeSettingsEffective[_runtimeSettingsSelectedSection] is JsonObject fallbackSection)
		{
			foreach (var pair in fallbackSection)
			{
				var meta = new JsonObject
				{
					["path"] = $"{_runtimeSettingsSelectedSection}.{pair.Key}",
					["section"] = _runtimeSettingsSelectedSection,
					["key"] = pair.Key,
					["label"] = HumanizeRuntimeSettingsKey(pair.Key),
					["kind"] = InferRuntimeSettingsKind(pair.Value),
					["value"] = pair.Value?.DeepClone(),
					["default"] = pair.Value?.DeepClone(),
					["owner"] = "brain_runtime",
					["surface"] = voiceOnly ? "voice" : "advanced",
					["restart_required"] = false,
				};
				fields.Add(meta);
			}
		}

		foreach (JsonObject field in fields)
			AddRuntimeSettingsFieldRow(field, voiceOnly);

		_runtimeSettingsRowsBuilding = false;
		RefreshRuntimeSettingsOwnerCard(voiceOnly);
	}

	private List<JsonObject> RuntimeSettingsFieldsForSection(string section, bool voiceOnly)
	{
		var result = new List<JsonObject>();
		if (_runtimeSettingsCatalog["fields"] is not JsonArray fields)
			return result;
		foreach (JsonNode node in fields)
		{
			if (node is not JsonObject field)
				continue;
			if (!string.Equals(ReadJsonNodeString(field["section"], ""), section, StringComparison.Ordinal))
				continue;
			string surface = ReadJsonNodeString(field["surface"], "advanced");
			if ((voiceOnly && surface != "voice") || (!voiceOnly && surface != "advanced"))
				continue;
			result.Add(field);
		}
		return result;
	}

	private void AddRuntimeSettingsFieldRow(JsonObject field, bool voiceOnly)
	{
		string path = ReadJsonNodeString(field["path"], "");
		string key = ReadJsonNodeString(field["key"], path);
		string labelText = ReadJsonNodeString(field["label"], HumanizeRuntimeSettingsKey(key));
		string kind = ReadJsonNodeString(field["kind"], "string");
		bool restartRequired = ReadJsonNodeBool(field["restart_required"], false);
		if (string.IsNullOrWhiteSpace(path))
			return;

		_runtimeSettingsFieldMeta[path] = field;
		JsonNode effectiveValue = ReadRuntimeSettingsEffectiveValue(path) ?? field["value"]?.DeepClone() ?? field["default"]?.DeepClone();
		JsonNode displayValue = _runtimeSettingsDraft.TryGetValue(path, out JsonNode draft) ? draft : effectiveValue;

		var row = new Panel
		{
			CustomMinimumSize = new Vector2(0f, 58f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		row.AddThemeStyleboxOverride("panel", SettingsCardStyle(
			new Color(1f, 0.985f, 0.995f, 0.72f),
			SettingsBorder,
			12));
		_runtimeSettingsRows.AddChild(row);

		var title = CreateUiLabel((restartRequired ? "↻  " : "") + labelText, 10, SettingsInk);
		title.Position = new Vector2(12f, 7f);
		title.Size = new Vector2(row.CustomMinimumSize.X > 0 ? row.CustomMinimumSize.X * 0.52f : 250f, 20f);
		StyleSettingsSingleLineLabel(title);
		title.TooltipText = restartRequired ? "Thay đổi này cần restart Brain." : "Có thể áp dụng live theo ACK của Brain.";
		row.AddChild(title);

		var rawPath = CreateUiLabel(path, 8, SettingsInkSoft);
		rawPath.Position = new Vector2(14f, 31f);
		rawPath.Size = new Vector2(270f, 16f);
		StyleSettingsSingleLineLabel(rawPath);
		rawPath.TooltipText = $"Owner: brain_runtime\nPath: {path}";
		row.AddChild(rawPath);

		Control editor = CreateRuntimeSettingsEditor(field, displayValue, path, voiceOnly);
		editor.Position = new Vector2(292f, 10f);
		editor.Size = new Vector2(220f, 36f);
		row.AddChild(editor);
	}

	private Control CreateRuntimeSettingsEditor(JsonObject field, JsonNode value, string path, bool voiceOnly)
	{
		string kind = ReadJsonNodeString(field["kind"], "string");
		if (kind == "bool")
		{
			var toggle = new CheckButton
			{
				Text = ReadJsonNodeBool(value, false) ? "Bật" : "Tắt",
				ButtonPressed = ReadJsonNodeBool(value, false),
				MouseFilter = Control.MouseFilterEnum.Stop,
			};
			toggle.AddThemeFontSizeOverride("font_size", 10);
			toggle.Toggled += enabled =>
			{
				if (_runtimeSettingsRowsBuilding)
					return;
				toggle.Text = enabled ? "Bật" : "Tắt";
				SetRuntimeSettingsDraftValue(path, JsonValue.Create(enabled), voiceOnly);
			};
			return toggle;
		}

		if (kind == "enum" && field["options"] is JsonArray options)
		{
			var option = new OptionButton
			{
				ClipText = true,
				TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
			};
			option.AddThemeFontSizeOverride("font_size", 9);
			string current = ReadJsonNodeString(value, "");
			int selected = 0;
			for (int i = 0; i < options.Count; i++)
			{
				string item = ReadJsonNodeString(options[i], "");
				option.AddItem(item);
				if (string.Equals(item, current, StringComparison.OrdinalIgnoreCase))
					selected = i;
			}
			if (option.ItemCount > 0)
				option.Select(selected);
			option.ItemSelected += index =>
			{
				if (_runtimeSettingsRowsBuilding)
					return;
				SetRuntimeSettingsDraftValue(path, JsonValue.Create(option.GetItemText((int)index)), voiceOnly);
			};
			return option;
		}

		var input = new LineEdit
		{
			Text = RuntimeSettingsNodeToText(value),
			PlaceholderText = kind is "int" or "float" ? "Nhập số" : "Nhập giá trị",
			ClearButtonEnabled = true,
		};
		input.AddThemeFontSizeOverride("font_size", 9);
		input.TooltipText = RuntimeSettingsConstraintTooltip(field);
		Action commit = () =>
		{
			if (_runtimeSettingsRowsBuilding)
				return;
			if (!TryParseRuntimeSettingsInput(field, input.Text, out JsonNode parsed, out string error))
			{
				input.AddThemeColorOverride("font_color", new Color(0.72f, 0.12f, 0.18f));
				input.TooltipText = error;
				SetSettingsStatus($"{path}: {error}");
				return;
			}
			input.RemoveThemeColorOverride("font_color");
			input.TooltipText = RuntimeSettingsConstraintTooltip(field);
			SetRuntimeSettingsDraftValue(path, parsed, voiceOnly);
		};
		input.TextSubmitted += _ => commit();
		input.FocusExited += commit;
		return input;
	}

	private void SetRuntimeSettingsDraftValue(string path, JsonNode value, bool voiceOnly)
	{
		JsonNode effective = ReadRuntimeSettingsEffectiveValue(path);
		if (JsonNodesEquivalent(effective, value))
			_runtimeSettingsDraft.Remove(path);
		else
			_runtimeSettingsDraft[path] = value?.DeepClone();
		RefreshRuntimeSettingsOwnerCard(voiceOnly);
		SetSettingsStatus($"Đã đổi {path}. Bấm Áp dụng để Brain xác nhận.");
	}

	private void RefreshRuntimeSettingsOwnerCard(bool voiceOnly)
	{
		string section = _runtimeSettingsSelectedSection;
		string label = RuntimeSettingsSectionLabel(section);
		int sectionFields = RuntimeSettingsFieldsForSection(section, voiceOnly).Count;
		int sectionDirty = _runtimeSettingsDraft.Keys.Count(path => path.StartsWith(section + ".", StringComparison.Ordinal));
		int totalDirty = CountRuntimeSettingsDraftForSurface(voiceOnly ? "voice" : "advanced");
		int restartDirty = _runtimeSettingsDraft.Keys.Count(path =>
			RuntimeSettingsFieldNeedsRestart(path)
			&& RuntimeSettingsFieldSurface(path) == (voiceOnly ? "voice" : "advanced"));

		if (_runtimeSettingsOwnerLabel != null)
		{
			_runtimeSettingsOwnerLabel.Text =
				$"{label}\nOwner: brain_runtime\n{sectionFields} key thật trong schema"
				+ (sectionDirty > 0 ? $"\n{sectionDirty} key của nhóm đang đổi" : "\nChưa đổi nhóm này");
		}
		if (_runtimeSettingsDirtyLabel != null)
			_runtimeSettingsDirtyLabel.Text = $"{totalDirty} thay đổi chưa áp dụng";
		if (_runtimeSettingsApplyLabel != null)
		{
			_runtimeSettingsApplyLabel.Text = totalDirty == 0
				? "Không có bản nháp. Giá trị đang hiển thị đến từ Brain runtime store."
				: $"Sẽ gửi {totalDirty} path qua settings_update"
					+ (restartDirty > 0 ? $"; {restartDirty} path cần restart." : "; tất cả có thể thử áp dụng live.");
		}
		if (_runtimeSettingsApplyButton != null)
			_runtimeSettingsApplyButton.Disabled = totalDirty == 0 || _chatSend == null;
		if (_runtimeSettingsResetSectionButton != null)
			_runtimeSettingsResetSectionButton.Disabled = sectionDirty == 0;
	}

	private void ApplyRuntimeSettingsDraft(string scope)
	{
		string surface = string.Equals(scope, "tts", StringComparison.OrdinalIgnoreCase) ? "voice" : "advanced";
		var patch = new JsonObject();
		int count = 0;
		foreach (var pair in _runtimeSettingsDraft)
		{
			if (!string.Equals(RuntimeSettingsFieldSurface(pair.Key), surface, StringComparison.Ordinal))
				continue;
			SetRuntimeSettingsPatchPath(patch, pair.Key, pair.Value?.DeepClone());
			count++;
		}

		if (count == 0)
		{
			SetSettingsStatus("Không có thay đổi runtime để áp dụng.");
			return;
		}
		if (_chatSend == null)
		{
			SetSettingsStatus("Brain chưa kết nối; bản nháp vẫn nằm ở panel và chưa được ghi vào runtime store.");
			return;
		}

		var payload = new JsonObject
		{
			["type"] = "settings_update",
			["scope"] = scope,
			["settings"] = patch,
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(payload.ToJsonString()));
		if (_runtimeSettingsApplyLabel != null)
			_runtimeSettingsApplyLabel.Text = error == Error.Ok
				? $"Đã gửi {count} path, đang chờ ACK…"
				: $"Không gửi được settings_update: {error}";
		SetSettingsStatus(error == Error.Ok
			? $"Đã gửi {count} key runtime xuống Brain."
			: $"Không gửi được runtime settings: {error}");
	}

	private void ApplyRuntimeSettingsAck(JsonElement ack)
	{
		string scope = ack.TryGetProperty("scope", out JsonElement scopeEl) ? scopeEl.GetString() ?? "advanced" : "advanced";
		int applied = 0;
		int rejected = 0;
		int restart = 0;
		if (ack.TryGetProperty("applied", out JsonElement appliedEl) && appliedEl.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in appliedEl.EnumerateArray())
			{
				string path = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : "";
				if (!string.IsNullOrWhiteSpace(path))
					_runtimeSettingsDraft.Remove(path);
				applied++;
			}
		}
		if (ack.TryGetProperty("rejected", out JsonElement rejectedEl) && rejectedEl.ValueKind == JsonValueKind.Array)
			rejected = rejectedEl.GetArrayLength();
		if (ack.TryGetProperty("restart_required", out JsonElement restartEl) && restartEl.ValueKind == JsonValueKind.Array)
			restart = restartEl.GetArrayLength();

		if (ack.TryGetProperty("effective", out JsonElement effective) && effective.ValueKind == JsonValueKind.Object)
			ApplyRuntimeSettingsEffectiveSnapshot(effective);

		string message = $"Brain áp dụng {applied} key";
		if (rejected > 0)
			message += $" · từ chối {rejected}";
		if (restart > 0)
			message += $" · cần restart {restart}";
		if (_runtimeSettingsApplyLabel != null)
			_runtimeSettingsApplyLabel.Text = message;
		SetSettingsStatus(message + ".");

		bool voiceOnly = string.Equals(scope, "tts", StringComparison.OrdinalIgnoreCase);
		RefreshRuntimeSettingsNavigation(voiceOnly);
		RebuildRuntimeSettingsRows(voiceOnly);
		RefreshRuntimeSettingsOwnerCard(voiceOnly);
	}

	private void ApplyRuntimeSettingsHealthSnapshot(JsonElement healthAck)
	{
		if (healthAck.TryGetProperty("effective", out JsonElement effective) && effective.ValueKind == JsonValueKind.Object)
			ApplyRuntimeSettingsEffectiveSnapshot(effective);
	}

	private void ApplyRuntimeSettingsEffectiveSnapshot(JsonElement effective)
	{
		try
		{
			if (effective.TryGetProperty("settings", out JsonElement settings) && settings.ValueKind == JsonValueKind.Object)
				_runtimeSettingsEffective = JsonNode.Parse(settings.GetRawText())?.AsObject() ?? new JsonObject();
			if (effective.TryGetProperty("catalog", out JsonElement catalog) && catalog.ValueKind == JsonValueKind.Object)
				_runtimeSettingsCatalog = JsonNode.Parse(catalog.GetRawText())?.AsObject() ?? new JsonObject();
			_runtimeSettingsRevision = effective.TryGetProperty("revision", out JsonElement revisionEl) && revisionEl.TryGetInt32(out int revision)
				? revision
				: _runtimeSettingsRevision;
			_runtimeSettingsSnapshotRequested = false;
			SyncLegacyTtsFieldsFromRuntimeSettings();

			if (_settingsPanel != null && _settingsPanel.Visible)
			{
				bool voiceOnly = _settingsModePage == SettingsModePage.Voice;
				if (_settingsModePage is SettingsModePage.Advanced or SettingsModePage.Voice)
				{
					RefreshRuntimeSettingsNavigation(voiceOnly);
					RebuildRuntimeSettingsRows(voiceOnly);
					RefreshRuntimeSettingsOwnerCard(voiceOnly);
				}
				RefreshSettingsRuntimeModelSummary();
			}
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[Settings] Khong parse duoc runtime settings snapshot: {ex.Message}");
		}
	}

	private void SyncLegacyTtsFieldsFromRuntimeSettings()
	{
		if (_runtimeSettingsEffective["tts"] is not JsonObject tts)
			return;
		_settingsTtsEnabled = ReadJsonNodeBool(tts["enabled"], _settingsTtsEnabled);
		_settingsTtsProvider = ReadJsonNodeString(tts["provider"], _settingsTtsProvider);
		_settingsTtsVoice = ReadJsonNodeString(tts["voice"], _settingsTtsVoice);
		_settingsElevenLabsVoiceId = ReadJsonNodeString(tts["elevenlabs_voice_id"], _settingsElevenLabsVoiceId);
		_settingsElevenLabsModel = ReadJsonNodeString(tts["elevenlabs_model"], _settingsElevenLabsModel);
		_settingsTtsLanguage = ReadJsonNodeString(tts["language"], _settingsTtsLanguage);
		if (GodotObject.IsInstanceValid(_settingsLanguageSelect))
		{
			for (int i = 0; i < _settingsLanguageSelect.ItemCount; i++)
			{
				if (_settingsLanguageSelect.GetItemMetadata(i).AsString() == NormalizeSettingsTtsLanguage(_settingsTtsLanguage))
				{
					_settingsLanguageSelect.Select(i);
					break;
				}
			}
		}
		_settingsTtsQuality = ReadJsonNodeString(tts["quality"], _settingsTtsQuality);
		_settingsTtsVolume = (float)ReadJsonNodeDouble(tts["volume"], _settingsTtsVolume);
		_settingsTtsSpeed = (float)ReadJsonNodeDouble(tts["speed"], _settingsTtsSpeed);
	}

	private void DiscardRuntimeSettingsSectionDraft(string section)
	{
		foreach (string path in _runtimeSettingsDraft.Keys
			.Where(path => path.StartsWith(section + ".", StringComparison.Ordinal))
			.ToArray())
			_runtimeSettingsDraft.Remove(path);
	}

	private int CountRuntimeSettingsDraftForSurface(string surface)
	{
		return _runtimeSettingsDraft.Keys.Count(path => string.Equals(RuntimeSettingsFieldSurface(path), surface, StringComparison.Ordinal));
	}

	private string RuntimeSettingsFieldSurface(string path)
	{
		if (_runtimeSettingsFieldMeta.TryGetValue(path, out JsonObject visibleMeta))
			return ReadJsonNodeString(visibleMeta["surface"], path.StartsWith("tts.", StringComparison.Ordinal) ? "voice" : "advanced");
		if (_runtimeSettingsCatalog["fields"] is JsonArray fields)
		{
			foreach (JsonNode node in fields)
			{
				if (node is JsonObject field && string.Equals(ReadJsonNodeString(field["path"], ""), path, StringComparison.Ordinal))
					return ReadJsonNodeString(field["surface"], path.StartsWith("tts.", StringComparison.Ordinal) ? "voice" : "advanced");
			}
		}
		return path.StartsWith("tts.", StringComparison.Ordinal) ? "voice" : "advanced";
	}

	private bool RuntimeSettingsFieldNeedsRestart(string path)
	{
		if (_runtimeSettingsFieldMeta.TryGetValue(path, out JsonObject visibleMeta))
			return ReadJsonNodeBool(visibleMeta["restart_required"], false);
		if (_runtimeSettingsCatalog["fields"] is JsonArray fields)
		{
			foreach (JsonNode node in fields)
			{
				if (node is JsonObject field && string.Equals(ReadJsonNodeString(field["path"], ""), path, StringComparison.Ordinal))
					return ReadJsonNodeBool(field["restart_required"], false);
			}
		}
		return false;
	}

	private JsonNode ReadRuntimeSettingsEffectiveValue(string path)
	{
		string[] parts = (path ?? "").Split('.', StringSplitOptions.RemoveEmptyEntries);
		JsonNode node = _runtimeSettingsEffective;
		foreach (string part in parts)
		{
			if (node is not JsonObject obj || !obj.TryGetPropertyValue(part, out node))
				return null;
		}
		return node?.DeepClone();
	}

	private static void SetRuntimeSettingsPatchPath(JsonObject root, string path, JsonNode value)
	{
		string[] parts = (path ?? "").Split('.', StringSplitOptions.RemoveEmptyEntries);
		if (parts.Length < 2)
			return;
		JsonObject current = root;
		for (int i = 0; i < parts.Length - 1; i++)
		{
			if (current[parts[i]] is not JsonObject child)
			{
				child = new JsonObject();
				current[parts[i]] = child;
			}
			current = child;
		}
		current[parts[^1]] = value;
	}

	private static bool TryParseRuntimeSettingsInput(JsonObject field, string text, out JsonNode value, out string error)
	{
		string kind = ReadJsonNodeString(field["kind"], "string");
		string raw = text ?? "";
		error = "";
		value = null;
		if (kind == "int")
		{
			if (!long.TryParse(raw.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out long parsed))
			{
				error = "Cần một số nguyên hợp lệ.";
				return false;
			}
			double min = ReadJsonNodeDouble(field["min"], double.NegativeInfinity);
			double max = ReadJsonNodeDouble(field["max"], double.PositiveInfinity);
			if (parsed < min || parsed > max)
			{
				error = $"Giá trị phải nằm trong {min:0.###}..{max:0.###}.";
				return false;
			}
			value = JsonValue.Create(parsed);
			return true;
		}
		if (kind == "float")
		{
			if (!double.TryParse(raw.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed))
			{
				error = "Cần một số thực hợp lệ, dùng dấu chấm thập phân.";
				return false;
			}
			double min = ReadJsonNodeDouble(field["min"], double.NegativeInfinity);
			double max = ReadJsonNodeDouble(field["max"], double.PositiveInfinity);
			if (parsed < min || parsed > max)
			{
				error = $"Giá trị phải nằm trong {min:0.###}..{max:0.###}.";
				return false;
			}
			value = JsonValue.Create(parsed);
			return true;
		}
		value = JsonValue.Create(raw.Trim());
		return true;
	}

	private static string RuntimeSettingsConstraintTooltip(JsonObject field)
	{
		string kind = ReadJsonNodeString(field["kind"], "string");
		if (kind is "int" or "float")
		{
			double min = ReadJsonNodeDouble(field["min"], double.NaN);
			double max = ReadJsonNodeDouble(field["max"], double.NaN);
			double step = ReadJsonNodeDouble(field["step"], double.NaN);
			if (!double.IsNaN(min) && !double.IsNaN(max))
				return $"Miền hợp lệ: {min:0.###}..{max:0.###}" + (double.IsNaN(step) ? "" : $" · bước {step:0.###}");
		}
		return ReadJsonNodeBool(field["restart_required"], false)
			? "Cần restart Brain sau khi áp dụng."
			: "Brain sẽ validate giá trị khi nhận ACK.";
	}

	private int ReadCatalogSchemaVersion()
	{
		return _runtimeSettingsCatalog["schema_version"] is JsonValue value && value.TryGetValue(out int schema)
			? schema
			: 0;
	}

	private static string RuntimeSettingsNodeToText(JsonNode node)
	{
		if (node == null)
			return "";
		if (node is JsonValue value)
		{
			if (value.TryGetValue(out string text)) return text ?? "";
			if (value.TryGetValue(out bool boolean)) return boolean ? "true" : "false";
			if (value.TryGetValue(out long integer)) return integer.ToString(CultureInfo.InvariantCulture);
			if (value.TryGetValue(out double number)) return number.ToString("0.########", CultureInfo.InvariantCulture);
		}
		return node.ToJsonString();
	}

	private static string InferRuntimeSettingsKind(JsonNode node)
	{
		if (node is JsonValue value)
		{
			if (value.TryGetValue(out bool _)) return "bool";
			if (value.TryGetValue(out int _) || value.TryGetValue(out long _)) return "int";
			if (value.TryGetValue(out float _) || value.TryGetValue(out double _)) return "float";
		}
		return "string";
	}

	private static string HumanizeRuntimeSettingsKey(string key)
	{
		string text = (key ?? "").Replace("_seconds", " (giây)").Replace('_', ' ').Trim();
		return text.Length == 0 ? key ?? "" : char.ToUpperInvariant(text[0]) + text.Substring(1);
	}

	private static bool JsonNodesEquivalent(JsonNode left, JsonNode right)
	{
		if (left == null && right == null) return true;
		if (left == null || right == null) return false;
		return string.Equals(left.ToJsonString(), right.ToJsonString(), StringComparison.Ordinal);
	}

	private static string ReadJsonNodeString(JsonNode node, string fallback)
	{
		if (node is JsonValue value)
		{
			if (value.TryGetValue(out string text)) return text ?? fallback;
			return RuntimeSettingsNodeToText(node);
		}
		return fallback;
	}

	private static bool ReadJsonNodeBool(JsonNode node, bool fallback)
	{
		if (node is JsonValue value)
		{
			if (value.TryGetValue(out bool boolean)) return boolean;
			if (value.TryGetValue(out string text)) return IsTruthy(text);
			if (value.TryGetValue(out int integer)) return integer != 0;
		}
		return fallback;
	}

	private static double ReadJsonNodeDouble(JsonNode node, double fallback)
	{
		if (node is JsonValue value)
		{
			if (value.TryGetValue(out double number)) return number;
			if (value.TryGetValue(out float single)) return single;
			if (value.TryGetValue(out int integer)) return integer;
			if (value.TryGetValue(out long longInteger)) return longInteger;
			if (value.TryGetValue(out string text) && double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed)) return parsed;
		}
		return fallback;
	}
}
