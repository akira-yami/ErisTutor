using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

// ─────────────────────────────────────────────────────────────────────────────
//  TAB GIA SƯ — Eris dùng lane tutor/research nền.
//  Một thẻ nhỏ nổi góc màn hình: tiêu đề + trạng thái động (đang gõ/đọc/ghi nhớ),
//  câu hỏi gõ ra từng chữ (typewriter), rồi câu trả lời gõ ra khi xong. Tự ẩn.
//  Data đến qua UDP 4242 với parse_mode="tutor_event" (xem TutorStageEmitter).
//  Thuần trang trí: không bắt chuột (click xuyên qua).
// ─────────────────────────────────────────────────────────────────────────────
public partial class UdpListener : Node2D
{
	private sealed class ResearchPanelState
	{
		public string EpisodeId { get; init; } = "";
		public string Mode { get; init; } = "";
		public string Stage { get; init; } = "";
		public string Topic { get; init; } = "";
		public string VerificationStatus { get; init; } = "";
		public int SourceCount { get; init; }
		public int TrustedSourceCount { get; init; }
		public int ClaimCount { get; init; }
		public int OrphanClaimCount { get; init; }
		public int ContestedClaimCount { get; init; }
		public int WarningCount { get; init; }
		public float CitationCoverage { get; init; }
		public bool SurfaceCaution { get; init; }
		public bool HasMetrics { get; init; }
		public bool HasVerification { get; init; }
		public bool Persisted { get; init; }
	}

	private static readonly Vector2 TutorPanelMinSize = new(560f, 190f);

	private Panel _tutorPanel;
	private VBoxContainer _tutorRoot;
	private Label _tutorTitleLabel;
	private Label _tutorStatusLabel;
	private HBoxContainer _tutorResearchChipRow;
	private HBoxContainer _tutorStageRow;
	private readonly Dictionary<string, Label> _tutorStageLabels = new();
	private Label _tutorModeChipLabel;
	private Label _tutorVerifyChipLabel;
	private Label _tutorSourceChipLabel;
	private Label _tutorCitationChipLabel;
	private Button _tutorHideButton;
	private RichTextLabel _tutorQuestionLabel;
	private RichTextLabel _tutorAnswerLabel;
	private Tween _tutorTypeTween;
	private Tween _tutorDotsTween;
	private Tween _tutorPulseTween;
	private ulong _tutorHideAtMs = 0;
	private string _tutorEpisodeId = "";
	private ResearchPanelState _tutorResearchState;

	private static readonly Color TutorInk = new(0.50f, 0.12f, 0.34f);
	private static readonly Color TutorInkSoft = new(0.66f, 0.40f, 0.56f);
	private static readonly Color TutorPink = new(0.97f, 0.46f, 0.66f);
	private static readonly Color TutorGold = new(0.90f, 0.62f, 0.26f);
	private static readonly Color TutorGreen = new(0.34f, 0.58f, 0.40f);
	private static readonly Color TutorWarn = new(0.83f, 0.47f, 0.20f);
	private static readonly Color TutorRed = new(0.78f, 0.22f, 0.30f);

	[Export] public float TutorPanelAutoHideSeconds = 14f;

