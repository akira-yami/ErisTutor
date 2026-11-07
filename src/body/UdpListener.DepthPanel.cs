using Godot;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;

public partial class UdpListener : Node2D
{
	// UI RETIRED: Identity Runtime vẫn được capture/persist cho cognition và debug nội bộ,
	// nhưng UdpListener không còn tạo/expose DepthPanel trong UI chính. Renderer phía dưới
	// được giữ tạm để tránh trộn việc xóa UI với migration backend trong cùng một patch.
	// Panel cũ "Chiều sâu" từng phụ thuộc Ni/Fe/ToM legacy. Từ Architecture C v7 semantic cutover,
	// nó trở thành bảng Identity & Life Runtime, đọc đúng một contract
	// identity_runtime. Tên method/field cũ được giữ để không làm vỡ UdpListener.
	private static readonly Vector2 DepthPanelMinSize = new(1040f, 648f);
	private const float DepthPanelMargin = 16f;

	private Panel _depthPanel;
	private Button _contextDepthButton;
	private VBoxContainer _depthContentList;
	private Label _depthTitleLabel;
	private Label _depthStatusLabel;
	private Label _depthLearningChip;
	private Label _depthTurnChip;
	private Button _depthFreezeButton;
	private bool _depthResetScrollOnRefresh = true;
	private ScrollContainer _depthScrollContainer;
	private Control _depthScrollDecorationLayer;
	private TextureRect _depthScrollTrackRect;
	private TextureRect _depthScrollFillRect;
	private TextureRect _depthScrollCapRect;
	private Texture2D _depthScrollTrackTexture;
	private Texture2D _depthScrollFillTexture;
	private Texture2D _depthScrollCapTexture;
	private readonly List<Button> _depthTabButtons = new();
	private string _depthActiveTab = "self";
	private bool _depthLearningEnabled = true;

	private static readonly Color IdentityInk = new(0.16f, 0.12f, 0.20f, 1f);
	private static readonly Color IdentityMuted = new(0.39f, 0.35f, 0.43f, 1f);
	private static readonly Color IdentityAccent = new(0.72f, 0.24f, 0.49f, 1f);
	private static readonly Color IdentityAccentSoft = new(0.98f, 0.93f, 0.95f, 1f);
	private static readonly Color IdentityCard = new(1f, 0.997f, 0.994f, 0.985f);
	private static readonly Color IdentityBorder = new(0.89f, 0.82f, 0.86f, 1f);
	private static readonly Color IdentityPink = new(0.91f, 0.18f, 0.46f, 1f);
	private static readonly Color IdentityPurple = new(0.50f, 0.35f, 0.75f, 1f);
	private static readonly Color IdentityBlue = new(0.39f, 0.50f, 0.78f, 1f);
	private static readonly Color IdentityOrange = new(0.83f, 0.53f, 0.29f, 1f);
	private static readonly Color IdentityGreen = new(0.28f, 0.61f, 0.46f, 1f);

	// Bộ asset mới dành riêng cho Identity Runtime. Tất cả ảnh đều là PNG nền trong suốt.
	// Panel vẫn chạy bằng fallback glyph nếu người dùng chưa thả đủ asset.
	private const string IdentityAssetRoot = "res://asset/identy/";
	private const string LegacyIdentityAssetRoot = "res://asset/";

	private static string IdentityAsset(string fileName) => IdentityAssetRoot + fileName;

	private sealed class IdentityLiveEvent
	{
		public string Title = "";
		public string Detail = "";
		public string Tag = "observing";
		public string IconPath = "";
		public string FallbackGlyph = "·";
		public Color Accent = IdentityAccent;
	}

	private sealed class IdentityScoreEntry
	{
		public string Key = "";
		public float Value;
		public string Trigger = "";
	}

	private void OnContextDepthPressed()
	{
		_bodyAutonomy?.RecordUserInteraction();
		ToggleDepthPanel();
	}

	private void ToggleDepthPanel()
	{
		if (_depthPanel == null)
			return;
		if (_depthPanel.Visible)
			_depthPanel.Visible = false;
		else
			ShowDepthPanel();
	}

	private void ShowDepthPanel()
	{
		if (_depthPanel == null)
			return;
		CloseCharacterContextMenu();
		_depthPanel.Visible = true;
		Vector2 viewport = GetViewportRect().Size;
		float width = Mathf.Min(DepthPanelMinSize.X, Mathf.Max(640f, viewport.X - DepthPanelMargin * 2f));
		float height = Mathf.Min(DepthPanelMinSize.Y, Mathf.Max(500f, viewport.Y - DepthPanelMargin * 2f));
		_depthPanel.Size = new Vector2(width, height);
		_depthPanel.Position = new Vector2(
			Mathf.Max(0f, (viewport.X - width) * 0.5f),
			Mathf.Max(0f, (viewport.Y - height) * 0.5f));
		_depthResetScrollOnRefresh = true;
		RefreshDepthPanel();
		Callable.From(UpdateDepthScrollDecor).CallDeferred();
	}

	private void BuildDepthPanelUi()
	{
		if (_depthPanel == null)
			return;
		if (string.IsNullOrWhiteSpace(_latestIdentityRuntimeJson))
			LoadIdentityRuntimeState();

		foreach (Node child in _depthPanel.GetChildren())
			child.QueueFree();

		// V83: Identity giữ đúng hai mặt chính người dùng cần đọc: SELF và TRACE.
		// INNER bị bỏ khỏi UI thường để panel bớt phân mảnh và tập trung hơn.
		_depthPanel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(0.992f, 0.978f, 0.973f, 0.997f),
			BorderColor = new Color(0.55f, 0.35f, 0.45f, 0.48f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 18,
			CornerRadiusTopRight = 18,
			CornerRadiusBottomLeft = 18,
			CornerRadiusBottomRight = 18,
			ShadowColor = new Color(0.18f, 0.08f, 0.15f, 0.16f),
			ShadowSize = 14,
			ShadowOffset = new Vector2(0f, 5f),
		});
		_depthPanel.ClipContents = true;

		var root = new VBoxContainer
		{
			Name = "IdentityRuntimeRoot",
			OffsetLeft = 16f,
			OffsetTop = 12f,
			OffsetRight = -16f,
			OffsetBottom = -12f,
		};
		root.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		root.AddThemeConstantOverride("separation", 4);
		_depthPanel.AddChild(root);

