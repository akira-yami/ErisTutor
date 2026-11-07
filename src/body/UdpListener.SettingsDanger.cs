using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

public partial class UdpListener : Node2D
{
	// Danger Zone là command center bảo trì, không phải một bãi toggle.
	// UI chỉ hiện hành động theo cấp độ: an toàn, reset phiên, xóa dài hạn và đặt lại toàn bộ.
	// Mọi thao tác đi qua preview của Brain trước khi execute để Body không tự đoán phạm vi dữ liệu.
	private Label _dangerCatalogStatusLabel;
	private Label _dangerBackupStatusLabel;
	private Label _dangerVocalCacheLabel;
	private Label _dangerSemanticIndexLabel;
	private ConfirmationDialog _dangerConfirmDialog;
	private Label _dangerConfirmSummaryLabel;
	private Label _dangerConfirmPhraseHintLabel;
	private LineEdit _dangerConfirmInput;
	private Button _dangerConfirmButton;
	private string _dangerBackupRoot = "";
	private string _dangerPendingAction = "";
	private string _dangerPendingToken = "";
	private string _dangerPendingPhrase = "";
	private string _dangerPendingConfirmationMode = "typed";
	private string _dangerPendingConfirmLabel = "Xác nhận";
	private string _dangerPendingSeverity = "danger";
	private JsonObject _dangerPendingOptions = new();
	private bool _dangerPreviewInFlight = false;
	private string _dangerPreviewKey = "";
	private long _dangerPreviewRequestedAtMs = 0L;
	private long _dangerPendingExpiresAtMs = 0L;
	private const long DangerPreviewDebounceMs = 1500L;

	private static readonly Color DangerRed = new(0.72f, 0.12f, 0.22f);
	private static readonly Color DangerRedDeep = new(0.62f, 0.06f, 0.16f);
	private static readonly Color DangerRedSoft = new(1.00f, 0.91f, 0.93f, 0.94f);
	private static readonly Color DangerAmber = new(0.67f, 0.35f, 0.05f);
	private static readonly Color DangerAmberSoft = new(1.00f, 0.96f, 0.87f, 0.94f);
	private static readonly Color DangerSafe = new(0.46f, 0.18f, 0.34f);
	private static readonly Color DangerSafeSoft = new(1.00f, 0.98f, 0.99f, 0.94f);
	private static readonly Color DangerSuccess = new(0.18f, 0.48f, 0.30f);

	private void ResetDangerSettingsUiReferences()
	{
		_dangerCatalogStatusLabel = null;
		_dangerBackupStatusLabel = null;
		_dangerVocalCacheLabel = null;
		_dangerSemanticIndexLabel = null;
		_dangerConfirmDialog = null;
		_dangerConfirmSummaryLabel = null;
		_dangerConfirmPhraseHintLabel = null;
		_dangerConfirmInput = null;
		_dangerConfirmButton = null;
		_dangerBackupRoot = "";
		_dangerPreviewInFlight = false;
		_dangerPreviewKey = "";
		_dangerPreviewRequestedAtMs = 0L;
		_dangerPendingExpiresAtMs = 0L;
	}

