using Godot;
using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;
using System.Threading;

public partial class UdpListener : Node2D
{
	// Companion capability panel
	// --------------------------
	// Panel này KHÔNG phải bảng điểm hay dashboard quản trị. Nó chỉ trình bày
	// working model mà ERIS đang dùng để chọn cách nói với user: phần nào có thể
	// nói như technical peer, phần nào nên guided, và phần nào vẫn chưa đủ evidence.
	// Toàn bộ correction/reset vẫn đi qua trusted runtime action; UI không tự sửa score.
	private static readonly Vector2 CapabilityPanelPreferredSize = new(920f, 700f);
	private static readonly Vector2 CapabilityPanelMinSize = new(820f, 600f);

	private enum CapabilityViewMode
	{
		Overview,
		Evidence,
	}

	private Panel _capabilityPanel;
	private Button _contextCapabilityButton;
	private VBoxContainer _capabilityContent;
	private Label _capabilityStatus;
	private Button _capabilityOverviewButton;
	private Button _capabilityEvidenceButton;
	private CapabilityViewMode _capabilityViewMode = CapabilityViewMode.Overview;
	private JsonElement _capabilitySnapshot;
	private bool _hasCapabilitySnapshot;
	private string _capabilityRequestId = "";
	private bool _capabilityPending;
	private ulong _capabilityRequestAtMs;

	private void InitializeCapabilityPanel()
	{
		// Panel "Eris hiểu bạn" độc lập đã retire khỏi Desktop UI. Không dựng control trong scene.
		_capabilityPanel = null;
		_contextCapabilityButton = null;
	}

	private void OnContextCapabilityPressed()
	{
		OpenLearnerPortraitPortal();
	}

	private void ShowCapabilityPanel() => OpenLearnerPortraitPortal();

	private void ShowNativeCapabilityPanel()
	{
		if (_capabilityPanel == null) return;
		CloseCharacterContextMenu();
		Vector2 viewport = GetViewportRect().Size;
		Vector2 target = new(
			Mathf.Clamp(viewport.X - 120f, CapabilityPanelMinSize.X, CapabilityPanelPreferredSize.X),
			Mathf.Clamp(viewport.Y - 90f, CapabilityPanelMinSize.Y, CapabilityPanelPreferredSize.Y));
		_capabilityPanel.Size = target;
		_capabilityPanel.Position = new Vector2(
			Mathf.Max(16f, (viewport.X - target.X) * 0.5f),
			Mathf.Max(16f, (viewport.Y - target.Y) * 0.5f));
		_capabilityPanel.Visible = true;
		SendCapabilityPanelRequest();
	}

	private void CloseCapabilityPanel()
	{
		if (_capabilityPanel != null) _capabilityPanel.Visible = false;
	}