	private void BuildTutorPanelUi()
	{
		_tutorPanel = new Panel
		{
			Name = "TutorPanel",
			Visible = false,
			CustomMinimumSize = TutorPanelMinSize,
			Size = TutorPanelMinSize,
			ZIndex = 97,
			MouseFilter = Control.MouseFilterEnum.Ignore, // trang trí: không chặn chuột
		};
		_tutorPanel.AddThemeStyleboxOverride("panel", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.965f, 0.982f, 0.97f),
			BorderColor = new Color(0.96f, 0.72f, 0.82f, 1f),
			BorderWidthLeft = 2, BorderWidthTop = 2, BorderWidthRight = 2, BorderWidthBottom = 2,
			CornerRadiusTopLeft = 22, CornerRadiusTopRight = 22,
			CornerRadiusBottomRight = 22, CornerRadiusBottomLeft = 22,
			ShadowColor = new Color(0.85f, 0.45f, 0.62f, 0.22f),
			ShadowSize = 8,
		});
		GetNode<Control>("CanvasLayer/Control").AddChild(_tutorPanel);

		var root = new VBoxContainer();
		root.SetAnchorsPreset(Control.LayoutPreset.FullRect);
		root.OffsetLeft = 16f; root.OffsetTop = 12f; root.OffsetRight = -16f; root.OffsetBottom = -12f;
		root.AddThemeConstantOverride("separation", 6);
		root.MouseFilter = Control.MouseFilterEnum.Ignore;
		_tutorPanel.AddChild(root);
		_tutorRoot = root;

		// Header: tiêu đề + trạng thái động
		var header = new HBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
		header.AddThemeConstantOverride("separation", 8);
		header.MouseFilter = Control.MouseFilterEnum.Ignore;
		root.AddChild(header);

		_tutorTitleLabel = MakeTutorLabel("✦ Eris đang nghiên cứu", 15, TutorInk);
		_tutorTitleLabel.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
		header.AddChild(_tutorTitleLabel);

		_tutorStatusLabel = MakeTutorLabel("", 12, TutorGold);
		_tutorStatusLabel.HorizontalAlignment = HorizontalAlignment.Right;
		header.AddChild(_tutorStatusLabel);

		// Nút Ẩn — phần DUY NHẤT bấm được. Mọi chỗ khác click xuyên qua, user không
		// thể chạm vào lane nghiên cứu nền của Eris (tránh bấm bậy làm AI lỗi).
		_tutorHideButton = new Button
		{
			Text = "✕",
			CustomMinimumSize = new Vector2(26f, 24f),
			MouseFilter = Control.MouseFilterEnum.Stop,
			MouseDefaultCursorShape = Control.CursorShape.PointingHand,
			TooltipText = "Ẩn",
		};
		var hideStyle = new StyleBoxFlat
		{
			BgColor = new Color(0.97f, 0.86f, 0.91f, 0.95f),
			CornerRadiusTopLeft = 10, CornerRadiusTopRight = 10,
			CornerRadiusBottomRight = 10, CornerRadiusBottomLeft = 10,
			ContentMarginLeft = 6, ContentMarginRight = 6, ContentMarginTop = 2, ContentMarginBottom = 2,
		};
		var hideHover = (StyleBoxFlat)hideStyle.Duplicate();
		hideHover.BgColor = TutorPink;
		_tutorHideButton.AddThemeStyleboxOverride("normal", hideStyle);
		_tutorHideButton.AddThemeStyleboxOverride("hover", hideHover);
		_tutorHideButton.AddThemeStyleboxOverride("pressed", hideStyle);
		_tutorHideButton.AddThemeStyleboxOverride("focus", hideStyle);
		_tutorHideButton.AddThemeColorOverride("font_color", TutorInk);
		_tutorHideButton.AddThemeColorOverride("font_hover_color", Colors.White);
		_tutorHideButton.AddThemeFontSizeOverride("font_size", 13);
		_tutorHideButton.Pressed += HideTutorPanel;
		header.AddChild(_tutorHideButton);

		_tutorResearchChipRow = new HBoxContainer
		{
			Visible = false,
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		_tutorResearchChipRow.AddThemeConstantOverride("separation", 6);
		root.AddChild(_tutorResearchChipRow);

		_tutorModeChipLabel = MakeTutorChip("mode", TutorInkSoft);
		_tutorVerifyChipLabel = MakeTutorChip("verify", TutorGold);
		_tutorSourceChipLabel = MakeTutorChip("nguồn", TutorInkSoft);
		_tutorCitationChipLabel = MakeTutorChip("cite", TutorInkSoft);
		_tutorResearchChipRow.AddChild(_tutorModeChipLabel);
		_tutorResearchChipRow.AddChild(_tutorVerifyChipLabel);
		_tutorResearchChipRow.AddChild(_tutorSourceChipLabel);
		_tutorResearchChipRow.AddChild(_tutorCitationChipLabel);

		_tutorStageRow = new HBoxContainer
		{
			Visible = false,
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		_tutorStageRow.AddThemeConstantOverride("separation", 5);
		foreach (var step in new (string Key, string Label)[]
		{
			("queued", "Xếp lượt"),
			("collecting", "Thu thập"),
			("verifying", "Kiểm chứng"),
			("synthesizing", "Tổng hợp"),
			("done", "Hoàn tất"),
		})
		{
			Label chip = MakeTutorChip(step.Label, TutorInkSoft);
			chip.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
			_tutorStageLabels[step.Key] = chip;
			_tutorStageRow.AddChild(chip);
		}
		root.AddChild(_tutorStageRow);

		root.AddChild(new ColorRect
		{
			Color = new Color(0.96f, 0.80f, 0.88f, 0.5f),
			CustomMinimumSize = new Vector2(0f, 1f),
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		});

		_tutorQuestionLabel = MakeTutorRich(TutorInk);
		root.AddChild(_tutorQuestionLabel);

		_tutorAnswerLabel = MakeTutorRich(new Color(0.30f, 0.40f, 0.34f));
		_tutorAnswerLabel.Visible = false;
		root.AddChild(_tutorAnswerLabel);
	}

	private Label MakeTutorLabel(string text, int size, Color color)
	{
		var lbl = new Label { Text = text };
		lbl.AddThemeFontSizeOverride("font_size", size);
		lbl.AddThemeColorOverride("font_color", color);
		lbl.MouseFilter = Control.MouseFilterEnum.Ignore;
		return lbl;
	}

	private Label MakeTutorChip(string text, Color color)
	{
		var lbl = MakeTutorLabel(text, 11, color);
		lbl.CustomMinimumSize = new Vector2(82f, 22f);
		lbl.HorizontalAlignment = HorizontalAlignment.Center;
		lbl.VerticalAlignment = VerticalAlignment.Center;
		lbl.AddThemeStyleboxOverride("normal", new StyleBoxFlat
		{
			BgColor = new Color(1f, 0.92f, 0.95f, 0.78f),
			BorderColor = new Color(0.96f, 0.76f, 0.84f, 0.85f),
			BorderWidthLeft = 1, BorderWidthTop = 1, BorderWidthRight = 1, BorderWidthBottom = 1,
			CornerRadiusTopLeft = 9, CornerRadiusTopRight = 9,
			CornerRadiusBottomRight = 9, CornerRadiusBottomLeft = 9,
			ContentMarginLeft = 7, ContentMarginRight = 7, ContentMarginTop = 2, ContentMarginBottom = 2,
		});
		return lbl;
	}

	private RichTextLabel MakeTutorRich(Color color)
	{
		var rt = new RichTextLabel
		{
			FitContent = true,
			ScrollActive = false,
			AutowrapMode = TextServer.AutowrapMode.WordSmart,
			SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		rt.AddThemeFontSizeOverride("normal_font_size", 13);
		rt.AddThemeColorOverride("default_color", color);
		return rt;
	}

	// Trả true nếu raw là một tutor_event (đã xử lý xong, caller nên return).
	private bool TryHandleTutorEvent(string raw)
	{
		if (string.IsNullOrEmpty(raw) || raw.IndexOf("tutor_event", StringComparison.Ordinal) < 0)
			return false;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return false;
			if (!root.TryGetProperty("parse_mode", out var pm) || pm.GetString() != "tutor_event")
				return false;
			if (!root.TryGetProperty("tutor", out var t) || t.ValueKind != JsonValueKind.Object)
				return true;

			string episodeId = TutorStr(t, "episode_id");
			string stage = TutorStr(t, "stage");
			string question = TutorStr(t, "question");
			if (string.IsNullOrEmpty(question)) question = TutorStr(t, "question_preview");
			string answer = TutorStr(t, "answer");
			int claims = TutorInt(t, "claims");
			string failReason = TutorStr(t, "fail_reason");

			ResearchPanelState research = TryReadResearchState(root, episodeId, stage, question);
			if (!string.IsNullOrWhiteSpace(episodeId) && episodeId != _tutorEpisodeId)
			{
				_tutorEpisodeId = episodeId;
				if (research == null)
					_tutorResearchState = null;
			}
			if (research != null)
			{
				_tutorResearchState = research;
			}
			else if (_tutorResearchState != null
				&& (string.IsNullOrWhiteSpace(episodeId) || episodeId == _tutorResearchState.EpisodeId || episodeId == _tutorEpisodeId))
			{
				research = _tutorResearchState;
			}

			DriveTutorPanel(stage, question, answer, claims, failReason, research);
			return true;
		}
		catch (Exception)
		{
			return false;
		}
	}

	private void DriveTutorPanel(string stage, string question, string answer, int claims, string failReason, ResearchPanelState research)
	{
		if (_tutorPanel == null)
			return;

		if (!_tutorPanel.Visible)
			ShowTutorPanel();
		_tutorHideAtMs = 0; // còn diễn tiến thì không tự ẩn
		StartTutorPulse();  // "thở" nhẹ để Ngài biết Eris đang dùng lane nghiên cứu
		UpdateTutorResearchChips(research);

		switch (stage)
		{
			case "queued":
				_tutorAnswerLabel.Visible = false;
				_tutorAnswerLabel.Text = "";
				SetTutorQuestion(TutorTopicForDisplay(research, question));
				StartTutorDots(TutorModeVerb(research, "đang chuẩn bị tra"));
				break;
			case "asking_tutor":
				SetTutorQuestion(TutorTopicForDisplay(research, question));
				StartTutorDots(TutorModeVerb(research, "đang thu thập"));
				break;
			case "verifying":
				SetTutorQuestion(TutorTopicForDisplay(research, question));
				StartTutorDots("đang lọc nguồn");
				break;
			case "verified":
				SetTutorQuestion(TutorTopicForDisplay(research, question));
				StopTutorDots(TutorVerifiedText(research));
				break;
			case "forming":
				_tutorAnswerLabel.Visible = false;
				_tutorAnswerLabel.Text = "";
				SetTutorQuestion(question);
				StartTutorDots("đang nghĩ câu hỏi");
				break;
			case "opening":
				StartTutorDots("đang mở trợ lý nghiên cứu");
				break;
			case "asking":
				StopTutorDots("đang gõ…");
				TypeTutorText(_tutorQuestionLabel, question);
				break;
			case "reading":
				StartTutorDots("đang đọc trả lời");
				break;
			case "digesting":
				StartTutorDots("đang ghi nhớ");
				break;
			case "done":
				StopTutorPulse();
				StopTutorDots(TutorDoneText(claims, research));
				if (!string.IsNullOrWhiteSpace(answer))
				{
					_tutorAnswerLabel.Visible = true;
					TypeTutorText(_tutorAnswerLabel, answer);
				}
				ScheduleTutorHide();
				break;
			case "failed":
				StopTutorPulse();
				StopTutorDots(TutorFailText(failReason));
				ScheduleTutorHide();
				break;
			default:
				break;
		}
	}

	private static string TutorTopicForDisplay(ResearchPanelState research, string fallback)
	{
		string topic = research?.Topic ?? "";
		if (!string.IsNullOrWhiteSpace(topic))
			return topic;
		return fallback ?? "";
	}

	private static string TutorModeVerb(ResearchPanelState research, string fallback)
	{
		string mode = research?.Mode ?? "";
		if (mode == "quick_search")
			return "đang search nhanh";
		if (mode == "research")
			return "đang hỏi nguồn nghiên cứu";
		return fallback;
	}

	private static string TutorVerifiedText(ResearchPanelState research)
	{
		if (research == null)
			return "đã kiểm chứng ✓";
		if (research.VerificationStatus == "passed")
			return "đã kiểm chứng ✓";
		if (research.VerificationStatus == "failed")
			return "nguồn yếu ⚠";
		if (research.WarningCount > 0 || research.SurfaceCaution)
			return "có cảnh báo ⚠";
		return "đã lọc xong ✓";
	}

	private static string TutorDoneText(int claims, ResearchPanelState research)
	{
		if (claims > 0)
			return $"đã kiểm chứng và lưu {claims} claim ✓";
		if (research != null && research.Persisted)
			return "đã kiểm chứng và lưu ✓";
		if (research != null)
			return TutorVerifiedText(research) + " · chưa xác nhận lưu";
		return "đã hoàn tất ✓";
	}

	private static string TutorVerifyChipText(string verifyText, ResearchPanelState research)
	{
		if (string.IsNullOrWhiteSpace(verifyText))
			return "đang tra";
		if (verifyText == "passed")
			return "đủ nguồn";
		if (verifyText == "passed_with_warnings")
			return "cảnh báo";
		if (verifyText == "failed")
			return "nguồn yếu";
		if (verifyText == "queued")
			return "xếp lượt";
		if (verifyText == "asking_tutor")
			return research != null && research.Mode == "quick_search" ? "đang search" : "đang hỏi";
		if (verifyText == "verifying")
			return "đang lọc";
		if (verifyText == "verified")
			return "đã lọc";
		return verifyText.Length > 14 ? verifyText.Substring(0, 14) : verifyText;
	}

	private static string TutorFailText(string reason)
	{
		switch (reason)
		{
			case "captcha_or_expired": return "cần đăng nhập lại…";
			case "timeout": return "trợ lý nghiên cứu phản hồi quá lâu";
			case "empty_answer": return "không rõ câu trả lời";
			case "formulation_failed": return "chưa nghĩ ra câu hỏi";
			case "gate_rejected_known": return "cái này biết rồi";
			case "gate_rejected_trivial": return "thôi, chuyện vặt";
			case "gate_rejected": return "thôi, cái này biết rồi";
			case "selector_miss": return "không mở được ô hỏi";
			case "network_error": return "mạng trục trặc";
			default: return "hỏi hụt rồi";
		}
	}

	private void UpdateTutorResearchChips(ResearchPanelState research)
	{
		if (_tutorResearchChipRow == null)
			return;
		if (research == null)
		{
			_tutorResearchChipRow.Visible = false;
			if (_tutorStageRow != null)
				_tutorStageRow.Visible = false;
			if (_tutorTitleLabel != null)
				_tutorTitleLabel.Text = "✦ Eris đang nghiên cứu";
			return;
		}

		_tutorResearchChipRow.Visible = true;
		UpdateTutorResearchStage(research.Stage);
		if (_tutorTitleLabel != null)
			_tutorTitleLabel.Text = research.Mode == "quick_search"
				? "✦ Eris đang tìm nhanh"
				: "✦ Eris đang nghiên cứu và kiểm chứng";
		string modeText = research.Mode == "quick_search" ? "search nhanh" : "research";
		SetTutorChip(_tutorModeChipLabel, modeText, TutorInkSoft);

		Color verifyColor = TutorGold;
		string verifyText = string.IsNullOrWhiteSpace(research.VerificationStatus) ? research.Stage : research.VerificationStatus;
		if (verifyText == "passed") verifyColor = TutorGreen;
		else if (verifyText == "failed") verifyColor = TutorRed;
		else if (research.WarningCount > 0 || research.SurfaceCaution) verifyColor = TutorWarn;
		SetTutorChip(_tutorVerifyChipLabel, TutorVerifyChipText(verifyText, research), verifyColor);

		if (research.HasMetrics)
		{
			string src = $"nguồn {research.SourceCount}";
			if (research.TrustedSourceCount > 0)
				src += $" / tin {research.TrustedSourceCount}";
			SetTutorChip(_tutorSourceChipLabel, src, research.SourceCount > 0 ? TutorGreen : TutorWarn);
			string citeText = $"cite {Mathf.RoundToInt(research.CitationCoverage * 100f)}%";
			Color citeColor = research.CitationCoverage >= 0.75f ? TutorGreen : TutorWarn;
			if (research.ContestedClaimCount > 0)
			{
				citeText = $"mâu thuẫn {research.ContestedClaimCount}";
				citeColor = TutorRed;
			}
			else if (research.OrphanClaimCount > 0)
			{
				citeText = $"mồ côi {research.OrphanClaimCount}";
				citeColor = TutorWarn;
			}
			SetTutorChip(_tutorCitationChipLabel, citeText, citeColor);
		}
		else
		{
			SetTutorChip(_tutorSourceChipLabel, "nguồn …", TutorInkSoft);
			SetTutorChip(_tutorCitationChipLabel, "cite …", TutorInkSoft);
		}

		CallDeferred(nameof(FitTutorPanelHeight));
	}

	private void UpdateTutorResearchStage(string rawStage)
	{
		if (_tutorStageRow == null)
			return;
		_tutorStageRow.Visible = true;
		string active = NormalizeTutorStage(rawStage);
		string[] order = { "queued", "collecting", "verifying", "synthesizing", "done" };
		int activeIndex = Array.IndexOf(order, active);
		for (int index = 0; index < order.Length; index++)
		{
			if (!_tutorStageLabels.TryGetValue(order[index], out Label label))
				continue;
			bool current = index == activeIndex;
			bool completed = activeIndex >= 0 && index < activeIndex;
			Color color = current ? TutorPink : completed ? TutorGreen : TutorInkSoft;
			label.AddThemeColorOverride("font_color", color);
			label.Modulate = new Color(1f, 1f, 1f, current ? 1f : completed ? 0.82f : 0.52f);
		}
	}

	private static string NormalizeTutorStage(string stage)
	{
		return (stage ?? "").Trim().ToLowerInvariant() switch
		{
			"queued" or "forming" => "queued",
			"asking_tutor" or "opening" or "asking" or "reading" => "collecting",
			"verifying" => "verifying",
			"verified" or "digesting" => "synthesizing",
			"done" => "done",
			_ => "",
		};
	}


	private void SetTutorChip(Label label, string text, Color color)
	{
		if (label == null)
			return;
		label.Text = string.IsNullOrWhiteSpace(text) ? "…" : text;
		label.AddThemeColorOverride("font_color", color);
	}

	private static ResearchPanelState TryReadResearchState(JsonElement root, string episodeId, string fallbackStage, string fallbackTopic)
	{
		if (!root.TryGetProperty("research", out var research) || research.ValueKind != JsonValueKind.Object)
			return null;

		string mode = TutorStr(research, "mode");
		string stage = TutorStr(research, "stage");
		string topic = TutorStr(research, "topic");
		if (string.IsNullOrWhiteSpace(stage))
			stage = fallbackStage ?? "";
		if (string.IsNullOrWhiteSpace(topic))
			topic = fallbackTopic ?? "";

		string verificationStatus = "";
		int orphanClaims = 0;
		int contestedClaims = 0;
		float citationCoverage = 0f;
		bool surfaceCaution = false;
		bool hasVerification = false;
		bool persisted = TutorBool(research, "persisted") || TutorBool(research, "memory_persisted");
		if (research.TryGetProperty("verification", out var verification) && verification.ValueKind == JsonValueKind.Object)
		{
			hasVerification = true;
			verificationStatus = TutorStr(verification, "overall_status");
			orphanClaims = TutorInt(verification, "orphan_claim_count");
			contestedClaims = TutorInt(verification, "contested_claim_count");
			citationCoverage = TutorFloat(verification, "citation_coverage");
			surfaceCaution = TutorBool(verification, "surface_caution");
		}

		int sourceCount = 0;
		int trustedSourceCount = 0;
		int claimCount = 0;
		int warningCount = 0;
		bool hasMetrics = false;
		if (research.TryGetProperty("metrics", out var metrics) && metrics.ValueKind == JsonValueKind.Object)
		{
			hasMetrics = true;
			if (string.IsNullOrWhiteSpace(mode)) mode = TutorStr(metrics, "mode");
			sourceCount = TutorInt(metrics, "source_count");
			trustedSourceCount = TutorInt(metrics, "trusted_source_count");
			claimCount = TutorInt(metrics, "claim_count");
			warningCount = TutorInt(metrics, "warning_count");
			if (string.IsNullOrWhiteSpace(verificationStatus)) verificationStatus = TutorStr(metrics, "verification_status");
			if (citationCoverage <= 0f) citationCoverage = TutorFloat(metrics, "citation_coverage");
			if (orphanClaims == 0) orphanClaims = TutorInt(metrics, "orphan_claim_count");
			if (contestedClaims == 0) contestedClaims = TutorInt(metrics, "contested_claim_count");
			if (!surfaceCaution) surfaceCaution = TutorBool(metrics, "should_surface_cautiously");
		}

		return new ResearchPanelState
		{
			EpisodeId = episodeId ?? "",
			Mode = string.IsNullOrWhiteSpace(mode) ? "research" : mode,
			Stage = stage,
			Topic = topic,
			VerificationStatus = verificationStatus,
			SourceCount = sourceCount,
			TrustedSourceCount = trustedSourceCount,
			ClaimCount = claimCount,
			OrphanClaimCount = orphanClaims,
			ContestedClaimCount = contestedClaims,
			WarningCount = warningCount,
			CitationCoverage = Mathf.Clamp(citationCoverage, 0f, 1f),
			SurfaceCaution = surfaceCaution,
			HasMetrics = hasMetrics,
			HasVerification = hasVerification,
			Persisted = persisted,
		};
	}

	private void SetTutorQuestion(string question)
	{
		if (_tutorQuestionLabel == null)
			return;
		KillTutorTypeTween();
		_tutorQuestionLabel.Text = string.IsNullOrWhiteSpace(question) ? "" : question;
		_tutorQuestionLabel.VisibleCharacters = -1;
		CallDeferred(nameof(FitTutorPanelHeight));
	}

	private void TypeTutorText(RichTextLabel label, string text)
	{
		if (label == null)
			return;
		KillTutorTypeTween();
		text ??= "";
		// Đặt FULL text ngay để panel tự dãn đúng chiều cao một lần (ổn định, không
		// giật), rồi chỉ animate số ký tự hiện ra bằng VisibleCharacters.
		label.Text = text;
		label.VisibleCharacters = 0;
		CallDeferred(nameof(FitTutorPanelHeight));
		if (text.Length == 0)
		{
			label.VisibleCharacters = -1;
			return;
		}
		float dur = Mathf.Max(0.2f, text.Length * 0.035f);
		_tutorTypeTween = CreateTween();
		_tutorTypeTween.TweenProperty(label, "visible_characters", text.Length, dur).From(0);
		_tutorTypeTween.TweenCallback(Callable.From(() =>
		{
			if (label != null) label.VisibleCharacters = -1;
		}));
	}

	// Tự dãn chiều cao panel theo nội dung (câu hỏi/trả lời dài ngắn khác nhau).
	// Rộng cố định -> RichTextLabel wrap -> GetCombinedMinimumSize cho chiều cao thật.
	private void FitTutorPanelHeight()
	{
		if (_tutorPanel == null || _tutorRoot == null || !_tutorPanel.Visible)
			return;
		float content = _tutorRoot.GetCombinedMinimumSize().Y;
		float wanted = Mathf.Max(TutorPanelMinSize.Y, content + 24f); // 12 trên + 12 dưới
		float screenY = GetViewportRect().Size.Y;
		float maxH = Mathf.Max(TutorPanelMinSize.Y, screenY - _tutorPanel.Position.Y - 16f);
		float h = Mathf.Min(wanted, maxH);
		_tutorPanel.Size = new Vector2(TutorPanelMinSize.X, h);
	}

	private void KillTutorTypeTween()
	{
		if (_tutorTypeTween != null && _tutorTypeTween.IsValid())
			_tutorTypeTween.Kill();
		_tutorTypeTween = null;
	}

	private void StartTutorDots(string baseText)
	{
		if (_tutorStatusLabel == null)
			return;
		if (_tutorDotsTween != null && _tutorDotsTween.IsValid())
			_tutorDotsTween.Kill();
		_tutorDotsTween = CreateTween().SetLoops();
		_tutorDotsTween.TweenMethod(
			Callable.From<int>(n => { if (_tutorStatusLabel != null) _tutorStatusLabel.Text = baseText + new string('.', n % 4); }),
			0, 3, 1.2f);
	}

	private void StopTutorDots(string finalText)
	{
		if (_tutorDotsTween != null && _tutorDotsTween.IsValid())
			_tutorDotsTween.Kill();
		_tutorDotsTween = null;
		if (_tutorStatusLabel != null)
			_tutorStatusLabel.Text = finalText ?? "";
	}

	// "Thở" nhẹ toàn panel khi Eris đang dùng lane nghiên cứu — tín hiệu để Ngài biết.
	private void StartTutorPulse()
	{
		if (_tutorPanel == null)
			return;
		if (_tutorPulseTween != null && _tutorPulseTween.IsValid())
			return; // đang thở rồi, đừng chồng tween
		_tutorPulseTween = CreateTween().SetLoops();
		_tutorPulseTween.TweenProperty(_tutorPanel, "modulate:a", 0.70f, 0.7f)
			.SetTrans(Tween.TransitionType.Sine).SetEase(Tween.EaseType.InOut);
		_tutorPulseTween.TweenProperty(_tutorPanel, "modulate:a", 1.0f, 0.7f)
			.SetTrans(Tween.TransitionType.Sine).SetEase(Tween.EaseType.InOut);
	}

	private void StopTutorPulse()
	{
		if (_tutorPulseTween != null && _tutorPulseTween.IsValid())
			_tutorPulseTween.Kill();
		_tutorPulseTween = null;
		if (_tutorPanel != null)
			_tutorPanel.Modulate = new Color(1f, 1f, 1f, 1f);
	}

	private void HideTutorPanel()
	{
		if (_tutorPanel == null)
			return;
		_tutorPanel.Visible = false;
		_tutorHideAtMs = 0;
		_tutorResearchState = null;
		UpdateTutorResearchChips(null);
		StopTutorDots("");
		StopTutorPulse();
		KillTutorTypeTween();
	}

	private void ShowTutorPanel()
	{
		if (_tutorPanel == null)
			return;
		_tutorPanel.Visible = true;
		_tutorPanel.ClipContents = true;
		_tutorPanel.Size = TutorPanelMinSize;
		Vector2 screen = GetViewportRect().Size;
		_tutorPanel.Position = new Vector2(
			Mathf.Max(12f, screen.X - TutorPanelMinSize.X - 30f),
			48f);
		CallDeferred(nameof(FitTutorPanelHeight));
	}

	private void ScheduleTutorHide()
	{
		if (TutorPanelAutoHideSeconds <= 0f)
		{
			_tutorHideAtMs = 0;
			return;
		}
		_tutorHideAtMs = Time.GetTicksMsec() + (ulong)(TutorPanelAutoHideSeconds * 1000f);
	}

	private void UpdateTutorPanelAutoHide()
	{
		if (_tutorPanel == null || !_tutorPanel.Visible || _tutorHideAtMs == 0)
			return;
		if (Time.GetTicksMsec() < _tutorHideAtMs)
			return;
		_tutorPanel.Visible = false;
		_tutorHideAtMs = 0;
		_tutorResearchState = null;
		UpdateTutorResearchChips(null);
		StopTutorDots("");
		StopTutorPulse();
		KillTutorTypeTween();
	}

	private static string TutorStr(JsonElement obj, string key)
	{
		if (obj.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String)
			return v.GetString() ?? "";
		return "";
	}

	private static int TutorInt(JsonElement obj, string key)
	{
		if (obj.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out int n))
			return n;
		return 0;
	}

	private static float TutorFloat(JsonElement obj, string key)
	{
		if (obj.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetSingle(out float n))
			return n;
		return 0f;
	}

	private static bool TutorBool(JsonElement obj, string key)
	{
		if (!obj.TryGetProperty(key, out var v))
			return false;
		if (v.ValueKind == JsonValueKind.True) return true;
		if (v.ValueKind == JsonValueKind.False) return false;
		if (v.ValueKind == JsonValueKind.String)
		{
			string text = v.GetString() ?? "";
			return text.Equals("true", StringComparison.OrdinalIgnoreCase)
				|| text.Equals("1", StringComparison.OrdinalIgnoreCase)
				|| text.Equals("yes", StringComparison.OrdinalIgnoreCase);
		}
		return false;
	}
}