	private void BuildDangerSettingsPage(Control root)
	{
		var intro = CreateUiLabel(
			"Bảo trì an toàn nằm riêng. Chỉ thao tác xóa dữ liệu dài hạn mới dùng nút đỏ và yêu cầu nhập xác nhận.",
			11,
			DangerAmber);
		intro.Position = new Vector2(44f, 204f);
		intro.Size = new Vector2(1088f, 22f);
		intro.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		root.AddChild(intro);

		BuildDangerStatusStrip(root);

		var safeCard = AddSettingsCard(root, new Rect2(32f, 292f, 548f, 310f), "Bảo trì & trạng thái hiện tại");
		_dangerVocalCacheLabel = AddDangerMaintenanceTile(
			safeCard,
			new Vector2(18f, 50f),
			"Dọn cache giọng",
			"Đang đọc dung lượng cache…",
			"clear_vocal_cache",
			"Dọn cache");
		_dangerSemanticIndexLabel = AddDangerMaintenanceTile(
			safeCard,
			new Vector2(280f, 50f),
			"Dựng lại semantic index",
			"Đang kiểm tra embedding backend…",
			"reindex_semantic_memory",
			"Dựng lại");
		AddDangerMaintenanceTile(
			safeCard,
			new Vector2(18f, 116f),
			"Hủy tác vụ đang chờ",
			"Dừng TTS, future nền và queue chưa phát.",
			"cancel_pending",
			"Hủy tác vụ");
		AddDangerMaintenanceTile(
			safeCard,
			new Vector2(280f, 116f),
			"Xuất backup ZIP",
			"Đóng gói memory hiện tại, không sửa file nguồn.",
			"export_backup",
			"Xuất backup");

		AddDangerSectionDivider(safeCard, 184f, "Đặt lại trạng thái hiện tại");
		AddDangerResetRow(
			safeCard,
			214f,
			"Làm mới trò chuyện",
			"Xóa context của phiên, giữ nguyên ký ức dài hạn.",
			"refresh_conversation",
			"Làm mới");
		AddDangerResetRow(
			safeCard,
			248f,
			"Reset trạng thái phiên",
			"Đưa cảm xúc và quan hệ runtime về baseline.",
			"reset_session_state",
			"Reset phiên");
		AddDangerResetRow(
			safeCard,
			282f,
			"Reset nội tâm hiện tại",
			"Xóa dòng suy nghĩ, bận tâm và private-life state hiện tại.",
			"reset_inner_state",
			"Reset nội tâm");

		var persistentCard = AddSettingsCard(root, new Rect2(598f, 292f, 550f, 310f), "Ký ức dài hạn");
		var persistentHint = CreateUiLabel("Mỗi thao tác bên dưới tự tạo backup trước khi thay đổi dữ liệu.", 10, SettingsInkSoft);
		persistentHint.Position = new Vector2(18f, 44f);
		persistentHint.Size = new Vector2(500f, 20f);
		persistentCard.AddChild(persistentHint);

		AddDangerPersistentRow(
			persistentCard,
			70f,
			"Quên mô hình về bạn",
			"Xóa user model, ToM và điều Eris tin rằng đã biết về bạn.",
			"reset_user_model",
			"Quên mô hình");
		AddDangerPersistentRow(
			persistentCard,
			132f,
			"Xóa quan hệ dài hạn",
			"Xóa mức độ thân thiết, residue cảm xúc và anchor quan hệ.",
			"reset_long_term_relationship",
			"Xóa quan hệ");
		AddDangerPersistentRow(
			persistentCard,
			194f,
			"Xóa repair & xung đột",
			"Xóa các mốc hiểu lầm, đổ vỡ và lịch sử từng sửa chữa.",
			"clear_repair_history",
			"Xóa lịch sử");

		AddDangerFactoryResetCard(persistentCard, 256f);

		_settingsStatusNoteLabel = CreateUiLabel("", 10, DangerRed);
		_settingsStatusNoteLabel.Position = new Vector2(44f, 617f);
		_settingsStatusNoteLabel.Size = new Vector2(920f, 24f);
		_settingsStatusNoteLabel.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		root.AddChild(_settingsStatusNoteLabel);

		var close = CreateSettingsButton("Đóng", CloseSettingsPanel, primary: false);
		close.Position = new Vector2(1044f, 614f);
		close.Size = new Vector2(104f, 38f);
		root.AddChild(close);

		BuildDangerConfirmationDialog();
	}