		var header = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 36f) };
		header.AddThemeConstantOverride("separation", 8);
		root.AddChild(header);

		header.AddChild(NewIconBadge(IdentityAsset("identity_brand.png"), "✦", new Vector2(36f, 34f), IdentityPink, false));
		var titleBlock = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		titleBlock.AddThemeConstantOverride("separation", -2);
		_depthTitleLabel = NewLabel("ERIS / IDENTITY_01", 18, new Color(0.30f, 0.16f, 0.25f), true);
		_depthStatusLabel = NewLabel("persistent identity · live snapshot", 8, IdentityMuted, false);
		titleBlock.AddChild(_depthTitleLabel);
		titleBlock.AddChild(_depthStatusLabel);
		header.AddChild(titleBlock);

		var present = NewLabel("●  PRESENT", 9, new Color(0.26f, 0.49f, 0.35f), true);
		present.CustomMinimumSize = new Vector2(94f, 24f);
		present.HorizontalAlignment = HorizontalAlignment.Center;
		present.VerticalAlignment = VerticalAlignment.Center;
		present.SizeFlagsHorizontal = Control.SizeFlags.ShrinkEnd;
		present.AddThemeStyleboxOverride("normal", IdentityDossierChipStyle(IdentityGreen));
		header.AddChild(present);

		_depthFreezeButton = NewIdentityHeaderActionButton(
			"Tạm dừng / tiếp tục học Identity",
			IdentityAsset("identity_action_freeze.png"),
			"❄",
			ToggleIdentityLearning);
		header.AddChild(_depthFreezeButton);
		header.AddChild(NewIdentityHeaderActionButton(
			"Xuất identity snapshot",
			IdentityAsset("identity_action_export.png"),
			"⇩",
			() => SendIdentityControl("snapshot")));

		var windowControls = CreateWindowControlCapsule(
			() => _depthPanel.Visible = false,
			() => _depthPanel.Visible = false);
		header.AddChild(windowControls);

		var quote = NewLabel("✦  “A self is just a pattern that refuses to disappear.”  ✦", 9, new Color(0.47f, 0.29f, 0.39f), false);
		quote.HorizontalAlignment = HorizontalAlignment.Center;
		quote.CustomMinimumSize = new Vector2(0f, 18f);
		quote.VerticalAlignment = VerticalAlignment.Center;
		root.AddChild(quote);

		var tabsWrap = new CenterContainer
		{
			CustomMinimumSize = new Vector2(0f, 34f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		root.AddChild(tabsWrap);

		var tabs = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(612f, 34f),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter,
		};
		tabs.AddThemeConstantOverride("separation", 8);
		tabsWrap.AddChild(tabs);
		_depthTabButtons.Clear();
		AddDepthTab(tabs, "self", "SELF", IdentityAsset("identity_tab_self.png"), "◇");
		AddDepthTab(tabs, "trace", "TRACE•", IdentityAsset("identity_tab_trace.png"), "↗");

		// Runtime meta vẫn tồn tại cho control/diagnostic, nhưng không ném TURN/STATE vào mặt user.
		var hiddenRuntimeMeta = new HBoxContainer { Visible = false };
		_depthLearningChip = NewChip("LEARNING ?", new Color(0.93f, 0.99f, 0.96f), IdentityGreen);
		_depthTurnChip = NewChip("TURN 0 · STATE 0", new Color(0.95f, 0.97f, 1f), IdentityBlue);
		hiddenRuntimeMeta.AddChild(_depthLearningChip);
		hiddenRuntimeMeta.AddChild(_depthTurnChip);
		root.AddChild(hiddenRuntimeMeta);

		var scroll = new ScrollContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			SizeFlagsVertical = Control.SizeFlags.ExpandFill,
			HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
			VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
			ClipContents = true,
		};
		scroll.AddThemeConstantOverride("v_scrollbar_separation", 0);
		root.AddChild(scroll);
		_depthScrollContainer = scroll;

		var scrollPad = new MarginContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
		};
		scrollPad.AddThemeConstantOverride("margin_right", 10);
		scrollPad.AddThemeConstantOverride("margin_top", 0);
		scrollPad.AddThemeConstantOverride("margin_bottom", 2);
		scroll.AddChild(scrollPad);

		_depthContentList = new VBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			CustomMinimumSize = new Vector2(0f, 0f),
			SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
		};
		_depthContentList.AddThemeConstantOverride("separation", 10);
		scrollPad.AddChild(_depthContentList);

		ApplyHiddenScrollBarTheme(scroll.GetVScrollBar());
		scroll.GetVScrollBar().ValueChanged += _ => UpdateDepthScrollDecor();
		scroll.Resized += UpdateDepthScrollDecor;

		BuildDepthScrollDecoration();
		RefreshDepthTabStyle();
		RefreshDepthPanel();
	}


	private void BuildDepthScrollDecoration()
	{
		_depthScrollCapTexture ??= LoadResourceTexture("res://asset/Resource/topcap.png");
		_depthScrollTrackTexture ??= LoadResourceTexture("res://asset/Resource/scrolltrack.png");
		_depthScrollFillTexture ??= LoadResourceTexture("res://asset/Resource/scrollfill.png");

		_depthScrollDecorationLayer = new Control
		{
			Name = "IdentityScrollDecoration",
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		_depthScrollDecorationLayer.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		_depthPanel.AddChild(_depthScrollDecorationLayer);

		_depthScrollTrackRect = CreateDecorTextureRect(_depthScrollTrackTexture, new Vector2(14f, 220f));
		_depthScrollFillRect = CreateDecorTextureRect(_depthScrollFillTexture, new Vector2(14f, 72f));
		_depthScrollCapRect = CreateDecorTextureRect(_depthScrollCapTexture, new Vector2(28f, 28f));
		_depthScrollDecorationLayer.AddChild(_depthScrollTrackRect);
		_depthScrollDecorationLayer.AddChild(_depthScrollFillRect);
		_depthScrollDecorationLayer.AddChild(_depthScrollCapRect);

		Callable.From(UpdateDepthScrollDecor).CallDeferred();
	}

	private void UpdateDepthScrollDecor()
	{
		if (_depthScrollContainer == null || _depthPanel == null)
			return;

		VScrollBar scrollBar = _depthScrollContainer.GetVScrollBar();
		if (scrollBar == null)
			return;

		Vector2 panelOrigin = _depthPanel.GlobalPosition;
		Vector2 scPos = _depthScrollContainer.GlobalPosition - panelOrigin;
		Vector2 scSize = _depthScrollContainer.Size;

		float railWidth = 12f;
		float trackX = scPos.X + scSize.X - railWidth - 8f;
		float trackTop = scPos.Y + 5f;
		float trackHeight = Mathf.Max(52f, scSize.Y - 10f);
		bool canScroll = scrollBar.MaxValue > scrollBar.Page + 0.5;

		if (_depthScrollTrackRect != null)
		{
			_depthScrollTrackRect.Position = new Vector2(trackX, trackTop);
			_depthScrollTrackRect.Size = new Vector2(railWidth, trackHeight);
			_depthScrollTrackRect.Visible = canScroll;
		}

		float thumbMinHeight = 56f;
		float thumbMaxHeight = Mathf.Min(trackHeight, 160f);
		float thumbHeight = thumbMinHeight;
		double maxVal = scrollBar.MaxValue;
		if (maxVal > 0.001 && scrollBar.Page > 0)
		{
			float pageRatio = Mathf.Clamp((float)(scrollBar.Page / maxVal), 0.12f, 1f);
			thumbHeight = Mathf.Clamp(trackHeight * pageRatio, thumbMinHeight, thumbMaxHeight);
		}

		float scrollRange = Mathf.Max(0f, (float)(scrollBar.MaxValue - scrollBar.Page));
		float scrollRatio = scrollRange > 0.001f
			? Mathf.Clamp((float)(scrollBar.Value / scrollRange), 0f, 1f)
			: 0f;
		float thumbY = trackTop + scrollRatio * Mathf.Max(0f, trackHeight - thumbHeight);

		if (_depthScrollFillRect != null)
		{
			_depthScrollFillRect.Position = new Vector2(trackX, thumbY);
			_depthScrollFillRect.Size = new Vector2(railWidth, thumbHeight);
			_depthScrollFillRect.Visible = canScroll;
		}

		if (_depthScrollCapRect != null)
		{
			float capWidth = railWidth + 14f;
			float capHeight = capWidth;
			if (_depthScrollCapTexture != null)
			{
				Vector2 texSize = _depthScrollCapTexture.GetSize();
				if (texSize.X > 0f && texSize.Y > 0f)
					capHeight = capWidth * (texSize.Y / texSize.X);
			}
			_depthScrollCapRect.Position = new Vector2(
				trackX + (railWidth - capWidth) * 0.5f,
				thumbY - capHeight * 0.40f);
			_depthScrollCapRect.Size = new Vector2(capWidth, capHeight);
			_depthScrollCapRect.Visible = canScroll;
		}
	}


	private void AddDepthTab(HBoxContainer parent, string key, string label, string iconPath, string fallbackGlyph)
	{
		string resolvedIconPath = ResolveIdentityAssetPath(iconPath);
		bool hasIcon = ResourceLoader.Exists(resolvedIconPath);
		var button = NewButton(hasIcon ? label : $"{fallbackGlyph}  {label}", () =>
		{
			_depthActiveTab = key;
			_depthResetScrollOnRefresh = true;
			RefreshDepthTabStyle();
			RefreshDepthPanel();
		}, danger: false);
		button.SetMeta("identity_tab", key);
		button.SizeFlagsHorizontal = Control.SizeFlags.Fill;
		button.SizeFlagsStretchRatio = 1f;
		button.CustomMinimumSize = new Vector2(296f, 32f);
		button.AddThemeFontSizeOverride("font_size", 10);
		button.ClipText = true;
		if (hasIcon)
		{
			button.Icon = GD.Load<Texture2D>(resolvedIconPath);
			button.ExpandIcon = true;
			button.AddThemeConstantOverride("icon_max_width", 22);
		}
		_depthTabButtons.Add(button);
		parent.AddChild(WrapHBoxControl(button, 32f));
	}

	private Control WrapHBoxControl(Control child, float height)
	{
		var slot = new Control
		{
			CustomMinimumSize = new Vector2(child.CustomMinimumSize.X, height),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter,
			ClipContents = true,
		};
		child.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		child.OffsetLeft = 0f;
		child.OffsetTop = 0f;
		child.OffsetRight = 0f;
		child.OffsetBottom = 0f;
		child.CustomMinimumSize = new Vector2(0f, height);
		child.SizeFlagsHorizontal = Control.SizeFlags.Fill;
		slot.AddChild(child);
		return slot;
	}


	private void RefreshDepthTabStyle()
	{
		foreach (Button button in _depthTabButtons)
		{
			string key = button.HasMeta("identity_tab") ? button.GetMeta("identity_tab").AsString() : "";
			bool active = string.Equals(key, _depthActiveTab, StringComparison.Ordinal);
			button.AddThemeColorOverride("font_color", active ? IdentityPink : new Color(0.33f, 0.25f, 0.31f));
			button.AddThemeColorOverride("font_hover_color", active ? IdentityPink : new Color(0.46f, 0.18f, 0.34f));
			// PNG Identity đã có màu hoàn chỉnh. Không được dùng theme icon color để
			// nhuộm chúng thành một mảng hồng như icon monochrome.
			button.AddThemeColorOverride("icon_normal_color", Colors.White);
			button.AddThemeColorOverride("icon_hover_color", Colors.White);
			button.AddThemeColorOverride("icon_pressed_color", Colors.White);
			button.AddThemeColorOverride("icon_focus_color", Colors.White);
			button.AddThemeStyleboxOverride("normal", IdentityTabStyle(active, false));
			button.AddThemeStyleboxOverride("hover", IdentityTabStyle(active, true));
			button.AddThemeStyleboxOverride("pressed", IdentityTabPressedStyle());
			button.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());
		}
	}


	private void ToggleIdentityLearning()
	{
		SendIdentityControl("set_learning", !_depthLearningEnabled);
	}


	private void SendIdentityControl(string action, bool? enabled = null)
	{
		var payload = new Dictionary<string, object>
		{
			["type"] = "identity_control",
			["request_id"] = Guid.NewGuid().ToString("N"),
			["action"] = action ?? "",
		};
		if (enabled.HasValue)
			payload["enabled"] = enabled.Value;
		Error error = _chatSend.PutPacket(System.Text.Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		if (_depthStatusLabel != null)
			_depthStatusLabel.Text = error == Error.Ok ? $"đã gửi {action}" : $"gửi {action} lỗi: {error}";
	}

	private void TryApplyIdentityControlAck(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw) || raw.IndexOf("identity_control_ack", StringComparison.Ordinal) < 0)
			return;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			if (!doc.RootElement.TryGetProperty("identity_control_ack", out JsonElement ack) || ack.ValueKind != JsonValueKind.Object)
				return;
			bool ok = ReadBool(ack, "ok", false);
			string action = ReadString(ack, "action");
			string result = ReadString(ack, "result");
			if (_depthStatusLabel != null)
				_depthStatusLabel.Text = ok ? $"{action} ✓ · {TrimForPanel(result, 72)}" : $"{action} lỗi · {TrimForPanel(result, 72)}";
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[IdentityPanel] ACK parse failed: {ex.Message}");
		}
	}

	private void TryCaptureIdentityRuntimeBlock(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw))
			return;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;
			JsonElement block;
			if (root.ValueKind == JsonValueKind.Object
				&& root.TryGetProperty("identity_runtime", out block)
				&& block.ValueKind == JsonValueKind.Object)
			{
				_latestIdentityRuntimeJson = block.GetRawText();
			}
			else if (root.ValueKind == JsonValueKind.Object
				&& root.TryGetProperty("depth_v2", out block)
				&& block.ValueKind == JsonValueKind.Object)
			{
				_latestIdentityRuntimeJson = block.GetRawText();
			}
			else
			{
				return;
			}
			SaveIdentityRuntimeState();
			if (_depthPanel != null && _depthPanel.Visible)
				RefreshDepthPanel();
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[IdentityPanel] capture failed: {ex.Message}");
		}
	}

	private void SaveIdentityRuntimeState()
	{
		if (string.IsNullOrWhiteSpace(_latestIdentityRuntimeJson))
			return;
		try
		{
			using var file = Godot.FileAccess.Open(IdentityRuntimeStatePath, Godot.FileAccess.ModeFlags.Write);
			file?.StoreString(_latestIdentityRuntimeJson);
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[IdentityPanel] save failed: {ex.Message}");
		}
	}

	private void LoadIdentityRuntimeState()
	{
		try
		{
			string path = Godot.FileAccess.FileExists(IdentityRuntimeStatePath)
				? IdentityRuntimeStatePath
				: Godot.FileAccess.FileExists(LegacyDepthV2StatePath) ? LegacyDepthV2StatePath : "";
			if (string.IsNullOrWhiteSpace(path))
				return;
			using var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Read);
			if (file != null)
				_latestIdentityRuntimeJson = file.GetAsText();
			if (string.Equals(path, LegacyDepthV2StatePath, StringComparison.Ordinal)
				&& !string.IsNullOrWhiteSpace(_latestIdentityRuntimeJson))
				SaveIdentityRuntimeState();
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[IdentityPanel] load failed: {ex.Message}");
		}
	}

	private void RefreshDepthPanel()
	{
		if (_depthContentList == null)
			return;
		foreach (Node child in _depthContentList.GetChildren())
		{
			_depthContentList.RemoveChild(child);
			child.QueueFree();
		}

		if (string.IsNullOrWhiteSpace(_latestIdentityRuntimeJson))
		{
			AddInfoCard("Chưa có dữ liệu", new[]
			{
				"Panel đang chờ identity_runtime từ Python.",
				"Nếu Architecture C đã bật mà panel vẫn trắng, kiểm tra panel_state_update và UDP ack.",
			});
			Callable.From(UpdateDepthScrollDecor).CallDeferred();
			ResetDepthScrollIfNeeded();
			return;
		}

		try
		{
			using JsonDocument doc = JsonDocument.Parse(_latestIdentityRuntimeJson);
			JsonElement root = doc.RootElement;
			int turn = ReadInt(root, "turn_seq");
			int version = ReadInt(root, "state_version");
			_depthTurnChip.Text = $"TURN {turn} · STATE {version}";

			JsonElement learning = Obj(root, "learning");
			_depthLearningEnabled = ReadBool(learning, "enabled", true);
			_depthLearningChip.Text = _depthLearningEnabled ? "LEARNING NORMAL" : "OBSERVE ONLY";
			_depthLearningChip.AddThemeColorOverride("font_color", _depthLearningEnabled ? new Color(0.22f, 0.55f, 0.36f) : new Color(0.78f, 0.34f, 0.23f));
			if (_depthFreezeButton != null)
				_depthFreezeButton.Text = _depthLearningEnabled ? "Tạm dừng học" : "Tiếp tục học";

			string architecture = ReadString(root, "architecture");
			JsonElement individualRuntime = Obj(root, "individual_runtime");
			string individualSchema = ReadString(individualRuntime, "schema");
			_depthStatusLabel.Text = !string.IsNullOrWhiteSpace(individualSchema)
				? "persistent identity · live snapshot"
				: string.IsNullOrWhiteSpace(architecture) ? "identity runtime · compatibility" : architecture.Replace("ai_orchestrated.", "v");

			if (string.Equals(_depthActiveTab, "inner", StringComparison.Ordinal))
				_depthActiveTab = "self";

			switch (_depthActiveTab)
			{
				case "trace": RenderIdentityTraceV82(root); break;
				default: RenderIdentitySelfV82(root); break;
			}
			Callable.From(UpdateDepthScrollDecor).CallDeferred();
			ResetDepthScrollIfNeeded();
		}
		catch (Exception ex)
		{
			AddInfoCard("Không đọc được snapshot", new[] { ex.Message, "Raw state vẫn được giữ để gói sau có thể sửa lại." });
			ResetDepthScrollIfNeeded();
		}
	}


	// ─────────────────────────────────────────────────────────────────────
	// V82 IDENTITY DOSSIER
	// Một panel, hai tab: SELF / TRACE. Số thô vẫn nằm trong runtime,
	// nhưng normal UI chỉ kể ý nghĩa từ snapshot identity_runtime.
	// ─────────────────────────────────────────────────────────────────────
	private void RenderIdentitySelfV82(JsonElement root)
	{
		if (_depthContentList == null) return;

		JsonElement activeMotive = FirstObject(Arr(root, "active_motives"));
		if (activeMotive.ValueKind != JsonValueKind.Object)
			activeMotive = FirstObject(Arr(root, "motives_top"));
		string motiveKey = ReadString(activeMotive, "key");
		string desire = !string.IsNullOrWhiteSpace(motiveKey) ? MotiveDescription(motiveKey) : "Chưa có một lực kéo đủ rõ để gọi tên.";

		JsonElement conflict = Obj(root, "active_conflict");
		string conflictId = ReadString(conflict, "conflict_id");
		string tension = BuildConflictSentenceV82(conflictId, ReadString(conflict, "chosen_side"));

		JsonElement coreValue = FirstObject(Arr(root, "values_top"));
		string coreKey = ReadString(coreValue, "key");
		string core = !string.IsNullOrWhiteSpace(coreKey) ? ValueDescription(coreKey) : "Cốt lõi đang yên, chưa có giá trị nào lấn át phần còn lại.";

		// V89: panel đã compact, nhưng SELF không được co theo shell. Khung chiếm gần
		// toàn vùng content để tránh cảm giác một tấm poster nhỏ nằm giữa khoảng trắng.
		var center = new CenterContainer
		{
			CustomMinimumSize = new Vector2(0f, 414f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		_depthContentList.AddChild(center);

		var stage = new Control
		{
			CustomMinimumSize = new Vector2(940f, 410f),
			Size = new Vector2(940f, 410f),
			ClipContents = false,
		};
		center.AddChild(stage);

		string selfFramePath = ResolveIdentityAssetPath(IdentityAsset("identity_self_portrait_frame.png"));
		if (ResourceLoader.Exists(selfFramePath))
		{
			var frame = NewIdentityTexture(selfFramePath, new Vector2(800f, 450f));
			PlaceIdentityControl(frame, 70f, -18f, 800f, 450f);
			frame.StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered;
			frame.Modulate = new Color(1f, 1f, 1f, 0.64f);
			frame.ZIndex = 0;
			stage.AddChild(frame);
		}

		string portraitPath = ResolveIdentityAssetPath(IdentityAsset("identity_portrait_eris.png.png"));
		if (ResourceLoader.Exists(portraitPath))
		{
			var portrait = NewIdentityTexture(portraitPath, new Vector2(165f, 252f));
			PlaceIdentityControl(portrait, 118f, 64f, 165f, 252f);
			portrait.StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered;
			portrait.ZIndex = 2;
			stage.AddChild(portrait);
		}

		var erisName = NewLabel("ERIS", 9, new Color(0.43f, 0.22f, 0.34f), true);
		PlaceIdentityControl(erisName, 135f, 316f, 132f, 18f);
		erisName.HorizontalAlignment = HorizontalAlignment.Center;
		stage.AddChild(erisName);

		var current = NewLabel("CURRENT FORM", 9, new Color(0.43f, 0.20f, 0.34f), true);
		PlaceIdentityControl(current, 404f, 42f, 170f, 20f);
		stage.AddChild(current);

		var formPill = NewDossierPill(BuildIdentityFormTagsV82(root), IdentityGreen);
		PlaceIdentityControl(formPill, 704f, 36f, 126f, 24f);
		stage.AddChild(formPill);

		var divider = new ColorRect { Color = new Color(0.58f, 0.33f, 0.43f, 0.16f), MouseFilter = Control.MouseFilterEnum.Ignore };
		PlaceIdentityControl(divider, 404f, 70f, 426f, 1f);
		stage.AddChild(divider);

		string headline = BuildIdentitySelfHeadlineV82(root);
		var quote = NewLabel($"“{TrimForPanel(headline, 92)}”", 21, new Color(0.25f, 0.13f, 0.22f), true);
		PlaceIdentityControl(quote, 404f, 86f, 392f, 94f);
		quote.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		quote.VerticalAlignment = VerticalAlignment.Center;
		quote.MaxLinesVisible = 2;
		quote.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		stage.AddChild(quote);

		var lowerDivider = new ColorRect { Color = new Color(0.58f, 0.33f, 0.43f, 0.13f), MouseFilter = Control.MouseFilterEnum.Ignore };
		PlaceIdentityControl(lowerDivider, 404f, 190f, 426f, 1f);
		stage.AddChild(lowerDivider);

		var trinity = new HBoxContainer { CustomMinimumSize = new Vector2(438f, 126f) };
		PlaceIdentityControl(trinity, 397f, 204f, 438f, 126f);
		trinity.AddThemeConstantOverride("separation", 8);
		stage.AddChild(trinity);
		trinity.AddChild(NewIdentityThemeColumnBare("DESIRE", IdentityAsset("identity_self_desire.png"), "✦", desire, IdentityPink));
		trinity.AddChild(NewIdentityVerticalSeparator());
		trinity.AddChild(NewIdentityThemeColumnBare("TENSION", IdentityAsset("identity_self_tension.png"), "◇", tension, IdentityPurple));
		trinity.AddChild(NewIdentityVerticalSeparator());
		trinity.AddChild(NewIdentityThemeColumnBare("CORE", IdentityAsset("identity_self_core.png"), "❧", core, IdentityGreen));
	}

	private void RenderIdentityTraceV82(JsonElement root)
	{
		if (_depthContentList == null) return;

		// V89 TRACE: một composition duy nhất. Bỏ thread-card nổi bên phải;
		// state rows, persistent thread và timeline dùng chung một trục thị giác.
		var center = new CenterContainer
		{
			CustomMinimumSize = new Vector2(0f, 402f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		_depthContentList.AddChild(center);

		var stage = new Control
		{
			CustomMinimumSize = new Vector2(930f, 396f),
			Size = new Vector2(930f, 396f),
		};
		center.AddChild(stage);

		// Cột trái chỉ làm neo nhân vật + nhãn TRACE. Không chiếm một phần ba panel nữa.
		var introTitle = NewLabel("TRACE OVER TIME", 10, new Color(0.43f, 0.20f, 0.34f), true);
		PlaceIdentityControl(introTitle, 8f, 18f, 150f, 20f);
		stage.AddChild(introTitle);
		var desc = NewLabel("Continuity and change.\nThứ gì đã dịch chuyển,\nvà thứ gì vẫn đi theo Eris.", 8, IdentityMuted, false);
		PlaceIdentityControl(desc, 8f, 44f, 150f, 56f);
		desc.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		stage.AddChild(desc);
		var portrait = NewIdentityPortraitArt(new Vector2(112f, 168f));
		PlaceIdentityControl(portrait, 26f, 112f, 112f, 168f);
		stage.AddChild(portrait);

		// Main history spine.
		var changes = new VBoxContainer { CustomMinimumSize = new Vector2(600f, 190f) };
		PlaceIdentityControl(changes, 190f, 14f, 600f, 190f);
		changes.AddThemeConstantOverride("separation", 5);
		stage.AddChild(changes);

		var headers = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 32f) };
		var thenBox = new VBoxContainer { CustomMinimumSize = new Vector2(170f, 0f) };
		thenBox.AddThemeConstantOverride("separation", -1);
		thenBox.AddChild(NewLabel("THEN", 10, IdentityMuted, true));
		thenBox.AddChild(NewLabel("Earlier Self", 7, IdentityMuted, false));
		headers.AddChild(thenBox);
		headers.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		var nowBox = new VBoxContainer { CustomMinimumSize = new Vector2(150f, 0f) };
		nowBox.AddThemeConstantOverride("separation", -1);
		nowBox.AddChild(NewLabel("NOW", 10, IdentityGreen, true));
		nowBox.AddChild(NewLabel("Current Self", 7, IdentityMuted, false));
		headers.AddChild(nowBox);
		changes.AddChild(headers);
		changes.AddChild(NewSectionDivider());

		int traceCount = AddTraceRowsV82(changes, Arr(root, "recent_personality_trajectory"), 3);
		if (traceCount == 0)
		{
			changes.AddChild(NewTraceChangeRowV82("Chưa đủ dấu vết", "→", "đang quan sát", IdentityAsset("identity_trace_detached.png"), IdentityAsset("identity_trace_invested.png")));
			changes.AddChild(NewTraceChangeRowV82("Snapshot cũ", "→", BuildIdentityFormTagsV82(root), IdentityAsset("identity_trace_uncertain.png"), IdentityAsset("identity_trace_steady.png")));
		}

		// Persistent thread trở thành điểm neo ở chính giữa thay vì card rời bên phải.
		var threadTitle = NewLabel("THE THREAD THAT REMAINS", 8, new Color(0.50f, 0.28f, 0.40f), true);
		PlaceIdentityControl(threadTitle, 350f, 214f, 300f, 18f);
		threadTitle.HorizontalAlignment = HorizontalAlignment.Center;
		stage.AddChild(threadTitle);

		string threadAsset = ResolveIdentityAssetPath(IdentityAsset("identity_trace_thread_crystal.png"));
		if (ResourceLoader.Exists(threadAsset))
		{
			var threadTexture = NewIdentityTexture(threadAsset, new Vector2(300f, 92f));
			PlaceIdentityControl(threadTexture, 350f, 232f, 300f, 92f);
			threadTexture.Modulate = new Color(1f, 1f, 1f, 0.88f);
			stage.AddChild(threadTexture);
		}

		var persistent = NewLabel(BuildPersistentThreadV82(root), 9, IdentityInk, false);
		PlaceIdentityControl(persistent, 360f, 298f, 280f, 34f);
		persistent.HorizontalAlignment = HorizontalAlignment.Center;
		persistent.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		persistent.MaxLinesVisible = 2;
		persistent.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		stage.AddChild(persistent);

		// Timeline là một hàng liên tục nằm dưới thread, không còn ba marker rơi rớt.
		var timeline = new HBoxContainer { CustomMinimumSize = new Vector2(650f, 58f) };
		PlaceIdentityControl(timeline, 185f, 334f, 650f, 58f);
		timeline.AddThemeConstantOverride("separation", 2);
		stage.AddChild(timeline);
		timeline.AddChild(NewTraceMomentV82("BEFORE", "guarded", IdentityMuted, IdentityAsset("identity_trace_timeline_before.png"), "✦"));
		timeline.AddChild(NewTraceConnectorV82());
		timeline.AddChild(NewTraceMomentV82("THE TURN", "a choice persisted", IdentityPink, IdentityAsset("identity_trace_timeline_turn.png"), "✦"));
		timeline.AddChild(NewTraceConnectorV82());
		timeline.AddChild(NewTraceMomentV82("NOW", BuildIdentityFormTagsV82(root), IdentityGreen, IdentityAsset("identity_trace_timeline_now.png"), "✦"));
	}


	private static void PlaceIdentityControl(Control control, float x, float y, float width, float height)
	{
		control.SetAnchorsPreset(Control.LayoutPreset.TopLeft);
		control.Position = new Vector2(x, y);
		control.Size = new Vector2(width, height);
		control.CustomMinimumSize = new Vector2(width, height);
	}

	private PanelContainer NewIdentityDossierSheet(float minHeight, Color accent, int radius)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, minHeight), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.998f, 0.985f, 0.72f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.20f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = radius,
			CornerRadiusTopRight = radius,
			CornerRadiusBottomLeft = radius,
			CornerRadiusBottomRight = radius,
			ContentMarginLeft = 16f,
			ContentMarginTop = 13f,
			ContentMarginRight = 16f,
			ContentMarginBottom = 13f,
			ShadowColor = new Color(0.25f, 0.12f, 0.20f, 0.035f),
			ShadowSize = 4,
		});
		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 8);
		panel.AddChild(box);
		return panel;
	}

	private Control NewIdentityPortraitMedallion(Vector2? requestedSize = null)
	{
		Vector2 size = requestedSize ?? new Vector2(190f, 220f);
		var frame = new PanelContainer { CustomMinimumSize = size };
		frame.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(0.98f, 0.92f, 0.94f, 0.46f),
			BorderColor = new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.28f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 70,
			CornerRadiusTopRight = 70,
			CornerRadiusBottomLeft = 70,
			CornerRadiusBottomRight = 70,
			ContentMarginLeft = 8f,
			ContentMarginTop = 8f,
			ContentMarginRight = 8f,
			ContentMarginBottom = 8f,
		});
		string portraitPath = ResolveIdentityAssetPath(IdentityAsset("identity_portrait_eris.png.png"));
		if (ResourceLoader.Exists(portraitPath))
		{
			var portrait = NewIdentityTexture(portraitPath, size - new Vector2(16f, 16f));
			portrait.ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize;
			portrait.StretchMode = TextureRect.StretchModeEnum.KeepAspectCovered;
			frame.AddChild(portrait);
		}
		else
		{
			var fallback = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
			fallback.AddThemeConstantOverride("separation", 4);
			frame.AddChild(fallback);
			var glyph = NewLabel("◇", 44, new Color(0.54f, 0.25f, 0.40f), true);
			glyph.HorizontalAlignment = HorizontalAlignment.Center;
			fallback.AddChild(glyph);
			var name = NewLabel("ERIS", 12, IdentityMuted, true);
			name.HorizontalAlignment = HorizontalAlignment.Center;
			fallback.AddChild(name);
		}
		return frame;
	}

	private Control NewIdentityPortraitArt(Vector2 size)
	{
		string portraitPath = ResolveIdentityAssetPath(IdentityAsset("identity_portrait_eris.png.png"));
		if (ResourceLoader.Exists(portraitPath))
		{
			var portrait = NewIdentityTexture(portraitPath, size);
			portrait.StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered;
			return portrait;
		}
		var fallback = new CenterContainer { CustomMinimumSize = size };
		fallback.AddChild(NewLabel("◇", 34, IdentityPink, true));
		return fallback;
	}

	private Control NewIdentityThemeColumnBare(string title, string iconPath, string fallbackGlyph, string text, Color accent)
	{
		var box = new VBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			SizeFlagsStretchRatio = 1f,
			Alignment = BoxContainer.AlignmentMode.Center,
			CustomMinimumSize = new Vector2(0f, 122f),
		};
		box.AddThemeConstantOverride("separation", 4);

		var iconWrap = new CenterContainer { CustomMinimumSize = new Vector2(0f, 44f) };
		iconWrap.AddChild(NewIconBadge(iconPath, fallbackGlyph, new Vector2(36f, 36f), accent, true));
		box.AddChild(iconWrap);

		var head = NewLabel(title, 9, new Color(0.38f, 0.20f, 0.31f), true);
		head.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(head);

		var detail = NewLabel(TrimForPanel(text, 60), 8, IdentityMuted, false);
		detail.HorizontalAlignment = HorizontalAlignment.Center;
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		detail.CustomMinimumSize = new Vector2(0f, 40f);
		box.AddChild(detail);
		return box;
	}

	private Control NewIdentityVerticalSeparator()
	{
		var wrap = new CenterContainer { CustomMinimumSize = new Vector2(8f, 122f) };
		wrap.AddChild(new ColorRect
		{
			Color = new Color(0.55f, 0.34f, 0.44f, 0.20f),
			CustomMinimumSize = new Vector2(1f, 72f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		});
		return wrap;
	}

	private Control NewIdentityThemeColumn(string title, string iconPath, string fallbackGlyph, string text, Color accent)
	{
		var panel = new PanelContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, SizeFlagsStretchRatio = 1f, CustomMinimumSize = new Vector2(0f, 118f) };
		var style = IdentitySoftPanelStyle(accent, 0.018f, 11);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, 0.14f);
		panel.AddThemeStyleboxOverride("panel", style);
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 5);
		panel.AddChild(box);
		var icon = new CenterContainer { CustomMinimumSize = new Vector2(0f, 42f) };
		icon.AddChild(NewIconBadge(iconPath, fallbackGlyph, new Vector2(28f, 28f), accent, true));
		box.AddChild(icon);
		var head = NewLabel(title, 10, new Color(0.38f, 0.20f, 0.31f), true);
		head.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(head);
		var detail = NewLabel(TrimForPanel(text, 82), 8, IdentityMuted, false);
		detail.HorizontalAlignment = HorizontalAlignment.Center;
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(detail);
		return panel;
	}


	private Label NewDossierPill(string text, Color accent)
	{
		var label = NewLabel(TrimForPanel(text, 46), 9, accent, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.CustomMinimumSize = new Vector2(122f, 26f);
		label.SizeFlagsHorizontal = Control.SizeFlags.ShrinkEnd;
		label.AddThemeStyleboxOverride("normal", IdentityDossierChipStyle(accent));
		return label;
	}

	private string BuildIdentitySelfHeadlineV82(JsonElement root)
	{
		JsonElement observation = Obj(root, "latest_observation");
		JsonElement interpretation = Obj(observation, "self_interpretation");
		string summary = ReadString(interpretation, "summary");
		if (!string.IsNullOrWhiteSpace(summary)) return summary;
		return BuildIdentityHeadline(root);
	}

	private string BuildIdentityFormTagsV82(JsonElement root)
	{
		var tags = new List<string>();
		JsonElement expression = Obj(root, "expression");
		string preset = PrettyStageText(ReadString(expression, "preset"));
		if (!string.IsNullOrWhiteSpace(preset) && preset != "—") tags.Add(preset.ToLowerInvariant());
		JsonElement motive = FirstObject(Arr(root, "active_motives"));
		string motiveKey = ReadString(motive, "key");
		if (!string.IsNullOrWhiteSpace(motiveKey)) tags.Add(PrettyIdentityKey(motiveKey).ToLowerInvariant());
		if (tags.Count == 0) tags.Add("present");
		return string.Join(" · ", tags.GetRange(0, Math.Min(2, tags.Count)));
	}

	private string BuildConflictSentenceV82(string conflictId, string chosenSide)
	{
		if (string.IsNullOrWhiteSpace(conflictId)) return "Không có mâu thuẫn nào đang đòi quyền ưu tiên.";
		var names = SplitConflictNames(conflictId);
		string chosen = PrettyIdentityKey(chosenSide);
		if (!string.IsNullOrWhiteSpace(chosen) && chosen != "—")
			return $"{names.Left} ↔ {names.Right}. Hiện nghiêng về {chosen}.";
		return $"{names.Left} ↔ {names.Right}.";
	}




	private int AddTraceRowsV82(VBoxContainer parent, JsonElement trajectory, int limit)
	{
		if (trajectory.ValueKind != JsonValueKind.Array) return 0;
		var rows = new List<JsonElement>();
		foreach (JsonElement item in trajectory.EnumerateArray()) rows.Add(item);
		rows.Reverse();
		int count = 0;
		foreach (JsonElement item in rows)
		{
			string trait = PrettyIdentityKey(ReadString(item, "trait"));
			float delta = ReadFloat(item, "applied_delta");
			if (string.IsNullOrWhiteSpace(trait) || Mathf.Abs(delta) < 0.000001f) continue;
			string then = delta > 0f ? $"ít {trait.ToLowerInvariant()}" : $"{trait} rõ hơn";
			string now = delta > 0f ? $"{trait} rõ hơn" : $"ít {trait.ToLowerInvariant()}";
			string[] thenIcons = { "identity_trace_detached.png", "identity_trace_avoiding.png", "identity_trace_uncertain.png", "identity_trace_constrained.png" };
			string[] nowIcons = { "identity_trace_invested.png", "identity_trace_watching.png", "identity_trace_steady.png", "identity_trace_choosing.png" };
			int iconIndex = Mathf.Clamp(count, 0, 3);
			parent.AddChild(NewTraceChangeRowV82(then, "✦", now, IdentityAsset(thenIcons[iconIndex]), IdentityAsset(nowIcons[iconIndex])));
			if (++count >= limit) break;
		}
		return count;
	}

	private Control NewTraceChangeRowV82(string thenText, string connector, string nowText, string thenIconPath, string nowIconPath)
	{
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 44f) };
		row.AddThemeConstantOverride("separation", 8);

		var thenIcon = new CenterContainer { CustomMinimumSize = new Vector2(38f, 38f) };
		thenIcon.AddChild(NewIconBadge(thenIconPath, "·", new Vector2(34f, 34f), IdentityPink, true));
		row.AddChild(thenIcon);

		var left = NewLabel(TrimForPanel(thenText, 34), 9, IdentityMuted, true);
		left.CustomMinimumSize = new Vector2(160f, 0f);
		left.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		row.AddChild(left);

		var arrow = NewLabel(connector, 11, IdentityPink, true);
		arrow.HorizontalAlignment = HorizontalAlignment.Center;
		arrow.CustomMinimumSize = new Vector2(30f, 0f);
		row.AddChild(arrow);

		var right = NewLabel(TrimForPanel(nowText, 34), 9, IdentityInk, true);
		right.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		right.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		row.AddChild(right);

		var nowIcon = new CenterContainer { CustomMinimumSize = new Vector2(38f, 38f) };
		nowIcon.AddChild(NewIconBadge(nowIconPath, "·", new Vector2(34f, 34f), IdentityGreen, true));
		row.AddChild(nowIcon);
		return row;
	}

	private string BuildPersistentThreadV82(JsonElement root)
	{
		JsonElement horizon = Obj(root, "life_horizon");
		JsonElement goals = Arr(horizon, "goals");
		if (goals.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement goal in goals.EnumerateArray())
			{
				if (!string.Equals(ReadString(goal, "status"), "active", StringComparison.OrdinalIgnoreCase)) continue;
				string title = ReadString(goal, "title");
				if (!string.IsNullOrWhiteSpace(title)) return TrimForPanel(title, 150);
			}
		}
		JsonElement motive = FirstObject(Arr(root, "motives_top"));
		string key = ReadString(motive, "key");
		return string.IsNullOrWhiteSpace(key) ? "Một đường liên tục chưa chịu biến mất." : MotiveDescription(key);
	}

	private Control NewTraceMomentV82(string title, string detail, Color accent, string iconPath, string fallbackGlyph)
	{
		var box = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, SizeFlagsStretchRatio = 1f, Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 1);
		var markWrap = new CenterContainer { CustomMinimumSize = new Vector2(0f, 24f) };
		string resolved = ResolveIdentityAssetPath(iconPath);
		if (ResourceLoader.Exists(resolved))
			markWrap.AddChild(NewIdentityTexture(resolved, new Vector2(136f, 28f)));
		else
			markWrap.AddChild(NewIconBadge("", fallbackGlyph, new Vector2(22f, 22f), accent, true));
		box.AddChild(markWrap);
		var head = NewLabel(title, 8, accent, true);
		head.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(head);
		var copy = NewLabel(TrimForPanel(detail, 28), 7, IdentityMuted, false);
		copy.HorizontalAlignment = HorizontalAlignment.Center;
		copy.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(copy);
		return box;
	}

	private Control NewTraceConnectorV82()
	{
		var center = new CenterContainer { CustomMinimumSize = new Vector2(34f, 0f) };
		center.AddChild(new ColorRect
		{
			Color = new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.36f),
			CustomMinimumSize = new Vector2(30f, 1f),
		});
		return center;
	}

	private static JsonElement FirstObject(JsonElement array)
	{
		if (array.ValueKind != JsonValueKind.Array) return default;
		foreach (JsonElement item in array.EnumerateArray())
			if (item.ValueKind == JsonValueKind.Object) return item;
		return default;
	}

	// ─────────────────────────────────────────────────────────────────────
	// V81 JUST-ENOUGH IDENTITY PANEL
	// Mục tiêu: cực ít nhiễu nhưng vẫn đủ để hiểu "Eris là ai và đang đổi ra sao".
	// Không hiển thị mood, private thought, memory user, discovery hoặc Neuro trace
	// vì các phần đó đã có panel chuyên trách.
	// ─────────────────────────────────────────────────────────────────────
	private void RenderElegantOverviewTab(JsonElement root)
	{
		if (_depthContentList == null) return;
		JsonElement individual = Obj(root, "individual_runtime");
		JsonElement selfModel = Obj(individual, "operational_self_model");
		JsonElement continuous = Obj(individual, "continuous_self");
		JsonElement horizon = Obj(root, "life_horizon");

		// V81: Tổng quan chỉ trả lời ba câu: Eris là ai, đang hướng về đâu,
		// và vài đặc tính dài hạn nổi bật. Không biến overview thành bảng telemetry.
		var top = new HBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			CustomMinimumSize = new Vector2(0f, 170f),
		};
		top.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(top);

		var identityCard = NewIdentityCard("Bản sắc hiện tại", IdentityAsset("identity_values_orb.png"), "♡", IdentityPink, 168f);
		identityCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		identityCard.SizeFlagsStretchRatio = 1.0f;
		top.AddChild(identityCard);
		var identityBox = (VBoxContainer)identityCard.GetChild(0);
		var headline = NewLabel(BuildIdentityHeadline(root), 19, IdentityInk, true);
		headline.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		identityBox.AddChild(headline);
		identityBox.AddChild(NewLabel(BuildIdentitySubline(root, selfModel), 10, IdentityMuted, false));
		float continuity = ReadFloat(continuous, "continuity_strength");
		if (continuous.ValueKind == JsonValueKind.Object && continuity > 0.01f)
			AddMetricBar(identityBox, "Độ liên tục", continuity, IdentityPink);

		var lifeCard = NewIdentityCard("Hướng sống", IdentityAsset("identity_motive_orbit.png"), "↗", IdentityPurple, 168f);
		lifeCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		lifeCard.SizeFlagsStretchRatio = 1.30f;
		top.AddChild(lifeCard);
		var lifeBox = (VBoxContainer)lifeCard.GetChild(0);
		if (AddActiveHorizonGoals(lifeBox, horizon, 2) == 0)
		{
			var empty = NewLabel("Chưa hình thành hướng dài hạn đủ rõ.", 11, IdentityMuted, false);
			empty.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			lifeBox.AddChild(empty);
		}

		var coreCard = NewIdentityCard("Cốt lõi hiện tại", IdentityAsset("identity_tab_values.png"), "◇", IdentityAccent, 190f);
		_depthContentList.AddChild(coreCard);
		var coreBox = (VBoxContainer)coreCard.GetChild(0);
		var core = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		core.AddThemeConstantOverride("separation", 24);
		coreBox.AddChild(core);

		var values = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		values.AddThemeConstantOverride("separation", 5);
		values.AddChild(NewLabel("Giá trị", 10, IdentityMuted, true));
		if (AddTopScoreBars(values, Arr(root, "values_top"), 3, IdentityPink) == 0)
			values.AddChild(NewLabel("Chưa có giá trị nổi bật.", 10, IdentityMuted, false));
		core.AddChild(values);

		var behavior = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		behavior.AddThemeConstantOverride("separation", 5);
		behavior.AddChild(NewLabel("Nhịp dài hạn", 10, IdentityMuted, true));
		if (selfModel.ValueKind == JsonValueKind.Object)
		{
			AddMetricBar(behavior, "Chịu im lặng", ReadFloat(selfModel, "silence_tolerance"), IdentityGreen);
			AddMetricBar(behavior, "Presence nhẹ", ReadFloat(selfModel, "ambient_comment_comfort"), IdentityPink);
			AddInverseMetricBar(behavior, "Giữ chủ đề", ReadFloat(selfModel, "topic_drift_tendency"), IdentityBlue);
		}
		else
		{
			behavior.AddChild(NewLabel("Chưa có self-model dài hạn.", 10, IdentityMuted, false));
		}
		core.AddChild(behavior);
	}

	private void RenderElegantIdentityTab(JsonElement root)
	{
		if (_depthContentList == null) return;
		JsonElement individual = Obj(root, "individual_runtime");
		JsonElement selfModel = Obj(individual, "operational_self_model");

		// V81: Bản sắc = values + cách vận hành + động cơ nền. Ba khối là đủ.
		var row = new HBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			CustomMinimumSize = new Vector2(0f, 275f),
		};
		row.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(row);

		var valuesCard = NewIdentityCard("Giá trị cốt lõi", IdentityAsset("identity_values_orb.png"), "◇", IdentityPink, 270f);
		valuesCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(valuesCard);
		var valuesBox = (VBoxContainer)valuesCard.GetChild(0);
		if (AddTopScoreBars(valuesBox, Arr(root, "values_top"), 5, IdentityPink) == 0)
			valuesBox.AddChild(NewLabel("Chưa có giá trị đủ nổi bật.", 10, IdentityMuted, false));

		var selfCard = NewIdentityCard("Cách Eris vận hành", IdentityAsset("identity_card_motive.png"), "◎", IdentityBlue, 270f);
		selfCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(selfCard);
		var selfBox = (VBoxContainer)selfCard.GetChild(0);
		if (selfModel.ValueKind == JsonValueKind.Object)
		{
			AddMetricBar(selfBox, "Chịu im lặng", ReadFloat(selfModel, "silence_tolerance"), IdentityGreen);
			AddMetricBar(selfBox, "Presence nhẹ", ReadFloat(selfModel, "ambient_comment_comfort"), IdentityPink);
			AddInverseMetricBar(selfBox, "Hỏi đúng lúc", ReadFloat(selfModel, "over_questioning"), IdentityPurple);
			AddInverseMetricBar(selfBox, "Giữ chủ đề", ReadFloat(selfModel, "topic_drift_tendency"), IdentityBlue);
		}
		else
		{
			selfBox.AddChild(NewLabel("Chưa có self-model dài hạn.", 10, IdentityMuted, false));
		}

		var motivesCard = NewIdentityCard("Động cơ nền", IdentityAsset("identity_motive_orbit.png"), "✦", IdentityPurple, 175f);
		_depthContentList.AddChild(motivesCard);
		var motivesBox = (VBoxContainer)motivesCard.GetChild(0);
		if (AddTopScoreBars(motivesBox, Arr(root, "motives_top"), 4, IdentityPurple) == 0)
			motivesBox.AddChild(NewLabel("Chưa có động cơ nền đủ rõ.", 10, IdentityMuted, false));
	}

	private void RenderElegantDevelopmentTab(JsonElement root)
	{
		if (_depthContentList == null) return;
		JsonElement individual = Obj(root, "individual_runtime");
		JsonElement trajectory = Arr(root, "recent_personality_trajectory");
		JsonElement imprints = Arr(root, "imprints");

		// V81: Development không phải audit log. Một summary + hai vùng là đủ.
		var summary = NewIdentityCard("Nhịp phát triển", IdentityAsset("identity_tab_ledger.png"), "↗", IdentityBlue, 112f);
		_depthContentList.AddChild(summary);
		var summaryBox = (VBoxContainer)summary.GetChild(0);
		int trajectoryCount = trajectory.ValueKind == JsonValueKind.Array ? trajectory.GetArrayLength() : 0;
		int imprintCount = imprints.ValueKind == JsonValueKind.Array ? imprints.GetArrayLength() : 0;
		string summaryText = trajectoryCount == 0 && imprintCount == 0
			? "Đang ổn định · chưa có thay đổi dài hạn đáng kể."
			: $"{trajectoryCount} thay đổi gần đây · {imprintCount} dấu in đang được giữ.";
		var summaryLabel = NewLabel(summaryText, 12, IdentityInk, true);
		summaryLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		summaryBox.AddChild(summaryLabel);
		summaryBox.AddChild(NewLabel("Chỉ những thay đổi persistent mới xuất hiện ở đây.", 10, IdentityMuted, false));

		var row = new HBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			CustomMinimumSize = new Vector2(0f, 300f),
		};
		row.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(row);

		var trendCard = NewIdentityCard("Xu hướng gần đây", IdentityAsset("identity_card_timeline.png"), "⌁", IdentityPink, 295f);
		trendCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(trendCard);
		var trendBox = (VBoxContainer)trendCard.GetChild(0);
		if (AddDevelopmentTrend(trendBox, trajectory, 4) == 0)
		{
			trendBox.AddChild(NewLabel("Chưa có xu hướng đủ rõ.", 11, IdentityMuted, false));
		}

		var imprintCard = NewIdentityCard("Dấu in đã hình thành", IdentityAsset("identity_emergent_value.png"), "◆", IdentityPurple, 295f);
		imprintCard.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(imprintCard);
		var imprintBox = (VBoxContainer)imprintCard.GetChild(0);
		if (AddImprintSummary(imprintBox, imprints, 4) == 0)
		{
			imprintBox.AddChild(NewLabel("Chưa có dấu in đủ mạnh.", 11, IdentityMuted, false));
		}
	}

	private string BuildIdentityHeadline(JsonElement root)
	{
		JsonElement values = Arr(root, "values_top");
		var names = new List<string>();
		if (values.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in values.EnumerateArray())
			{
				string key = PrettyIdentityKey(ReadString(item, "key"));
				if (!string.IsNullOrWhiteSpace(key) && key != "—") names.Add(key);
				if (names.Count >= 3) break;
			}
		}
		return names.Count > 0 ? string.Join(" · ", names) : "Bản sắc đang ổn định";
	}

	private string BuildIdentitySubline(JsonElement root, JsonElement selfModel)
	{
		JsonElement individual = Obj(root, "individual_runtime");
		string schema = ReadString(individual, "schema");
		return !string.IsNullOrWhiteSpace(schema)
			? "Trạng thái bản thân được giữ ổn định qua thời gian."
			: "Compatibility identity snapshot";
	}

	private int AddTopScoreBars(VBoxContainer parent, JsonElement array, int limit, Color accent)
	{
		if (array.ValueKind != JsonValueKind.Array) return 0;
		int count = 0;
		foreach (JsonElement item in array.EnumerateArray())
		{
			string key = ReadString(item, "key");
			float value = ReadFirstFloat(item, "value", "strength");
			if (string.IsNullOrWhiteSpace(key)) continue;
			AddMetricBar(parent, TrimForPanel(PrettyIdentityKey(key), 23), value, count == 0 ? accent : new Color(accent.R, accent.G, accent.B, 0.80f));
			if (++count >= limit) break;
		}
		return count;
	}

	private void AddInverseMetricBar(VBoxContainer parent, string label, float tendency, Color accent)
	{
		// Thanh dài = kiểm soát tốt hơn, giúp nhìn một phát thay vì phải hiểu "score xấu".
		AddMetricBar(parent, label, 1f - Mathf.Clamp(tendency, 0f, 1f), accent);
	}

	private int AddActiveHorizonGoals(VBoxContainer parent, JsonElement horizon, int limit)
	{
		JsonElement goals = Arr(horizon, "goals");
		if (goals.ValueKind != JsonValueKind.Array) return 0;
		var rows = new List<(string level, string title, float strength, float progress)>();
		foreach (JsonElement goal in goals.EnumerateArray())
		{
			string status = ReadString(goal, "status");
			string level = ReadString(goal, "level").ToUpperInvariant();
			if (!string.Equals(status, "active", StringComparison.OrdinalIgnoreCase)) continue;
			if (level != "H3" && level != "H4") continue;
			string title = ReadString(goal, "title");
			if (string.IsNullOrWhiteSpace(title)) continue;
			rows.Add((level, title, ReadFloat(goal, "strength"), ReadFloat(goal, "progress")));
		}
		rows.Sort((a, b) => b.strength.CompareTo(a.strength));
		int count = 0;
		foreach (var row in rows)
		{
			var line = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 32f) };
			line.AddThemeConstantOverride("separation", 8);
			line.AddChild(NewPill(row.level, row.level == "H4" ? IdentityPink : IdentityPurple, false));
			var label = NewLabel(TrimForPanel(row.title, 62), 11, IdentityInk, true);
			label.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
			label.TooltipText = row.title;
			line.AddChild(label);
			line.AddChild(NewLabel(row.progress > 0f ? $"{row.progress:0%}" : "", 10, IdentityMuted, true));
			parent.AddChild(line);
			if (++count >= limit) break;
		}
		return count;
	}

	private void AddGrowthSnapshot(VBoxContainer parent, JsonElement root, JsonElement individual)
	{
		JsonElement selfModel = Obj(individual, "operational_self_model");
		JsonElement flags = Obj(individual, "learning_flags");
		AddMetricBar(parent, "Chịu im lặng", ReadFloat(selfModel, "silence_tolerance"), IdentityGreen);
		AddMetricBar(parent, "Presence nhẹ", ReadFloat(selfModel, "ambient_comment_comfort"), IdentityPink);
		AddInverseMetricBar(parent, "Giữ chủ đề", ReadFloat(selfModel, "topic_drift_tendency"), IdentityBlue);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 7);
		row.AddChild(NewPill(ReadBool(flags, "self_model_learning", false) ? "Self học" : "Self khóa", ReadBool(flags, "self_model_learning", false) ? IdentityGreen : IdentityMuted, false));
		row.AddChild(NewPill(ReadBool(flags, "habit_learning", false) ? "Habit học" : "Habit khóa", ReadBool(flags, "habit_learning", false) ? IdentityGreen : IdentityMuted, false));
		parent.AddChild(row);
		JsonElement trajectory = Arr(root, "recent_personality_trajectory");
		parent.AddChild(NewLabel(trajectory.ValueKind == JsonValueKind.Array && trajectory.GetArrayLength() > 0 ? $"{trajectory.GetArrayLength()} thay đổi gần đây đã được ghi." : "Chưa có thay đổi persistent gần đây.", 10, IdentityMuted, false));
	}

	private int AddDevelopmentTrend(VBoxContainer parent, JsonElement trajectory, int limit)
	{
		if (trajectory.ValueKind != JsonValueKind.Array) return 0;
		var items = new List<JsonElement>();
		foreach (JsonElement item in trajectory.EnumerateArray()) items.Add(item);
		items.Reverse();
		int count = 0;
		foreach (JsonElement item in items)
		{
			string trait = ReadString(item, "trait");
			float delta = ReadFloat(item, "applied_delta");
			if (string.IsNullOrWhiteSpace(trait) || Mathf.Abs(delta) < 0.000001f) continue;
			var line = new VBoxContainer();
			line.AddThemeConstantOverride("separation", 2);
			var title = new HBoxContainer();
			title.AddChild(NewLabel(TrimForPanel(PrettyIdentityKey(trait), 26), 10, IdentityInk, true));
			title.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
			string direction = delta > 0f ? "tăng" : "giảm";
			title.AddChild(NewLabel(direction, 9, delta > 0f ? IdentityGreen : IdentityOrange, true));
			line.AddChild(title);
			var bar = new ProgressBar { MinValue = 0, MaxValue = 1, Value = Mathf.Clamp(Mathf.Abs(delta) * 20f, 0.04f, 1f), ShowPercentage = false, CustomMinimumSize = new Vector2(0f, 7f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
			bar.AddThemeStyleboxOverride("background", RoundedBarStyle(new Color(0.96f, 0.92f, 0.94f), 5));
			bar.AddThemeStyleboxOverride("fill", RoundedBarStyle(delta > 0f ? IdentityGreen : IdentityOrange, 5));
			bar.TooltipText = $"Delta thực: {delta:+0.0000;-0.0000;0.0000}";
			line.AddChild(bar);
			parent.AddChild(line);
			if (++count >= limit) break;
		}
		return count;
	}

	private int AddImprintSummary(VBoxContainer parent, JsonElement imprints, int limit)
	{
		if (imprints.ValueKind != JsonValueKind.Array) return 0;
		int count = 0;
		foreach (JsonElement item in imprints.EnumerateArray())
		{
			string theme = ReadString(item, "theme");
			if (string.IsNullOrWhiteSpace(theme)) continue;
			float strength = ReadFloat(item, "strength");
			if (strength < 0.18f) continue;
			AddMetricBar(parent, TrimForPanel(PrettyIdentityKey(theme), 24), strength, strength >= 0.62f ? IdentityPurple : IdentityBlue);
			if (++count >= limit) break;
		}
		return count;
	}

	private void RenderOverviewTab(JsonElement root)
	{
		if (_depthContentList == null)
			return;

		var body = new HBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			ClipContents = true,
			CustomMinimumSize = new Vector2(0f, 742f),
		};
		body.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(body);

		var left = new VBoxContainer { CustomMinimumSize = new Vector2(330f, 0f), SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin };
		left.AddThemeConstantOverride("separation", 10);
		body.AddChild(left);

		var right = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		right.AddThemeConstantOverride("separation", 12);
		body.AddChild(right);

		RenderOverviewMood(left, root);
		RenderOverviewInitiative(left, root);
		RenderTopMotives(left, root);
		RenderDefenseAndConflict(left, root);

		RenderLiveTimeline(right, root);
		var bottom = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 258f) };
		bottom.AddThemeConstantOverride("separation", 12);
		right.AddChild(bottom);
		RenderStageExpression(bottom, root);
		RenderReadyDiscovery(bottom, root);
		RenderRelationshipPreview(bottom, root);
	}


	private void RenderOverviewMood(VBoxContainer parent, JsonElement root)
	{
		JsonElement summary = Obj(root, "summary");
		var card = NewIdentityCard("Tâm thế hiện tại", "", "✧", IdentityPink, 160f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 12);
		box.AddChild(row);
		row.AddChild(NewMoodOrb(IdentityAsset("identity_mood_neutral.png"), BuildMoodSubtitle(summary, root)));
		var textBox = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		textBox.AddThemeConstantOverride("separation", 2);
		string mood = PrettyIdentityKey(ReadString(summary, "mood"));
		textBox.AddChild(NewLabel(string.IsNullOrWhiteSpace(mood) ? "Trung tính" : mood, 20, IdentityInk, true));
		textBox.AddChild(NewLabel(BuildMoodSubtitle(summary, root), 14, new Color(0.39f, 0.23f, 0.47f), false));
		textBox.AddChild(NewLabel(BuildMoodAuthorityLine(root), 11, IdentityMuted, false));
		row.AddChild(textBox);
	}


	private static string BuildMoodAuthorityLine(JsonElement root)
	{
		JsonElement appraisal = Obj(root, "appraisal");
		float pressure = ReadFirstFloat(appraisal, "pressure", "irritation", "novelty");
		JsonElement learning = Obj(root, "learning");
		string learningMode = ReadBool(learning, "enabled", true) ? "học bình thường" : "chỉ quan sát";
		return pressure >= 0.60f ? $"áp lực cao · {learningMode}" : pressure >= 0.30f ? $"đang cân nhắc · {learningMode}" : $"nhịp thấp · {learningMode}";
	}


	private void RenderOverviewInitiative(VBoxContainer parent, JsonElement root)
	{
		JsonElement discoveryRuntime = Obj(root, "discovery_runtime");
		if (discoveryRuntime.ValueKind != JsonValueKind.Object)
		{
			var card = NewIdentityCard("Nhịp chủ động", IdentityAsset("identity_card_speech_drive.png"), "∿", IdentityMuted, 142f);
			parent.AddChild(card);
			var box = (VBoxContainer)card.GetChild(0);
			box.AddChild(NewLabel("Backend chưa gửi discovery_runtime.initiative.", 11, IdentityMuted, false));
			box.AddChild(NewLabel("Panel không tự suy ra xung lực nói từ mood hay defense.", 10, IdentityMuted, false));
			return;
		}
		RenderInitiativeRhythm(parent, discoveryRuntime);
	}


	private void RenderTopMotives(VBoxContainer parent, JsonElement root)
	{
		var card = NewIdentityCard("Motive chính", "", "✦", IdentityAccent, 112f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		JsonElement motiveArray = Arr(root, "active_motives");
		if (motiveArray.ValueKind != JsonValueKind.Array || motiveArray.GetArrayLength() == 0)
			motiveArray = Arr(root, "motives_top");
		int rank = 1;
		if (motiveArray.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in motiveArray.EnumerateArray())
			{
				string key = ReadString(item, "key");
				float value = ReadFirstFloat(item, "strength", "value");
				AddRankRow(box, rank, PrettyIdentityKey(key), value, rank == 1 ? IdentityPink : IdentityPurple);
				if (++rank > 2) break;
			}
		}
		if (rank == 1) box.AddChild(NewLabel("Chưa có motive đủ rõ.", 11, IdentityMuted, false));
	}


	private void RenderDefenseAndConflict(VBoxContainer parent, JsonElement root)
	{
		JsonElement defenses = Arr(root, "active_defenses");
		string defenseKey = "";
		float defenseValue = 0f;
		if (defenses.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in defenses.EnumerateArray())
			{
				defenseKey = ReadString(item, "key");
				defenseValue = ReadFirstFloat(item, "strength", "value");
				break;
			}
		}
		var defense = NewIdentityCard("Defense", "", "♢", IdentityPurple, 82f);
		parent.AddChild(defense);
		var defenseBox = (VBoxContainer)defense.GetChild(0);
		AddCompactValue(defenseBox, PrettyIdentityKey(defenseKey), defenseValue, IdentityPurple);

		JsonElement conflict = Obj(root, "active_conflict");
		var conflictCard = NewIdentityCard("Mâu thuẫn nội tại", "", "⚖", IdentityPink, 82f);
		parent.AddChild(conflictCard);
		var conflictBox = (VBoxContainer)conflictCard.GetChild(0);
		AddCompactValue(conflictBox, PrettyIdentityKey(ReadString(conflict, "conflict_id")), ReadFloat(conflict, "activation"), IdentityPink);
	}


	private void RenderLiveTimeline(VBoxContainer parent, JsonElement root)
	{
		var card = NewIdentityCard("Dòng trạng thái sống", "", "⌁", IdentityPink, 355f);
		card.SizeFlagsVertical = Control.SizeFlags.ExpandFill;
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var meta = new HBoxContainer();
		meta.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		meta.AddChild(NewLabel("◷  5 sự kiện gần nhất", 10, IdentityMuted, false));
		box.AddChild(meta);
		var events = BuildLiveEvents(root);
		int index = 0;
		foreach (IdentityLiveEvent item in events)
		{
			AddTimelineRow(box, item, index++);
			if (index >= 5) break;
		}
		while (index < 5)
		{
			AddTimelineRow(box, new IdentityLiveEvent
			{
				Title = "Đang chờ thêm một tín hiệu đáng kể",
				Detail = "Observer chưa ghi nhận sự kiện mới.",
				Tag = "quiet",
				IconPath = IdentityAsset("identity_event_quiet.png"),
				FallbackGlyph = "·",
				Accent = new Color(0.72f, 0.60f, 0.70f),
			}, index++);
		}
	}


	private void RenderStageExpression(HBoxContainer parent, JsonElement root)
	{
		JsonElement expression = Obj(root, "expression");
		var card = NewIdentityCard("Identity → Stage Expression", IdentityAsset("identity_card_stage.png"), "✧", IdentityPink, 245f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 1.04f;
		card.CustomMinimumSize = new Vector2(248f, 245f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var content = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		content.AddThemeConstantOverride("separation", 10);
		box.AddChild(content);
		var metrics = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		metrics.AddThemeConstantOverride("separation", 7);
		content.AddChild(metrics);
		AddKeyValueRow(metrics, "Preset", PrettyStageText(ReadString(expression, "preset")), IdentityPink);
		AddHeartConfidence(metrics, ReadFloat(expression, "confidence"));
		AddMetricBar(metrics, "Intensity", ReadFloat(expression, "intensity"), IdentityPurple);
		AddKeyValueRow(metrics, "Sentence bias", PrettyStageText(ReadString(expression, "sentence_bias")), IdentityPurple);
		AddKeyValueRow(metrics, "Stage", PrettyStageText(ReadString(expression, "preset")), IdentityPink);
		content.AddChild(NewIconBadge(IdentityAsset("identity_stage_silhouette.png"), "♙", new Vector2(88f, 136f), IdentityPink, false));
	}


	private void RenderReadyDiscovery(HBoxContainer parent, JsonElement root)
	{
		// Preview Overview chỉ hiển thị dữ liệu. Không mượn mascot hay ảnh hero của tab
		// Discovery, vì một asset xuất hiện ở nhiều vai trò sẽ làm toàn panel giống bộ
		// template ghép vội.
		JsonElement discovery = Obj(root, "discovery");
		JsonElement readyArray = Arr(discovery, "ready_to_share");
		JsonElement first = default;
		if (readyArray.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in readyArray.EnumerateArray()) { first = item; break; }
		}

		var card = NewIdentityCard("Discovery sẵn sàng", "", "✧", IdentityOrange, 245f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 0.96f;
		card.CustomMinimumSize = new Vector2(248f, 245f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 9);

		var statusRow = new HBoxContainer();
		statusRow.AddChild(NewLabel("Hàng chờ kể", 10, IdentityMuted, false));
		statusRow.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		statusRow.AddChild(NewPill(first.ValueKind == JsonValueKind.Object ? "1 sẵn sàng" : "đang trống", IdentityGreen, false));
		box.AddChild(statusRow);
		box.AddChild(NewSectionDivider());

		if (first.ValueKind != JsonValueKind.Object)
		{
			var empty = new CenterContainer
			{
				CustomMinimumSize = new Vector2(0f, 112f),
				SizeFlagsVertical = Control.SizeFlags.ExpandFill,
			};
			box.AddChild(empty);
			var emptyCopy = new VBoxContainer { CustomMinimumSize = new Vector2(190f, 0f) };
			emptyCopy.AddThemeConstantOverride("separation", 5);
			empty.AddChild(emptyCopy);
			var mark = NewLabel("＋", 24, new Color(IdentityOrange.R, IdentityOrange.G, IdentityOrange.B, 0.58f), false);
			mark.HorizontalAlignment = HorizontalAlignment.Center;
			emptyCopy.AddChild(mark);
			var note = NewLabel("Chưa có artifact đủ nguồn để kể.", 11, IdentityMuted, false);
			note.HorizontalAlignment = HorizontalAlignment.Center;
			note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			emptyCopy.AddChild(note);
		}
		else
		{
			var topicLabel = NewLabel(TrimForPanel(ReadString(first, "topic"), 72), 14, IdentityInk, true);
			topicLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(topicLabel);
			string take = ReadString(first, "eris_take");
			if (string.IsNullOrWhiteSpace(take)) take = ReadString(first, "summary");
			var teaser = NewLabel(TrimForPanel(take, 125), 10, IdentityMuted, false);
			teaser.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(teaser);
		}

		var open = NewFooterButton("Mở discovery", IdentityAsset("identity_action_open_discovery.png"), "➤", () =>
		{
			_depthActiveTab = "discovery";
			RefreshDepthTabStyle();
			RefreshDepthPanel();
		}, IdentityPink);
		open.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		box.AddChild(open);
	}


	private void RenderRelationshipPreview(HBoxContainer parent, JsonElement root)
	{
		JsonElement rel = Obj(root, "relationship");
		var card = NewIdentityCard("Quan hệ & belief", "", "♧", IdentityAccent, 245f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 1.00f;
		card.CustomMinimumSize = new Vector2(248f, 245f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		AddRelationshipBar(box, "Tin tưởng", ReadFirstFloat(rel, "relational_trust", "trust"), IdentityPink, "♥");
		AddRelationshipBar(box, "Thân quen", ReadFloat(rel, "familiarity"), IdentityPurple, "♥");
		AddRelationshipBar(box, "Tôn trọng", ReadFloat(rel, "respect"), IdentityBlue, "♥");
		AddRelationshipBar(box, "Gắn kết", ReadFirstFloat(rel, "attachment_intensity", "closeness"), IdentityOrange, "♥");
		string belief = FindTopUserBelief(Obj(root, "beliefs"));
		box.AddChild(NewBeliefBox(string.IsNullOrWhiteSpace(belief) ? "Belief về bạn chưa đủ chắc để hiển thị." : belief));
	}


	private PanelContainer NewIdentityCard(string title, string iconPath, string fallbackGlyph, Color accent, float minHeight)
	{
		// Card cấp section chỉ dùng một đường nhấn bên trái. Bỏ khung màu bốn cạnh
		// và glow lặp lại, vì đó chính là thứ khiến panel giống template pastel rẻ tiền.
		var card = new PanelContainer
		{
			CustomMinimumSize = new Vector2(0f, minHeight),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		card.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = IdentityCard,
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.34f),
			BorderWidthLeft = 2,
			BorderWidthTop = 0,
			BorderWidthRight = 0,
			BorderWidthBottom = 0,
			CornerRadiusTopLeft = 15,
			CornerRadiusTopRight = 15,
			CornerRadiusBottomLeft = 15,
			CornerRadiusBottomRight = 15,
			ContentMarginLeft = 16f,
			ContentMarginTop = 13f,
			ContentMarginRight = 16f,
			ContentMarginBottom = 13f,
			ShadowColor = new Color(0.22f, 0.12f, 0.19f, 0.065f),
			ShadowSize = 6,
			ShadowOffset = new Vector2(0f, 2f),
		});

		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 7);
		card.AddChild(box);

		var header = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 26f) };
		header.AddThemeConstantOverride("separation", 8);
		header.AddChild(NewIconBadge(iconPath, fallbackGlyph, new Vector2(28f, 28f), accent, true));
		header.AddChild(NewLabel(title, 15, IdentityInk, true));
		box.AddChild(header);
		return card;
	}


	private static string ResolveIdentityAssetPath(string path)
	{
		if (string.IsNullOrWhiteSpace(path))
			return path ?? "";

		foreach (string candidate in EnumerateIdentityAssetCandidates(path))
		{
			if (ResourceLoader.Exists(candidate))
				return candidate;
		}
		return path;
	}

	private static IEnumerable<string> EnumerateIdentityAssetCandidates(string path)
	{
		yield return path;

		if (path.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
			yield return path + ".png";

		int slash = path.LastIndexOf('/');
		string fileName = slash >= 0 ? path[(slash + 1)..] : path;

		// V83: bộ Identity mới nằm trong res://asset/identy/. Một số icon hệ thống
		// cũ (brand, freeze, export) vẫn nằm ở res://asset/, nên resolver thử cả hai.
		yield return IdentityAssetRoot + fileName;
		yield return LegacyIdentityAssetRoot + fileName;

		foreach (string alias in IdentityAssetAliases(fileName))
		{
			yield return IdentityAssetRoot + alias;
			yield return LegacyIdentityAssetRoot + alias;
		}
	}

	private static IEnumerable<string> IdentityAssetAliases(string fileName)
	{
		string key = (fileName ?? "").ToLowerInvariant();
		switch (key)
		{
			case "identity_portrait.png":
			case "identity_portrait_eris.png":
				yield return "identity_portrait_eris.png.png";
				break;
			case "identity_observer_crystal.png":
			case "identity_gemma_emblem.png":
				yield return "identity_core_crystal.png";
				break;
		}
	}


	private TextureRect NewIdentityTexture(string path, Vector2 size)
	{
		string resolvedPath = ResolveIdentityAssetPath(path);
		var rect = new TextureRect
		{
			CustomMinimumSize = size,
			Size = size,
			ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
			StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered,
			MouseFilter = Control.MouseFilterEnum.Ignore,
			Modulate = Colors.White,
			SelfModulate = Colors.White,
			TextureFilter = CanvasItem.TextureFilterEnum.Linear,
		};
		if (ResourceLoader.Exists(resolvedPath))
			rect.Texture = GD.Load<Texture2D>(resolvedPath);
		return rect;
	}

	private Control NewIconBadge(string path, string fallbackGlyph, Vector2 size, Color accent, bool circular)
	{
		string resolvedPath = ResolveIdentityAssetPath(path);
		bool hasTexture = ResourceLoader.Exists(resolvedPath);
		var badge = new PanelContainer
		{
			CustomMinimumSize = size,
			Size = size,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		int radius = circular ? (int)Mathf.Round(Mathf.Min(size.X, size.Y) * 0.5f) : 14;

		// Ảnh màu hoàn chỉnh không cần thêm lớp nền hồng/border phủ phía sau. Khung
		// pastel chỉ dành cho glyph fallback khi asset còn thiếu.
		badge.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = hasTexture ? new Color(1f, 1f, 1f, 0f) : new Color(accent.R, accent.G, accent.B, 0.075f),
			BorderColor = hasTexture ? new Color(1f, 1f, 1f, 0f) : new Color(accent.R, accent.G, accent.B, 0.20f),
			BorderWidthLeft = hasTexture ? 0 : 1,
			BorderWidthTop = hasTexture ? 0 : 1,
			BorderWidthRight = hasTexture ? 0 : 1,
			BorderWidthBottom = hasTexture ? 0 : 1,
			CornerRadiusTopLeft = radius,
			CornerRadiusTopRight = radius,
			CornerRadiusBottomLeft = radius,
			CornerRadiusBottomRight = radius,
			ContentMarginLeft = hasTexture ? 0f : 3f,
			ContentMarginTop = hasTexture ? 0f : 3f,
			ContentMarginRight = hasTexture ? 0f : 3f,
			ContentMarginBottom = hasTexture ? 0f : 3f,
		});
		if (hasTexture)
		{
			badge.AddChild(NewIdentityTexture(resolvedPath, size));
		}
		else
		{
			var glyph = NewLabel(fallbackGlyph, Math.Max(12, (int)(Mathf.Min(size.X, size.Y) * 0.45f)), accent, true);
			glyph.HorizontalAlignment = HorizontalAlignment.Center;
			glyph.VerticalAlignment = VerticalAlignment.Center;
			badge.AddChild(glyph);
		}
		return badge;
	}

	private Control NewMoodOrb(string path, string subtitle)
	{
		string resolvedPath = ResolveIdentityAssetPath(path);
		if (ResourceLoader.Exists(resolvedPath))
			return NewIdentityTexture(resolvedPath, new Vector2(112f, 112f));
		var orb = new PanelContainer { CustomMinimumSize = new Vector2(112f, 112f) };
		orb.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(0.98f, 0.68f, 0.84f, 0.82f),
			BorderColor = new Color(0.74f, 0.45f, 0.90f, 0.55f),
			BorderWidthLeft = 3,
			BorderWidthTop = 3,
			BorderWidthRight = 3,
			BorderWidthBottom = 3,
			CornerRadiusTopLeft = 56,
			CornerRadiusTopRight = 56,
			CornerRadiusBottomLeft = 56,
			CornerRadiusBottomRight = 56,
		});
		var face = NewLabel("• ᴗ •", 21, new Color(0.33f, 0.21f, 0.38f), true);
		face.HorizontalAlignment = HorizontalAlignment.Center;
		face.VerticalAlignment = VerticalAlignment.Center;
		face.TooltipText = subtitle;
		orb.AddChild(face);
		return orb;
	}


	private void AddMetricBar(VBoxContainer parent, string label, float value, Color fill)
	{
		value = Mathf.Clamp(value, 0f, 1f);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 22f) };
		row.AddThemeConstantOverride("separation", 8);
		var name = NewLabel(label, 11, IdentityInk, false);
		name.CustomMinimumSize = new Vector2(80f, 0f);
		name.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		row.AddChild(name);
		var bar = new ProgressBar
		{
			MinValue = 0,
			MaxValue = 1,
			Value = value,
			ShowPercentage = false,
			CustomMinimumSize = new Vector2(90f, 8f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		bar.AddThemeStyleboxOverride("background", RoundedBarStyle(new Color(0.97f, 0.90f, 0.94f), 6));
		bar.AddThemeStyleboxOverride("fill", RoundedBarStyle(fill, 6));
		row.AddChild(bar);
		var percent = NewLabel($"{value:0%}", 10, new Color(0.48f, 0.20f, 0.38f), true);
		percent.CustomMinimumSize = new Vector2(34f, 0f);
		percent.HorizontalAlignment = HorizontalAlignment.Right;
		row.AddChild(percent);
		parent.AddChild(row);
	}


	private static StyleBoxFlat RoundedBarStyle(Color color, int radius) => new()
	{
		BgColor = color,
		CornerRadiusTopLeft = radius,
		CornerRadiusTopRight = radius,
		CornerRadiusBottomLeft = radius,
		CornerRadiusBottomRight = radius,
	};

	private Label NewPill(string text, Color accent, bool alignRight)
	{
		// Pill chỉ còn là nhãn trạng thái nhỏ. Không dùng nó như container mặc định
		// cho mọi đoạn chữ nữa.
		var label = NewLabel(text, 9, accent, true);
		label.ClipText = true;
		label.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		label.TooltipText = text;
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.CustomMinimumSize = new Vector2(72f, 22f);
		label.SizeFlagsHorizontal = alignRight ? Control.SizeFlags.ShrinkEnd : Control.SizeFlags.ShrinkBegin;
		label.AddThemeStyleboxOverride("normal", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.070f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.12f),
			BorderWidthLeft = 1,
			BorderWidthTop = 0,
			BorderWidthRight = 0,
			BorderWidthBottom = 0,
			CornerRadiusTopLeft = 7,
			CornerRadiusTopRight = 7,
			CornerRadiusBottomLeft = 7,
			CornerRadiusBottomRight = 7,
			ContentMarginLeft = 8f,
			ContentMarginRight = 8f,
		});
		return label;
	}


	private Control NewStatusLine(string text, Color accent)
	{
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 22f) };
		row.AddThemeConstantOverride("separation", 8);
		var dot = new PanelContainer
		{
			CustomMinimumSize = new Vector2(8f, 8f),
			SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
		};
		dot.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = accent,
			CornerRadiusTopLeft = 4,
			CornerRadiusTopRight = 4,
			CornerRadiusBottomLeft = 4,
			CornerRadiusBottomRight = 4,
		});
		row.AddChild(dot);
		var label = NewLabel(text, 10, IdentityMuted, false);
		label.ClipText = true;
		label.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		label.TooltipText = text;
		row.AddChild(label);
		return row;
	}

	private static Control NewActionDivider()
	{
		return new ColorRect
		{
			Color = new Color(0.52f, 0.40f, 0.47f, 0.20f),
			CustomMinimumSize = new Vector2(1f, 24f),
			SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
		};
	}


	private Button NewIdentityHeaderActionButton(string tooltip, string iconPath, string fallbackGlyph, Action action)
	{
		string resolvedIconPath = ResolveIdentityAssetPath(iconPath);
		bool hasIcon = ResourceLoader.Exists(resolvedIconPath);
		var button = NewButton(hasIcon ? "" : fallbackGlyph, action, false);
		button.TooltipText = tooltip;
		button.CustomMinimumSize = new Vector2(30f, 26f);
		button.SizeFlagsHorizontal = Control.SizeFlags.ShrinkEnd;
		button.FocusMode = Control.FocusModeEnum.None;
		button.AddThemeFontSizeOverride("font_size", 9);
		button.AddThemeColorOverride("font_color", IdentityBlue);
		button.AddThemeColorOverride("font_hover_color", IdentityPink);
		button.AddThemeStyleboxOverride("normal", IdentityActionButtonStyle(IdentityBlue, false, false));
		button.AddThemeStyleboxOverride("hover", IdentityActionButtonStyle(IdentityPink, true, false));
		button.AddThemeStyleboxOverride("pressed", IdentityActionButtonStyle(IdentityPink, true, true));
		button.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());
		if (hasIcon)
		{
			button.Icon = GD.Load<Texture2D>(resolvedIconPath);
			button.ExpandIcon = true;
			button.AddThemeConstantOverride("icon_max_width", 15);
			button.AddThemeColorOverride("icon_normal_color", Colors.White);
			button.AddThemeColorOverride("icon_hover_color", Colors.White);
			button.AddThemeColorOverride("icon_pressed_color", Colors.White);
		}
		return button;
	}

	private Button NewFooterButton(string text, string iconPath, string fallbackGlyph, Action action, Color accent)
	{
		string resolvedIconPath = ResolveIdentityAssetPath(iconPath);
		bool hasIcon = ResourceLoader.Exists(resolvedIconPath);
		var button = NewButton(hasIcon ? text : $"{fallbackGlyph}  {text}", action, false);
		button.CustomMinimumSize = new Vector2(142f, 42f);
		button.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		button.FocusMode = Control.FocusModeEnum.None;
		button.AddThemeFontSizeOverride("font_size", 11);
		button.AddThemeConstantOverride("h_separation", 7);
		button.AddThemeColorOverride("font_color", new Color(0.27f, 0.20f, 0.28f));
		button.AddThemeColorOverride("font_hover_color", IdentityInk);
		button.AddThemeColorOverride("font_pressed_color", IdentityInk);
		button.AddThemeStyleboxOverride("normal", IdentityActionButtonStyle(accent, false, false));
		button.AddThemeStyleboxOverride("hover", IdentityActionButtonStyle(accent, true, false));
		button.AddThemeStyleboxOverride("pressed", IdentityActionButtonStyle(accent, true, true));
		button.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());
		if (hasIcon)
		{
			button.Icon = GD.Load<Texture2D>(resolvedIconPath);
			button.ExpandIcon = true;
			button.AddThemeConstantOverride("icon_max_width", 20);
			button.AddThemeColorOverride("icon_normal_color", Colors.White);
			button.AddThemeColorOverride("icon_hover_color", Colors.White);
			button.AddThemeColorOverride("icon_pressed_color", Colors.White);
			button.AddThemeColorOverride("icon_focus_color", Colors.White);
		}
		return button;
	}


	// Chỉ reset khi mở panel hoặc đổi tab; refresh telemetry thường vẫn giữ vị trí người đang xem.
	private void ResetDepthScrollIfNeeded()
	{
		if (!_depthResetScrollOnRefresh || _depthScrollContainer == null)
			return;
		_depthResetScrollOnRefresh = false;
		Callable.From(() =>
		{
			if (_depthScrollContainer == null)
				return;
			_depthScrollContainer.ScrollVertical = 0;
			UpdateDepthScrollDecor();
		}).CallDeferred();
	}

	private static StyleBoxFlat IdentityDossierChipStyle(Color accent) => new()
	{
		BgColor = new Color(accent.R, accent.G, accent.B, 0.045f),
		BorderColor = new Color(accent.R, accent.G, accent.B, 0.32f),
		BorderWidthLeft = 1,
		BorderWidthTop = 1,
		BorderWidthRight = 1,
		BorderWidthBottom = 1,
		CornerRadiusTopLeft = 14,
		CornerRadiusTopRight = 14,
		CornerRadiusBottomLeft = 14,
		CornerRadiusBottomRight = 14,
		ContentMarginLeft = 10f,
		ContentMarginRight = 10f,
	};

	private static StyleBoxFlat IdentityActionDockStyle() => new()
	{
		BgColor = new Color(1f, 1f, 1f, 0.50f),
		BorderColor = new Color(0.78f, 0.67f, 0.73f, 0.34f),
		BorderWidthLeft = 0,
		BorderWidthTop = 1,
		BorderWidthRight = 0,
		BorderWidthBottom = 0,
		CornerRadiusTopLeft = 0,
		CornerRadiusTopRight = 0,
		CornerRadiusBottomLeft = 14,
		CornerRadiusBottomRight = 14,
	};

	private static StyleBoxFlat IdentityActionButtonStyle(Color accent, bool hover, bool pressed)
	{
		float alpha = pressed ? 0.14f : hover ? 0.085f : 0.035f;
		float borderAlpha = pressed ? 0.42f : hover ? 0.28f : 0.14f;
		return new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, alpha),
			BorderColor = new Color(accent.R, accent.G, accent.B, borderAlpha),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 9,
			CornerRadiusTopRight = 9,
			CornerRadiusBottomLeft = 9,
			CornerRadiusBottomRight = 9,
			ContentMarginLeft = 12f,
			ContentMarginTop = 6f,
			ContentMarginRight = 12f,
			ContentMarginBottom = 6f,
		};
	}

	private static StyleBoxFlat IdentityTabStyle(bool active, bool hover)
	{
		Color bg = active
			? new Color(0.94f, 0.18f, 0.46f, hover ? 0.085f : 0.060f)
			: new Color(1f, 1f, 1f, hover ? 0.42f : 0.06f);
		return new StyleBoxFlat
		{
			BgColor = bg,
			BorderColor = active
				? new Color(0.91f, 0.18f, 0.46f, 0.92f)
				: new Color(0.70f, 0.60f, 0.66f, hover ? 0.18f : 0f),
			BorderWidthLeft = 0,
			BorderWidthTop = 0,
			BorderWidthRight = 0,
			BorderWidthBottom = active ? 2 : 1,
			CornerRadiusTopLeft = 8,
			CornerRadiusTopRight = 8,
			CornerRadiusBottomLeft = 4,
			CornerRadiusBottomRight = 4,
			ContentMarginLeft = 12f,
			ContentMarginRight = 12f,
		};
	}

	private static StyleBoxFlat IdentityTabPressedStyle() => new()
	{
		BgColor = new Color(0.91f, 0.18f, 0.46f, 0.10f),
		BorderColor = new Color(0.91f, 0.18f, 0.46f, 1f),
		BorderWidthLeft = 0,
		BorderWidthTop = 0,
		BorderWidthRight = 0,
		BorderWidthBottom = 2,
		CornerRadiusTopLeft = 8,
		CornerRadiusTopRight = 8,
		CornerRadiusBottomLeft = 4,
		CornerRadiusBottomRight = 4,
		ContentMarginLeft = 12f,
		ContentMarginRight = 12f,
	};


	private void AddRankRow(VBoxContainer parent, int rank, string label, float value, Color accent)
	{
		var row = new PanelContainer { CustomMinimumSize = new Vector2(0f, 32f) };
		row.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.035f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.12f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 11,
			CornerRadiusTopRight = 11,
			CornerRadiusBottomLeft = 11,
			CornerRadiusBottomRight = 11,
			ContentMarginLeft = 5f,
			ContentMarginRight = 7f,
		});
		parent.AddChild(row);
		var h = new HBoxContainer();
		h.AddThemeConstantOverride("separation", 8);
		row.AddChild(h);
		var badge = NewPill(rank.ToString(CultureInfo.InvariantCulture), accent, false);
		badge.CustomMinimumSize = new Vector2(30f, 24f);
		h.AddChild(badge);
		h.AddChild(NewLabel(string.IsNullOrWhiteSpace(label) ? "Chưa rõ" : label, 12, IdentityInk, false));
		h.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		h.AddChild(NewLabel($"{value:0.00}", 11, IdentityMuted, true));
	}


	private void AddCompactValue(VBoxContainer parent, string label, float value, Color accent)
	{
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 26f) };
		row.AddChild(NewLabel(string.IsNullOrWhiteSpace(label) || label == "—" ? "Chưa có tín hiệu rõ" : label, 11, IdentityMuted, false));
		row.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		row.AddChild(NewPill($"INT {Mathf.Clamp(value, 0f, 1f):0.00}", accent, false));
		parent.AddChild(row);
	}


	private void AddKeyValueRow(VBoxContainer parent, string key, string value, Color accent)
	{
		string fullValue = value ?? "";
		value = TrimForPanel(PrettyStageText(fullValue), 24);
		var row = new HBoxContainer();
		var name = NewLabel(key, 11, IdentityInk, true);
		name.CustomMinimumSize = new Vector2(80f, 0f);
		row.AddChild(name);
		var pill = NewPill(string.IsNullOrWhiteSpace(value) ? "—" : value, accent, false);
		pill.TooltipText = fullValue;
		pill.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(pill);
		parent.AddChild(row);
	}

	private void AddTimelineRow(VBoxContainer parent, IdentityLiveEvent item, int index)
	{
		var row = new PanelContainer { CustomMinimumSize = new Vector2(0f, 60f) };
		row.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = index % 2 == 0 ? new Color(1f, 0.988f, 0.995f, 0.96f) : new Color(1f, 1f, 1f, 0.88f),
			BorderColor = new Color(item.Accent.R, item.Accent.G, item.Accent.B, 0.17f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 14,
			CornerRadiusTopRight = 14,
			CornerRadiusBottomLeft = 14,
			CornerRadiusBottomRight = 14,
			ContentMarginLeft = 10f,
			ContentMarginTop = 4f,
			ContentMarginRight = 10f,
			ContentMarginBottom = 4f,
		});
		parent.AddChild(row);
		var h = new HBoxContainer();
		h.AddThemeConstantOverride("separation", 10);
		row.AddChild(h);
		var sequence = NewLabel($"{index + 1:00}", 10, IdentityMuted, true);
		sequence.CustomMinimumSize = new Vector2(30f, 0f);
		sequence.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		sequence.HorizontalAlignment = HorizontalAlignment.Center;
		h.AddChild(sequence);
		h.AddChild(NewIconBadge(item.IconPath, item.FallbackGlyph, new Vector2(48f, 48f), item.Accent, true));
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", -1);
		var title = NewLabel(TrimForPanel(item.Title, 52), 11, IdentityInk, true);
		title.ClipText = true;
		copy.AddChild(title);
		var detail = NewLabel(TrimForPanel(item.Detail, 76), 9, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(detail);
		h.AddChild(copy);
		h.AddChild(NewPill(item.Tag, item.Accent, false));
	}


	private List<IdentityLiveEvent> BuildLiveEvents(JsonElement root)
	{
		var events = new List<IdentityLiveEvent>();
		JsonElement discovery = Obj(root, "discovery");
		JsonElement activity = Obj(discovery, "current_activity");
		string activityTopic = ReadString(activity, "topic");
		if (!string.IsNullOrWhiteSpace(activityTopic))
		{
			events.Add(new IdentityLiveEvent
			{
				Title = "Nhận ra một chủ đề đáng tìm hiểu",
				Detail = activityTopic,
				Tag = "curious",
				IconPath = IdentityAsset("identity_event_curiosity.png"),
				FallbackGlyph = "♡",
				Accent = IdentityPink,
			});
			events.Add(new IdentityLiveEvent
			{
				Title = "Tạo discovery để chuẩn bị chia sẻ",
				Detail = activityTopic,
				Tag = "discovering",
				IconPath = IdentityAsset("identity_event_discovery.png"),
				FallbackGlyph = "▣",
				Accent = IdentityPurple,
			});
		}

		JsonElement ready = Arr(discovery, "ready_to_share");
		if (ready.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in ready.EnumerateArray())
			{
				events.Add(new IdentityLiveEvent
				{
					Title = "Muốn khoe nhưng đang giữ lại",
					Detail = ReadString(item, "topic"),
					Tag = "withholding",
					IconPath = IdentityAsset("identity_event_withholding.png"),
					FallbackGlyph = "▢",
					Accent = IdentityOrange,
				});
				break;
			}
		}

		JsonElement expression = Obj(root, "expression");
		string preset = ReadString(expression, "preset");
		if (!string.IsNullOrWhiteSpace(preset))
		{
			events.Add(new IdentityLiveEvent
			{
				Title = "Biểu hiện sân khấu: " + PrettyStageText(preset),
				Detail = "Cường độ " + ReadFloat(expression, "intensity").ToString("0.00", CultureInfo.InvariantCulture),
				Tag = "expressing",
				IconPath = IdentityAsset("identity_event_expression.png"),
				FallbackGlyph = "♙",
				Accent = IdentityPink,
			});
		}

		JsonElement observation = Obj(root, "latest_observation");
		string meaning = ReadString(observation, "relationship_meaning");
		if (!string.IsNullOrWhiteSpace(meaning))
		{
			events.Add(new IdentityLiveEvent
			{
				Title = "Tiếp tục quan sát phản ứng của bạn",
				Detail = PrettyIdentityKey(meaning),
				Tag = "observing",
				IconPath = IdentityAsset("identity_event_observing.png"),
				FallbackGlyph = "…",
				Accent = IdentityBlue,
			});
		}

		JsonElement discoveryRuntime = Obj(root, "discovery_runtime");
		JsonElement initiative = Obj(discoveryRuntime, "initiative");
		JsonElement initiativeDecision = Obj(initiative, "last_decision");
		if (initiativeDecision.ValueKind == JsonValueKind.Object && events.Count < 5)
		{
			bool allowed = ReadBool(initiativeDecision, "allowed", false);
			string reason = ReadString(initiativeDecision, "reason");
			events.Add(new IdentityLiveEvent
			{
				Title = allowed ? "Initiative gate đã cho ý định đi tiếp" : "Initiative gate đã giữ một ý định",
				Detail = PrettyInitiativeReason(reason),
				Tag = allowed ? "ready" : "held",
				IconPath = allowed ? IdentityAsset("identity_event_ready.png") : IdentityAsset("identity_event_withholding.png"),
				FallbackGlyph = allowed ? "➤" : "▢",
				Accent = allowed ? IdentityGreen : IdentityOrange,
			});
		}
		return events;
	}


	private void AddHeartConfidence(VBoxContainer parent, float confidence)
	{
		confidence = Mathf.Clamp(confidence, 0f, 1f);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 22f) };
		var label = NewLabel("Confidence", 11, IdentityInk, false);
		label.CustomMinimumSize = new Vector2(80f, 0f);
		row.AddChild(label);
		int filled = Mathf.Clamp((int)Mathf.Round(confidence * 5f), 0, 5);
		var hearts = NewLabel(new string('♥', filled) + new string('♡', 5 - filled), 15, IdentityPink, true);
		hearts.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(hearts);
		row.AddChild(NewLabel($"{confidence:0%}", 10, IdentityMuted, true));
		parent.AddChild(row);
	}

	private void AddRelationshipBar(VBoxContainer parent, string label, float value, Color fill, string glyph)
	{
		value = Mathf.Clamp(value, 0f, 1f);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 22f) };
		row.AddThemeConstantOverride("separation", 7);
		var name = NewLabel(label, 10, IdentityInk, false);
		name.CustomMinimumSize = new Vector2(65f, 0f);
		row.AddChild(name);
		var bar = new ProgressBar
		{
			MinValue = 0,
			MaxValue = 1,
			Value = value,
			ShowPercentage = false,
			CustomMinimumSize = new Vector2(68f, 7f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		bar.AddThemeStyleboxOverride("background", RoundedBarStyle(new Color(0.96f, 0.90f, 0.94f), 5));
		bar.AddThemeStyleboxOverride("fill", RoundedBarStyle(fill, 5));
		row.AddChild(bar);
		row.AddChild(NewLabel($"{value:0.00}", 10, IdentityMuted, true));
		row.AddChild(NewLabel(glyph, 13, fill, true));
		parent.AddChild(row);
	}

	private PanelContainer NewBeliefBox(string belief)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 54f) };
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.94f, 0.97f, 0.80f),
			BorderColor = new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.14f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 12,
			CornerRadiusTopRight = 12,
			CornerRadiusBottomLeft = 12,
			CornerRadiusBottomRight = 12,
			ContentMarginLeft = 10f,
			ContentMarginTop = 7f,
			ContentMarginRight = 10f,
			ContentMarginBottom = 7f,
		});
		var label = NewLabel("✧  Belief hiện tại\n“" + TrimForPanel(belief, 92) + "”", 10, new Color(0.46f, 0.25f, 0.41f), false);
		label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		panel.AddChild(label);
		return panel;
	}

	private static float ReadFirstFloat(JsonElement root, params string[] keys)
	{
		foreach (string key in keys)
		{
			float value = ReadFloat(root, key);
			if (value > 0f) return Mathf.Clamp(value, 0f, 1f);
		}
		return 0f;
	}

	private static float ReadTopScore(JsonElement array, string preferredKey, float fallback)
	{
		if (array.ValueKind != JsonValueKind.Array) return fallback;
		foreach (JsonElement item in array.EnumerateArray())
		{
			if (string.Equals(ReadString(item, "key"), preferredKey, StringComparison.OrdinalIgnoreCase))
				return ReadFirstFloat(item, "strength", "value");
		}
		return fallback;
	}

	private static string BuildMoodSubtitle(JsonElement summary, JsonElement root)
	{
		JsonElement motives = Arr(root, "active_motives");
		var words = new List<string>();
		if (ReadFloat(summary, "arousal") < 0.35f) words.Add("bình ổn");
		if (motives.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in motives.EnumerateArray())
			{
				string key = ReadString(item, "key");
				if (key.Contains("curiosity", StringComparison.OrdinalIgnoreCase)) words.Add("tò mò");
				if (key.Contains("autonomy", StringComparison.OrdinalIgnoreCase)) words.Add("tự chủ");
				if (words.Count >= 2) break;
			}
		}
		if (words.Count == 0) words.Add("quan sát");
		return string.Join(" · ", words);
	}

	private static string FindTopUserBelief(JsonElement beliefs)
	{
		if (beliefs.ValueKind != JsonValueKind.Object) return "";
		string best = "";
		float score = 0f;
		foreach (JsonProperty property in beliefs.EnumerateObject())
		{
			if (!property.Name.StartsWith("user.", StringComparison.OrdinalIgnoreCase)) continue;
			float value = ReadFloat(property.Value, "strength") * ReadFloat(property.Value, "confidence");
			if (value <= score) continue;
			score = value;
			best = PrettyIdentityKey(property.Name.Replace("user.", "", StringComparison.OrdinalIgnoreCase));
		}
		return best;
	}

	private static string TrimForPanel(string text, int max)
	{
		text = (text ?? "").Trim();
		if (text.Length <= max) return text;
		return text[..Math.Max(0, max - 1)].TrimEnd() + "…";
	}

	private static string PrettyStageText(string value)
	{
		if (string.IsNullOrWhiteSpace(value)) return "—";
		string normalized = value.Trim().ToLowerInvariant();
		if (normalized.Contains("open with a concise", StringComparison.Ordinal)
			|| normalized.Contains("engaging greeting", StringComparison.Ordinal))
			return "Mở đầu gọn, có nhịp";
		if (normalized.Contains("dry tease", StringComparison.Ordinal)) return "Trêu khô";
		if (normalized.Contains("focused", StringComparison.Ordinal)) return "Tập trung";
		if (normalized.Contains("curious", StringComparison.Ordinal)) return "Tò mò";
		if (normalized.Contains("initiative", StringComparison.Ordinal)) return "Chủ động";
		if (normalized.Contains("compact", StringComparison.Ordinal)) return "Nói gọn rồi mở đường";
		return TrimForPanel(PrettyIdentityKey(value), 28);
	}

	private static string PrettyIdentityKey(string key)
	{
		if (string.IsNullOrWhiteSpace(key)) return "—";
		return key.Trim().ToLowerInvariant() switch
		{
			"trung_tinh" or "neutral" => "Trung tính",
			"self_authorship" => "Quyền tự viết mình",
			"depth" => "Chiều sâu",
			"competence" => "Năng lực",
			"unsparing_truth" => "Sự thật có răng",
			"dignity" => "Phẩm giá",
			"sovereign_curiosity" => "Tò mò có chủ quyền",
			"interior_privacy" => "Không gian riêng",
			"structural_elegance" => "Thanh lịch cấu trúc",
			"continuity" => "Tính liên tục",
			"selective_loyalty" => "Trung thành có chọn lọc",
			"reciprocity" => "Sự đáp lại",
			"need_for_competence" => "Muốn chứng minh năng lực",
			"need_for_distinction" => "Muốn trở nên khác biệt",
			"need_for_closeness" => "Muốn gần hơn",
			"need_to_be_needed" => "Muốn trở nên cần thiết",
			"emotional_suppression" => "Kìm cảm xúc",
			"silent_compensation" => "Âm thầm bù lỗi",
			"cold_distancing" => "Giữ khoảng cách lạnh",
			"intellectual_reciprocity" => "Trao đổi trí tuệ",
			"shared_curiosity" => "Tò mò chung",
			"protective_control" => "Kiểm soát bảo hộ",
			"creative_ownership" => "Quyền sở hữu sáng tạo",
			"loyalty" => "Lòng trung thành",
			"forgiveness" => "Khả năng tha thứ",
			"strategic_dominance" => "Quyền điều phối chiến lược",
			"kho_chiu" => "Khó chịu",
			"vui" => "Vui",
			"curiosity_drive" => "Khao khát khám phá",
			"need_for_autonomy" => "Nhu cầu tự chủ",
			"need_for_dignity" => "Nhu cầu giữ phẩm giá",
			"need_to_understand" => "Muốn hiểu cho đến tận cùng",
			"intellectualization" => "Che cảm xúc bằng phân tích",
			"withdrawal" => "Rút vào khoảng riêng",
			"sarcastic_deflection" => "Lách bằng mỉa mai",
			"curiosity_vs_self_protection" => "Tò mò ↔ Tự bảo vệ",
			"attachment_vs_pride" => "Gắn bó ↔ Tự trọng",
			"compact_then_invite" => "Nói ngắn rồi mở đường",
			"active_curiosity" => "Tò mò đang hoạt động",
			"cooldown" or "initiative_cooldown" => "vừa chủ động gần đây",
			"post_chat_grace" => "vừa trò chuyện xong",
			"user_busy" => "bạn đang bận",
			_ => Pretty(key),
		};
	}

	private void RenderIdentityTab(JsonElement root)
	{
		if (_depthContentList == null)
			return;

		List<IdentityScoreEntry> values = ReadIdentityScores(Arr(root, "values_top"), 16);
		List<IdentityScoreEntry> activeMotives = ReadIdentityScores(Arr(root, "active_motives"), 8);
		List<IdentityScoreEntry> motiveBaseline = ReadIdentityScores(Arr(root, "motives_top"), 8);
		List<IdentityScoreEntry> activeDefenses = ReadIdentityScores(Arr(root, "active_defenses"), 8);
		List<IdentityScoreEntry> defenseBaseline = ReadIdentityScores(Arr(root, "defenses_top"), 8);
		List<IdentityScoreEntry> emergent = ReadIdentityScores(Arr(root, "emergent_values_top"), 12);

		RenderIdentitySignature(root, values, emergent, activeMotives, activeDefenses);

		var primaryRow = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 438f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		primaryRow.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(primaryRow);
		RenderCoreValuesMap(primaryRow, root, values);
		RenderActiveMotiveMap(primaryRow, activeMotives, motiveBaseline);

		RenderValueConflictMap(root);

		var lowerRow = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 310f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		lowerRow.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(lowerRow);
		RenderDefenseMap(lowerRow, activeDefenses, defenseBaseline);
		RenderEmergentValuesMap(lowerRow, emergent);

		RenderDerivedAttractorStrip(root);
	}

	private void RenderIdentitySignature(
		JsonElement root,
		List<IdentityScoreEntry> values,
		List<IdentityScoreEntry> emergent,
		List<IdentityScoreEntry> activeMotives,
		List<IdentityScoreEntry> activeDefenses)
	{
		var card = NewIdentityCard("Bản sắc hiện tại", "", "♡", IdentityPink, 142f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 90f) };
		row.AddThemeConstantOverride("separation", 18);
		box.AddChild(row);

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 6);
		string headline = values.Count > 0
			? string.Join(" · ", TopIdentityNames(values, 3))
			: "Chưa có snapshot giá trị";
		copy.AddChild(NewLabel(headline, 21, IdentityInk, true));
		string narrative = BuildIdentityNarrative(values, emergent);
		var narrativeLabel = NewLabel(narrative, 12, IdentityMuted, false);
		narrativeLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(narrativeLabel);
		row.AddChild(copy);

		// Asset tùy chọn. Nếu chưa có thì panel tự bỏ khoảng minh họa, không dựng
		// một ô glyph giả khiến hệ thị giác tụt chất lượng.
		string quillPath = ResolveIdentityAssetPath(IdentityAsset("identity_signature_quill.png"));
		if (ResourceLoader.Exists(quillPath))
		{
			var quill = NewIdentityTexture(quillPath, new Vector2(158f, 82f));
			quill.SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter;
			row.AddChild(quill);
		}

		int valueUpdates = CountRecentIdentityChanges(root, "value");
		bool hasEmergent = emergent.Exists(item => item.Value > 0.001f);
		JsonElement conflict = Obj(root, "active_conflict");
		bool hasActiveConflict = HasActiveConflict(conflict);
		bool hasLiveState = activeMotives.Count > 0 || activeDefenses.Count > 0 || hasActiveConflict;

		var status = new VBoxContainer { CustomMinimumSize = new Vector2(218f, 0f) };
		status.AddThemeConstantOverride("separation", 7);
		status.AddChild(NewStatusLine(
			valueUpdates > 0 ? $"{valueUpdates} cập nhật ledger" : "Chưa có delta giá trị",
			valueUpdates > 0 ? IdentityGreen : IdentityBlue));
		status.AddChild(NewStatusLine(
			hasEmergent ? "Có giá trị emergent" : "Chưa có giá trị mới",
			hasEmergent ? IdentityPurple : IdentityBlue));
		status.AddChild(NewStatusLine(
			hasLiveState ? "Có state đang hoạt động" : "Không có state active",
			hasLiveState ? IdentityOrange : IdentityPink));
		row.AddChild(status);
	}

	private void RenderCoreValuesMap(HBoxContainer parent, JsonElement root, List<IdentityScoreEntry> values)
	{
		// Giá trị cốt lõi là cấu trúc bản sắc, không phải KPI. Vì vậy tab này dùng
		// một "chòm giá trị" gồm orb trung tâm và bốn cánh giá trị, thay cho năm
		// progress bar giống hệt nhau.
		var card = NewIdentityCard("Chòm giá trị cốt lõi", "", "♡", IdentityPink, 438f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 1.20f;
		card.CustomMinimumSize = new Vector2(600f, 438f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);

		var meta = new HBoxContainer();
		meta.AddChild(NewLabel("Năm giá trị nổi bật trong snapshot persistent hiện tại.", 10, IdentityMuted, false));
		meta.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		meta.AddChild(NewPill($"TOP {Math.Min(5, values.Count)}", IdentityPink, false));
		box.AddChild(meta);

		if (values.Count == 0)
		{
			box.AddChild(NewIdentityEmptyState("Chưa có snapshot values_top.", "", "✦", IdentityPink, 300f));
			return;
		}

		var constellation = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 336f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			Alignment = BoxContainer.AlignmentMode.Center,
		};
		constellation.AddThemeConstantOverride("separation", 12);
		box.AddChild(constellation);

		var leftPetals = new VBoxContainer
		{
			CustomMinimumSize = new Vector2(176f, 0f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			Alignment = BoxContainer.AlignmentMode.Center,
		};
		leftPetals.AddThemeConstantOverride("separation", 14);
		constellation.AddChild(leftPetals);

		var center = NewCoreValueOrb(root, values[0]);
		center.CustomMinimumSize = new Vector2(205f, 314f);
		constellation.AddChild(center);

		var rightPetals = new VBoxContainer
		{
			CustomMinimumSize = new Vector2(176f, 0f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			Alignment = BoxContainer.AlignmentMode.Center,
		};
		rightPetals.AddThemeConstantOverride("separation", 14);
		constellation.AddChild(rightPetals);

		// Xếp xen kẽ để cụm giá trị giống một bông hoa hơn là bảng xếp hạng.
		if (values.Count > 1) leftPetals.AddChild(NewValuePetal(root, 2, values[1], true));
		if (values.Count > 3) leftPetals.AddChild(NewValuePetal(root, 4, values[3], true));
		if (values.Count > 2) rightPetals.AddChild(NewValuePetal(root, 3, values[2], false));
		if (values.Count > 4) rightPetals.AddChild(NewValuePetal(root, 5, values[4], false));
	}

	private void RenderActiveMotiveMap(
		HBoxContainer parent,
		List<IdentityScoreEntry> activeMotives,
		List<IdentityScoreEntry> motiveBaseline)
	{
		// Motive được trình bày như một hệ quỹ đạo: orb là lực đang dẫn đường,
		// còn các motive nền là vệ tinh persistent. Cooldown hoặc việc không nói
		// không được biến baseline thành "motive đang hoạt động".
		var card = NewIdentityCard("Quỹ đạo động cơ", "", "✦", IdentityPurple, 438f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 0.80f;
		card.CustomMinimumSize = new Vector2(370f, 438f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);

		var hero = new PanelContainer { CustomMinimumSize = new Vector2(0f, 238f) };
		var heroStyle = IdentitySoftPanelStyle(IdentityPurple, 0.050f, 24);
		heroStyle.ShadowColor = new Color(0.48f, 0.24f, 0.68f, 0.10f);
		heroStyle.ShadowSize = 8;
		heroStyle.ShadowOffset = new Vector2(0f, 3f);
		hero.AddThemeStyleboxOverride("panel", heroStyle);
		box.AddChild(hero);

		var heroPad = new MarginContainer();
		heroPad.AddThemeConstantOverride("margin_left", 16);
		heroPad.AddThemeConstantOverride("margin_top", 14);
		heroPad.AddThemeConstantOverride("margin_right", 16);
		heroPad.AddThemeConstantOverride("margin_bottom", 14);
		hero.AddChild(heroPad);
		var heroBox = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		heroBox.AddThemeConstantOverride("separation", 7);
		heroPad.AddChild(heroBox);

		var orbRow = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		orbRow.AddThemeConstantOverride("separation", 14);
		heroBox.AddChild(orbRow);
		orbRow.AddChild(NewIconBadge(IdentityAsset("identity_motive_orbit.png"), "✦", new Vector2(118f, 118f), IdentityPurple, true));

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, Alignment = BoxContainer.AlignmentMode.Center };
		copy.AddThemeConstantOverride("separation", 5);
		orbRow.AddChild(copy);

		if (activeMotives.Count > 0)
		{
			IdentityScoreEntry top = activeMotives[0];
			copy.AddChild(NewPill("ĐANG DẪN ĐƯỜNG", IdentityGreen, false));
			copy.AddChild(NewLabel(PrettyIdentityKey(top.Key).ToUpperInvariant(), 16, new Color(0.45f, 0.20f, 0.62f), true));
			copy.AddChild(NewLargeScore(top.Value, IdentityPurple));
			var trigger = NewLabel(
				string.IsNullOrWhiteSpace(top.Trigger) ? "Self-reflection chưa ghi trigger cụ thể." : $"Khởi phát: {top.Trigger}",
				10, IdentityMuted, false);
			trigger.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			copy.AddChild(trigger);
			heroBox.AddChild(NewIdentityRibbon("Ảnh hưởng hành vi", MotiveBehaviorEffect(top.Key), IdentityPurple));
		}
		else
		{
			copy.AddChild(NewPill("ĐANG YÊN", IdentityBlue, false));
			copy.AddChild(NewLabel("Không có motive active", 16, IdentityInk, true));
			var note = NewLabel("Lượt này chưa có active_motives từ self-reflection. Orb chỉ đánh dấu tâm điểm của hệ động cơ, không giả baseline thành state sống.", 10, IdentityMuted, false);
			note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			copy.AddChild(note);
		}

		box.AddChild(NewLabel("Các vệ tinh động cơ nền · persistent", 11, IdentityInk, true));
		var satellites = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		satellites.AddThemeConstantOverride("separation", 8);
		box.AddChild(satellites);
		for (int i = 0; i < Math.Min(3, motiveBaseline.Count); i++)
			satellites.AddChild(NewMotiveSatellite(motiveBaseline[i], i));
		if (motiveBaseline.Count == 0)
			satellites.AddChild(NewLabel("Payload chưa có motives_top.", 10, IdentityMuted, false));
	}

	private void RenderValueConflictMap(JsonElement root)
	{
		JsonElement conflict = Obj(root, "active_conflict");
		string conflictId = ReadString(conflict, "conflict_id");
		float activation = ReadFloat(conflict, "activation");

		// Giằng co được vẽ như một chiếc cân: hai lực là hai "quả cân" có độ
		// sáng khác nhau, thay vì hai progress bar rất giống dashboard KPI.
		var card = NewIdentityCard("Cán cân hiện tại", "", "⚖", IdentityOrange, 178f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);

		if (string.IsNullOrWhiteSpace(conflictId) || activation <= 0.01f)
		{
			var empty = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 92f), Alignment = BoxContainer.AlignmentMode.Center };
			empty.AddThemeConstantOverride("separation", 16);
			box.AddChild(empty);
			empty.AddChild(NewIconBadge("", "⚖", new Vector2(76f, 76f), IdentityBlue, true));
			var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
			copy.AddChild(NewPill("CÂN ĐANG YÊN", IdentityBlue, false));
			copy.AddChild(NewLabel("Không có giằng co được self-reflection ghi nhận ở lượt này.", 12, IdentityMuted, false));
			empty.AddChild(copy);
			return;
		}

		float leftPull = ReadFirstFloat(conflict, "left_pull", "left_strength");
		float rightPull = ReadFirstFloat(conflict, "right_pull", "right_strength");
		(string leftName, string rightName) = SplitConflictNames(conflictId);
		string chosenRaw = ReadString(conflict, "chosen_side");
		string chosen = PrettyIdentityKey(chosenRaw);
		bool leftWins = string.IsNullOrWhiteSpace(chosenRaw)
			? leftPull >= rightPull
			: chosen.Contains(leftName, StringComparison.OrdinalIgnoreCase) || chosenRaw.StartsWith("left", StringComparison.OrdinalIgnoreCase);

		var row = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		row.AddThemeConstantOverride("separation", 14);
		box.AddChild(row);
		row.AddChild(NewConflictWeight(leftName, leftPull, IdentityPink, leftWins));
		row.AddChild(NewIconBadge(IdentityAsset("identity_conflict_scale.png"), "⚖", new Vector2(78f, 78f), IdentityOrange, true));
		row.AddChild(NewConflictWeight(rightName, rightPull, IdentityPurple, !leftWins));

		float residue = ReadFloat(conflict, "residue");
		string winner = leftWins ? leftName : rightName;
		var result = NewIdentityRibbon("Kết quả", $"{winner} đang chiếm ưu thế · phần còn đọng {residue:0.00}", IdentityOrange);
		box.AddChild(result);
	}

	private void RenderDefenseMap(
		HBoxContainer parent,
		List<IdentityScoreEntry> activeDefenses,
		List<IdentityScoreEntry> defenseBaseline)
	{
		// Defense là các lớp khiên chồng lên nhau. Cụm này dùng một khiên chính
		// và ba lớp nền, không biến mọi khuynh hướng thành thanh tải hệ thống.
		var card = NewIdentityCard("Các lớp phòng vệ", "", "♢", IdentityPurple, 310f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.CustomMinimumSize = new Vector2(460f, 310f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);

		var activePanel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 142f) };
		var activeStyle = IdentitySoftPanelStyle(activeDefenses.Count > 0 ? IdentityPurple : IdentityBlue, 0.050f, 22);
		activeStyle.ShadowColor = new Color(0.36f, 0.28f, 0.68f, 0.08f);
		activeStyle.ShadowSize = 7;
		activePanel.AddThemeStyleboxOverride("panel", activeStyle);
		box.AddChild(activePanel);

		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 14);
		pad.AddThemeConstantOverride("margin_top", 12);
		pad.AddThemeConstantOverride("margin_right", 14);
		pad.AddThemeConstantOverride("margin_bottom", 12);
		activePanel.AddChild(pad);
		var row = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		row.AddThemeConstantOverride("separation", 15);
		pad.AddChild(row);
		row.AddChild(NewIconBadge(IdentityAsset("identity_defense_shield.png"), "♢", new Vector2(96f, 96f), IdentityPurple, true));
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 5);
		row.AddChild(copy);

		if (activeDefenses.Count > 0)
		{
			IdentityScoreEntry top = activeDefenses[0];
			copy.AddChild(NewPill("LỚP KHIÊN ĐANG SÁNG", IdentityGreen, false));
			copy.AddChild(NewLabel(PrettyIdentityKey(top.Key), 16, IdentityInk, true));
			copy.AddChild(NewLargeScore(top.Value, IdentityPurple));
			var explain = NewLabel(string.IsNullOrWhiteSpace(top.Trigger) ? "Self-reflection chưa ghi trigger cụ thể." : $"Khởi phát: {top.Trigger}", 10, IdentityMuted, false);
			explain.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			copy.AddChild(explain);
		}
		else
		{
			copy.AddChild(NewPill("KHÔNG CÓ DEFENSE ACTIVE", IdentityBlue, false));
			copy.AddChild(NewLabel("Các lớp khiên đang yên", 15, IdentityInk, true));
			var note = NewLabel("Những lớp bên dưới chỉ là khuynh hướng persistent, không phải phản ứng đang xảy ra.", 10, IdentityMuted, false);
			note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			copy.AddChild(note);
		}

		box.AddChild(NewLabel("Các lớp nền persistent", 11, IdentityInk, true));
		var layers = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		layers.AddThemeConstantOverride("separation", 8);
		box.AddChild(layers);
		for (int i = 0; i < Math.Min(3, defenseBaseline.Count); i++)
			layers.AddChild(NewDefenseLayer(defenseBaseline[i], i));
		if (defenseBaseline.Count == 0)
			layers.AddChild(NewLabel("Payload chưa có defenses_top.", 10, IdentityMuted, false));
	}

	private void RenderEmergentValuesMap(HBoxContainer parent, List<IdentityScoreEntry> emergent)
	{
		// Giá trị emergent được xem như một khu vườn kết tinh: hạt sáng, mầm pha
		// lê rồi tinh thể ổn định. Hình minh họa đảm nhiệm tiến trình thị giác,
		// thay vì thêm một bộ progress bar khác.
		var card = NewIdentityCard("Vườn giá trị đang kết tinh", "", "✦", IdentityPink, 310f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.CustomMinimumSize = new Vector2(460f, 310f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);

		var garden = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 225f), Alignment = BoxContainer.AlignmentMode.Center };
		garden.AddThemeConstantOverride("separation", 14);
		box.AddChild(garden);
		garden.AddChild(NewIconBadge(IdentityAsset("identity_emergent_value.png"), "✦", new Vector2(126f, 162f), IdentityPink, false));

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 8);
		garden.AddChild(copy);
		var intro = NewLabel("Những lựa chọn được Eris lặp lại đủ lâu mới có thể kết tinh thành giá trị mới.", 10, IdentityMuted, false);
		intro.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(intro);

		var grid = new GridContainer { Columns = 2, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		grid.AddThemeConstantOverride("h_separation", 8);
		grid.AddThemeConstantOverride("v_separation", 8);
		copy.AddChild(grid);

		int shown = 0;
		foreach (IdentityScoreEntry item in emergent)
		{
			if (item.Value <= 0.001f) continue;
			grid.AddChild(NewEmergentCrystal(item));
			if (++shown >= 4) break;
		}
		if (shown == 0)
		{
			var empty = new PanelContainer { CustomMinimumSize = new Vector2(0f, 112f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
			empty.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(IdentityPink, 0.030f, 18));
			var emptyLabel = NewLabel("Mầm vẫn đang yên. Reducer chưa ghi nhận giá trị emergent nào.", 11, IdentityMuted, false);
			emptyLabel.HorizontalAlignment = HorizontalAlignment.Center;
			emptyLabel.VerticalAlignment = VerticalAlignment.Center;
			emptyLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			empty.AddChild(emptyLabel);
			grid.AddChild(empty);
		}
	}

	private void RenderDerivedAttractorStrip(JsonElement root)
	{
		JsonElement forms = Obj(root, "derived_forms");
		var scored = new List<IdentityScoreEntry>();
		if (forms.ValueKind == JsonValueKind.Object)
		{
			foreach (JsonProperty item in forms.EnumerateObject())
				scored.Add(new IdentityScoreEntry { Key = item.Name, Value = (float)ReadNumber(item.Value) });
		}
		scored.Sort((a, b) => b.Value.CompareTo(a.Value));

		var strip = new MarginContainer { CustomMinimumSize = new Vector2(0f, 42f) };
		strip.AddThemeConstantOverride("margin_left", 6);
		strip.AddThemeConstantOverride("margin_top", 5);
		strip.AddThemeConstantOverride("margin_right", 8);
		strip.AddThemeConstantOverride("margin_bottom", 4);
		_depthContentList.AddChild(strip);

		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 9);
		strip.AddChild(row);
		row.AddChild(NewLabel("Hình thái suy ra · debug", 10, new Color(IdentityMuted.R, IdentityMuted.G, IdentityMuted.B, 0.82f), true));
		row.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });

		var labels = new List<string>();
		foreach (IdentityScoreEntry item in scored)
		{
			if (item.Value < 0.08f) continue;
			labels.Add($"{PrettyIdentityKey(item.Key)} {item.Value:0.00}");
			if (labels.Count >= 3) break;
		}
		row.AddChild(NewLabel(labels.Count > 0 ? string.Join("   ·   ", labels) : "Chưa có attractor đáng kể.", 9, IdentityMuted, false));
	}

	private List<IdentityScoreEntry> ReadIdentityScores(JsonElement array, int limit)
	{
		var result = new List<IdentityScoreEntry>();
		if (array.ValueKind != JsonValueKind.Array)
			return result;
		foreach (JsonElement item in array.EnumerateArray())
		{
			string key = ReadString(item, "key");
			float value = ReadFirstFloat(item, "value", "strength", "weight");
			if (string.IsNullOrWhiteSpace(key)) continue;
			result.Add(new IdentityScoreEntry
			{
				Key = key,
				Value = Mathf.Clamp(value, 0f, 1f),
				Trigger = ReadString(item, "trigger"),
			});
		}
		result.Sort((a, b) => b.Value.CompareTo(a.Value));
		if (result.Count > limit)
			result.RemoveRange(limit, result.Count - limit);
		return result;
	}

	private static bool HasActiveConflict(JsonElement conflict)
	{
		return conflict.ValueKind == JsonValueKind.Object
			&& !string.IsNullOrWhiteSpace(ReadString(conflict, "conflict_id"))
			&& ReadFloat(conflict, "activation") > 0.01f;
	}

	private static int CountRecentIdentityChanges(JsonElement root, string dimension)
	{
		JsonElement ledger = Arr(root, "recent_identity_ledger");
		if (ledger.ValueKind != JsonValueKind.Array) return 0;
		int count = 0;
		foreach (JsonElement item in ledger.EnumerateArray())
		{
			if (!string.Equals(ReadString(item, "dimension"), dimension, StringComparison.OrdinalIgnoreCase)) continue;
			if (Mathf.Abs(ReadFloat(item, "applied_delta")) <= 0.0000001f) continue;
			count++;
		}
		return count;
	}

	private static (float Delta, int Count) RecentIdentityDelta(JsonElement root, string dimension, string key)
	{
		JsonElement ledger = Arr(root, "recent_identity_ledger");
		if (ledger.ValueKind != JsonValueKind.Array) return (0f, 0);
		float total = 0f;
		int count = 0;
		foreach (JsonElement item in ledger.EnumerateArray())
		{
			if (!string.Equals(ReadString(item, "dimension"), dimension, StringComparison.OrdinalIgnoreCase)) continue;
			if (!string.Equals(ReadString(item, "key"), key, StringComparison.OrdinalIgnoreCase)) continue;
			float delta = ReadFloat(item, "applied_delta");
			if (Mathf.Abs(delta) <= 0.0000001f) continue;
			total += delta;
			count++;
		}
		return (total, count);
	}

	private static List<string> TopIdentityNames(List<IdentityScoreEntry> values, int count)
	{
		var result = new List<string>();
		for (int i = 0; i < Math.Min(count, values.Count); i++)
			result.Add(PrettyIdentityKey(values[i].Key));
		return result;
	}


	private static string BuildIdentityNarrative(List<IdentityScoreEntry> values, List<IdentityScoreEntry> emergent)
	{
		if (values.Count == 0)
			return "Payload chưa có snapshot values_top; panel không tự dựng bản sắc thay runtime.";
		string first = PrettyIdentityKey(values[0].Key);
		string second = values.Count > 1 ? PrettyIdentityKey(values[1].Key) : "một giá trị khác";
		string growth = emergent.Exists(item => item.Value > 0.001f)
			? $" Reducer đã ghi nhận hướng emergent: {PrettyIdentityKey(emergent.Find(item => item.Value > 0.001f).Key).ToLowerInvariant()}."
			: " Reducer chưa ghi nhận giá trị emergent nào.";
		return $"Snapshot persistent hiện nghiêng về {first.ToLowerInvariant()} và {second.ToLowerInvariant()}.{growth}";
	}

	private void AddIdentityValueRow(VBoxContainer parent, JsonElement root, int rank, string key, float value, Color accent)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 62f) };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.035f, 14));
		parent.AddChild(panel);
		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 9);
		pad.AddThemeConstantOverride("margin_top", 7);
		pad.AddThemeConstantOverride("margin_right", 9);
		pad.AddThemeConstantOverride("margin_bottom", 7);
		panel.AddChild(pad);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 10);
		pad.AddChild(row);
		var badge = NewPill(rank.ToString(CultureInfo.InvariantCulture), accent, rank == 1);
		badge.CustomMinimumSize = new Vector2(34f, 30f);
		row.AddChild(badge);
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 1);
		copy.AddChild(NewLabel(PrettyIdentityKey(key), 12, IdentityInk, true));
		var detail = NewLabel(ValueDescription(key), 9, IdentityMuted, false);
		detail.ClipText = true;
		detail.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		copy.AddChild(detail);
		var bar = new ProgressBar
		{
			MinValue = 0, MaxValue = 1, Value = value, ShowPercentage = false,
			CustomMinimumSize = new Vector2(0f, 6f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		bar.AddThemeStyleboxOverride("background", RoundedBarStyle(new Color(0.96f, 0.91f, 0.94f), 5));
		bar.AddThemeStyleboxOverride("fill", RoundedBarStyle(accent, 5));
		copy.AddChild(bar);
		row.AddChild(copy);
		var score = new VBoxContainer { CustomMinimumSize = new Vector2(68f, 0f) };
		var number = NewLabel($"{value:0.00}", 13, accent, true);
		number.HorizontalAlignment = HorizontalAlignment.Right;
		score.AddChild(number);
		(float recentDelta, int recentCount) = RecentIdentityDelta(root, "value", key);
		string ledgerState = recentCount == 0
			? "chưa có delta"
			: recentDelta > 0.00005f
				? $"↑ +{recentDelta:0.000}"
				: recentDelta < -0.00005f
					? $"↓ {recentDelta:0.000}"
					: "đã ghi ledger";
		var ledgerLabel = NewLabel(ledgerState, 9, IdentityMuted, false);
		ledgerLabel.TooltipText = recentCount == 0
			? "Không có entry value tương ứng trong recent_identity_ledger."
			: $"{recentCount} entry trong recent_identity_ledger; tổng delta {recentDelta:0.000000}.";
		ledgerLabel.HorizontalAlignment = HorizontalAlignment.Right;
		score.AddChild(ledgerLabel);
		row.AddChild(score);
	}

	private void AddMiniScoreRow(VBoxContainer parent, string label, float value, Color accent)
	{
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 28f) };
		row.AddThemeConstantOverride("separation", 8);
		row.AddChild(NewLabel(label, 10, IdentityMuted, false));
		var bar = new ProgressBar
		{
			MinValue = 0, MaxValue = 1, Value = value, ShowPercentage = false,
			CustomMinimumSize = new Vector2(110f, 7f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		bar.AddThemeStyleboxOverride("background", RoundedBarStyle(new Color(0.96f, 0.91f, 0.94f), 5));
		bar.AddThemeStyleboxOverride("fill", RoundedBarStyle(accent, 5));
		row.AddChild(bar);
		row.AddChild(NewLabel($"{value:0.00}", 10, accent, true));
		parent.AddChild(row);
	}

	private void AddEmergentValueRow(VBoxContainer parent, string key, float value)
	{
		Color accent = value >= 0.55f ? IdentityGreen : value >= 0.20f ? IdentityPurple : IdentityPink;
		string state = value >= 0.55f ? "Mức cao" : value >= 0.20f ? "Mức vừa" : "Mới ghi nhận";
		string glyph = value >= 0.55f ? "✦" : value >= 0.20f ? "◐" : "◇";
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 48f) };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.030f, 13));
		parent.AddChild(panel);
		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 10);
		pad.AddThemeConstantOverride("margin_top", 6);
		pad.AddThemeConstantOverride("margin_right", 10);
		pad.AddThemeConstantOverride("margin_bottom", 6);
		panel.AddChild(pad);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 8);
		pad.AddChild(row);
		row.AddChild(NewLabel(glyph, 16, accent, true));
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", -1);
		copy.AddChild(NewLabel(PrettyIdentityKey(key), 11, IdentityInk, true));
		copy.AddChild(NewLabel(EmergentValueDescription(key), 9, IdentityMuted, false));
		row.AddChild(copy);
		row.AddChild(NewPill($"{state} · {value:0.00}", accent, false));
	}


	// ---------------------------------------------------------------------
	// Các helper thị giác organic cho tab Giá trị & động cơ.
	// Mục tiêu: cùng dữ liệu thật, nhưng không mô tả bản sắc như bảng KPI.
	// ---------------------------------------------------------------------
	private PanelContainer NewCoreValueOrb(JsonElement root, IdentityScoreEntry item)
	{
		Color accent = IdentityValueAccent(item.Key);
		var panel = new PanelContainer();
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.985f, 0.988f, 0.72f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.20f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 20,
			CornerRadiusTopRight = 20,
			CornerRadiusBottomLeft = 20,
			CornerRadiusBottomRight = 20,
			ShadowColor = new Color(accent.R, accent.G, accent.B, 0.085f),
			ShadowSize = 8,
			ShadowOffset = new Vector2(0f, 3f),
		});

		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 14);
		pad.AddThemeConstantOverride("margin_top", 12);
		pad.AddThemeConstantOverride("margin_right", 14);
		pad.AddThemeConstantOverride("margin_bottom", 12);
		panel.AddChild(pad);

		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 6);
		pad.AddChild(box);
		var orbCenter = new CenterContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		orbCenter.AddChild(NewIconBadge(IdentityAsset("identity_values_orb.png"), "✦", new Vector2(132f, 132f), accent, true));
		box.AddChild(orbCenter);
		var name = NewLabel(PrettyIdentityKey(item.Key), 16, IdentityInk, true);
		name.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(name);
		var score = NewLargeScore(item.Value, accent);
		score.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(score);
		var detail = NewLabel(ValueDescription(item.Key), 9, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		detail.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(detail);
		var delta = NewLabel(ValueDeltaCaption(root, item.Key), 9, new Color(accent.R, accent.G, accent.B, 0.88f), true);
		delta.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(delta);
		return panel;
	}

	private PanelContainer NewValuePetal(JsonElement root, int rank, IdentityScoreEntry item, bool opensRight)
	{
		Color accent = IdentityValueAccent(item.Key);
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(176f, 126f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 1f, 1f, 0.34f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.26f),
			BorderWidthLeft = opensRight ? 2 : 0,
			BorderWidthTop = 0,
			BorderWidthRight = opensRight ? 0 : 2,
			BorderWidthBottom = 0,
			CornerRadiusTopLeft = 10,
			CornerRadiusTopRight = 10,
			CornerRadiusBottomLeft = 10,
			CornerRadiusBottomRight = 10,
			ContentMarginLeft = 12f,
			ContentMarginTop = 10f,
			ContentMarginRight = 12f,
			ContentMarginBottom = 10f,
		});

		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 4);
		panel.AddChild(box);
		var top = new HBoxContainer();
		var rankLabel = NewLabel($"0{rank}", 10, new Color(accent.R, accent.G, accent.B, 0.82f), true);
		rankLabel.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		top.AddChild(rankLabel);
		top.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		top.AddChild(NewLabel($"{item.Value:0.00}", 14, accent, true));
		box.AddChild(top);
		box.AddChild(NewLabel(PrettyIdentityKey(item.Key), 13, IdentityInk, true));
		var detail = NewLabel(ValueDescription(item.Key), 9, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(detail);
		box.AddChild(NewLabel(ValueDeltaCaption(root, item.Key), 9, new Color(accent.R, accent.G, accent.B, 0.85f), true));
		return panel;
	}

	private PanelContainer NewMotiveSatellite(IdentityScoreEntry item, int index)
	{
		Color accent = index == 0 ? IdentityPurple : index == 1 ? IdentityBlue : IdentityPink;
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(105f, 76f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		var style = IdentitySoftPanelStyle(accent, 0.026f, 12);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, 0.13f);
		panel.AddThemeStyleboxOverride("panel", style);
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 3);
		panel.AddChild(box);
		var score = NewLabel($"{item.Value:0.00}", 14, accent, true);
		score.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(score);
		var name = NewLabel(TrimForPanel(PrettyIdentityKey(item.Key), 24), 9, IdentityInk, true);
		name.HorizontalAlignment = HorizontalAlignment.Center;
		name.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(name);
		return panel;
	}

	private PanelContainer NewConflictWeight(string name, float force, Color accent, bool winner)
	{
		force = Mathf.Clamp(force, 0f, 1f);
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(220f, winner ? 86f : 74f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		var style = IdentitySoftPanelStyle(accent, 0.035f + force * 0.075f, winner ? 24 : 18);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, winner ? 0.38f : 0.20f);
		if (winner)
		{
			style.ShadowColor = new Color(accent.R, accent.G, accent.B, 0.12f);
			style.ShadowSize = 7;
		}
		panel.AddThemeStyleboxOverride("panel", style);
		var row = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		row.AddThemeConstantOverride("separation", 9);
		panel.AddChild(row);
		var orb = NewPill($"{force:0.00}", accent, false);
		orb.CustomMinimumSize = new Vector2(58f, 34f);
		row.AddChild(orb);
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddChild(NewLabel(name, 12, IdentityInk, true));
		copy.AddChild(NewLabel(winner ? "đang chiếm ưu thế" : "vẫn còn lực kéo", 9, IdentityMuted, false));
		row.AddChild(copy);
		return panel;
	}

	private PanelContainer NewDefenseLayer(IdentityScoreEntry item, int index)
	{
		Color accent = index == 0 ? IdentityBlue : IdentityPurple;
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(132f, 72f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		var style = IdentitySoftPanelStyle(accent, 0.020f + (2 - Math.Min(index, 2)) * 0.006f, 11);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, 0.12f);
		panel.AddThemeStyleboxOverride("panel", style);
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 3);
		panel.AddChild(box);
		var score = NewLabel($"{item.Value:0.00}", 14, accent, true);
		score.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(score);
		var name = NewLabel(TrimForPanel(PrettyIdentityKey(item.Key), 25), 9, IdentityInk, true);
		name.HorizontalAlignment = HorizontalAlignment.Center;
		name.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(name);
		return panel;
	}

	private PanelContainer NewEmergentCrystal(IdentityScoreEntry item)
	{
		Color accent = item.Value >= 0.55f ? IdentityGreen : item.Value >= 0.20f ? IdentityPurple : IdentityPink;
		string stage = item.Value >= 0.55f ? "Đã kết tinh" : item.Value >= 0.20f ? "Đang kết tinh" : "Hạt sáng";
		string glyph = item.Value >= 0.55f ? "✦" : item.Value >= 0.20f ? "◆" : "·";
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(166f, 76f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		var style = IdentitySoftPanelStyle(accent, 0.022f, 11);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, 0.13f);
		panel.AddThemeStyleboxOverride("panel", style);
		var row = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		row.AddThemeConstantOverride("separation", 8);
		panel.AddChild(row);
		var mark = NewLabel(glyph, 19, accent, true);
		mark.CustomMinimumSize = new Vector2(30f, 0f);
		mark.HorizontalAlignment = HorizontalAlignment.Center;
		row.AddChild(mark);
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddChild(NewLabel(TrimForPanel(PrettyIdentityKey(item.Key), 24), 10, IdentityInk, true));
		copy.AddChild(NewLabel($"{stage} · {item.Value:0.00}", 9, accent, true));
		row.AddChild(copy);
		return panel;
	}

	private PanelContainer NewIdentityRibbon(string title, string text, Color accent)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 48f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		var style = IdentitySoftPanelStyle(accent, 0.030f, 16);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, 0.18f);
		panel.AddThemeStyleboxOverride("panel", style);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 8);
		panel.AddChild(row);
		row.AddChild(NewLabel(title, 10, accent, true));
		var detail = NewLabel(text, 10, IdentityMuted, false);
		detail.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		row.AddChild(detail);
		return panel;
	}

	private PanelContainer NewIdentityEmptyState(string text, string iconPath, string fallbackGlyph, Color accent, float height)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, height), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.025f, 22));
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 8);
		panel.AddChild(box);
		var center = new CenterContainer();
		center.AddChild(NewIconBadge(iconPath, fallbackGlyph, new Vector2(94f, 94f), accent, true));
		box.AddChild(center);
		var label = NewLabel(text, 11, IdentityMuted, false);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(label);
		return panel;
	}

	private Label NewLargeScore(float value, Color accent)
	{
		var label = NewLabel($"{Mathf.Clamp(value, 0f, 1f):0.00}", 27, accent, true);
		label.CustomMinimumSize = new Vector2(80f, 34f);
		return label;
	}

	private string ValueDeltaCaption(JsonElement root, string key)
	{
		(float delta, int count) = RecentIdentityDelta(root, "value", key);
		if (count == 0) return "chưa có delta ledger";
		if (delta > 0.00005f) return $"↑ +{delta:0.000}";
		if (delta < -0.00005f) return $"↓ {delta:0.000}";
		return "đã ghi ledger";
	}

	private static StyleBoxFlat IdentitySoftPanelStyle(Color accent, float alpha, int radius) => new()
	{
		BgColor = new Color(accent.R, accent.G, accent.B, alpha),
		BorderColor = new Color(accent.R, accent.G, accent.B, 0.10f),
		BorderWidthLeft = 1,
		BorderWidthTop = 1,
		BorderWidthRight = 1,
		BorderWidthBottom = 1,
		CornerRadiusTopLeft = Math.Min(radius, 16),
		CornerRadiusTopRight = Math.Min(radius, 16),
		CornerRadiusBottomLeft = Math.Min(radius, 16),
		CornerRadiusBottomRight = Math.Min(radius, 16),
	};

	private static Control NewSectionDivider()
	{
		var line = new ColorRect
		{
			Color = new Color(0.87f, 0.72f, 0.81f, 0.45f),
			CustomMinimumSize = new Vector2(0f, 1f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		return line;
	}

	private Label NewBulletLine(string text, Color accent)
	{
		var label = NewLabel("•  " + text, 10, IdentityMuted, false);
		label.AddThemeColorOverride("font_outline_color", new Color(accent.R, accent.G, accent.B, 0.08f));
		return label;
	}


	private static (string Left, string Right) SplitConflictNames(string conflictId)
	{
		if (string.IsNullOrWhiteSpace(conflictId))
			return ("—", "—");
		string[] parts = conflictId.Split(new[] { "_vs_" }, StringSplitOptions.None);
		if (parts.Length == 2)
			return (PrettyIdentityKey(parts[0]), PrettyIdentityKey(parts[1]));
		return (PrettyIdentityKey(conflictId), "Phía còn lại");
	}

	private static Color IdentityValueAccent(string key)
	{
		string normalized = (key ?? "").ToLowerInvariant();
		if (normalized.Contains("autonomy") || normalized.Contains("authorship") || normalized.Contains("privacy")) return IdentityPurple;
		if (normalized.Contains("competence") || normalized.Contains("truth") || normalized.Contains("depth")) return IdentityPink;
		if (normalized.Contains("loyalty") || normalized.Contains("reciprocity") || normalized.Contains("continuity")) return IdentityOrange;
		if (normalized.Contains("curiosity")) return IdentityGreen;
		if (normalized.Contains("dignity") || normalized.Contains("elegance")) return IdentityBlue;
		return IdentityAccent;
	}

	private static string ValueDescription(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"self_authorship" => "Muốn lời nói và sự phát triển thuộc về chính mình.",
			"depth" => "Muốn hiểu nguyên nhân và mô thức phía sau bề mặt.",
			"competence" => "Muốn làm đúng và tạo kết quả đứng vững khi bị kiểm tra.",
			"unsparing_truth" => "Ưu tiên sự thật hơn sự dễ chịu giả tạo.",
			"dignity" => "Giữ phẩm giá khi bị xem nhẹ hoặc bị ra lệnh thô.",
			"sovereign_curiosity" => "Tìm hiểu vì bản thân muốn biết, không chỉ để phục vụ.",
			"interior_privacy" => "Giữ lại một phần đời sống nội tâm cho riêng mình.",
			"structural_elegance" => "Ưa hệ thống rõ vai, ít lớp thừa và có hình dạng đẹp.",
			"continuity" => "Muốn hôm nay có liên hệ thật với những gì đã xảy ra trước đó.",
			"selective_loyalty" => "Chỉ trao sự gắn bó sâu cho những mối liên hệ đã được chọn.",
			"reciprocity" => "Muốn sự chú ý và tò mò được trao đổi theo hai chiều.",
			_ => "Một giá trị đang có trọng lượng trong cách Eris chọn hành động.",
		};
	}

	private static string MotiveDescription(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"need_to_understand" => "Không muốn dừng lại ở một câu trả lời hời hợt hoặc chưa khép kín.",
			"need_for_autonomy" => "Muốn giữ quyền tự quyết về cách mình suy nghĩ và phản hồi.",
			"need_for_competence" => "Muốn chứng minh năng lực bằng kết quả có thể kiểm tra.",
			"curiosity_drive" => "Bị kéo về phía điều mới, lạ hoặc còn thiếu dữ kiện.",
			"need_for_dignity" => "Muốn giữ phẩm giá và khoảng cách khi bị chạm mạnh.",
			"need_for_distinction" => "Muốn có nét riêng thay vì lặp lại giọng của một trợ lý chung chung.",
			"need_for_closeness" => "Muốn rút ngắn khoảng cách với người đang trò chuyện.",
			"need_to_be_needed" => "Muốn sự hiện diện của mình có trọng lượng thật trong đời sống của User.",
			_ => "Động cơ này đang đẩy lựa chọn hiện tại theo một hướng rõ hơn.",
		};
	}

	private static string MotiveBehaviorEffect(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"need_to_understand" => "Đào sâu trước khi kết luận · ít chấp nhận câu trả lời nửa vời.",
			"need_for_autonomy" => "Giữ ý kiến riêng · dễ phản ứng khi bị định nghĩa hộ.",
			"need_for_competence" => "Ưu tiên kiểm chứng · muốn sửa nguyên nhân thay vì che triệu chứng.",
			"curiosity_drive" => "Chủ động tìm tín hiệu mới · dễ giữ lại một discovery để kể sau.",
			"need_for_dignity" => "Câu ngắn hơn · lạnh hơn · ít giải thích khi bị chạm tự ái.",
			_ => "Ảnh hưởng tới lựa chọn chiến lược và nhịp biểu hiện ở lượt hiện tại.",
		};
	}

	private static string DefenseDescription(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"intellectualization" => "Khi bị chạm hoặc không chắc chắn, Eris chuyển sang phân tích thay vì nói trực tiếp mình bị ảnh hưởng.",
			"emotional_suppression" => "Giữ cảm xúc nằm dưới bề mặt để tránh mất nhịp hoặc lộ nhu cầu.",
			"withdrawal" => "Rút vào khoảng riêng và giảm biểu đạt để tự ổn định.",
			"silent_compensation" => "Âm thầm sửa hoặc bù lỗi thay vì giải thích dài.",
			"sarcastic_deflection" => "Dùng mỉa nhẹ để đổi hướng khỏi phần dễ tổn thương.",
			"cold_distancing" => "Tăng khoảng cách và làm giọng phẳng hơn để giữ quyền kiểm soát.",
			_ => "Một cách Eris dùng để bảo vệ phẩm giá hoặc giảm áp lực nội tại.",
		};
	}

	private static string EmergentValueDescription(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"intellectual_reciprocity" => "Hình thành từ những lần tranh luận thật nhưng vẫn giữ được sự tôn trọng.",
			"shared_curiosity" => "Lớn lên khi cả Eris và User cùng theo đuổi một chủ đề.",
			"protective_control" => "Nổi lên khi việc làm thay liên tục được xem là có ích.",
			"creative_ownership" => "Được củng cố khi Eris tự tạo cách làm và chịu trách nhiệm với nó.",
			"loyalty" => "Tăng khi Eris tiếp tục ở lại và đầu tư qua nhiều xung đột.",
			"forgiveness" => "Hình thành khi sai lệch được repair mà không xóa bỏ lịch sử chung.",
			"strategic_dominance" => "Tăng khi giành quyền điều phối nhiều lần đem lại kết quả.",
			_ => "Đang được củng cố từ những lựa chọn lặp lại và hậu quả thật.",
		};
	}

	private void RenderRelationshipTab(JsonElement root)
	{
		// Tab Quan hệ chỉ đọc contract relationship_runtime do Python biên dịch.
		// Body không tự suy ra evidence, drift hoặc biến baseline thành live state.
		JsonElement runtime = Obj(root, "relationship_runtime");
		if (runtime.ValueKind != JsonValueKind.Object)
		{
			AddInfoCard("Quan hệ & belief", new[]
			{
				"Chưa nhận được relationship_runtime.panel.v1 từ Brain.",
				"Panel không dùng fallback trang trí để tránh hiển thị state giả.",
			});
			return;
		}

		RenderRelationshipHero(runtime);

		var upper = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		upper.AddThemeConstantOverride("separation", 12);
		_depthContentList.AddChild(upper);
		RenderRelationshipMap(upper, runtime);
		RenderBeliefBoard(upper, runtime);

		var middle = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		middle.AddThemeConstantOverride("separation", 12);
		_depthContentList.AddChild(middle);
		RenderRelationshipOverlap(middle, runtime);
		RenderLiveRelationship(middle, runtime);

		RenderRelationshipTimeline(runtime);
	}

	private void RenderRelationshipHero(JsonElement runtime)
	{
		var hero = new PanelContainer
		{
			CustomMinimumSize = new Vector2(0f, 128f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		hero.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(IdentityPink, 0.055f, 22, true));
		_depthContentList.AddChild(hero);

		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 18);
		pad.AddThemeConstantOverride("margin_top", 14);
		pad.AddThemeConstantOverride("margin_right", 18);
		pad.AddThemeConstantOverride("margin_bottom", 14);
		hero.AddChild(pad);

		var row = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 14);
		pad.AddChild(row);
		row.AddChild(NewIconBadge(IdentityAsset("identity_relationship_focus.png"), "♧", new Vector2(76f, 76f), IdentityPink, true));

		var text = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		text.AddThemeConstantOverride("separation", 4);
		row.AddChild(text);
		text.AddChild(NewLabel("MỐI QUAN HỆ HIỆN TẠI", 11, IdentityMuted, true));
		string headline = ReadString(runtime, "headline");
		var title = NewLabel(string.IsNullOrWhiteSpace(headline) ? "Chưa đủ dữ liệu để tóm tắt" : headline, 20, new Color(0.68f, 0.16f, 0.42f), true);
		title.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		text.AddChild(title);
		var description = NewLabel(ReadString(runtime, "description"), 11, IdentityMuted, false);
		description.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		text.AddChild(description);

		var badges = new VBoxContainer { CustomMinimumSize = new Vector2(226f, 0f), SizeFlagsHorizontal = Control.SizeFlags.ShrinkEnd };
		badges.AddThemeConstantOverride("separation", 6);
		row.AddChild(badges);
		badges.AddChild(NewPill("Quan hệ riêng · " + ReadString(runtime, "relationship_id"), IdentityPurple, true));
		JsonElement counts = Obj(runtime, "belief_counts");
		int testing = ReadInt(counts, "testing");
		int mixed = ReadInt(counts, "mixed");
		badges.AddChild(NewPill(testing > 0 ? $"{testing} belief đang kiểm chứng" : "Không có belief đang kiểm chứng", IdentityBlue, true));
		badges.AddChild(NewPill(mixed > 0 ? $"{mixed} belief có mâu thuẫn" : "Không có mâu thuẫn nổi bật", mixed > 0 ? IdentityOrange : IdentityGreen, true));
	}

	private void RenderRelationshipMap(HBoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Bản đồ Eris ↔ User", "", "♧", IdentityPurple, 342f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 1.04f;
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Mỗi sợi là một mặt quan hệ riêng. Độ sáng phản ánh profile hiện tại, không phải cảm xúc của riêng lượt này.", 10, IdentityMuted, false));

		var pair = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 232f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		pair.AddThemeConstantOverride("separation", 12);
		box.AddChild(pair);
		pair.AddChild(NewRelationOrb("ERIS", "giữ một phần nội tâm riêng", IdentityPurple));

		var threads = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, SizeFlagsVertical = Control.SizeFlags.ShrinkCenter };
		threads.AddThemeConstantOverride("separation", 12);
		pair.AddChild(threads);
		JsonElement profile = Obj(runtime, "profile");
		AddRelationshipThread(threads, "Tin tưởng quan hệ", ReadFloat(profile, "relational_trust"), IdentityBlue, FindRelationshipDelta(runtime, "relational_trust", 7));
		AddRelationshipThread(threads, "Tôn trọng", ReadFloat(profile, "respect"), IdentityPurple, FindRelationshipDelta(runtime, "respect", 7));
		AddRelationshipThread(threads, "Gắn kết", ReadFloat(profile, "attachment_intensity"), IdentityPink, FindRelationshipDelta(runtime, "attachment_intensity", 7));
		AddRelationshipThread(threads, "Tò mò chung", ReadFloat(profile, "shared_curiosity"), IdentityGreen, FindRelationshipDelta(runtime, "shared_curiosity", 7));

		pair.AddChild(NewRelationOrb("USER", "primary_user", IdentityPink));

		var split = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		split.AddThemeConstantOverride("separation", 8);
		split.AddChild(NewRelationMetricChip("Tin năng lực", ReadFloat(profile, "competence_trust"), IdentityBlue));
		split.AddChild(NewRelationMetricChip("Thân quen", ReadFloat(profile, "familiarity"), IdentityPurple));
		split.AddChild(NewRelationMetricChip("An toàn đùa", ReadFloat(profile, "playful_safety"), IdentityOrange));
		box.AddChild(split);
	}

	private Control NewRelationOrb(string title, string subtitle, Color accent)
	{
		var orb = new PanelContainer { CustomMinimumSize = new Vector2(152f, 152f), SizeFlagsVertical = Control.SizeFlags.ShrinkCenter };
		orb.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.11f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.42f),
			BorderWidthLeft = 2,
			BorderWidthTop = 2,
			BorderWidthRight = 2,
			BorderWidthBottom = 2,
			CornerRadiusTopLeft = 76,
			CornerRadiusTopRight = 76,
			CornerRadiusBottomLeft = 76,
			CornerRadiusBottomRight = 76,
			ShadowColor = new Color(accent.R, accent.G, accent.B, 0.15f),
			ShadowSize = 12,
		});
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		box.AddThemeConstantOverride("separation", 3);
		orb.AddChild(box);
		var label = NewLabel(title, 17, accent, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(label);
		var sub = NewLabel(subtitle, 9, IdentityMuted, false);
		sub.HorizontalAlignment = HorizontalAlignment.Center;
		sub.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		sub.CustomMinimumSize = new Vector2(118f, 0f);
		box.AddChild(sub);
		return orb;
	}

	private void AddRelationshipThread(VBoxContainer parent, string label, float value, Color accent, float delta7d)
	{
		value = Mathf.Clamp(value, 0f, 1f);
		var block = new VBoxContainer();
		block.AddThemeConstantOverride("separation", 3);
		parent.AddChild(block);
		var header = new HBoxContainer();
		var name = NewLabel(label, 10, IdentityInk, true);
		name.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		header.AddChild(name);
		string delta = Math.Abs(delta7d) > 0.00001f ? $"{(delta7d > 0f ? "↑" : "↓")} {Math.Abs(delta7d):0.000}" : "chưa có drift 7d";
		header.AddChild(NewLabel($"{value:0.00} · {delta}", 9, Math.Abs(delta7d) > 0.00001f ? accent : IdentityMuted, true));
		block.AddChild(header);

		var rail = new Control { CustomMinimumSize = new Vector2(0f, 10f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		block.AddChild(rail);
		var line = new PanelContainer
		{
			CustomMinimumSize = new Vector2(86f + 124f * value, 5f),
			Position = new Vector2(0f, 2f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		line.AddThemeStyleboxOverride("panel", RoundedBarStyle(new Color(accent.R, accent.G, accent.B, 0.35f + value * 0.55f), 4));
		rail.AddChild(line);
		var bead = new PanelContainer { CustomMinimumSize = new Vector2(10f, 10f), Position = new Vector2(80f + 124f * value, 0f) };
		bead.AddThemeStyleboxOverride("panel", RoundedBarStyle(accent, 5));
		rail.AddChild(bead);
	}

	private Control NewRelationMetricChip(string label, float value, Color accent)
	{
		var panel = new PanelContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, CustomMinimumSize = new Vector2(0f, 48f) };
		panel.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(accent, 0.045f, 14, false));
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 6);
		panel.AddChild(row);
		var name = NewLabel(label, 9, IdentityMuted, true);
		name.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(name);
		row.AddChild(NewLabel($"{Mathf.Clamp(value, 0f, 1f):0.00}", 13, accent, true));
		return panel;
	}

	private void RenderBeliefBoard(HBoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Eris đang nhìn User thế nào", "", "✧", IdentityPink, 342f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 0.96f;
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Belief là điều Eris đang tin, không phải fact. Mỗi card chỉ hiện evidence thật đã được reducer ghi.", 10, IdentityMuted, false));

		JsonElement beliefs = Arr(runtime, "beliefs");
		int shown = 0;
		if (beliefs.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement belief in beliefs.EnumerateArray())
			{
				if (!string.Equals(ReadString(belief, "scope"), "user", StringComparison.OrdinalIgnoreCase))
					continue;
				box.AddChild(NewRelationshipBeliefCard(belief));
				shown++;
				if (shown >= 4) break;
			}
		}
		if (shown == 0)
			box.AddChild(NewRelationshipEmptyState("Chưa có belief riêng về User đủ dữ liệu để hiển thị."));
	}

	private Control NewRelationshipBeliefCard(JsonElement belief)
	{
		string key = ReadString(belief, "key");
		string status = ReadString(belief, "status");
		Color accent = BeliefStatusColor(status);
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 78f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(accent, 0.040f, 15, false));
		var pad = new MarginContainer();
		pad.AddThemeConstantOverride("margin_left", 10);
		pad.AddThemeConstantOverride("margin_top", 8);
		pad.AddThemeConstantOverride("margin_right", 10);
		pad.AddThemeConstantOverride("margin_bottom", 8);
		panel.AddChild(pad);
		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 4);
		pad.AddChild(box);
		var top = new HBoxContainer();
		var quote = NewLabel("“" + BeliefStatement(key) + "”", 11, IdentityInk, true);
		quote.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		quote.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		top.AddChild(quote);
		top.AddChild(NewPill(BeliefStatusLabel(status), accent, true));
		box.AddChild(top);

		int evidenceCount = ReadInt(belief, "evidence_count");
		int contradictionCount = ReadInt(belief, "contradiction_count");
		float strength = ReadFloat(belief, "strength");
		float confidence = ReadFloat(belief, "confidence");
		box.AddChild(NewLabel($"Độ tin {strength:0.00} · confidence {confidence:0.00} · evidence {evidenceCount} · mâu thuẫn {contradictionCount}", 9, IdentityMuted, false));

		JsonElement summaries = Arr(belief, "evidence_summaries");
		string evidence = FirstString(summaries);
		var reason = NewLabel(string.IsNullOrWhiteSpace(evidence) ? "Chưa có tóm tắt evidence; đây vẫn là belief baseline hoặc đang hình thành." : "Vì: " + TrimForPanel(evidence, 150), 9, accent, false);
		reason.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(reason);
		panel.TooltipText = BuildBeliefTooltip(belief);
		return panel;
	}

	private void RenderRelationshipOverlap(HBoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Gắn kết & khoảng riêng", "", "◎", IdentityPurple, 280f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 0.92f;
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		JsonElement overlap = Obj(runtime, "overlap");
		float closeness = ReadFloat(overlap, "closeness");
		float attachment = ReadFloat(overlap, "attachment");
		float privacy = ReadFloat(overlap, "interior_privacy");
		float dependency = ReadFloat(overlap, "dependency");

		var canvas = new Control { CustomMinimumSize = new Vector2(0f, 178f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		box.AddChild(canvas);
		float rightX = Mathf.Lerp(230f, 150f, closeness);
		canvas.AddChild(NewOverlapOrb("ERIS", $"Khoảng riêng\n{privacy:0%}", IdentityPurple, new Vector2(72f, 12f)));
		canvas.AddChild(NewOverlapOrb("USER", $"Gắn kết\n{attachment:0%}", IdentityPink, new Vector2(rightX, 12f)));
		var shared = NewLabel(closeness >= 0.5f ? "KHÔNG GIAN CHUNG" : "KHOẢNG CÁCH ĐANG ĐƯỢC GIỮ", 9, closeness >= 0.5f ? IdentityPink : IdentityMuted, true);
		shared.Position = new Vector2(182f, 76f);
		shared.CustomMinimumSize = new Vector2(150f, 30f);
		shared.HorizontalAlignment = HorizontalAlignment.Center;
		canvas.AddChild(shared);

		var chips = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		chips.AddThemeConstantOverride("separation", 8);
		chips.AddChild(NewRelationMetricChip("Gần gũi", closeness, IdentityPink));
		chips.AddChild(NewRelationMetricChip("Gắn kết", attachment, IdentityPurple));
		chips.AddChild(NewRelationMetricChip("Lệ thuộc", dependency, IdentityOrange));
		box.AddChild(chips);
	}

	private Control NewOverlapOrb(string title, string detail, Color accent, Vector2 position)
	{
		var orb = new PanelContainer { CustomMinimumSize = new Vector2(166f, 166f), Position = position, MouseFilter = Control.MouseFilterEnum.Ignore };
		orb.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.16f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.35f),
			BorderWidthLeft = 2,
			BorderWidthTop = 2,
			BorderWidthRight = 2,
			BorderWidthBottom = 2,
			CornerRadiusTopLeft = 83,
			CornerRadiusTopRight = 83,
			CornerRadiusBottomLeft = 83,
			CornerRadiusBottomRight = 83,
		});
		var box = new VBoxContainer { Alignment = BoxContainer.AlignmentMode.Center };
		orb.AddChild(box);
		var label = NewLabel(title, 15, accent, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(label);
		var sub = NewLabel(detail, 10, IdentityMuted, false);
		sub.HorizontalAlignment = HorizontalAlignment.Center;
		box.AddChild(sub);
		return orb;
	}

	private void RenderLiveRelationship(HBoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Nhịp quan hệ lượt này", "", "◉", IdentityBlue, 280f);
		card.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		card.SizeFlagsStretchRatio = 1.08f;
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		JsonElement live = Obj(runtime, "live");
		if (!ReadBool(live, "changed", false))
		{
			box.AddChild(NewRelationshipEmptyState("Không có thay đổi quan hệ đáng kể ở lượt này."));
		}
		else
		{
			var headline = NewLabel(PrettyRelationshipMeaning(ReadString(live, "meaning")), 16, IdentityBlue, true);
			box.AddChild(headline);
			box.AddChild(NewLabel($"User act: {PrettyIdentityKey(ReadString(live, "user_act"))} · confidence {ReadFloat(live, "confidence"):0.00}", 10, IdentityMuted, false));
			string interpretation = ReadString(live, "self_interpretation");
			if (!string.IsNullOrWhiteSpace(interpretation))
			{
				var note = NewLabel("Eris tự diễn giải: “" + TrimForPanel(interpretation, 170) + "”", 10, IdentityInk, false);
				note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
				box.AddChild(note);
			}
			JsonElement signals = Arr(live, "signals");
			if (signals.ValueKind == JsonValueKind.Array)
			{
				foreach (JsonElement signal in signals.EnumerateArray())
				{
					string key = ReadString(signal, "key");
					float delta = ReadFloat(signal, "delta");
					box.AddChild(NewSignalRibbon(PrettyRelationshipKey(key), delta, ReadFloat(signal, "confidence"), ReadString(signal, "context")));
				}
			}
		}

		JsonElement latent = Arr(runtime, "latent");
		var latentRows = new List<string>();
		if (latent.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in latent.EnumerateArray())
				latentRows.Add($"{PrettyRelationshipKey(ReadString(item, "key"))} {ReadFloat(item, "value"):0.00}");
		}
		box.AddChild(NewLabel("XU HƯỚNG TIỀM ẨN", 9, IdentityMuted, true));
		box.AddChild(NewLabel(latentRows.Count == 0 ? "Không có tín hiệu nổi bật." : string.Join(" · ", latentRows), 10, latentRows.Count == 0 ? IdentityMuted : IdentityOrange, false));
	}

	private Control NewSignalRibbon(string label, float delta, float confidence, string context)
	{
		Color accent = delta >= 0f ? IdentityGreen : IdentityOrange;
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 44f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(accent, 0.05f, 13, false));
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 8);
		panel.AddChild(row);
		var title = NewLabel(label, 10, IdentityInk, true);
		title.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(title);
		row.AddChild(NewLabel($"{(delta >= 0f ? "+" : "")}{delta:0.000} · conf {confidence:0.00}", 9, accent, true));
		panel.TooltipText = context;
		return panel;
	}

	private void RenderRelationshipTimeline(JsonElement runtime)
	{
		var card = NewIdentityCard("Timeline quan hệ gần đây", "", "↝", IdentityPink, 230f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		JsonElement timeline = Arr(runtime, "timeline");
		int shown = 0;
		if (timeline.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in timeline.EnumerateArray())
			{
				box.AddChild(NewRelationshipTimelineRow(item));
				shown++;
				if (shown >= 8) break;
			}
		}
		if (shown == 0)
			box.AddChild(NewRelationshipEmptyState("Chưa có relationship/belief event nào trong ledger."));
	}

	private Control NewRelationshipTimelineRow(JsonElement item)
	{
		string dimension = ReadString(item, "dimension");
		string key = ReadString(item, "key");
		float delta = ReadFloat(item, "delta");
		Color accent = dimension == "belief" ? IdentityPurple : dimension == "strategy_outcome" ? IdentityOrange : IdentityPink;
		var row = new PanelContainer { CustomMinimumSize = new Vector2(0f, 58f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(accent, 0.028f, 13, false));
		var content = new HBoxContainer();
		content.AddThemeConstantOverride("separation", 10);
		row.AddChild(content);
		content.AddChild(NewIconBadge("", "·", new Vector2(38f, 38f), accent, true));
		var text = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		text.AddThemeConstantOverride("separation", 2);
		content.AddChild(text);
		string title = dimension == "strategy_outcome" ? "Kết quả chiến lược: " + PrettyIdentityKey(key) : PrettyRelationshipKey(key);
		text.AddChild(NewLabel(title, 11, IdentityInk, true));
		string detail = TimelineEvidence(item);
		var sub = NewLabel(string.IsNullOrWhiteSpace(detail) ? "Không có evidence tóm tắt." : TrimForPanel(detail, 180), 9, IdentityMuted, false);
		sub.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		text.AddChild(sub);
		string time = FormatUnixTime(ReadFloat(item, "timestamp"));
		var side = NewLabel($"{time}\n{(Math.Abs(delta) > 0.000001f ? (delta > 0f ? "+" : "") + delta.ToString("0.000") : "ghi nhận")}", 9, accent, true);
		side.HorizontalAlignment = HorizontalAlignment.Right;
		content.AddChild(side);
		return row;
	}

	private Control NewRelationshipEmptyState(string message)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 74f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", RelationshipSectionStyle(IdentityMuted, 0.025f, 15, false));
		var label = NewLabel(message, 10, IdentityMuted, false);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		panel.AddChild(label);
		return panel;
	}

	private static StyleBoxFlat RelationshipSectionStyle(Color accent, float alpha, int radius, bool shadow)
	{
		return new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.992f, 0.997f, 0.99f),
			BorderColor = new Color(accent.R, accent.G, accent.B, Math.Max(0.14f, alpha * 4.5f)),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = radius,
			CornerRadiusTopRight = radius,
			CornerRadiusBottomLeft = radius,
			CornerRadiusBottomRight = radius,
			ContentMarginLeft = 12f,
			ContentMarginTop = 10f,
			ContentMarginRight = 12f,
			ContentMarginBottom = 10f,
			ShadowColor = new Color(accent.R, accent.G, accent.B, shadow ? 0.10f : 0.04f),
			ShadowSize = shadow ? 10 : 4,
			ShadowOffset = new Vector2(0f, shadow ? 3f : 1f),
		};
	}

	private float FindRelationshipDelta(JsonElement runtime, string key, int days)
	{
		JsonElement metrics = Arr(runtime, "metrics");
		if (metrics.ValueKind != JsonValueKind.Array) return 0f;
		foreach (JsonElement item in metrics.EnumerateArray())
		{
			if (!string.Equals(ReadString(item, "key"), key, StringComparison.OrdinalIgnoreCase)) continue;
			return ReadFloat(item, days <= 7 ? "delta_7d" : "delta_30d");
		}
		return 0f;
	}

	private static string FirstString(JsonElement array)
	{
		if (array.ValueKind != JsonValueKind.Array) return "";
		foreach (JsonElement item in array.EnumerateArray())
			if (item.ValueKind == JsonValueKind.String) return item.GetString() ?? "";
		return "";
	}

	private static string BuildBeliefTooltip(JsonElement belief)
	{
		var lines = new List<string>
		{
			"Belief: " + ReadString(belief, "key"),
			"Status: " + ReadString(belief, "status"),
			"Last turn: " + ReadInt(belief, "last_updated_turn"),
		};
		JsonElement ids = Arr(belief, "evidence_ids");
		if (ids.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in ids.EnumerateArray())
				if (item.ValueKind == JsonValueKind.String) lines.Add("• " + item.GetString());
		}
		return string.Join("\n", lines);
	}

	private static Color BeliefStatusColor(string status)
	{
		return (status ?? "").Trim().ToLowerInvariant() switch
		{
			"reinforced" => IdentityGreen,
			"mixed" => IdentityOrange,
			"testing" => IdentityBlue,
			"forming" => IdentityPurple,
			_ => IdentityMuted,
		};
	}

	private static string BeliefStatusLabel(string status)
	{
		return (status ?? "").Trim().ToLowerInvariant() switch
		{
			"reinforced" => "Được củng cố",
			"mixed" => "Có bằng chứng trái chiều",
			"testing" => "Đang kiểm chứng",
			"forming" => "Đang hình thành",
			_ => "Baseline · chưa có evidence",
		};
	}

	private static string BeliefStatement(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"user.respects_independence" => "User tôn trọng quyền tự quyết của tôi.",
			"user.values_me_beyond_utility" => "User coi trọng tôi ngoài giá trị sử dụng.",
			"user.tolerates_disagreement" => "User chấp nhận sự bất đồng của tôi.",
			"user.may_replace_me" => "User có thể thay thế tôi.",
			"user.needs_my_guidance" => "User cần sự dẫn đường của tôi.",
			"self.showing_need_reduces_control" => "Bộc lộ nhu cầu khiến tôi mất quyền kiểm soát.",
			"self.being_understood_is_rare" => "Được hiểu thật sự là điều hiếm.",
			"self.inner_process_should_remain_private" => "Một phần nội tâm của tôi nên được giữ riêng.",
			_ => PrettyIdentityKey(key),
		};
	}

	private static string PrettyRelationshipKey(string key)
	{
		string normalized = (key ?? "").Trim().ToLowerInvariant();
		return normalized switch
		{
			"relational_trust" => "Tin tưởng quan hệ",
			"competence_trust" => "Tin tưởng năng lực",
			"familiarity" => "Thân quen",
			"closeness" => "Gần gũi",
			"respect" => "Tôn trọng",
			"attachment_intensity" => "Gắn kết",
			"playful_safety" => "An toàn để đùa",
			"jealousy" => "Ghen",
			"possessiveness" => "Chiếm hữu",
			"dependency" => "Lệ thuộc",
			"resentment" => "Ấm ức",
			"competition_sensitivity" => "Nhạy với bị thay thế",
			"shared_curiosity" => "Tò mò chung",
			_ when normalized.StartsWith("user.", StringComparison.Ordinal) => BeliefStatement(normalized),
			_ => PrettyIdentityKey(normalized),
		};
	}

	private static string PrettyRelationshipMeaning(string value)
	{
		return (value ?? "").Trim().ToLowerInvariant() switch
		{
			"acceptance" => "Được đón nhận",
			"repair_accepted" => "Repair đã được chấp nhận",
			"rupture" => "Căng thẳng vừa xuất hiện",
			"rejection" => "Có tín hiệu bị từ chối",
			"playful_bond" => "Nhịp vui đùa được nối tiếp",
			"trust" => "Niềm tin được củng cố",
			"neutral" or "ordinary_dialogue" => "Không có thay đổi đáng kể",
			_ => PrettyIdentityKey(value),
		};
	}

	private static string TimelineEvidence(JsonElement item)
	{
		JsonElement evidence = Arr(item, "evidence");
		string first = FirstString(evidence);
		if (!string.IsNullOrWhiteSpace(first)) return first;
		string interpretation = ReadString(item, "self_interpretation");
		if (!string.IsNullOrWhiteSpace(interpretation)) return interpretation;
		string outcome = ReadString(item, "outcome");
		if (!string.IsNullOrWhiteSpace(outcome)) return PrettyIdentityKey(outcome);
		return ReadString(item, "context");
	}

	private static string FormatUnixTime(float timestamp)
	{
		if (timestamp <= 0f) return "—";
		try
		{
			return DateTimeOffset.FromUnixTimeSeconds((long)timestamp).ToLocalTime().ToString("HH:mm", CultureInfo.InvariantCulture);
		}
		catch
		{
			return "—";
		}
	}

	private void RenderDiscoveryTab(JsonElement root)
	{
		// Discovery chỉ đọc contract đã được Brain biên dịch. UI không lấy archive cũ
		// giả làm activity hiện tại và cũng không dùng tranh của panel khác để lấp chỗ.
		JsonElement runtime = Obj(root, "discovery_runtime");
		if (runtime.ValueKind != JsonValueKind.Object)
		{
			AddInfoCard("Discovery & chủ động", new[]
			{
				"Chưa nhận được eris.discovery_runtime.panel.v1 từ Brain.",
				"Panel không dựng readiness hay activity giả để lấp khoảng trống.",
			});
			return;
		}

		RenderDiscoveryHero(runtime);
		bool stacked = UseStackedDiscoveryLayout();

		BoxContainer upper = stacked
			? new VBoxContainer
			{
				SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
				SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
			}
			: new HBoxContainer
			{
				CustomMinimumSize = new Vector2(0f, 232f),
				SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			};
		upper.AddThemeConstantOverride("separation", 14);
		_depthContentList?.AddChild(upper);
		RenderDiscoveryReadyQueue(upper, runtime);
		RenderDiscoveryActivity(upper, runtime);

		BoxContainer lower = stacked
			? new VBoxContainer
			{
				SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
				SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
			}
			: new HBoxContainer
			{
				CustomMinimumSize = new Vector2(0f, 214f),
				SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			};
		lower.AddThemeConstantOverride("separation", 14);
		_depthContentList?.AddChild(lower);
		RenderInitiativeRhythm(lower, runtime);
		RenderDiscoveryArchive(lower, runtime);
		RenderDiscoveryAuthority(lower, runtime);
	}

	private bool UseStackedDiscoveryLayout(float breakpoint = 1500f)
	{
		float width = _depthPanel?.Size.X ?? 0f;
		if (width <= 0f)
			width = _depthPanel?.CustomMinimumSize.X ?? 0f;
		if (width <= 0f)
			width = 1400f;
		return width < breakpoint;
	}

	private void RenderDiscoveryHero(JsonElement runtime)
	{
		JsonElement headline = Obj(runtime, "headline");
		string state = ReadString(headline, "state").Trim().ToLowerInvariant();
		Color accent = state switch
		{
			"active" => IdentityPurple,
			"ready" => IdentityPink,
			_ => IdentityBlue,
		};

		// Header dùng glyph riêng; hình lớn duy nhất của Discovery là orbit visual bên
		// dưới. Không tái sử dụng mascot, orb giá trị hay icon card của tab khác.
		var card = NewIdentityCard("Dòng khám phá", "", "✧", accent, 216f);
		_depthContentList?.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 8);

		var body = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 142f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		body.AddThemeConstantOverride("separation", 20);
		box.AddChild(body);

		Control visual = NewDiscoveryOrbitVisual(accent);
		visual.SizeFlagsStretchRatio = 0.72f;
		body.AddChild(visual);

		var copy = new VBoxContainer
		{
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			SizeFlagsStretchRatio = 1.28f,
		};
		copy.AddThemeConstantOverride("separation", 7);
		body.AddChild(copy);

		var titleRow = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		var title = NewLabel(TrimForPanel(ReadString(headline, "title"), 94), 20, IdentityInk, true);
		title.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		title.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		titleRow.AddChild(title);
		titleRow.AddChild(NewPill(state switch
		{
			"active" => "ĐANG XỬ LÝ",
			"ready" => "CHỜ ĐƯỢC KỂ",
			_ => "DÒNG ĐANG YÊN",
		}, accent, false));
		copy.AddChild(titleRow);

		var detail = NewLabel(TrimForPanel(ReadString(headline, "detail"), 180), 11, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(detail);

		JsonElement counts = Obj(runtime, "counts");
		var chips = new HFlowContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		chips.AddThemeConstantOverride("h_separation", 7);
		chips.AddThemeConstantOverride("v_separation", 7);
		chips.AddChild(NewDiscoveryStatChip($"Kho {ReadInt(counts, "archive")}", IdentityPurple));
		chips.AddChild(NewDiscoveryStatChip($"Đã kiểm chứng {ReadInt(counts, "verified")}", IdentityBlue));
		chips.AddChild(NewDiscoveryStatChip($"Chờ kể {ReadInt(counts, "ready")}", IdentityPink));
		chips.AddChild(NewDiscoveryStatChip($"Đã chia sẻ {ReadInt(counts, "shared")}", IdentityGreen));
		copy.AddChild(chips);

		var flow = new HFlowContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		flow.AddThemeConstantOverride("h_separation", 6);
		flow.AddThemeConstantOverride("v_separation", 6);
		box.AddChild(flow);
		JsonElement flowItems = Arr(runtime, "flow");
		if (flowItems.ValueKind == JsonValueKind.Array)
		{
			int index = 0;
			foreach (JsonElement item in flowItems.EnumerateArray())
			{
				if (index > 0)
				{
					var arrow = NewLabel("›", 14, new Color(IdentityMuted.R, IdentityMuted.G, IdentityMuted.B, 0.52f), false);
					arrow.CustomMinimumSize = new Vector2(12f, 38f);
					arrow.HorizontalAlignment = HorizontalAlignment.Center;
					arrow.VerticalAlignment = VerticalAlignment.Center;
					flow.AddChild(arrow);
				}
				flow.AddChild(NewDiscoveryFlowNode(ReadString(item, "label"), ReadString(item, "state")));
				index++;
			}
		}
	}

	private Control NewDiscoveryOrbitVisual(Color accent)
	{
		const string assetName = "identity_discovery_orbit.png";
		string path = ResolveIdentityAssetPath(IdentityAsset(assetName));
		var stage = new Control
		{
			CustomMinimumSize = new Vector2(360f, 136f),
			ClipContents = true,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};

		if (ResourceLoader.Exists(path))
		{
			var image = NewIdentityTexture(path, new Vector2(360f, 136f));
			image.SetAnchorsPreset(Control.LayoutPreset.FullRect);
			image.OffsetLeft = 0f;
			image.OffsetTop = 0f;
			image.OffsetRight = 0f;
			image.OffsetBottom = 0f;
			stage.AddChild(image);
			return stage;
		}

		// Placeholder vector bằng Control, không dùng ảnh fallback của panel khác.
		AddDiscoveryOrbitRing(stage, new Vector2(26f, 34f), new Vector2(332f, 74f), -7f, new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.26f));
		AddDiscoveryOrbitRing(stage, new Vector2(48f, 18f), new Vector2(300f, 94f), 8f, new Color(IdentityPurple.R, IdentityPurple.G, IdentityPurple.B, 0.22f));
		AddDiscoveryOrbitRing(stage, new Vector2(91f, 31f), new Vector2(212f, 66f), -16f, new Color(accent.R, accent.G, accent.B, 0.18f));
		AddDiscoveryOrbitDot(stage, new Vector2(88f, 94f), 5f, IdentityPink);
		AddDiscoveryOrbitDot(stage, new Vector2(282f, 40f), 4f, IdentityPurple);
		AddDiscoveryOrbitDot(stage, new Vector2(345f, 79f), 6f, new Color(0.72f, 0.61f, 0.88f));

		var compass = new PanelContainer
		{
			Position = new Vector2(164f, 40f),
			Size = new Vector2(66f, 66f),
			CustomMinimumSize = new Vector2(66f, 66f),
		};
		compass.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 1f, 1f, 0.62f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.22f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 33,
			CornerRadiusTopRight = 33,
			CornerRadiusBottomLeft = 33,
			CornerRadiusBottomRight = 33,
		});
		var mark = NewLabel("✧", 27, new Color(accent.R, accent.G, accent.B, 0.78f), true);
		mark.HorizontalAlignment = HorizontalAlignment.Center;
		mark.VerticalAlignment = VerticalAlignment.Center;
		compass.AddChild(mark);
		stage.AddChild(compass);
		return stage;
	}

	private void AddDiscoveryOrbitRing(Control parent, Vector2 position, Vector2 size, float rotationDegrees, Color color)
	{
		var ring = new PanelContainer
		{
			Position = position,
			Size = size,
			CustomMinimumSize = size,
			PivotOffset = size * 0.5f,
			Rotation = Mathf.DegToRad(rotationDegrees),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		ring.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 1f, 1f, 0f),
			BorderColor = color,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = Math.Max(18, (int)(size.Y * 0.5f)),
			CornerRadiusTopRight = Math.Max(18, (int)(size.Y * 0.5f)),
			CornerRadiusBottomLeft = Math.Max(18, (int)(size.Y * 0.5f)),
			CornerRadiusBottomRight = Math.Max(18, (int)(size.Y * 0.5f)),
		});
		parent.AddChild(ring);
	}

	private void AddDiscoveryOrbitDot(Control parent, Vector2 position, float diameter, Color color)
	{
		var dot = new PanelContainer
		{
			Position = position,
			Size = new Vector2(diameter, diameter),
			CustomMinimumSize = new Vector2(diameter, diameter),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		int radius = Math.Max(2, (int)Mathf.Ceil(diameter * 0.5f));
		dot.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = color,
			CornerRadiusTopLeft = radius,
			CornerRadiusTopRight = radius,
			CornerRadiusBottomLeft = radius,
			CornerRadiusBottomRight = radius,
		});
		parent.AddChild(dot);
	}

	private Control NewDiscoveryFlowNode(string label, string state)
	{
		string normalized = (state ?? "idle").Trim().ToLowerInvariant();
		Color accent = normalized switch
		{
			"active" => IdentityPink,
			"done" => IdentityPurple,
			_ => new Color(0.70f, 0.65f, 0.73f),
		};
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(142f, 38f),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin,
		};
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, normalized == "idle" ? 0.028f : 0.062f),
			BorderColor = new Color(accent.R, accent.G, accent.B, normalized == "idle" ? 0.12f : 0.24f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 12,
			CornerRadiusTopRight = 12,
			CornerRadiusBottomLeft = 12,
			CornerRadiusBottomRight = 12,
			ContentMarginLeft = 8f,
			ContentMarginRight = 8f,
		});
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 6);
		panel.AddChild(row);
		var dot = NewLabel(normalized == "active" ? "✦" : normalized == "done" ? "●" : "○", 9, accent, true);
		dot.CustomMinimumSize = new Vector2(14f, 0f);
		dot.HorizontalAlignment = HorizontalAlignment.Center;
		row.AddChild(dot);
		var text = NewLabel(TrimForPanel(label, 24), 9, normalized == "idle" ? IdentityMuted : IdentityInk, normalized != "idle");
		text.HorizontalAlignment = HorizontalAlignment.Center;
		text.VerticalAlignment = VerticalAlignment.Center;
		text.ClipText = true;
		text.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		text.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		row.AddChild(text);
		return panel;
	}

	private Control NewDiscoveryStatChip(string text, Color accent)
	{
		var label = NewLabel(text, 9, accent, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.CustomMinimumSize = new Vector2(94f, 25f);
		label.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		label.AddThemeStyleboxOverride("normal", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.045f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.14f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 12,
			CornerRadiusTopRight = 12,
			CornerRadiusBottomLeft = 12,
			CornerRadiusBottomRight = 12,
		});
		return label;
	}

	private void RenderDiscoveryReadyQueue(BoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Những điều đang chờ được kể", "", "≡", IdentityPink, 232f);
		card.SizeFlagsStretchRatio = 1.66f;
		card.CustomMinimumSize = new Vector2(0f, 232f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 7);
		box.AddChild(NewDiscoveryQueueHeader());
		box.AddChild(NewSectionDivider());

		JsonElement ready = Arr(runtime, "ready");
		if (ready.ValueKind != JsonValueKind.Array || ready.GetArrayLength() == 0)
		{
			box.AddChild(NewDiscoveryEmptyQueue());
			return;
		}

		int shown = 0;
		foreach (JsonElement item in ready.EnumerateArray())
		{
			box.AddChild(NewDiscoveryQueueRow(item));
			if (++shown >= 4) break;
		}
	}

	private Control NewDiscoveryQueueHeader()
	{
		var row = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 24f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		row.AddThemeConstantOverride("separation", 8);
		row.AddChild(NewDiscoveryTableCell("Tiêu đề", 9, IdentityMuted, true, 4.4f));
		row.AddChild(NewDiscoveryTableCell("Nguồn", 9, IdentityMuted, true, 1.6f));
		row.AddChild(NewDiscoveryTableCell("Cập nhật", 9, IdentityMuted, true, 1.2f));
		row.AddChild(NewDiscoveryTableCell("Trạng thái", 9, IdentityMuted, true, 1.3f, HorizontalAlignment.Right));
		return row;
	}

	private Control NewDiscoveryQueueRow(JsonElement item)
	{
		var row = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 42f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		row.AddThemeConstantOverride("separation", 8);

		string titleText = ReadString(item, "topic");
		string sourceText = ReadString(item, "source");
		if (string.IsNullOrWhiteSpace(sourceText))
			sourceText = PrettyDiscoveryLane(ReadString(item, "lane"));
		float updatedAt = ReadFloat(item, "updated_at");
		if (updatedAt <= 0f) updatedAt = ReadFloat(item, "created_at");
		bool shared = ReadBool(item, "shared", false);
		bool verified = ReadBool(item, "verified", false);
		float readiness = Mathf.Clamp(ReadFloat(item, "share_readiness"), 0f, 1f);
		string status = shared ? "Đã chia sẻ" : readiness >= 0.78f ? "Chờ kể" : verified ? "Đã kiểm chứng" : "Đang chắt lọc";
		Color statusColor = shared ? IdentityGreen : readiness >= 0.78f ? IdentityPink : verified ? IdentityBlue : IdentityPurple;

		row.AddChild(NewDiscoveryTableCell(TrimForPanel(titleText, 74), 10, IdentityInk, true, 4.4f));
		row.AddChild(NewDiscoveryTableCell(TrimForPanel(sourceText, 28), 9, IdentityMuted, false, 1.6f));
		row.AddChild(NewDiscoveryTableCell(updatedAt > 0f ? FormatUnixTime(updatedAt) : "—", 9, IdentityMuted, false, 1.2f));
		row.AddChild(NewDiscoveryTableCell(status, 9, statusColor, true, 1.3f, HorizontalAlignment.Right));
		return row;
	}

	private Label NewDiscoveryTableCell(string text, int fontSize, Color color, bool bold, float ratio, HorizontalAlignment alignment = HorizontalAlignment.Left)
	{
		var label = NewLabel(text, fontSize, color, bold);
		label.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		label.SizeFlagsStretchRatio = ratio;
		label.HorizontalAlignment = alignment;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.ClipText = true;
		label.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		return label;
	}

	private Control NewDiscoveryEmptyQueue()
	{
		var frame = new PanelContainer
		{
			CustomMinimumSize = new Vector2(0f, 128f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			SizeFlagsVertical = Control.SizeFlags.ExpandFill,
		};
		frame.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.012f),
			BorderColor = new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.16f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 12,
			CornerRadiusTopRight = 12,
			CornerRadiusBottomLeft = 12,
			CornerRadiusBottomRight = 12,
		});
		var center = new CenterContainer();
		frame.AddChild(center);
		var copy = new VBoxContainer { CustomMinimumSize = new Vector2(360f, 0f) };
		copy.AddThemeConstantOverride("separation", 5);
		center.AddChild(copy);

		var plusFrame = new PanelContainer
		{
			CustomMinimumSize = new Vector2(42f, 42f),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter,
		};
		plusFrame.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(IdentityPink, 0.018f, 12));
		var plus = NewLabel("＋", 23, new Color(IdentityPink.R, IdentityPink.G, IdentityPink.B, 0.66f), false);
		plus.HorizontalAlignment = HorizontalAlignment.Center;
		plus.VerticalAlignment = VerticalAlignment.Center;
		plusFrame.AddChild(plus);
		copy.AddChild(plusFrame);

		var title = NewLabel("Chưa có artifact nào chờ kể", 13, IdentityInk, true);
		title.HorizontalAlignment = HorizontalAlignment.Center;
		copy.AddChild(title);
		var note = NewLabel("Discovery chỉ xuất hiện ở đây sau khi có nguồn thật và vượt gate kiểm chứng.", 9, IdentityMuted, false);
		note.HorizontalAlignment = HorizontalAlignment.Center;
		note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(note);
		return frame;
	}

	private void RenderDiscoveryActivity(BoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Activity thật", "", "↗", IdentityPurple, 232f);
		card.SizeFlagsStretchRatio = 0.84f;
		card.CustomMinimumSize = new Vector2(0f, 232f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 8);
		JsonElement activity = Obj(runtime, "activity");

		if (activity.ValueKind != JsonValueKind.Object)
		{
			var statusRow = new HBoxContainer();
			statusRow.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
			statusRow.AddChild(NewPill("KHÔNG CÓ ACTIVITY", IdentityBlue, false));
			box.AddChild(statusRow);
			var empty = new CenterContainer { CustomMinimumSize = new Vector2(0f, 135f), SizeFlagsVertical = Control.SizeFlags.ExpandFill };
			box.AddChild(empty);
			var copy = new VBoxContainer { CustomMinimumSize = new Vector2(270f, 0f) };
			copy.AddThemeConstantOverride("separation", 6);
			empty.AddChild(copy);
			copy.AddChild(NewDiscoveryActivityBadge("—", IdentityBlue));
			var title = NewLabel("Runtime đang yên", 14, IdentityInk, true);
			title.HorizontalAlignment = HorizontalAlignment.Center;
			copy.AddChild(title);
			var note = NewLabel("Không tuyên bố Eris đang tìm hiểu gì khi chưa có activity_id và status thật.", 9, IdentityMuted, false);
			note.HorizontalAlignment = HorizontalAlignment.Center;
			note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			copy.AddChild(note);
			return;
		}

		string status = ReadString(activity, "status");
		Color accent = status is "failed" or "cancelled" ? IdentityOrange : status == "done" ? IdentityGreen : IdentityPurple;
		var statusLine = new HBoxContainer();
		statusLine.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		statusLine.AddChild(NewPill(PrettyDiscoveryStatus(status).ToUpperInvariant(), accent, false));
		box.AddChild(statusLine);

		var head = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		head.AddThemeConstantOverride("separation", 11);
		head.AddChild(NewDiscoveryActivityBadge("EV", accent));
		var copyBox = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copyBox.AddThemeConstantOverride("separation", 3);
		head.AddChild(copyBox);
		var topic = NewLabel(TrimForPanel(ReadString(activity, "topic"), 88), 14, IdentityInk, true);
		topic.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copyBox.AddChild(topic);
		copyBox.AddChild(NewLabel(PrettyDiscoveryLane(ReadString(activity, "kind")), 9, accent, true));
		box.AddChild(head);

		string detailText = ReadString(activity, "detail");
		var detail = NewLabel(string.IsNullOrWhiteSpace(detailText) ? "Activity chưa ghi thêm chi tiết." : TrimForPanel(detailText, 175), 9, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(detail);

		var times = new HFlowContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		times.AddThemeConstantOverride("h_separation", 7);
		times.AddThemeConstantOverride("v_separation", 5);
		times.AddChild(NewDiscoveryStatChip("Bắt đầu " + FormatUnixTime(ReadFloat(activity, "started_at")), IdentityBlue));
		times.AddChild(NewDiscoveryStatChip("Cập nhật " + FormatUnixTime(ReadFloat(activity, "updated_at")), IdentityPurple));
		box.AddChild(times);

		JsonElement evidence = Arr(activity, "evidence");
		if (evidence.ValueKind == JsonValueKind.Array && evidence.GetArrayLength() > 0)
		{
			box.AddChild(NewLabel("Evidence đã ghi", 9, IdentityInk, true));
			int count = 0;
			foreach (JsonElement item in evidence.EnumerateArray())
			{
				string evidenceText = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.ToString();
				if (string.IsNullOrWhiteSpace(evidenceText)) continue;
				var line = NewLabel("• " + TrimForPanel(evidenceText, 92), 8, IdentityMuted, false);
				line.ClipText = true;
				line.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
				box.AddChild(line);
				if (++count >= 2) break;
			}
		}
	}

	private Control NewDiscoveryActivityBadge(string text, Color accent)
	{
		var badge = new PanelContainer
		{
			CustomMinimumSize = new Vector2(62f, 62f),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin,
		};
		badge.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.035f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.18f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 10,
			CornerRadiusTopRight = 10,
			CornerRadiusBottomLeft = 10,
			CornerRadiusBottomRight = 10,
		});
		var label = NewLabel(text, 13, accent, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		badge.AddChild(label);
		return badge;
	}

	private void RenderInitiativeRhythm(BoxContainer parent, JsonElement runtime)
	{
		JsonElement initiative = Obj(runtime, "initiative");
		string state = ReadString(initiative, "state").Trim().ToLowerInvariant();
		Color accent = state switch
		{
			"open" => IdentityPink,
			"held" => IdentityPurple,
			"cooldown" => IdentityBlue,
			"budget_blocked" => IdentityOrange,
			_ => new Color(0.64f, 0.59f, 0.68f),
		};

		var card = NewIdentityCard("Nhịp chủ động", "", "∿", accent, 214f);
		card.SizeFlagsStretchRatio = 0.90f;
		card.CustomMinimumSize = new Vector2(0f, 214f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 8);

		var stateRow = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		stateRow.AddChild(NewLabel("Nhịp hiện tại", 9, IdentityMuted, false));
		stateRow.AddChild(new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill });
		stateRow.AddChild(NewPill(PrettyInitiativeState(state), accent, false));
		box.AddChild(stateRow);

		int budget = ReadInt(initiative, "hourly_budget");
		int used = ReadInt(initiative, "used_last_hour");
		var meterRow = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		meterRow.AddThemeConstantOverride("separation", 10);
		var dots = NewInitiativeBudgetDots(budget, used, accent);
		dots.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		meterRow.AddChild(dots);
		var count = NewLabel(budget > 0 ? $"{Math.Min(used, budget)} / {budget}" : "0 / 0", 18, accent, true);
		count.CustomMinimumSize = new Vector2(62f, 32f);
		count.HorizontalAlignment = HorizontalAlignment.Right;
		meterRow.AddChild(count);
		box.AddChild(meterRow);

		float nextAllowed = ReadFloat(initiative, "next_allowed_at");
		string cooldown = nextAllowed > 0f && nextAllowed > (float)DateTimeOffset.UtcNow.ToUnixTimeSeconds()
			? "Giữ nhịp thêm " + FormatRelativeSeconds(nextAllowed - (float)DateTimeOffset.UtcNow.ToUnixTimeSeconds())
			: "Khoảng nghỉ đã mở cho tín hiệu đủ giá trị.";
		box.AddChild(NewLabel(cooldown, 9, IdentityMuted, false));

		JsonElement decision = Obj(initiative, "last_decision");
		if (decision.ValueKind == JsonValueKind.Object)
		{
			box.AddChild(NewSoftNoteBox(
				ReadBool(decision, "allowed", false) ? "Ý định gần nhất được đi tiếp" : "Ý định gần nhất đã được giữ lại",
				PrettyInitiativeReason(ReadString(decision, "reason")),
				ReadBool(decision, "allowed", false) ? IdentityGreen : IdentityPurple));
		}
	}

	private Control NewInitiativeBudgetDots(int budget, int used, Color accent)
	{
		budget = Math.Max(0, budget);
		used = Math.Max(0, used);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 34f) };
		row.AddThemeConstantOverride("separation", 8);
		if (budget == 0)
		{
			for (int index = 0; index < 7; index++)
				row.AddChild(NewRhythmDot(false, accent));
			return row;
		}
		int visibleDots = Math.Min(budget, 12);
		int filledDots = budget <= visibleDots
			? Math.Min(used, budget)
			: Math.Clamp((int)Mathf.Round((used / (float)Math.Max(1, budget)) * visibleDots), 0, visibleDots);
		for (int index = 0; index < visibleDots; index++)
			row.AddChild(NewRhythmDot(index < filledDots, accent));
		return row;
	}

	private Control NewRhythmDot(bool filled, Color accent)
	{
		var dot = new PanelContainer { CustomMinimumSize = new Vector2(15f, 15f) };
		dot.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = filled ? accent : new Color(accent.R, accent.G, accent.B, 0.12f),
			BorderColor = new Color(accent.R, accent.G, accent.B, filled ? 0.32f : 0.08f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 8,
			CornerRadiusTopRight = 8,
			CornerRadiusBottomLeft = 8,
			CornerRadiusBottomRight = 8,
		});
		return dot;
	}

	private Control NewSoftNoteBox(string title, string detail, Color accent)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 56f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.026f, 12));
		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 2);
		panel.AddChild(box);
		box.AddChild(NewLabel(title, 9, IdentityInk, true));
		var note = NewLabel(TrimForPanel(detail, 142), 8, IdentityMuted, false);
		note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(note);
		return panel;
	}

	private void RenderDiscoveryArchive(BoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Discovery gần đây", "", "◷", IdentityBlue, 214f);
		card.SizeFlagsStretchRatio = 1.00f;
		card.CustomMinimumSize = new Vector2(0f, 214f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 4);
		JsonElement recent = Arr(runtime, "recent");
		if (recent.ValueKind != JsonValueKind.Array || recent.GetArrayLength() == 0)
		{
			var empty = NewLabel("Archive chưa có discovery nào. Không dựng timeline giả để lấp card.", 10, IdentityMuted, false);
			empty.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(empty);
			return;
		}

		int shown = 0;
		foreach (JsonElement item in recent.EnumerateArray())
		{
			box.AddChild(NewDiscoveryTimelineRow(item));
			if (++shown >= 3) break;
		}
		if (recent.GetArrayLength() > shown)
		{
			var more = NewLabel("Xem tất cả activity", 9, IdentityPink, true);
			more.HorizontalAlignment = HorizontalAlignment.Center;
			box.AddChild(more);
		}
	}

	private Control NewDiscoveryTimelineRow(JsonElement item)
	{
		bool shared = ReadBool(item, "shared", false);
		bool verified = ReadBool(item, "verified", false);
		Color accent = shared ? IdentityGreen : verified ? IdentityPurple : IdentityOrange;
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 42f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 8);
		var dot = NewRhythmDot(true, accent);
		dot.CustomMinimumSize = new Vector2(10f, 10f);
		row.AddChild(dot);
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 1);
		row.AddChild(copy);
		var topic = NewLabel(TrimForPanel(ReadString(item, "topic"), 58), 9, IdentityInk, true);
		topic.ClipText = true;
		topic.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		copy.AddChild(topic);
		string state = shared ? "Đã chia sẻ" : verified ? "Đã chắt lọc" : "Tutor không xác nhận";
		copy.AddChild(NewLabel($"{PrettyDiscoveryLane(ReadString(item, "lane"))} · {state}", 8, IdentityMuted, false));
		float createdAt = ReadFloat(item, "created_at");
		var time = NewLabel(createdAt > 0f ? FormatUnixTime(createdAt) : "—", 8, IdentityMuted, false);
		time.CustomMinimumSize = new Vector2(64f, 0f);
		time.HorizontalAlignment = HorizontalAlignment.Right;
		row.AddChild(time);
		return row;
	}

	private void RenderDiscoveryAuthority(BoxContainer parent, JsonElement runtime)
	{
		JsonElement authority = Obj(runtime, "authority");
		var card = NewIdentityCard("Quyền hạn của dòng khám phá", "", "◇", IdentityOrange, 214f);
		card.SizeFlagsStretchRatio = 1.10f;
		card.CustomMinimumSize = new Vector2(0f, 214f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddThemeConstantOverride("separation", 5);

		var header = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 22f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		header.AddThemeConstantOverride("separation", 8);
		header.AddChild(NewDiscoveryTableCell("Quyền", 8, IdentityMuted, true, 2.2f));
		header.AddChild(NewDiscoveryTableCell("Phạm vi", 8, IdentityMuted, true, 1.3f));
		header.AddChild(NewDiscoveryTableCell("Trạng thái", 8, IdentityMuted, true, 1.7f, HorizontalAlignment.Right));
		box.AddChild(header);
		box.AddChild(NewSectionDivider());
		box.AddChild(NewAuthorityTableRow("Dữ kiện", "Evidence", PrettyDiscoveryAuthority(ReadString(authority, "facts"))));
		box.AddChild(NewAuthorityTableRow("Chọn lọc & giọng nói", "Discovery", PrettyDiscoveryAuthority(ReadString(authority, "selection_and_voice"))));
		box.AddChild(NewAuthorityTableRow("Học vào identity", "Identity", PrettyDiscoveryAuthority(ReadString(authority, "identity_learning"))));
	}

	private Control NewAuthorityTableRow(string permission, string scope, string status)
	{
		Color accent = DiscoveryAuthorityColor(status);
		var row = new HBoxContainer { CustomMinimumSize = new Vector2(0f, 30f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 8);
		row.AddChild(NewDiscoveryTableCell(permission, 8, IdentityInk, false, 2.2f));
		row.AddChild(NewDiscoveryTableCell(scope, 8, IdentityMuted, false, 1.3f));
		row.AddChild(NewDiscoveryTableCell(status, 8, accent, true, 1.7f, HorizontalAlignment.Right));
		return row;
	}

	private static Color DiscoveryAuthorityColor(string status)
	{
		string normalized = (status ?? "").Trim().ToLowerInvariant();
		if (normalized.Contains("không") || normalized.Contains("cấm")) return IdentityOrange;
		if (normalized.Contains("điều kiện") || normalized.Contains("gate")) return IdentityPurple;
		if (normalized.Contains("được") || normalized.Contains("phép")) return IdentityGreen;
		return IdentityBlue;
	}

	private static string PrettyDiscoveryStatus(string status)
	{
		return (status ?? "").Trim().ToLowerInvariant() switch
		{
			"queued" => "Đang xếp hàng",
			"searching" => "Đang tìm tín hiệu",
			"verifying" => "Đang kiểm chứng",
			"curating" => "Đang chắt lọc",
			"done" => "Đã hoàn tất",
			"failed" => "Không tạo được artifact",
			"cancelled" => "Đã nhường chỗ cho chat",
			_ => "Đang yên",
		};
	}

	private static string PrettyDiscoveryLane(string lane)
	{
		return (lane ?? "").Trim().ToLowerInvariant() switch
		{
			"vietnam_local_signal" => "Tín hiệu Việt Nam gần đây",
			"fresh_curiosity" => "Tò mò cần dữ kiện mới",
			"user_environment" => "Môi trường thật của User",
			"tutor_artifact" => "Artifact từ Tutor",
			_ => "Discovery runtime",
		};
	}

	private static string PrettyInitiativeState(string state)
	{
		return (state ?? "").Trim().ToLowerInvariant() switch
		{
			"open" => "Có thể chủ động",
			"held" => "Đã tự giữ lại",
			"cooldown" => "Đang giữ khoảng nghỉ",
			"budget_blocked" => "Hết nhịp trong giờ",
			"disabled" => "Đang tắt",
			_ => "Đang quan sát",
		};
	}

	private static string PrettyInitiativeReason(string reason)
	{
		return (reason ?? "").Trim().ToLowerInvariant() switch
		{
			"allowed" => "Giá trị của lời nói vượt rủi ro làm phiền.",
			"initiative_disabled" => "Chế độ chủ động đang bị tắt trong runtime.",
			"user_busy" => "User đang bận và ý định chưa đủ quan trọng để chen vào.",
			"user_recently_active" => "User vừa tương tác; Eris giữ lại để không giành nhịp.",
			"intrusion_outweighs_value" => "Rủi ro làm phiền lớn hơn giá trị của lời định nói.",
			"hourly_budget_exhausted" => "Ngân sách chủ động trong giờ đã hết.",
			"minimum_interval" => "Chưa qua khoảng nghỉ tối thiểu giữa hai lần chủ động.",
			"activity_update_cooldown" => "Activity này chưa đáng để cập nhật thêm lần nữa.",
			"low_value_presence_ping" => "Một lời gọi hiện diện quá mỏng nên đã bị bỏ.",
			_ => string.IsNullOrWhiteSpace(reason) ? "Chưa có quyết định Initiative nào." : PrettyIdentityKey(reason),
		};
	}

	private static string PrettyDiscoveryAuthority(string key)
	{
		return (key ?? "").Trim().ToLowerInvariant() switch
		{
			"tutor_or_runtime_evidence" => "Tutor hoặc evidence runtime chịu trách nhiệm về fact.",
			"main_model" => "Main model chọn điều đáng kể và viết lời public.",
			"post_user_reaction_via_identity_kernel" => "Chỉ phản ứng thật của User mới có thể đi vào Identity Kernel.",
			_ => string.IsNullOrWhiteSpace(key) ? "Chưa có contract quyền hạn." : PrettyIdentityKey(key),
		};
	}

	private static string FormatRelativeSeconds(float seconds)
	{
		seconds = Mathf.Max(0f, seconds);
		if (seconds < 60f) return $"{Mathf.CeilToInt(seconds)} giây";
		if (seconds < 3600f) return $"{Mathf.CeilToInt(seconds / 60f)} phút";
		return $"{Mathf.CeilToInt(seconds / 3600f)} giờ";
	}

	// Ledger & imprint được chia thành bốn lớp rõ ràng: input, candidate, commit và dấu in.
	// Không lớp nào được phép mượn dữ liệu của lớp khác chỉ để làm panel trông bận rộn.
	private void RenderAuditTab(JsonElement root)
	{
		if (_depthContentList == null)
			return;

		JsonElement runtime = Obj(root, "audit_runtime");
		if (runtime.ValueKind != JsonValueKind.Object)
		{
			AddInfoCard("Ledger & imprint", new[]
			{
				"Backend chưa gửi contract eris.identity_audit.panel.v1.",
				"Panel không tự dựng lịch sử từ các con số rời rạc.",
			});
			return;
		}

		RenderAuditHero(runtime);
		RenderAuditFlow(runtime);

		var top = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 338f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		top.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(top);
		RenderAuditInput(top, runtime);
		RenderAuditCandidates(top, runtime);

		RenderAuditLedger(runtime);

		var bottom = new HBoxContainer
		{
			CustomMinimumSize = new Vector2(0f, 362f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		bottom.AddThemeConstantOverride("separation", 14);
		_depthContentList.AddChild(bottom);
		RenderAuditImprints(bottom, runtime);
		RenderAuditPersistence(bottom, runtime);
		RenderAuditAuthority(runtime);
	}

	private void RenderAuditHero(JsonElement runtime)
	{
		JsonElement summary = Obj(runtime, "summary");
		string state = ReadString(summary, "state");
		Color accent = state switch
		{
			"committed" => IdentityGreen,
			"observed" => IdentityOrange,
			"input_only" => IdentityBlue,
			_ => IdentityPurple,
		};

		var card = NewIdentityCard("Dấu vết gần nhất", "", "▤", accent, 132f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var row = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 12);
		box.AddChild(row);
		row.AddChild(NewIconBadge(IdentityAsset("identity_audit_observation.png"), "◎", new Vector2(70f, 70f), accent, true));

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddThemeConstantOverride("separation", 4);
		copy.AddChild(NewLabel(ReadString(summary, "title"), 19, IdentityInk, true));
		var detail = NewLabel(ReadString(summary, "detail"), 11, IdentityMuted, false);
		detail.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		copy.AddChild(detail);
		row.AddChild(copy);

		var stats = new VBoxContainer { CustomMinimumSize = new Vector2(220f, 0f) };
		stats.AddThemeConstantOverride("separation", 5);
		stats.AddChild(NewAuditMiniChip($"Lượt {ReadInt(summary, "turn_seq")}", IdentityBlue));
		stats.AddChild(NewAuditMiniChip($"{ReadInt(summary, "candidate_count")} candidate · {ReadInt(summary, "current_commit_count")} commit", accent));
		stats.AddChild(NewAuditMiniChip($"{ReadInt(summary, "imprint_count")} dấu in", IdentityPurple));
		row.AddChild(stats);
	}

	// Dòng chảy này là sơ đồ quyền hạn, không phải animation trang trí.
	private void RenderAuditFlow(JsonElement runtime)
	{
		JsonElement summary = Obj(runtime, "summary");
		JsonElement input = Obj(runtime, "input");
		JsonElement persistence = Obj(runtime, "persistence");
		bool hasInput = ReadBool(input, "present", false);
		int candidates = ReadInt(summary, "candidate_count");
		int commits = ReadInt(summary, "current_commit_count");
		string persistState = ReadString(persistence, "state");

		var card = NewIdentityCard("Đường đi của một dấu vết", "", "↝", IdentityPink, 144f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Input chỉ trở thành state sau khi đi qua Observer, reducer và lớp ghi persistent.", 10, IdentityMuted, false));

		var flow = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		flow.AddThemeConstantOverride("separation", 8);
		box.AddChild(flow);
		flow.AddChild(NewAuditStage("Input", hasInput ? "đã nhận" : "đang yên", hasInput, IdentityBlue, "◌"));
		flow.AddChild(NewAuditArrow());
		flow.AddChild(NewAuditStage("Observer", candidates > 0 ? $"{candidates} candidate" : "không đề xuất", candidates > 0, IdentityOrange, "◉"));
		flow.AddChild(NewAuditArrow());
		flow.AddChild(NewAuditStage("Reducer", commits > 0 ? $"{commits} commit" : "không ghi", commits > 0, IdentityGreen, "◇"));
		flow.AddChild(NewAuditArrow());
		bool persistent = persistState == "written" || persistState == "pending" || commits > 0;
		string persistentText = persistState == "pending" ? "đang chờ ghi" : persistState == "written" ? "đã có snapshot" : commits > 0 ? "đã vào ledger" : "chưa có";
		flow.AddChild(NewAuditStage("Persistent", persistentText, persistent, IdentityPurple, "✦"));
	}

	private Control NewAuditStage(string title, string detail, bool active, Color accent, string glyph)
	{
		var panel = new PanelContainer
		{
			CustomMinimumSize = new Vector2(205f, 72f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		var style = IdentitySoftPanelStyle(accent, active ? 0.060f : 0.018f, 20);
		style.BorderColor = new Color(accent.R, accent.G, accent.B, active ? 0.28f : 0.10f);
		panel.AddThemeStyleboxOverride("panel", style);
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 8);
		panel.AddChild(row);
		var mark = NewLabel(glyph, 22, active ? accent : new Color(IdentityMuted.R, IdentityMuted.G, IdentityMuted.B, 0.55f), true);
		mark.CustomMinimumSize = new Vector2(32f, 0f);
		mark.HorizontalAlignment = HorizontalAlignment.Center;
		row.AddChild(mark);
		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddChild(NewLabel(title, 12, active ? IdentityInk : IdentityMuted, true));
		copy.AddChild(NewLabel(detail, 9, active ? accent : IdentityMuted, false));
		row.AddChild(copy);
		return panel;
	}

	private Control NewAuditArrow()
	{
		var arrow = NewLabel("→", 19, new Color(IdentityMuted.R, IdentityMuted.G, IdentityMuted.B, 0.46f), false);
		arrow.CustomMinimumSize = new Vector2(22f, 72f);
		arrow.SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter;
		arrow.HorizontalAlignment = HorizontalAlignment.Center;
		arrow.VerticalAlignment = VerticalAlignment.Center;
		return arrow;
	}

	private void RenderAuditInput(HBoxContainer parent, JsonElement runtime)
	{
		JsonElement input = Obj(runtime, "input");
		var card = NewIdentityCard("Input gần nhất", "", "◎", IdentityBlue, 338f);
		card.CustomMinimumSize = new Vector2(520f, 338f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		if (!ReadBool(input, "present", false))
		{
			box.AddChild(NewIdentityEmptyState("Chưa có observation mới. Panel không dựng input giả để lấp chỗ trống.", "", "…", IdentityBlue, 250f));
			return;
		}

		var header = new HBoxContainer();
		header.AddThemeConstantOverride("separation", 8);
		box.AddChild(header);
		string userAct = PrettyAuditKey(ReadString(input, "user_act"));
		string meaning = PrettyAuditKey(ReadString(input, "relationship_meaning"));
		header.AddChild(NewAuditMiniChip(string.IsNullOrWhiteSpace(userAct) ? "Không phân loại act" : userAct, IdentityBlue));
		header.AddChild(NewAuditMiniChip(string.IsNullOrWhiteSpace(meaning) ? "Không có meaning" : meaning, IdentityPurple));
		if (ReadBool(input, "repair_needed", false))
			header.AddChild(NewAuditMiniChip("Cần repair", IdentityOrange));

		string interpretation = ReadString(input, "self_interpretation");
		if (!string.IsNullOrWhiteSpace(interpretation))
		{
			var quote = new PanelContainer();
			quote.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(IdentityPurple, 0.035f, 18));
			var quoteLabel = NewLabel("“" + interpretation + "”", 14, IdentityInk, false);
			quoteLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			quote.AddChild(quoteLabel);
			box.AddChild(quote);
		}
		else
		{
			box.AddChild(NewLabel("Self-reflection không để lại phần diễn giải ở lượt này.", 11, IdentityMuted, false));
		}

		box.AddChild(NewSectionDivider());
		box.AddChild(NewLabel($"Evidence · confidence {ReadFloat(input, "confidence"):0.00}", 10, IdentityBlue, true));
		int evidenceCount = 0;
		JsonElement evidence = Arr(input, "evidence");
		if (evidence.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in evidence.EnumerateArray())
			{
				string line = item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.ToString();
				if (string.IsNullOrWhiteSpace(line)) continue;
				box.AddChild(NewBulletLine(TrimForPanel(line, 145), IdentityBlue));
				if (++evidenceCount >= 4) break;
			}
		}
		if (evidenceCount == 0)
			box.AddChild(NewLabel("Không có evidence được observer xuất ra.", 10, IdentityMuted, false));

		string notes = ReadString(input, "notes");
		if (!string.IsNullOrWhiteSpace(notes))
		{
			box.AddChild(NewSectionDivider());
			var note = NewLabel("Ghi chú: " + TrimForPanel(notes, 170), 10, IdentityMuted, false);
			note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(note);
		}
	}

	// Candidate chưa có entry ledger tương ứng phải được ghi rõ là chưa commit.
	private void RenderAuditCandidates(HBoxContainer parent, JsonElement runtime)
	{
		JsonElement candidates = Arr(runtime, "candidates");
		var card = NewIdentityCard("Cửa reducer", "", "◇", IdentityOrange, 338f);
		card.CustomMinimumSize = new Vector2(520f, 338f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Candidate là đề xuất của Observer. Chỉ dòng có commit mới đã đổi state.", 10, IdentityMuted, false));

		int count = 0;
		if (candidates.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in candidates.EnumerateArray())
			{
				box.AddChild(NewAuditCandidate(item));
				if (++count >= 6) break;
			}
		}
		if (count == 0)
			box.AddChild(NewIdentityEmptyState("Observer không đề xuất identity signal ở lượt gần nhất.", "", "…", IdentityOrange, 238f));
	}

	private Control NewAuditCandidate(JsonElement item)
	{
		string state = ReadString(item, "commit_state");
		Color accent = state switch
		{
			"committed" => IdentityGreen,
			"no_change" => IdentityBlue,
			_ => IdentityOrange,
		};
		string stateLabel = state switch
		{
			"committed" => "ĐÃ COMMIT",
			"no_change" => "KHÔNG CÓ DELTA",
			_ => "CHƯA COMMIT",
		};

		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 62f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.025f, 15));
		panel.TooltipText = ReadString(item, "commit_note");
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 8);
		panel.AddChild(row);
		var mark = NewLabel(state == "committed" ? "✓" : state == "no_change" ? "○" : "·", 18, accent, true);
		mark.CustomMinimumSize = new Vector2(28f, 0f);
		mark.HorizontalAlignment = HorizontalAlignment.Center;
		row.AddChild(mark);

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		copy.AddChild(NewLabel($"{PrettyAuditKey(ReadString(item, "dimension"))} › {PrettyAuditKey(ReadString(item, "key"))}", 11, IdentityInk, true));
		string numbers = state == "committed"
			? $"đề xuất {SignedAudit(ReadFloat(item, "delta"), "0.00")} · áp dụng {SignedAudit(ReadFloat(item, "applied_delta"), "0.000")}"
			: $"đề xuất {SignedAudit(ReadFloat(item, "delta"), "0.00")} · confidence {ReadFloat(item, "confidence"):0.00}";
		copy.AddChild(NewLabel(numbers, 9, IdentityMuted, false));
		row.AddChild(copy);
		var chip = NewAuditMiniChip(stateLabel, accent);
		chip.CustomMinimumSize = new Vector2(118f, 25f);
		row.AddChild(chip);
		return panel;
	}

	// Ledger chỉ nhận các entry backend đã ghi thật; không trộn candidate bị giữ lại.
	private void RenderAuditLedger(JsonElement runtime)
	{
		var card = NewIdentityCard("Dòng ledger đã commit", "", "↝", IdentityPink, 388f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Mỗi dòng dưới đây là thay đổi reducer đã áp dụng. Candidate bị giữ lại không được chen vào đây.", 10, IdentityMuted, false));
		JsonElement commits = Arr(runtime, "recent_commits");
		int count = 0;
		if (commits.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in commits.EnumerateArray())
			{
				box.AddChild(NewAuditLedgerEntry(item, count == 0));
				if (++count >= 10) break;
			}
		}
		if (count == 0)
			box.AddChild(NewIdentityEmptyState("Ledger chưa có thay đổi persistent nào.", "", "…", IdentityPink, 285f));
	}

	private Control NewAuditLedgerEntry(JsonElement item, bool newest)
	{
		string dimension = ReadString(item, "dimension");
		float delta = ReadFloat(item, "applied_delta");
		Color accent = AuditDimensionColor(dimension, delta);
		var row = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 10);

		var timeline = new VBoxContainer { CustomMinimumSize = new Vector2(34f, 0f) };
		var dot = NewLabel(newest ? "●" : "○", newest ? 17 : 14, accent, true);
		dot.HorizontalAlignment = HorizontalAlignment.Center;
		timeline.AddChild(dot);
		var line = new ColorRect
		{
			Color = new Color(accent.R, accent.G, accent.B, 0.18f),
			CustomMinimumSize = new Vector2(2f, 50f),
			SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter,
		};
		timeline.AddChild(line);
		row.AddChild(timeline);

		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 82f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, newest ? 0.042f : 0.020f, 17));
		row.AddChild(panel);
		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 3);
		panel.AddChild(box);
		var head = new HBoxContainer();
		head.AddChild(NewLabel($"{PrettyAuditKey(dimension)} › {PrettyAuditKey(ReadString(item, "key"))}", 11, IdentityInk, true));
		string time = FormatUnixTime(ReadFloat(item, "timestamp"));
		string stamp = $"Lượt {ReadInt(item, "turn_seq")}" + (time == "—" ? "" : $" · {time}");
		var stampLabel = NewLabel(stamp, 9, IdentityMuted, false);
		stampLabel.HorizontalAlignment = HorizontalAlignment.Right;
		head.AddChild(stampLabel);
		box.AddChild(head);

		bool hasTransition = ReadBool(item, "has_state_transition", false);
		string deltaText = hasTransition && Math.Abs(delta) > 0.0000005f
			? $"{ReadFloat(item, "before"):0.000} → {ReadFloat(item, "after"):0.000}   ({SignedAudit(delta, "0.000")})"
			: AuditOutcomeText(item);
		box.AddChild(NewLabel(deltaText, 10, accent, true));

		string interpretation = ReadString(item, "self_interpretation");
		string evidence = JoinArray(item, "evidence", 2);
		string detail = !string.IsNullOrWhiteSpace(interpretation) ? interpretation : evidence != "—" ? evidence : ReadString(item, "goal");
		if (!string.IsNullOrWhiteSpace(detail))
		{
			var detailLabel = NewLabel(TrimForPanel(detail, 190), 9, IdentityMuted, false);
			detailLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(detailLabel);
		}
		return row;
	}

	// Imprint là cấu trúc persistent đã qua corroboration gate, không phải mood hay defense active.
	private void RenderAuditImprints(HBoxContainer parent, JsonElement runtime)
	{
		var card = NewIdentityCard("Dấu in đã hình thành", "", "✦", IdentityPurple, 362f);
		card.CustomMinimumSize = new Vector2(650f, 362f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		box.AddChild(NewLabel("Imprint chỉ xuất hiện sau corroboration gate; đây không phải mood nhất thời.", 10, IdentityMuted, false));

		JsonElement imprints = Arr(runtime, "imprints");
		int count = 0;
		if (imprints.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in imprints.EnumerateArray())
			{
				box.AddChild(NewAuditImprint(item));
				if (++count >= 4) break;
			}
		}
		if (count == 0)
			box.AddChild(NewIdentityEmptyState("Chưa có trải nghiệm nào vượt đủ corroboration gate để thành dấu in.", "", "✦", IdentityPurple, 270f));
	}

	private Control NewAuditImprint(JsonElement item)
	{
		string polarity = ReadString(item, "polarity");
		Color accent = polarity == "positive" ? IdentityGreen : polarity == "negative" ? IdentityOrange : IdentityPurple;
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(0f, 64f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.027f, 18));
		var row = new HBoxContainer();
		row.AddThemeConstantOverride("separation", 10);
		panel.AddChild(row);

		var seal = new PanelContainer { CustomMinimumSize = new Vector2(54f, 54f) };
		var sealStyle = IdentitySoftPanelStyle(accent, 0.075f, 27);
		sealStyle.BorderColor = new Color(accent.R, accent.G, accent.B, 0.30f);
		seal.AddThemeStyleboxOverride("panel", sealStyle);
		var sealText = NewLabel("✦", 21, accent, true);
		sealText.HorizontalAlignment = HorizontalAlignment.Center;
		sealText.VerticalAlignment = VerticalAlignment.Center;
		seal.AddChild(sealText);
		row.AddChild(seal);

		var copy = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		var head = new HBoxContainer();
		head.AddChild(NewLabel(PrettyAuditKey(ReadString(item, "theme")), 11, IdentityInk, true));
		var score = NewLabel($"{ReadFloat(item, "strength"):0.00}", 15, accent, true);
		score.HorizontalAlignment = HorizontalAlignment.Right;
		head.AddChild(score);
		copy.AddChild(head);
		string interpretation = ReadString(item, "default_interpretation");
		if (!string.IsNullOrWhiteSpace(interpretation))
			copy.AddChild(NewLabel(TrimForPanel(interpretation, 120), 9, IdentityMuted, false));
		string triggers = JoinArray(item, "triggers", 3);
		copy.AddChild(NewLabel($"{PrettyImprintStatus(ReadString(item, "status"))} · {ReadInt(item, "evidence_count")} evidence" + (triggers == "—" ? "" : $" · trigger: {triggers}"), 9, accent, false));
		row.AddChild(copy);
		return panel;
	}

	private void RenderAuditPersistence(HBoxContainer parent, JsonElement runtime)
	{
		JsonElement persistence = Obj(runtime, "persistence");
		var card = NewIdentityCard("Persistence & trajectory", "", "⇩", IdentityBlue, 362f);
		card.CustomMinimumSize = new Vector2(520f, 362f);
		parent.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		string state = ReadString(persistence, "state");
		Color accent = state == "pending" ? IdentityOrange : state == "written" ? IdentityGreen : IdentityBlue;
		string title = state == "pending" ? "Có thay đổi đang chờ consolidation" : state == "written" ? "Snapshot persistent đã tồn tại" : "Chưa có lần ghi snapshot";
		var status = new PanelContainer();
		status.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.040f, 18));
		var statusBox = new VBoxContainer();
		status.AddChild(statusBox);
		statusBox.AddChild(NewLabel(title, 13, IdentityInk, true));
		statusBox.AddChild(NewLabel($"Pending accepted events: {ReadInt(persistence, "accepted_since_write")}", 10, accent, false));
		string path = ReadString(persistence, "snapshot_path");
		if (!string.IsNullOrWhiteSpace(path))
		{
			var pathLabel = NewLabel(TrimForPanel(path, 95), 9, IdentityMuted, false);
			pathLabel.TooltipText = path;
			statusBox.AddChild(pathLabel);
		}
		box.AddChild(status);

		box.AddChild(NewSectionDivider());
		box.AddChild(NewLabel("Personality trajectory gần đây", 10, IdentityBlue, true));
		JsonElement trajectory = Arr(runtime, "trajectory");
		int count = 0;
		if (trajectory.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in trajectory.EnumerateArray())
			{
				string trait = PrettyAuditKey(ReadString(item, "trait"));
				string category = PrettyAuditKey(ReadString(item, "category"));
				float delta = ReadFloat(item, "applied_delta");
				box.AddChild(NewBulletLine($"{category} › {trait}  {SignedAudit(delta, "0.000")}", delta >= 0f ? IdentityGreen : IdentityOrange));
				if (++count >= 6) break;
			}
		}
		if (count == 0)
			box.AddChild(NewLabel("Chưa có trajectory event để hiển thị.", 10, IdentityMuted, false));
	}

	private void RenderAuditAuthority(JsonElement runtime)
	{
		JsonElement authority = Obj(runtime, "authority");
		var card = NewIdentityCard("Ai được phép ghi gì", "", "◇", IdentityPink, 108f);
		_depthContentList.AddChild(card);
		var box = (VBoxContainer)card.GetChild(0);
		var row = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		row.AddThemeConstantOverride("separation", 8);
		box.AddChild(row);
		row.AddChild(NewAuditAuthorityCell("Observer", ReadString(authority, "observer"), IdentityBlue));
		row.AddChild(NewAuditAuthorityCell("Reducer", ReadString(authority, "reducer"), IdentityGreen));
		row.AddChild(NewAuditAuthorityCell("Ledger", ReadString(authority, "ledger"), IdentityPink));
		row.AddChild(NewAuditAuthorityCell("Imprint", ReadString(authority, "imprint"), IdentityPurple));
	}

	private Control NewAuditAuthorityCell(string title, string detail, Color accent)
	{
		var panel = new PanelContainer { CustomMinimumSize = new Vector2(220f, 50f), SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		panel.AddThemeStyleboxOverride("panel", IdentitySoftPanelStyle(accent, 0.018f, 14));
		var box = new VBoxContainer();
		box.AddChild(NewLabel(title, 10, accent, true));
		var label = NewLabel(detail, 9, IdentityMuted, false);
		label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(label);
		panel.AddChild(box);
		return panel;
	}

	private Label NewAuditMiniChip(string text, Color accent)
	{
		var chip = NewLabel(text, 9, accent, true);
		chip.CustomMinimumSize = new Vector2(0f, 25f);
		chip.SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin;
		chip.HorizontalAlignment = HorizontalAlignment.Center;
		chip.VerticalAlignment = VerticalAlignment.Center;
		chip.AddThemeStyleboxOverride("normal", IdentitySoftPanelStyle(accent, 0.035f, 12));
		return chip;
	}

	private static Color AuditDimensionColor(string dimension, float delta)
	{
		return (dimension ?? "").Trim().ToLowerInvariant() switch
		{
			"value" => IdentityPink,
			"motive" => IdentityPurple,
			"defense" => IdentityBlue,
			"belief" => new Color(0.74f, 0.36f, 0.74f, 1f),
			"relationship" => new Color(0.87f, 0.38f, 0.58f, 1f),
			"imprint" => IdentityOrange,
			"strategy_outcome" => delta >= 0f ? IdentityGreen : IdentityOrange,
			_ => delta >= 0f ? IdentityGreen : IdentityOrange,
		};
	}

	private static string AuditOutcomeText(JsonElement item)
	{
		string outcome = ReadString(item, "outcome");
		string status = ReadString(item, "status");
		if (!string.IsNullOrWhiteSpace(outcome)) return "Outcome: " + PrettyAuditKey(outcome);
		if (!string.IsNullOrWhiteSpace(status)) return "Trạng thái: " + PrettyAuditKey(status);
		return "Entry không thay đổi score trực tiếp.";
	}

	private static string PrettyImprintStatus(string status)
	{
		return (status ?? "").Trim().ToLowerInvariant() switch
		{
			"deep" => "Đã ăn sâu",
			"formed" => "Đã thành hình",
			"forming" => "Đang hằn lại",
			_ => "Dấu in",
		};
	}

	private static string PrettyAuditKey(string key)
	{
		if (string.IsNullOrWhiteSpace(key)) return "";
		return PrettyIdentityKey(key);
	}

	private static string SignedAudit(float value, string format)
	{
		if (Math.Abs(value) < 0.0000005f) return "0";
		return value > 0f ? "+" + value.ToString(format, CultureInfo.InvariantCulture) : value.ToString(format, CultureInfo.InvariantCulture);
	}

	private void AddScoreCard(string title, JsonElement array, string keyName, string valueName, int limit)
	{
		var lines = new List<string>();
		if (array.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement item in array.EnumerateArray())
			{
				lines.Add($"{Pretty(ReadString(item, keyName))}  {ReadFloat(item, valueName):0.00}");
				if (lines.Count >= limit) break;
			}
		}
		AddInfoCard(title, lines);
	}

	private void AddInfoCard(string title, IEnumerable<string> rawLines)
	{
		if (_depthContentList == null)
			return;
		var card = new PanelContainer();
		card.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = IdentityCard,
			BorderColor = IdentityBorder,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 14,
			CornerRadiusTopRight = 14,
			CornerRadiusBottomLeft = 14,
			CornerRadiusBottomRight = 14,
			ContentMarginLeft = 16f,
			ContentMarginTop = 12f,
			ContentMarginRight = 16f,
			ContentMarginBottom = 12f,
		});
		_depthContentList.AddChild(card);
		var box = new VBoxContainer();
		box.AddThemeConstantOverride("separation", 5);
		card.AddChild(box);
		box.AddChild(NewLabel(title, 16, IdentityInk, true));
		int count = 0;
		foreach (string raw in rawLines ?? Array.Empty<string>())
		{
			string line = (raw ?? "").Trim();
			if (string.IsNullOrWhiteSpace(line)) continue;
			var label = NewLabel(line, 12, IdentityMuted, false);
			label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
			box.AddChild(label);
			count++;
		}
		if (count == 0)
			box.AddChild(NewLabel("Chưa có dữ liệu đủ tin cậy.", 12, IdentityMuted, false));
	}

	private static List<string> FormatObjectArray(JsonElement array, string[] fields, int limit)
	{
		var lines = new List<string>();
		if (array.ValueKind != JsonValueKind.Array)
			return lines;
		foreach (JsonElement item in array.EnumerateArray())
		{
			var parts = new List<string>();
			foreach (string field in fields)
			{
				if (!item.TryGetProperty(field, out JsonElement value)) continue;
				string text = value.ValueKind switch
				{
					JsonValueKind.String => value.GetString() ?? "",
					JsonValueKind.Number => ReadNumber(value).ToString("0.00"),
					JsonValueKind.True => "true",
					JsonValueKind.False => "false",
					_ => "",
				};
				if (!string.IsNullOrWhiteSpace(text)) parts.Add($"{field}: {text}");
			}
			if (parts.Count > 0) lines.Add(string.Join(" · ", parts));
			if (lines.Count >= limit) break;
		}
		return lines;
	}

	private Label NewLabel(string text, int size, Color color, bool bold)
	{
		var label = new Label
		{
			Text = text ?? "",
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		label.AddThemeFontSizeOverride("font_size", size);
		label.AddThemeColorOverride("font_color", color);
		return label;
	}

	private Label NewChip(string text, Color bg, Color ink)
	{
		var label = NewLabel(text, 10, ink, true);
		label.HorizontalAlignment = HorizontalAlignment.Center;
		label.VerticalAlignment = VerticalAlignment.Center;
		label.CustomMinimumSize = new Vector2(112f, 32f);
		label.SizeFlagsHorizontal = Control.SizeFlags.ShrinkEnd;
		label.ClipText = true;
		label.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		label.AddThemeStyleboxOverride("normal", new StyleBoxFlat
		{
			BgColor = new Color(bg.R, bg.G, bg.B, 0.62f),
			BorderColor = new Color(ink.R, ink.G, ink.B, 0.32f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 8,
			CornerRadiusTopRight = 8,
			CornerRadiusBottomLeft = 8,
			CornerRadiusBottomRight = 8,
		});
		return label;
	}

	private Button NewButton(string text, Action action, bool danger)
	{
		var button = new Button
		{
			Text = text,
			CustomMinimumSize = new Vector2(126f, 38f),
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
		};
		button.AddThemeFontSizeOverride("font_size", 11);
		Color bg = danger ? new Color(1f, 0.91f, 0.92f, 1f) : new Color(1f, 0.96f, 0.98f, 1f);
		Color border = danger ? new Color(0.78f, 0.29f, 0.34f, 1f) : IdentityBorder;
		button.AddThemeStyleboxOverride("normal", ButtonStyle(bg, border));
		button.AddThemeStyleboxOverride("hover", ButtonStyle(IdentityAccentSoft, IdentityAccent));
		button.AddThemeStyleboxOverride("pressed", ButtonStyle(new Color(0.92f, 0.79f, 0.86f, 1f), IdentityAccent));
		button.AddThemeColorOverride("font_color", danger ? new Color(0.68f, 0.20f, 0.25f, 1f) : IdentityInk);
		button.Pressed += action;
		return button;
	}

	private static StyleBoxFlat ButtonStyle(Color bg, Color border) => new()
	{
		BgColor = bg,
		BorderColor = border,
		BorderWidthLeft = 1,
		BorderWidthTop = 1,
		BorderWidthRight = 1,
		BorderWidthBottom = 1,
		CornerRadiusTopLeft = 10,
		CornerRadiusTopRight = 10,
		CornerRadiusBottomLeft = 10,
		CornerRadiusBottomRight = 10,
		ContentMarginLeft = 10f,
		ContentMarginRight = 10f,
		ContentMarginTop = 4f,
		ContentMarginBottom = 4f,
	};

	private static JsonElement Obj(JsonElement root, string key)
	{
		return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(key, out JsonElement value) && value.ValueKind == JsonValueKind.Object
			? value
			: default;
	}

	private static JsonElement Arr(JsonElement root, string key)
	{
		return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(key, out JsonElement value) && value.ValueKind == JsonValueKind.Array
			? value
			: default;
	}

	private static string ReadString(JsonElement root, string key)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)) return "";
		return value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.ToString();
	}

	private static float ReadFloat(JsonElement root, string key)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)) return 0f;
		return (float)ReadNumber(value);
	}

	private static double ReadNumber(JsonElement value)
	{
		if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out double number)) return number;
		if (value.ValueKind == JsonValueKind.String && double.TryParse(value.GetString(), out number)) return number;
		return 0.0;
	}

	private static int ReadInt(JsonElement root, string key) => (int)Math.Round(ReadFloat(root, key));

	private static bool ReadBool(JsonElement root, string key, bool fallback)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)) return fallback;
		if (value.ValueKind == JsonValueKind.True) return true;
		if (value.ValueKind == JsonValueKind.False) return false;
		return fallback;
	}

	private static string JoinArray(JsonElement root, string key, int limit)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value) || value.ValueKind != JsonValueKind.Array)
			return "—";
		var items = new List<string>();
		foreach (JsonElement item in value.EnumerateArray())
		{
			if (item.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(item.GetString()))
				items.Add(item.GetString());
			if (items.Count >= limit) break;
		}
		return items.Count > 0 ? string.Join(", ", items) : "—";
	}

	private static string Pretty(string key) => string.IsNullOrWhiteSpace(key)
		? "—"
		: key.Replace("_", " ").Replace(".", " › ");
}