	private void BuildCapabilityPanelUi()
	{
		if (_capabilityPanel == null) return;

		// Tôn trọng visual language chủ đạo của ERIS Desktop: giấy sáng, hồng mềm,
		// ink tím nâu và border mảnh. Không dựng dark dashboard riêng cho panel này.
		_capabilityPanel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.985f, 0.993f, 0.995f),
			BorderColor = MemoryBorder,
			BorderWidthLeft = 1, BorderWidthTop = 1, BorderWidthRight = 1, BorderWidthBottom = 1,
			CornerRadiusTopLeft = 20, CornerRadiusTopRight = 20,
			CornerRadiusBottomLeft = 20, CornerRadiusBottomRight = 20,
			ShadowColor = new Color(0.55f, 0.10f, 0.30f, 0.16f), ShadowSize = 9,
		});
		_capabilityPanel.ClipContents = true;

		var margin = new MarginContainer();
		margin.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		margin.AddThemeConstantOverride("margin_left", 24);
		margin.AddThemeConstantOverride("margin_right", 24);
		margin.AddThemeConstantOverride("margin_top", 18);
		margin.AddThemeConstantOverride("margin_bottom", 18);
		_capabilityPanel.AddChild(margin);

		var root = new VBoxContainer();
		root.AddThemeConstantOverride("separation", 9);
		margin.AddChild(root);

		var header = new HBoxContainer();
		header.AddThemeConstantOverride("separation", 9);
		root.AddChild(header);
		var titleBox = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		titleBox.AddThemeConstantOverride("separation", 1);
		header.AddChild(titleBox);
		titleBox.AddChild(CreateUiLabel("ERIS HIỂU BẠN THẾ NÀO", 23, MemoryInk));
		var subtitle = CreateUiLabel("Working model, không phải bảng điểm.", 11, MemoryInkSoft);
		titleBox.AddChild(subtitle);

		var refresh = CreateMemoryButton("Cập nhật", SendCapabilityPanelRequest, primary: false);
		refresh.CustomMinimumSize = new Vector2(88f, 32f);
		header.AddChild(refresh);
		header.AddChild(CreateWindowControlCapsule(() => _capabilityPanel.Visible = false, CloseCapabilityPanel));

		var note = CreateUiLabel(
			"Mỗi nhận định đều dựa trên evidence, coverage và độ chắc chắn. ERIS có thể hiểu sai — bạn luôn có quyền sửa lại.",
			10, MemoryInkSoft);
		note.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		root.AddChild(note);

		// Hai view thay cho một dashboard dài: mặt chính chỉ nói điều đáng biết;
		// evidence/detail nằm sau một bước để panel vẫn có cảm giác companion.
		var modeRow = new HBoxContainer();
		modeRow.AddThemeConstantOverride("separation", 6);
		root.AddChild(modeRow);
		_capabilityOverviewButton = CreateCapabilityModeButton("Tổng quan", CapabilityViewMode.Overview);
		_capabilityEvidenceButton = CreateCapabilityModeButton("Bằng chứng", CapabilityViewMode.Evidence);
		modeRow.AddChild(_capabilityOverviewButton);
		modeRow.AddChild(_capabilityEvidenceButton);
		var modeSpacer = new Control { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		modeRow.AddChild(modeSpacer);
		_capabilityStatus = CreateUiLabel("Chưa đọc dữ liệu.", 9, MemoryInkSoft);
		modeRow.AddChild(_capabilityStatus);
		UpdateCapabilityModeButtons();

		var scroll = new ScrollContainer
		{
			SizeFlagsVertical = Control.SizeFlags.ExpandFill,
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
		};
		root.AddChild(scroll);
		_capabilityContent = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		_capabilityContent.AddThemeConstantOverride("separation", 11);
		scroll.AddChild(_capabilityContent);
		RenderCapabilityEmpty();
	}

	private Button CreateCapabilityModeButton(string text, CapabilityViewMode mode)
	{
		var button = CreateMemoryButton(text, () => SetCapabilityViewMode(mode), primary: false);
		button.CustomMinimumSize = new Vector2(96f, 30f);
		button.AddThemeFontSizeOverride("font_size", 10);
		return button;
	}

	private void SetCapabilityViewMode(CapabilityViewMode mode)
	{
		if (_capabilityViewMode == mode) return;
		_capabilityViewMode = mode;
		UpdateCapabilityModeButtons();
		RenderCapabilitySnapshot();
	}

	private void UpdateCapabilityModeButtons()
	{
		StyleCapabilityModeButton(_capabilityOverviewButton, _capabilityViewMode == CapabilityViewMode.Overview);
		StyleCapabilityModeButton(_capabilityEvidenceButton, _capabilityViewMode == CapabilityViewMode.Evidence);
	}

	private static void StyleCapabilityModeButton(Button button, bool selected)
	{
		if (button == null) return;
		Color bg = selected ? new Color(1f, 0.91f, 0.96f) : new Color(1f, 0.985f, 0.993f, 0.45f);
		Color border = selected ? MemoryPink : new Color(MemoryBorder.R, MemoryBorder.G, MemoryBorder.B, 0.52f);
		var style = new StyleBoxFlat
		{
			BgColor = bg,
			BorderColor = border,
			BorderWidthLeft = 1, BorderWidthTop = 1, BorderWidthRight = 1, BorderWidthBottom = 1,
			CornerRadiusTopLeft = 9, CornerRadiusTopRight = 9,
			CornerRadiusBottomLeft = 9, CornerRadiusBottomRight = 9,
			ContentMarginLeft = 9, ContentMarginRight = 9,
			ContentMarginTop = 4, ContentMarginBottom = 4,
		};
		button.AddThemeStyleboxOverride("normal", style);
		button.AddThemeStyleboxOverride("hover", style);
		button.AddThemeStyleboxOverride("pressed", style);
		button.AddThemeColorOverride("font_color", selected ? MemoryInk : MemoryInkSoft);
	}

	private void RenderCapabilityEmpty()
	{
		if (_capabilityContent == null) return;
		foreach (Node child in _capabilityContent.GetChildren()) child.QueueFree();
		var card = CreateMemoryCard(new Color(1f, 0.97f, 0.985f, 0.96f), 16);
		var margin = CapabilityCardMargin(16, 14);
		card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 5); margin.AddChild(box);
		box.AddChild(CreateUiLabel("ERIS chưa có snapshot để hình thành working model.", 14, MemoryInk));
		var text = CreateUiLabel("Bấm “Cập nhật” để đọc evidence hiện tại. Không đủ dữ liệu thì panel sẽ nói thẳng là chưa rõ.", 11, MemoryInkSoft);
		text.AutowrapMode = TextServer.AutowrapMode.WordSmart;
		box.AddChild(text);
		_capabilityContent.AddChild(card);
	}

	private void SendCapabilityPanelRequest() => SendCapabilityAction("snapshot");

	private void SendCapabilityAction(string action, Dictionary<string, object> extra = null)
	{
		if (_chatSend == null) return;
		_capabilityRequestId = $"capability-{Time.GetTicksMsec()}-{GD.Randi()}";
		var payload = new Dictionary<string, object>
		{
			["type"] = "capability_panel_request",
			["action"] = action,
			["request_id"] = _capabilityRequestId,
		};
		if (extra != null)
			foreach (KeyValuePair<string, object> pair in extra) payload[pair.Key] = pair.Value;
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		if (error != Error.Ok)
		{
			SetCapabilityStatus($"Không gửi được request: {error}", true);
			return;
		}
		_capabilityPending = true;
		_capabilityRequestAtMs = Time.GetTicksMsec();
		SetCapabilityStatus(action == "snapshot" ? "Đang đọc working model…" : "Đang cập nhật…");
	}

	private string _learnerPortraitRequestId = "";
	private string _learnerPortraitPathId = "";
	private int _learnerPortraitPage = 0;
	private bool _learnerPortraitPending;
	private ulong _learnerPortraitSentAtMs;

	private void SendLearnerPortraitRequest(string pathId = "", int page = -1)
	{
		if (_chatSend == null)
		{
			Volatile.Write(ref _learnerPortraitSnapshot,
				"{\"status\":\"error\",\"message\":\"Eris Brain chưa kết nối.\"}");
			return;
		}
		if (page >= 0) { _learnerPortraitPage = page; _learnerPortraitPathId = ""; }
		if (!string.IsNullOrEmpty(pathId)) _learnerPortraitPathId = pathId;
		_learnerPortraitRequestId = $"portrait-{Time.GetTicksMsec()}-{GD.Randi()}";
		var payload = new Dictionary<string, object> {
			["type"] = "capability_panel_request",
			["action"] = "portrait_snapshot",
			["request_id"] = _learnerPortraitRequestId,
			["path_id"] = _learnerPortraitPathId,
			["path_page"] = _learnerPortraitPage,
		};
		Error error = _chatSend.PutPacket(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(payload)));
		_learnerPortraitPending = error == Error.Ok;
		_learnerPortraitSentAtMs = Time.GetTicksMsec();
		if (error != Error.Ok)
			Volatile.Write(ref _learnerPortraitSnapshot, "{\"status\":\"error\",\"message\":\"Không gửi được yêu cầu tới Eris.\"}");
	}

	private bool TryHandleCapabilityPanelPacket(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw)) return false;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object
				|| !root.TryGetProperty("type", out JsonElement typeNode)
				|| !string.Equals(typeNode.GetString(), "capability_panel_ack", StringComparison.Ordinal))
				return false;
			string requestId = ReadJsonString(root, "request_id");
			if (ReadJsonString(root, "action") == "portrait_snapshot")
			{
				if (requestId != _learnerPortraitRequestId) return true;
				_learnerPortraitPending = false;
				string statusText = ReadJsonString(root, "status");
				if (root.TryGetProperty("learner_portrait", out JsonElement portrait) && portrait.ValueKind == JsonValueKind.Object)
				{
					string state = JsonSerializer.Serialize(new { status = statusText, data = portrait });
					Volatile.Write(ref _learnerPortraitSnapshot, state);
				}
				else
					Volatile.Write(ref _learnerPortraitSnapshot,
						"{\"status\":\"error\",\"message\":\"Chưa có dữ liệu hồ sơ.\"}");
				return true;
			}
			if (!string.IsNullOrWhiteSpace(_capabilityRequestId) && !string.IsNullOrWhiteSpace(requestId)
				&& !string.Equals(requestId, _capabilityRequestId, StringComparison.Ordinal)) return true;
			_capabilityPending = false;
			string status = ReadJsonString(root, "status");
			string message = ReadJsonString(root, "message");
			if (root.TryGetProperty("capability_panel", out JsonElement panel) && panel.ValueKind == JsonValueKind.Object)
				ApplyCapabilitySnapshot(panel);
			SetCapabilityStatus(string.IsNullOrWhiteSpace(message) ? "Đã cập nhật." : message, status == "error");
			return true;
		}
		catch (JsonException) { return false; }
		catch (Exception ex)
		{
			SetCapabilityStatus($"Không đọc được working model: {ex.Message}", true);
			return true;
		}
	}

	private void ApplyCapabilitySnapshot(JsonElement panel)
	{
		// Clone để snapshot sống độc lập với JsonDocument của packet vừa parse.
		_capabilitySnapshot = panel.Clone();
		_hasCapabilitySnapshot = true;
		RenderCapabilitySnapshot();
	}

	private void RenderCapabilitySnapshot()
	{
		if (_capabilityContent == null) return;
		foreach (Node child in _capabilityContent.GetChildren()) child.QueueFree();
		if (!_hasCapabilitySnapshot || _capabilitySnapshot.ValueKind != JsonValueKind.Object)
		{
			RenderCapabilityEmpty();
			return;
		}
		if (_capabilityViewMode == CapabilityViewMode.Overview)
			RenderCapabilityOverview(_capabilitySnapshot);
		else
			RenderCapabilityEvidence(_capabilitySnapshot);
	}

	private void RenderCapabilityOverview(JsonElement panel)
	{
		_capabilityContent.AddChild(CreateCapabilitySummaryCard(panel));
		_capabilityContent.AddChild(CreateCapabilitySectionHeader("ERIS ĐANG HIỂU BẠN"));

		if (panel.TryGetProperty("nhom_nang_luc", out JsonElement groups) && groups.ValueKind == JsonValueKind.Array)
		{
			int index = 0;
			foreach (JsonElement group in groups.EnumerateArray())
			{
				if (group.ValueKind != JsonValueKind.Object) continue;
				_capabilityContent.AddChild(CreateCapabilityOverviewRow(group, index++));
			}
			_capabilityContent.AddChild(CreateCapabilityGuidanceCard(groups));
		}

		if (panel.TryGetProperty("muc_tieu_hien_tai", out JsonElement goals) && goals.ValueKind == JsonValueKind.Array && goals.GetArrayLength() > 0)
		{
			_capabilityContent.AddChild(CreateCapabilitySectionHeader("Mục tiêu đang theo dõi"));
			int shown = 0;
			foreach (JsonElement goal in goals.EnumerateArray())
			{
				if (shown++ >= 2 || goal.ValueKind != JsonValueKind.Object) break;
				_capabilityContent.AddChild(CreateDevelopmentGoalCard(goal));
			}
		}

		var why = CreateMemoryButton("Vì sao Eris nghĩ vậy?", () => SetCapabilityViewMode(CapabilityViewMode.Evidence), primary: false);
		why.CustomMinimumSize = new Vector2(190f, 34f);
		_capabilityContent.AddChild(why);
	}

	private void RenderCapabilityEvidence(JsonElement panel)
	{
		var intro = CreateCapabilitySoftCard(
			"BẰNG CHỨNG & ĐỘ CHẮC CHẮN",
			"Mỗi nhận định bên dưới cho biết coverage, confidence và evidence gần đây. Self-report/correction không tự biến thành mastery.",
			MemoryPink);
		_capabilityContent.AddChild(intro);

		if (panel.TryGetProperty("nhom_nang_luc", out JsonElement groups) && groups.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement group in groups.EnumerateArray())
				if (group.ValueKind == JsonValueKind.Object) _capabilityContent.AddChild(CreateCapabilityGroupCard(group));

			var unknown = CreateCapabilityUnknownCard(groups);
			if (unknown != null) _capabilityContent.AddChild(unknown);
		}

		_capabilityContent.AddChild(CreateCapabilitySectionHeader("MỤC TIÊU & XU HƯỚNG"));
		if (panel.TryGetProperty("muc_tieu_hien_tai", out JsonElement goals) && goals.ValueKind == JsonValueKind.Array && goals.GetArrayLength() > 0)
		{
			foreach (JsonElement goal in goals.EnumerateArray())
				if (goal.ValueKind == JsonValueKind.Object) _capabilityContent.AddChild(CreateDevelopmentGoalCard(goal));
		}
		else
			_capabilityContent.AddChild(CreateUiLabel("Chưa theo dõi mục tiêu phát triển nào.", 10, MemoryInkSoft));

		if (panel.TryGetProperty("thong_ke_phat_trien_30_ngay", out JsonElement developmentStats) && developmentStats.ValueKind == JsonValueKind.Object)
			_capabilityContent.AddChild(CreateDevelopmentStatsCard(developmentStats));
		if (panel.TryGetProperty("xu_huong_30_ngay", out JsonElement trend) && trend.ValueKind == JsonValueKind.Array)
			_capabilityContent.AddChild(CreateTrendCard(trend));

		_capabilityContent.AddChild(CreateCapabilitySectionHeader("CÔNG CỤ & THÓI QUEN AI"));
		if (panel.TryGetProperty("cong_cu", out JsonElement tools) && tools.ValueKind == JsonValueKind.Array)
			_capabilityContent.AddChild(CreateToolStatsCard(tools));
		if (panel.TryGetProperty("thoi_quen_ai_30_ngay", out JsonElement ai) && ai.ValueKind == JsonValueKind.Object)
			_capabilityContent.AddChild(CreateAiStatsCard(ai));

		_capabilityContent.AddChild(CreateCapabilitySectionHeader("Đề xuất dài hạn"));
		if (panel.TryGetProperty("de_xuat_dai_han", out JsonElement recs) && recs.ValueKind == JsonValueKind.Array && recs.GetArrayLength() > 0)
		{
			foreach (JsonElement rec in recs.EnumerateArray())
				if (rec.ValueKind == JsonValueKind.Object) _capabilityContent.AddChild(CreateRecommendationCard(rec));
		}
		else
			_capabilityContent.AddChild(CreateUiLabel("Chưa đủ dữ liệu để đưa đề xuất dài hạn đáng tin.", 10, MemoryInkSoft));

		var resetAll = CreateMemoryButton("Đặt lại mô hình", ConfirmResetCapabilityProfile, primary: false, danger: true);
		resetAll.CustomMinimumSize = new Vector2(140f, 32f);
		_capabilityContent.AddChild(resetAll);
	}

	private Control CreateCapabilitySectionHeader(string text)
	{
		var label = CreateUiLabel(text, 11, MemoryInkSoft);
		label.CustomMinimumSize = new Vector2(0f, 24f);
		return label;
	}

	private Control CreateCapabilitySummaryCard(JsonElement panel)
	{
		string strongest = "";
		string uncertain = "";
		int strongestScore = -1;
		int lowestScore = 101;
		string lowest = "";
		int total = ReadJsonInt(panel, "tong_bang_chung");

		if (panel.TryGetProperty("nhom_nang_luc", out JsonElement groups) && groups.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement group in groups.EnumerateArray())
			{
				if (group.ValueKind != JsonValueKind.Object) continue;
				string name = ReadJsonString(group, "ten");
				if (!ReadJsonBool(group, "da_du_bang_chung"))
				{
					if (string.IsNullOrWhiteSpace(uncertain)) uncertain = name;
					continue;
				}
				int score = ReadJsonInt(group, "phan_tram");
				if (score > strongestScore) { strongestScore = score; strongest = name; }
				if (score < lowestScore) { lowestScore = score; lowest = name; }
			}
		}

		string summary;
		if (string.IsNullOrWhiteSpace(strongest))
			summary = "ERIS chưa có đủ evidence để giả định bạn mạnh/yếu ở mảng nào. Cứ làm việc bình thường; model sẽ rõ dần theo thời gian.";
		else if (!string.IsNullOrWhiteSpace(uncertain))
			summary = $"Evidence rõ nhất hiện ở {strongest}. {uncertain} vẫn là vùng chưa rõ, nên ERIS không được tự giả định trình độ ở đó.";
		else if (!string.IsNullOrWhiteSpace(lowest) && !string.Equals(lowest, strongest, StringComparison.Ordinal))
			summary = $"Evidence mạnh nhất hiện ở {strongest}; {lowest} vẫn đang phát triển hơn. ERIS nên thay đổi độ sâu giải thích theo từng mảng, không gắn một level chung cho bạn.";
		else
			summary = $"Evidence hiện tập trung ở {strongest}. ERIS sẽ dùng nó để chọn độ sâu giải thích, không dùng như điểm số con người.";

		var card = CreateMemoryCard(new Color(1f, 0.95f, 0.978f, 0.98f), 17, MemoryPink);
		var margin = CapabilityCardMargin(17, 14); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 6); margin.AddChild(box);
		box.AddChild(CreateUiLabel("Working model, không phải bảng điểm", 16, MemoryInk));
		var body = CreateUiLabel(summary, 12, MemoryInk); body.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(body);
		box.AddChild(CreateUiLabel($"{total} evidence đã được ghi nhận · chỉ evidence độc lập/đủ confidence mới được dùng để kết luận.", 9, MemoryInkSoft));
		return card;
	}

	private Control CreateCapabilityOverviewRow(JsonElement group, int index)
	{
		var wrap = new VBoxContainer();
		wrap.AddThemeConstantOverride("separation", 4);
		var row = new HBoxContainer(); row.AddThemeConstantOverride("separation", 8); wrap.AddChild(row);
		var left = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		left.AddThemeConstantOverride("separation", 1); row.AddChild(left);
		string name = ReadJsonString(group, "ten");
		left.AddChild(CreateUiLabel(name, 13, MemoryInk));
		left.AddChild(CreateUiLabel(CapabilityGroupDescription(ReadJsonString(group, "id")), 9, MemoryInkSoft));
		string stage = CapabilityStageLabel(group);
		row.AddChild(CreateCapabilityStageBadge(stage));

		int observed = ReadJsonInt(group, "ky_nang_da_quan_sat");
		int total = ReadJsonInt(group, "tong_ky_nang");
		int evidence = ReadJsonInt(group, "so_bang_chung");
		string confidence = ReadJsonString(group, "do_tin_cay");
		string meta = stage == "Chưa rõ"
			? $"Đã quan sát {observed}/{Mathf.Max(1, total)} chiều · {evidence} evidence · chưa đủ coverage để kết luận"
			: $"{observed}/{Mathf.Max(1, total)} chiều · {evidence} evidence · confidence {confidence}";
		var metaLabel = CreateUiLabel(meta, 9, MemoryInkSoft); metaLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart; wrap.AddChild(metaLabel);

		if (index >= 0)
		{
			var line = new HSeparator();
			line.AddThemeColorOverride("separator", new Color(MemoryBorder.R, MemoryBorder.G, MemoryBorder.B, 0.32f));
			wrap.AddChild(line);
		}
		return wrap;
	}

	private Control CreateCapabilityGuidanceCard(JsonElement groups)
	{
		string strong = "";
		string guided = "";
		int strongScore = -1;
		int guidedScore = 101;
		foreach (JsonElement group in groups.EnumerateArray())
		{
			if (group.ValueKind != JsonValueKind.Object) continue;
			string name = ReadJsonString(group, "ten");
			if (!ReadJsonBool(group, "da_du_bang_chung"))
			{
				if (string.IsNullOrWhiteSpace(guided)) guided = name;
				continue;
			}
			int score = ReadJsonInt(group, "phan_tram");
			if (score > strongScore) { strongScore = score; strong = name; }
			if (score < guidedScore && string.IsNullOrWhiteSpace(guided)) { guidedScore = score; guided = name; }
		}
		string headline = string.IsNullOrWhiteSpace(strong)
			? "Guided mặc định cho tới khi có evidence rõ hơn"
			: $"Peer ở {strong}{(!string.IsNullOrWhiteSpace(guided) ? $" · Guided ở {guided}" : "")}";
		return CreateCapabilitySoftCard(
			"CÁCH ERIS NÊN NÓI",
			headline + "\nKhông giảng lại thứ đã chứng minh. Chỉ challenge khi đúng phần bạn đang học hoặc reasoning đang bị outsource quá nhiều.",
			new Color(0.66f, 0.42f, 0.86f));
	}

	private Control CreateCapabilityGroupCard(JsonElement group)
	{
		string stage = CapabilityStageLabel(group);
		Color accent = CapabilityStageAccent(stage);
		var card = CreateMemoryCard(new Color(1f, 0.99f, 0.995f, 0.96f), 14, accent);
		var margin = CapabilityCardMargin(14, 11); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 7); margin.AddChild(box);

		var header = new HBoxContainer(); header.AddThemeConstantOverride("separation", 8); box.AddChild(header);
		var title = CreateUiLabel(ReadJsonString(group, "ten"), 14, MemoryInk); title.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill; header.AddChild(title);
		header.AddChild(CreateCapabilityStageBadge(stage));
		int observed = ReadJsonInt(group, "ky_nang_da_quan_sat");
		int total = ReadJsonInt(group, "tong_ky_nang");
		box.AddChild(CreateUiLabel($"Coverage {observed}/{Mathf.Max(1, total)} chiều · {ReadJsonInt(group, "so_bang_chung")} evidence · confidence {ReadJsonString(group, "do_tin_cay")}", 9, MemoryInkSoft));
		string coverageNote = ReadJsonString(group, "ghi_chu_do_phu");
		if (!string.IsNullOrWhiteSpace(coverageNote)) box.AddChild(CreateUiLabel(coverageNote, 9, MemoryInkSoft));

		if (group.TryGetProperty("ky_nang", out JsonElement skills) && skills.ValueKind == JsonValueKind.Array)
		{
			foreach (JsonElement skill in skills.EnumerateArray())
			{
				if (skill.ValueKind != JsonValueKind.Object) continue;
				string skillId = ReadJsonString(skill, "id");
				string name = ReadJsonString(skill, "ten");
				bool known = ReadJsonBool(skill, "da_du_bang_chung");
				int evidence = ReadJsonInt(skill, "so_bang_chung");
				int independent = ReadJsonInt(skill, "bang_chung_doc_lap");
				int counter = ReadJsonInt(skill, "counter_evidence");

				var skillBox = new VBoxContainer(); skillBox.AddThemeConstantOverride("separation", 3); box.AddChild(skillBox);
				var skillHead = new HBoxContainer(); skillBox.AddChild(skillHead);
				var skillTitle = CreateUiLabel(name, 10, known ? MemoryInk : MemoryInkSoft); skillTitle.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill; skillHead.AddChild(skillTitle);
				if (!known) skillHead.AddChild(CreateCapabilityStageBadge("Chưa rõ", compact: true));
				skillBox.AddChild(CreateUiLabel($"{independent} evidence độc lập / {evidence} tổng{(counter > 0 ? $" · {counter} counter-evidence" : "")}", 8, MemoryInkSoft));

				if (skill.TryGetProperty("bang_chung_gan_day", out JsonElement evidenceRows) && evidenceRows.ValueKind == JsonValueKind.Array)
				{
					int shown = 0;
					foreach (JsonElement evidenceRow in evidenceRows.EnumerateArray())
					{
						if (shown++ >= 2 || evidenceRow.ValueKind != JsonValueKind.Object) break;
						string desc = ReadJsonString(evidenceRow, "mo_ta");
						if (string.IsNullOrWhiteSpace(desc)) continue;
						var evidenceLabel = CreateUiLabel("↳ " + desc, 9, MemoryInkSoft);
						evidenceLabel.AutowrapMode = TextServer.AutowrapMode.WordSmart;
						skillBox.AddChild(evidenceLabel);
					}
				}

				var actions = new HBoxContainer(); actions.AddThemeConstantOverride("separation", 5); skillBox.AddChild(actions);
				actions.AddChild(CreateCapabilityMiniButton("Đúng", () => SendSkillFeedback(skillId, "confirmed")));
				actions.AddChild(CreateCapabilityMiniButton("Tôi đã biết", () => SendSkillFeedback(skillId, "already_knew")));
				actions.AddChild(CreateCapabilityMiniButton("Đánh giá sai / sửa lại", () => SendSkillFeedback(skillId, "wrong")));
				actions.AddChild(CreateCapabilityMiniButton("Đặt lại", () => SendSkillFeedback(skillId, "reset"), danger: true));
			}
		}
		return card;
	}

	private Control CreateCapabilityUnknownCard(JsonElement groups)
	{
		var unknown = new List<string>();
		foreach (JsonElement group in groups.EnumerateArray())
		{
			if (group.ValueKind != JsonValueKind.Object || !group.TryGetProperty("ky_nang", out JsonElement skills) || skills.ValueKind != JsonValueKind.Array) continue;
			foreach (JsonElement skill in skills.EnumerateArray())
			{
				if (skill.ValueKind != JsonValueKind.Object || ReadJsonBool(skill, "da_du_bang_chung")) continue;
				unknown.Add(ReadJsonString(skill, "ten"));
			}
		}
		if (unknown.Count == 0) return null;
		var card = CreateMemoryCard(new Color(0.99f, 0.985f, 1f, 0.86f), 13, new Color(0.55f, 0.60f, 0.78f));
		var margin = CapabilityCardMargin(13, 10); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		box.AddChild(CreateUiLabel("CHƯA BIẾT RÕ", 10, MemoryInkSoft));
		box.AddChild(CreateUiLabel(string.Join(" · ", unknown.GetRange(0, Mathf.Min(unknown.Count, 5))), 10, MemoryInk));
		box.AddChild(CreateUiLabel("Không có evidence đủ mạnh ≠ yếu. ERIS không được tự điền khoảng trống bằng phỏng đoán.", 9, MemoryInkSoft));
		return card;
	}

	private Control CreateCapabilitySoftCard(string eyebrow, string bodyText, Color accent)
	{
		var card = CreateMemoryCard(new Color(1f, 0.985f, 0.993f, 0.94f), 14, accent);
		var margin = CapabilityCardMargin(14, 11); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 5); margin.AddChild(box);
		box.AddChild(CreateUiLabel(eyebrow, 9, MemoryInkSoft));
		var body = CreateUiLabel(bodyText, 11, MemoryInk); body.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(body);
		return card;
	}

	private static string CapabilityGroupDescription(string id) => id switch
	{
		"debugging" => "Định vị lỗi, trace luồng gọi, phân biệt root cause / symptom",
		"architecture" => "Nhìn boundary, ownership và trách nhiệm giữa các phần",
		"verification" => "Khóa kết luận bằng diff, test và evidence có mục tiêu",
		"ai_operation" => "Khoanh scope và review output của coding agent",
		_ => "Evidence kỹ thuật đã quan sát",
	};

	private static string CapabilityStageLabel(JsonElement row)
	{
		if (!ReadJsonBool(row, "da_du_bang_chung")) return "Chưa rõ";
		int score = ReadJsonInt(row, "phan_tram");
		if (score >= 72) return "Mạnh";
		if (score >= 58) return "Đang phát triển";
		return "Đang học";
	}

	private static Color CapabilityStageAccent(string stage) => stage switch
	{
		"Mạnh" => new Color(0.34f, 0.62f, 0.50f),
		"Đang phát triển" => new Color(0.82f, 0.62f, 0.26f),
		"Đang học" => new Color(0.40f, 0.56f, 0.82f),
		_ => new Color(0.58f, 0.53f, 0.68f),
	};

	private Control CreateCapabilityStageBadge(string stage, bool compact = false)
	{
		Color accent = CapabilityStageAccent(stage);
		var panel = new PanelContainer();
		panel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(accent.R, accent.G, accent.B, 0.10f),
			BorderColor = new Color(accent.R, accent.G, accent.B, 0.36f),
			BorderWidthLeft = 1, BorderWidthTop = 1, BorderWidthRight = 1, BorderWidthBottom = 1,
			CornerRadiusTopLeft = 10, CornerRadiusTopRight = 10,
			CornerRadiusBottomLeft = 10, CornerRadiusBottomRight = 10,
			ContentMarginLeft = compact ? 7 : 9,
			ContentMarginRight = compact ? 7 : 9,
			ContentMarginTop = compact ? 2 : 3,
			ContentMarginBottom = compact ? 2 : 3,
		});
		var label = CreateUiLabel(stage, compact ? 8 : 9, accent);
		panel.AddChild(label);
		return panel;
	}

	private Control CreateTrendCard(JsonElement trend)
	{
		var card = CreateMemoryCard(new Color(1f, 0.99f, 0.995f, 0.92f), 13);
		var margin = CapabilityCardMargin(13, 9); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		box.AddChild(CreateUiLabel("Xu hướng 30 ngày", 10, MemoryInk));
		foreach (JsonElement row in trend.EnumerateArray())
		{
			string id = ReadJsonString(row, "id");
			string name = id switch { "debugging" => "Gỡ lỗi", "architecture" => "Kiến trúc", "verification" => "Kiểm chứng", "ai_operation" => "Vận hành AI", _ => id };
			string label = ReadJsonString(row, "nhan");
			string text = label == "Chưa đủ lịch sử" ? $"{name}: Chưa đủ lịch sử" : $"{name}: {ReadJsonInt(row, "delta"):+#;-#;0}% · {label}";
			box.AddChild(CreateUiLabel(text, 9, label == "Chưa đủ lịch sử" ? MemoryInkSoft : MemoryInk));
		}
		return card;
	}

	private Control CreateToolStatsCard(JsonElement tools)
	{
		var card = CreateMemoryCard(new Color(0.985f, 0.975f, 1f, 0.92f), 13);
		var margin = CapabilityCardMargin(13, 9); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		box.AddChild(CreateUiLabel("Khả năng sử dụng công cụ", 10, MemoryInk));
		foreach (JsonElement tool in tools.EnumerateArray())
		{
			string name = ReadJsonString(tool, "ten"); string stage = ReadJsonString(tool, "muc_do"); int count = ReadJsonInt(tool, "so_lan_chung_minh");
			box.AddChild(CreateUiLabel($"{name} — {stage}{(count > 0 ? $" · {count} lần có evidence" : "")}", 9, stage == "Chưa đủ dữ liệu" ? MemoryInkSoft : MemoryInk));
		}
		return card;
	}

	private Control CreateAiStatsCard(JsonElement ai)
	{
		var card = CreateMemoryCard(new Color(1f, 0.97f, 0.985f, 0.92f), 13);
		var margin = CapabilityCardMargin(13, 9); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		box.AddChild(CreateUiLabel("Thói quen làm việc với AI · 30 ngày", 10, MemoryInk));
		box.AddChild(CreateUiLabel($"Lượt kỹ thuật có signal: {ReadJsonInt(ai, "luot_ky_thuat_da_quan_sat")}", 9, MemoryInk));
		box.AddChild(CreateUiLabel($"Hypothesis có evidence: {ReadJsonInt(ai, "tu_neu_gia_thuyet_co_evidence")} · Chủ động kiểm chứng: {ReadJsonInt(ai, "chu_dong_kiem_chung")}", 9, MemoryInkSoft));
		box.AddChild(CreateUiLabel($"Chủ động học hỏi: {ReadJsonInt(ai, "chu_dong_hoc_hoi")} · Yêu cầu thực thi mù: {ReadJsonInt(ai, "yeu_cau_thuc_thi_mu")}", 9, MemoryInkSoft));
		box.AddChild(CreateUiLabel($"Rủi ro mất ownership reasoning: {ReadJsonInt(ai, "rui_ro_mat_ownership_reasoning")}% (không phải điểm năng lực)", 9, MemoryInkSoft));
		return card;
	}

	private Control CreateRecommendationCard(JsonElement rec)
	{
		var card = CreateMemoryCard(new Color(0.985f, 0.965f, 1f, 0.92f), 13, new Color(0.64f, 0.38f, 0.88f));
		var margin = CapabilityCardMargin(13, 10); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		box.AddChild(CreateUiLabel(ReadJsonString(rec, "tieu_de"), 12, MemoryInk));
		box.AddChild(CreateUiLabel($"Ưu tiên: {ReadJsonString(rec, "uu_tien")} · Thời hạn: {ReadJsonString(rec, "thoi_han")}", 9, MemoryPink));
		var reason = CreateUiLabel("Lý do: " + ReadJsonString(rec, "ly_do"), 9, MemoryInkSoft); reason.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(reason);
		var practice = CreateUiLabel("Thực hành: " + ReadJsonString(rec, "thuc_hanh"), 9, MemoryInkSoft); practice.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(practice);
		var success = CreateUiLabel("Dấu hiệu tiến bộ: " + ReadJsonString(rec, "dau_hieu_tien_bo"), 9, MemoryInkSoft); success.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(success);
		string recId = ReadJsonString(rec, "id");
		if (!string.IsNullOrWhiteSpace(recId))
		{
			var follow = CreateCapabilityMiniButton("Theo dõi mục này", () => SendCapabilityAction("follow_recommendation", new Dictionary<string, object> { ["recommendation_id"] = recId }));
			follow.CustomMinimumSize = new Vector2(138f, 27f);
			box.AddChild(follow);
		}
		return card;
	}

	private void ConfirmResetCapabilityProfile()
	{
		if (_capabilityPanel == null) return;
		var dialog = new ConfirmationDialog
		{
			Title = "Đặt lại mô hình năng lực",
			DialogText = "Thao tác này đặt lại toàn bộ estimate kỹ năng kỹ thuật của ERIS. Lịch sử calibration và các mục tiêu đang theo dõi vẫn được giữ. Bạn có chắc không?",
			OkButtonText = "Đặt lại",
			CancelButtonText = "Hủy",
			Size = new Vector2I(560, 220),
		};
		_capabilityPanel.AddChild(dialog);
		dialog.Confirmed += () =>
		{
			SendCapabilityAction("reset_profile");
			dialog.QueueFree();
		};
		dialog.Canceled += () => dialog.QueueFree();
		dialog.PopupCentered();
	}

	private void SendSkillFeedback(string skillId, string feedback)
	{
		if (string.IsNullOrWhiteSpace(skillId)) return;
		SendCapabilityAction("skill_feedback", new Dictionary<string, object>
		{
			["skill_id"] = skillId,
			["feedback"] = feedback,
		});
	}

	private Button CreateCapabilityMiniButton(string text, Action pressed, bool danger = false)
	{
		var button = CreateMemoryButton(text, pressed, primary: false, danger: danger);
		button.CustomMinimumSize = new Vector2(text.Length > 10 ? 102f : 82f, 25f);
		button.AddThemeFontSizeOverride("font_size", 8);
		return button;
	}

	private Control CreateDevelopmentGoalCard(JsonElement goal)
	{
		var card = CreateMemoryCard(new Color(0.975f, 0.985f, 1f, 0.94f), 13, new Color(0.34f, 0.52f, 0.86f));
		var margin = CapabilityCardMargin(13, 10); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 4); margin.AddChild(box);
		string goalId = ReadJsonString(goal, "id");
		box.AddChild(CreateUiLabel(ReadJsonString(goal, "tieu_de"), 12, MemoryInk));
		int progress = ReadJsonInt(goal, "tien_do");
		int gained = ReadJsonInt(goal, "da_them_bang_chung");
		int target = ReadJsonInt(goal, "muc_tieu_bang_chung");
		box.AddChild(CreateUiLabel($"Evidence mới: {gained}/{target} · {progress}%", 9, MemoryInkSoft));
		var bar = new ProgressBar { MinValue = 0, MaxValue = 100, Value = progress, ShowPercentage = false, CustomMinimumSize = new Vector2(0f, 5f) }; box.AddChild(bar);
		var practice = CreateUiLabel("Thực hành: " + ReadJsonString(goal, "thuc_hanh"), 9, MemoryInkSoft); practice.AutowrapMode = TextServer.AutowrapMode.WordSmart; box.AddChild(practice);
		if (!string.IsNullOrWhiteSpace(goalId))
			box.AddChild(CreateCapabilityMiniButton("Dừng theo dõi", () => SendCapabilityAction("stop_goal", new Dictionary<string, object> { ["goal_id"] = goalId }), danger: true));
		return card;
	}

	private Control CreateDevelopmentStatsCard(JsonElement stats)
	{
		var card = CreateMemoryCard(new Color(1f, 0.99f, 0.995f, 0.90f), 12);
		var margin = CapabilityCardMargin(12, 8); card.AddChild(margin);
		var box = new VBoxContainer(); box.AddThemeConstantOverride("separation", 3); margin.AddChild(box);
		box.AddChild(CreateUiLabel("Thống kê phát triển · 30 ngày", 9, MemoryInk));
		box.AddChild(CreateUiLabel($"Hoàn thành: {ReadJsonInt(stats, "muc_tieu_hoan_thanh")} · Đang theo dõi: {ReadJsonInt(stats, "muc_tieu_dang_theo_doi")} · Đã dừng: {ReadJsonInt(stats, "muc_tieu_da_dung")}", 8, MemoryInkSoft));
		box.AddChild(CreateUiLabel($"Lần bạn sửa đánh giá ERIS: {ReadJsonInt(stats, "lan_user_sua_danh_gia")}", 8, MemoryInkSoft));
		return card;
	}

	private static MarginContainer CapabilityCardMargin(int horizontal = 14, int vertical = 10)
	{
		var margin = new MarginContainer();
		margin.AddThemeConstantOverride("margin_left", horizontal); margin.AddThemeConstantOverride("margin_right", horizontal);
		margin.AddThemeConstantOverride("margin_top", vertical); margin.AddThemeConstantOverride("margin_bottom", vertical);
		return margin;
	}

	private void TickCapabilityPanelStatus()
	{
		if (!_capabilityPending) return;
		if (Time.GetTicksMsec() - _capabilityRequestAtMs > 6500UL)
		{
			_capabilityPending = false;
			SetCapabilityStatus("Brain chưa trả lời; thử Cập nhật lại.", true);
		}
	}

	private void SetCapabilityStatus(string text, bool error = false)
	{
		if (_capabilityStatus == null) return;
		_capabilityStatus.Text = text;
		_capabilityStatus.AddThemeColorOverride("font_color", error ? new Color(0.76f, 0.18f, 0.28f) : MemoryInkSoft);
	}
}