	private void BuildDangerStatusStrip(Control root)
	{
		var statusPanel = new Panel
		{
			Position = new Vector2(32f, 230f),
			Size = new Vector2(1116f, 50f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		var style = new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.985f, 0.992f, 0.96f),
			BorderColor = new Color(0.95f, 0.64f, 0.76f, 0.58f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 14,
			CornerRadiusTopRight = 14,
			CornerRadiusBottomLeft = 14,
			CornerRadiusBottomRight = 14,
		};
		statusPanel.AddThemeStyleboxOverride("panel", style);
		root.AddChild(statusPanel);

		var dot = CreateUiLabel("●", 12, DangerSuccess);
		dot.Position = new Vector2(18f, 9f);
		dot.Size = new Vector2(18f, 20f);
		statusPanel.AddChild(dot);

		_dangerCatalogStatusLabel = CreateUiLabel("Đang kết nối maintenance service…", 11, SettingsInk);
		_dangerCatalogStatusLabel.Position = new Vector2(38f, 7f);
		_dangerCatalogStatusLabel.Size = new Vector2(420f, 20f);
		statusPanel.AddChild(_dangerCatalogStatusLabel);

		_dangerBackupStatusLabel = CreateUiLabel("Backup gần nhất: đang đọc…", 9, SettingsInkSoft);
		_dangerBackupStatusLabel.Position = new Vector2(38f, 26f);
		_dangerBackupStatusLabel.Size = new Vector2(660f, 18f);
		statusPanel.AddChild(_dangerBackupStatusLabel);

		var guarantee = CreateUiLabel("Xóa sâu luôn preview và backup trước.", 9, DangerAmber);
		guarantee.Position = new Vector2(474f, 9f);
		guarantee.Size = new Vector2(330f, 20f);
		guarantee.HorizontalAlignment = HorizontalAlignment.Right;
		statusPanel.AddChild(guarantee);

		var refresh = CreateSettingsCompactButton("↻ Đọc lại", RequestMaintenanceCatalog, 9);
		refresh.Position = new Vector2(846f, 9f);
		refresh.Size = new Vector2(112f, 32f);
		statusPanel.AddChild(refresh);

		var openBackup = CreateSettingsCompactButton("Mở backup", OpenDangerBackupDirectory, 9);
		openBackup.Position = new Vector2(968f, 9f);
		openBackup.Size = new Vector2(130f, 32f);
		statusPanel.AddChild(openBackup);
	}

	private Label AddDangerMaintenanceTile(
		Control parent,
		Vector2 position,
		string title,
		string description,
		string action,
		string buttonText)
	{
		var tile = new Panel
		{
			Position = position,
			Size = new Vector2(250f, 58f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		var style = new StyleBoxFlat
		{
			BgColor = DangerSafeSoft,
			BorderColor = new Color(0.92f, 0.70f, 0.80f, 0.62f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 11,
			CornerRadiusTopRight = 11,
			CornerRadiusBottomLeft = 11,
			CornerRadiusBottomRight = 11,
		};
		tile.AddThemeStyleboxOverride("panel", style);
		parent.AddChild(tile);

		var titleLabel = CreateUiLabel(title, 11, SettingsInk);
		titleLabel.Position = new Vector2(12f, 8f);
		titleLabel.Size = new Vector2(140f, 18f);
		tile.AddChild(titleLabel);

		var descriptionLabel = CreateUiLabel(description, 8, SettingsInkSoft);
		descriptionLabel.Position = new Vector2(12f, 28f);
		descriptionLabel.Size = new Vector2(148f, 25f);
		descriptionLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		tile.AddChild(descriptionLabel);

		var button = CreateDangerActionButton(buttonText, () => RequestMaintenancePreview(action), "safe");
		button.Position = new Vector2(162f, 15f);
		button.Size = new Vector2(78f, 30f);
		tile.AddChild(button);
		return descriptionLabel;
	}

	private void AddDangerSectionDivider(Control parent, float y, string title)
	{
		var divider = new ColorRect
		{
			Color = new Color(0.92f, 0.65f, 0.76f, 0.38f),
			Position = new Vector2(18f, y),
			Size = new Vector2(512f, 1f),
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		parent.AddChild(divider);
		var label = CreateUiLabel(title, 10, DangerAmber);
		label.Position = new Vector2(18f, y + 7f);
		label.Size = new Vector2(300f, 18f);
		parent.AddChild(label);
	}

	private void AddDangerResetRow(
		Control parent,
		float y,
		string title,
		string description,
		string action,
		string buttonText)
	{
		var titleLabel = CreateUiLabel(title, 10, SettingsInk);
		titleLabel.Position = new Vector2(18f, y + 5f);
		titleLabel.Size = new Vector2(145f, 18f);
		parent.AddChild(titleLabel);

		var descriptionLabel = CreateUiLabel(description, 8, SettingsInkSoft);
		descriptionLabel.Position = new Vector2(166f, y + 5f);
		descriptionLabel.Size = new Vector2(252f, 20f);
		descriptionLabel.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		parent.AddChild(descriptionLabel);

		var button = CreateDangerActionButton(buttonText, () => RequestMaintenancePreview(action), "warning");
		button.Position = new Vector2(432f, y);
		button.Size = new Vector2(98f, 28f);
		parent.AddChild(button);
	}

	private void AddDangerPersistentRow(
		Control parent,
		float y,
		string title,
		string description,
		string action,
		string buttonText)
	{
		var row = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(514f, 54f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		var style = new StyleBoxFlat
		{
			BgColor = DangerRedSoft,
			BorderColor = new Color(0.91f, 0.42f, 0.51f, 0.64f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 11,
			CornerRadiusTopRight = 11,
			CornerRadiusBottomLeft = 11,
			CornerRadiusBottomRight = 11,
		};
		row.AddThemeStyleboxOverride("panel", style);
		parent.AddChild(row);

		var titleLabel = CreateUiLabel(title, 11, DangerRedDeep);
		titleLabel.Position = new Vector2(12f, 7f);
		titleLabel.Size = new Vector2(250f, 19f);
		row.AddChild(titleLabel);

		var backupBadge = CreateUiLabel("BACKUP", 7, DangerAmber);
		backupBadge.Position = new Vector2(268f, 7f);
		backupBadge.Size = new Vector2(58f, 16f);
		backupBadge.HorizontalAlignment = HorizontalAlignment.Center;
		row.AddChild(backupBadge);

		var descriptionLabel = CreateUiLabel(description, 8, SettingsInkSoft);
		descriptionLabel.Position = new Vector2(12f, 28f);
		descriptionLabel.Size = new Vector2(366f, 18f);
		descriptionLabel.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		row.AddChild(descriptionLabel);

		var button = CreateDangerActionButton(buttonText, () => RequestMaintenancePreview(action), "danger");
		button.Position = new Vector2(388f, 11f);
		button.Size = new Vector2(114f, 32f);
		row.AddChild(button);
	}

	private void AddDangerFactoryResetCard(Control parent, float y)
	{
		var panel = new Panel
		{
			Position = new Vector2(18f, y),
			Size = new Vector2(514f, 44f),
			MouseFilter = Control.MouseFilterEnum.Stop,
		};
		var style = new StyleBoxFlat
		{
			BgColor = new Color(0.76f, 0.10f, 0.22f, 0.94f),
			BorderColor = new Color(0.97f, 0.55f, 0.64f, 0.92f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 11,
			CornerRadiusTopRight = 11,
			CornerRadiusBottomLeft = 11,
			CornerRadiusBottomRight = 11,
		};
		panel.AddThemeStyleboxOverride("panel", style);
		parent.AddChild(panel);

		var title = CreateUiLabel("Đặt lại toàn bộ Eris", 11, Colors.White);
		title.Position = new Vector2(12f, 5f);
		title.Size = new Vector2(210f, 18f);
		panel.AddChild(title);

		var desc = CreateUiLabel("Không xóa API key, settings, raw event ledger hoặc backup.", 8, new Color(1f, 0.88f, 0.92f));
		desc.Position = new Vector2(12f, 23f);
		desc.Size = new Vector2(344f, 16f);
		desc.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
		panel.AddChild(desc);

		var button = CreateDangerActionButton("Xem phạm vi", () => RequestMaintenancePreview("factory_reset"), "critical");
		button.Position = new Vector2(386f, 7f);
		button.Size = new Vector2(116f, 30f);
		panel.AddChild(button);
	}

	private Button CreateDangerActionButton(string text, Action onPressed, string severity)
	{
		var button = CreateSettingsButton(text, onPressed, primary: false);
		string normalized = (severity ?? "safe").Trim().ToLowerInvariant();
		Color background;
		Color border;
		Color foreground;
		Color hoverBackground;

		switch (normalized)
		{
			case "critical":
				background = new Color(0.55f, 0.03f, 0.12f, 0.98f);
				border = new Color(1f, 0.66f, 0.72f, 0.98f);
				foreground = Colors.White;
				hoverBackground = new Color(0.66f, 0.05f, 0.16f, 1f);
				break;
			case "danger":
				background = new Color(0.74f, 0.12f, 0.22f, 0.94f);
				border = new Color(0.98f, 0.47f, 0.55f, 0.92f);
				foreground = Colors.White;
				hoverBackground = new Color(0.84f, 0.16f, 0.28f, 0.98f);
				break;
			case "warning":
				background = DangerAmberSoft;
				border = new Color(0.88f, 0.60f, 0.22f, 0.84f);
				foreground = DangerAmber;
				hoverBackground = new Color(1f, 0.91f, 0.74f, 0.98f);
				break;
			default:
				background = DangerSafeSoft;
				border = new Color(0.90f, 0.55f, 0.70f, 0.74f);
				foreground = DangerSafe;
				hoverBackground = new Color(1f, 0.92f, 0.96f, 0.98f);
				break;
		}

		var normal = new StyleBoxFlat
		{
			BgColor = background,
			BorderColor = border,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 9,
			CornerRadiusTopRight = 9,
			CornerRadiusBottomLeft = 9,
			CornerRadiusBottomRight = 9,
			ContentMarginLeft = 4f,
			ContentMarginRight = 4f,
			ContentMarginTop = 2f,
			ContentMarginBottom = 2f,
		};
		var hover = (StyleBoxFlat)normal.Duplicate();
		hover.BgColor = hoverBackground;
		button.AddThemeStyleboxOverride("normal", normal);
		button.AddThemeStyleboxOverride("hover", hover);
		button.AddThemeStyleboxOverride("pressed", hover);
		button.AddThemeStyleboxOverride("focus", hover);
		button.AddThemeColorOverride("font_color", foreground);
		button.AddThemeColorOverride("font_hover_color", foreground);
		button.AddThemeColorOverride("font_pressed_color", foreground);
		button.AddThemeFontSizeOverride("font_size", 9);
		return button;
	}

	private void BuildDangerConfirmationDialog()
	{
		_dangerConfirmDialog = new ConfirmationDialog
		{
			Title = "Xác nhận thao tác",
			Size = new Vector2I(640, 360),
			MinSize = new Vector2I(640, 360),
			OkButtonText = "Xác nhận",
			CancelButtonText = "Hủy",
		};
		_dangerConfirmDialog.Confirmed += ExecutePendingMaintenance;
		_settingsPanel.AddChild(_dangerConfirmDialog);

		var content = new VBoxContainer
		{
			Position = new Vector2(24f, 58f),
			Size = new Vector2(592f, 220f),
		};
		content.AddThemeConstantOverride("separation", 10);
		_dangerConfirmDialog.AddChild(content);

		_dangerConfirmSummaryLabel = CreateUiLabel("", 12, SettingsInk);
		_dangerConfirmSummaryLabel.CustomMinimumSize = new Vector2(580f, 132f);
		_dangerConfirmSummaryLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		content.AddChild(_dangerConfirmSummaryLabel);

		_dangerConfirmPhraseHintLabel = CreateUiLabel("", 10, DangerRed);
		_dangerConfirmPhraseHintLabel.CustomMinimumSize = new Vector2(580f, 24f);
		content.AddChild(_dangerConfirmPhraseHintLabel);

		_dangerConfirmInput = new LineEdit
		{
			PlaceholderText = "Nhập câu xác nhận",
			CustomMinimumSize = new Vector2(580f, 38f),
		};
		_dangerConfirmInput.TextChanged += _ => RefreshDangerConfirmButton();
		content.AddChild(_dangerConfirmInput);

		_dangerConfirmButton = _dangerConfirmDialog.GetOkButton();
		RefreshDangerConfirmButton();
	}

	private void RequestMaintenanceCatalog()
	{
		if (_chatSend == null || _processes == null || !_processes.IsVisionRunning)
		{
			if (_dangerCatalogStatusLabel != null)
				_dangerCatalogStatusLabel.Text = "Brain đang dừng · maintenance chưa sẵn sàng";
			SetSettingsStatus("Brain đang dừng; không thể đọc maintenance catalog.");
			return;
		}
		var payload = new { type = "maintenance_catalog", request_id = Guid.NewGuid().ToString("N") };
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		if (error == Error.Ok)
		{
			if (_dangerCatalogStatusLabel != null)
				_dangerCatalogStatusLabel.Text = "Đang đọc trạng thái maintenance từ Brain…";
		}
		else
			SetSettingsStatus($"Không gửi được maintenance_catalog: {error}");
	}

	private void RequestMaintenancePreview(string action)
	{
		if (_chatSend == null || _processes == null || !_processes.IsVisionRunning)
		{
			SetSettingsStatus("Brain đang dừng. Khởi động Brain trước để backend khóa và đồng bộ store.");
			return;
		}

		JsonObject options = BuildMaintenanceOptions(action);
		string previewKey = $"{action}|{options.ToJsonString()}";
		long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

		// Reuse a still-valid manifest instead of asking Brain to mint another
		// confirmation token for the exact same destructive action.
		if (!string.IsNullOrWhiteSpace(_dangerPendingToken)
			&& string.Equals(_dangerPendingAction, action, StringComparison.OrdinalIgnoreCase)
			&& string.Equals(_dangerPreviewKey, previewKey, StringComparison.Ordinal)
			&& _dangerPendingExpiresAtMs > nowMs)
		{
			_dangerConfirmDialog?.PopupCentered();
			SetSettingsStatus("Đang dùng lại preview còn hiệu lực từ Brain.");
			return;
		}

		if (string.Equals(_dangerPreviewKey, previewKey, StringComparison.Ordinal)
			&& ((_dangerPreviewInFlight && nowMs - _dangerPreviewRequestedAtMs < 5000L)
				|| nowMs - _dangerPreviewRequestedAtMs < DangerPreviewDebounceMs))
		{
			SetSettingsStatus("Preview cùng phạm vi đang được xử lý…");
			return;
		}

		string requestId = Guid.NewGuid().ToString("N");
		var payload = new JsonObject
		{
			["type"] = "maintenance_request",
			["request_id"] = requestId,
			["action"] = action,
			["dry_run"] = true,
			["options"] = options.DeepClone(),
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(payload.ToJsonString()));
		if (error == Error.Ok)
		{
			_dangerPreviewInFlight = true;
			_dangerPreviewKey = previewKey;
			_dangerPreviewRequestedAtMs = nowMs;
		}
		else
		{
			_dangerPreviewInFlight = false;
		}
		SetSettingsStatus(error == Error.Ok ? "Đang đọc phạm vi ảnh hưởng từ Brain…" : $"Không gửi được preview: {error}");
	}

	private JsonObject BuildMaintenanceOptions(string action)
	{
		if (string.Equals(action, "factory_reset", StringComparison.OrdinalIgnoreCase))
		{
			// Đặt lại toàn bộ mặc định giữ settings, key, raw event ledger và backup.
			return new JsonObject
			{
				["components"] = new JsonArray(
					"refresh_conversation",
					"reset_session_affect",
					"reset_user_model",
					"reset_long_term_relationship",
					"clear_repair_history",
					"reset_private_mind",
					"reset_private_life",
					"clear_vocal_cache")
			};
		}
		return new JsonObject();
	}

	private void TryApplyMaintenanceMessage(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw) || !raw.Contains("maintenance_", StringComparison.Ordinal))
			return;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;
			if (root.TryGetProperty("maintenance_catalog_ack", out JsonElement catalog) && catalog.ValueKind == JsonValueKind.Object)
			{
				int actions = catalog.TryGetProperty("actions", out JsonElement actionsEl) && actionsEl.ValueKind == JsonValueKind.Array ? actionsEl.GetArrayLength() : 0;
				if (_dangerCatalogStatusLabel != null)
					_dangerCatalogStatusLabel.Text = $"Brain sẵn sàng · {actions} thao tác có backend thật";

				if (catalog.TryGetProperty("backup_root", out JsonElement backupRootEl))
					_dangerBackupRoot = backupRootEl.GetString() ?? "";
				if (catalog.TryGetProperty("backup_summary", out JsonElement backupSummary) && backupSummary.ValueKind == JsonValueKind.Object)
					ApplyDangerBackupSummary(backupSummary);

				if (catalog.TryGetProperty("semantic_index", out JsonElement semantic) && semantic.ValueKind == JsonValueKind.Object)
				{
					bool backend = semantic.TryGetProperty("embedding_backend_ready", out JsonElement backendEl) && backendEl.ValueKind == JsonValueKind.True;
					bool supported = semantic.TryGetProperty("rebuild_supported", out JsonElement supportedEl) && supportedEl.ValueKind == JsonValueKind.True;
					if (_dangerSemanticIndexLabel != null)
						_dangerSemanticIndexLabel.Text = supported
							? (backend ? "Embedding backend sẵn sàng." : "Chưa có embedding backend.")
							: "Runtime không hỗ trợ rebuild.";
				}
				if (catalog.TryGetProperty("vocal_cache", out JsonElement cache) && cache.ValueKind == JsonValueKind.Object)
				{
					long bytes = cache.TryGetProperty("bytes", out JsonElement bytesEl) && bytesEl.TryGetInt64(out long parsed) ? parsed : 0L;
					int items = cache.TryGetProperty("items", out JsonElement itemsEl) && itemsEl.TryGetInt32(out int count) ? count : 0;
					if (_dangerVocalCacheLabel != null)
						_dangerVocalCacheLabel.Text = $"{FormatDangerBytes(bytes)} · {items} file cache.";
				}
				return;
			}

			if ((root.TryGetProperty("maintenance_preview_ack", out JsonElement preview)
				|| root.TryGetProperty("maintenance_preview", out preview))
				&& preview.ValueKind == JsonValueKind.Object)
			{
				RunLogger.LogStructured("DangerZone.preview", preview.GetRawText());
				ShowMaintenancePreview(preview);
				return;
			}

			if ((root.TryGetProperty("maintenance_execute_ack", out JsonElement result)
				|| root.TryGetProperty("maintenance_result", out result))
				&& result.ValueKind == JsonValueKind.Object)
			{
				RunLogger.LogStructured("DangerZone.execute", result.GetRawText());
				bool ok = result.TryGetProperty("ok", out JsonElement okEl) && okEl.ValueKind == JsonValueKind.True;
				string action = result.TryGetProperty("action", out JsonElement actionEl) ? actionEl.GetString() ?? "maintenance" : "maintenance";
				int changed = result.TryGetProperty("changed", out JsonElement changedEl) && changedEl.ValueKind == JsonValueKind.Array ? changedEl.GetArrayLength() : 0;
				int errors = result.TryGetProperty("errors", out JsonElement errorsEl) && errorsEl.ValueKind == JsonValueKind.Array ? errorsEl.GetArrayLength() : 0;
				string backup = result.TryGetProperty("backup_dir", out JsonElement backupEl) ? backupEl.GetString() ?? "" : "";
				if (result.TryGetProperty("details", out JsonElement details) && details.ValueKind == JsonValueKind.Object
					&& details.TryGetProperty("backup_path", out JsonElement backupPathEl))
					backup = backupPathEl.GetString() ?? backup;
				SetSettingsStatus(ok
					? $"Hoàn tất: {changed} thành phần{(string.IsNullOrWhiteSpace(backup) ? "" : " · đã tạo backup")}."
					: $"Thao tác lỗi: {errors} lỗi. Xem log để biết chi tiết.");
				if (ok)
					ApplyMaintenanceResultToUi(action, result);
				_dangerPreviewInFlight = false;
				_dangerPendingToken = "";
				_dangerPendingExpiresAtMs = 0L;
				RequestMaintenanceCatalog();
			}
		}
		catch (Exception ex)
		{
			RunLogger.LogErr($"[DangerZone] Khong parse duoc maintenance message: {ex.Message}");
		}
	}

	private void ApplyDangerBackupSummary(JsonElement summary)
	{
		if (_dangerBackupStatusLabel == null)
			return;
		int count = summary.TryGetProperty("count", out JsonElement countEl) && countEl.TryGetInt32(out int parsedCount) ? parsedCount : 0;
		long totalBytes = summary.TryGetProperty("total_bytes", out JsonElement totalEl) && totalEl.TryGetInt64(out long parsedBytes) ? parsedBytes : 0L;
		if (!summary.TryGetProperty("latest", out JsonElement latest) || latest.ValueKind != JsonValueKind.Object)
		{
			_dangerBackupStatusLabel.Text = count > 0
				? $"Backup: {count} bản · {FormatDangerBytes(totalBytes)}"
				: "Backup gần nhất: chưa có bản backup nào";
			return;
		}

		string action = latest.TryGetProperty("action", out JsonElement actionEl) ? actionEl.GetString() ?? "backup" : "backup";
		double createdAt = latest.TryGetProperty("created_at", out JsonElement createdEl) && createdEl.TryGetDouble(out double unix) ? unix : 0d;
		string createdText = createdAt > 0d
			? DateTimeOffset.FromUnixTimeSeconds((long)createdAt).LocalDateTime.ToString("dd/MM HH:mm")
			: "không rõ thời gian";
		_dangerBackupStatusLabel.Text = $"Backup gần nhất: {createdText} · {DangerActionDisplayName(action)} · tổng {count} bản / {FormatDangerBytes(totalBytes)}";
	}

	private void ShowMaintenancePreview(JsonElement preview)
	{
		_dangerPreviewInFlight = false;
		_dangerPendingAction = preview.TryGetProperty("action", out JsonElement actionEl) ? actionEl.GetString() ?? "" : "";
		_dangerPendingToken = preview.TryGetProperty("confirmation_token", out JsonElement tokenEl) ? tokenEl.GetString() ?? "" : "";
		string title = preview.TryGetProperty("title", out JsonElement titleEl) ? titleEl.GetString() ?? _dangerPendingAction : _dangerPendingAction;
		string description = preview.TryGetProperty("description", out JsonElement descEl) ? descEl.GetString() ?? "" : "";
		_dangerPendingSeverity = preview.TryGetProperty("severity", out JsonElement severityEl) ? severityEl.GetString() ?? "warning" : "warning";
		_dangerPendingConfirmationMode = preview.TryGetProperty("confirmation_mode", out JsonElement modeEl) ? modeEl.GetString() ?? "typed" : "typed";
		_dangerPendingConfirmLabel = preview.TryGetProperty("confirm_label", out JsonElement labelEl) ? labelEl.GetString() ?? "Xác nhận" : "Xác nhận";
		long totalBytes = preview.TryGetProperty("total_bytes", out JsonElement bytesEl) && bytesEl.TryGetInt64(out long parsed) ? parsed : 0L;
		int stores = preview.TryGetProperty("affected", out JsonElement affectedEl) && affectedEl.ValueKind == JsonValueKind.Array ? affectedEl.GetArrayLength() : 0;
		int items = 0;
		if (preview.TryGetProperty("summary", out JsonElement summary) && summary.ValueKind == JsonValueKind.Object
			&& summary.TryGetProperty("items_estimated", out JsonElement itemsEl) && itemsEl.TryGetInt32(out int parsedItems))
			items = parsedItems;
		bool reversible = preview.TryGetProperty("reversible", out JsonElement reversibleEl) && reversibleEl.ValueKind == JsonValueKind.True;
		bool requiresConfirmation = !preview.TryGetProperty("requires_confirmation", out JsonElement confirmEl) || confirmEl.ValueKind != JsonValueKind.False;
		int expires = preview.TryGetProperty("expires_in_seconds", out JsonElement expiresEl) && expiresEl.TryGetInt32(out int seconds) ? seconds : 120;
		_dangerPendingExpiresAtMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + Math.Max(1, expires) * 1000L;
		_dangerPendingOptions = preview.TryGetProperty("options", out JsonElement optionsEl) && optionsEl.ValueKind == JsonValueKind.Object
			? JsonNode.Parse(optionsEl.GetRawText())?.AsObject() ?? new JsonObject()
			: new JsonObject();

		_dangerPendingPhrase = preview.TryGetProperty("typed_confirmation", out JsonElement phraseEl)
			? phraseEl.GetString() ?? DangerConfirmationPhrase(_dangerPendingAction)
			: DangerConfirmationPhrase(_dangerPendingAction);
		if (!requiresConfirmation || string.Equals(_dangerPendingConfirmationMode, "none", StringComparison.OrdinalIgnoreCase))
		{
			SendPendingMaintenanceExecute(autoConfirm: true);
			return;
		}

		string impact = stores > 0
			? $"Phạm vi: {stores} kho dữ liệu · khoảng {items} mục · {FormatDangerBytes(totalBytes)}"
			: "Phạm vi: trạng thái runtime, không có file dữ liệu cố định.";
		string recovery = reversible
			? "Một bản backup sẽ được tạo trước khi thay đổi."
			: "Thao tác này không tạo backup vì dữ liệu có thể sinh lại hoặc chỉ tồn tại trong RAM.";
		if (_dangerConfirmSummaryLabel != null)
			_dangerConfirmSummaryLabel.Text = $"{title}\n{description}\n\n{impact}\n{recovery}\nPreview hết hạn sau {expires} giây.";

		bool typed = string.Equals(_dangerPendingConfirmationMode, "typed", StringComparison.OrdinalIgnoreCase);
		if (_dangerConfirmPhraseHintLabel != null)
		{
			_dangerConfirmPhraseHintLabel.Visible = typed;
			_dangerConfirmPhraseHintLabel.Text = typed ? $"Nhập chính xác: {_dangerPendingPhrase}" : "Kiểm tra phạm vi rồi xác nhận thao tác.";
		}
		if (_dangerConfirmInput != null)
		{
			_dangerConfirmInput.Visible = typed;
			_dangerConfirmInput.Text = "";
		}
		if (_dangerConfirmDialog != null)
			_dangerConfirmDialog.Title = title;
		StyleDangerConfirmButton(_dangerPendingSeverity, _dangerPendingConfirmLabel);
		RefreshDangerConfirmButton();
		_dangerConfirmDialog?.PopupCentered();
		SetSettingsStatus("Brain đã trả manifest. Kiểm tra phạm vi trước khi xác nhận.");
	}

	private void StyleDangerConfirmButton(string severity, string label)
	{
		if (_dangerConfirmButton == null)
			return;
		_dangerConfirmButton.Text = string.IsNullOrWhiteSpace(label) ? "Xác nhận" : label;
		string normalized = (severity ?? "warning").Trim().ToLowerInvariant();
		Color bg = normalized is "danger" or "critical" ? DangerRed : new Color(0.88f, 0.56f, 0.16f);
		Color hoverBg = normalized is "danger" or "critical" ? new Color(0.82f, 0.14f, 0.25f) : new Color(0.94f, 0.65f, 0.22f);
		var normal = new StyleBoxFlat
		{
			BgColor = bg,
			BorderColor = normalized == "critical" ? new Color(0.50f, 0.02f, 0.10f) : bg,
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 8,
			CornerRadiusTopRight = 8,
			CornerRadiusBottomLeft = 8,
			CornerRadiusBottomRight = 8,
		};
		var hover = (StyleBoxFlat)normal.Duplicate();
		hover.BgColor = hoverBg;
		_dangerConfirmButton.AddThemeStyleboxOverride("normal", normal);
		_dangerConfirmButton.AddThemeStyleboxOverride("hover", hover);
		_dangerConfirmButton.AddThemeStyleboxOverride("pressed", hover);
		_dangerConfirmButton.AddThemeColorOverride("font_color", Colors.White);
		_dangerConfirmButton.AddThemeColorOverride("font_hover_color", Colors.White);
	}

	private string DangerConfirmationPhrase(string action) => action switch
	{
		"factory_reset" => "ĐẶT LẠI TOÀN BỘ",
		"reset_user_model" => "QUÊN MÔ HÌNH",
		"reset_long_term_relationship" => "XÓA QUAN HỆ",
		"reset_private_mind" => "RESET NỘI TÂM",
		"reset_private_life" => "RESET ĐỜI SỐNG RIÊNG",
		"reset_inner_state" => "RESET NỘI TÂM",
		"clear_repair_history" => "XÓA XUNG ĐỘT",
		"reset_anchor_state" => "RESET ANCHOR",
		_ => "XÁC NHẬN",
	};

	private static string DangerActionDisplayName(string action) => action switch
	{
		"factory_reset" => "đặt lại toàn bộ",
		"reset_user_model" => "quên mô hình",
		"reset_long_term_relationship" => "xóa quan hệ",
		"clear_repair_history" => "xóa repair",
		"reset_private_mind" => "reset nội tâm",
		"reset_private_life" => "reset đời sống riêng",
		"export_backup" => "backup thủ công",
		_ => string.IsNullOrWhiteSpace(action) ? "backup" : action.Replace('_', ' '),
	};

	private void RefreshDangerConfirmButton()
	{
		if (_dangerConfirmButton == null)
			return;
		if (string.Equals(_dangerPendingConfirmationMode, "simple", StringComparison.OrdinalIgnoreCase))
		{
			_dangerConfirmButton.Disabled = string.IsNullOrWhiteSpace(_dangerPendingToken);
			return;
		}
		_dangerConfirmButton.Disabled = string.IsNullOrWhiteSpace(_dangerPendingToken)
			|| _dangerConfirmInput == null
			|| !string.Equals(_dangerConfirmInput.Text.Trim(), _dangerPendingPhrase, StringComparison.Ordinal);
	}

	private void ExecutePendingMaintenance()
	{
		SendPendingMaintenanceExecute(autoConfirm: false);
	}

	private void SendPendingMaintenanceExecute(bool autoConfirm)
	{
		if (_chatSend == null || string.IsNullOrWhiteSpace(_dangerPendingAction) || string.IsNullOrWhiteSpace(_dangerPendingToken))
			return;
		bool typed = string.Equals(_dangerPendingConfirmationMode, "typed", StringComparison.OrdinalIgnoreCase);
		if (typed && !autoConfirm && (_dangerConfirmInput == null || !string.Equals(_dangerConfirmInput.Text.Trim(), _dangerPendingPhrase, StringComparison.Ordinal)))
		{
			SetSettingsStatus("Câu xác nhận không khớp; thao tác chưa được gửi.");
			return;
		}

		string confirmation = typed && !autoConfirm
			? (_dangerConfirmInput?.Text.Trim() ?? "")
			: _dangerPendingPhrase;
		var payload = new JsonObject
		{
			["type"] = "maintenance_execute",
			["request_id"] = Guid.NewGuid().ToString("N"),
			["action"] = _dangerPendingAction,
			["dry_run"] = false,
			["preview_id"] = _dangerPendingToken,
			["confirmation_token"] = _dangerPendingToken,
			["typed_confirmation"] = confirmation,
			["options"] = _dangerPendingOptions.DeepClone(),
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(payload.ToJsonString()));
		SetSettingsStatus(error == Error.Ok ? "Đã gửi lệnh; Brain đang khóa và đồng bộ store…" : $"Không gửi được maintenance execute: {error}");
		_dangerPendingToken = "";
		RefreshDangerConfirmButton();
	}

	private void OpenDangerBackupDirectory()
	{
		try
		{
			string target = _dangerBackupRoot;
			if (string.IsNullOrWhiteSpace(target))
			{
				SetSettingsStatus("Chưa đọc được đường dẫn backup từ Brain. Bấm Đọc lại trước.");
				return;
			}
			Directory.CreateDirectory(target);
			OS.ShellOpen(target);
			SetSettingsStatus($"Đã mở thư mục backup: {target}");
		}
		catch (Exception ex)
		{
			SetSettingsStatus($"Không mở được backup: {ex.Message}");
		}
	}

	private static string FormatDangerBytes(long bytes)
	{
		if (bytes < 1024L) return $"{bytes} B";
		if (bytes < 1024L * 1024L) return $"{bytes / 1024d:0.0} KB";
		if (bytes < 1024L * 1024L * 1024L) return $"{bytes / (1024d * 1024d):0.0} MB";
		return $"{bytes / (1024d * 1024d * 1024d):0.00} GB";
	}
}
