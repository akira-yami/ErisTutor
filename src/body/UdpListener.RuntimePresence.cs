using Godot;
using System;
using System.Collections.Generic;
using System.Text;
using System.Text.Json;

public partial class UdpListener : Node2D
{
	// Contract panel chính cho runtime mới. emotion_debug vẫn được giữ như lớp
	// compatibility, nhưng khí sắc sống và trạng thái sân khấu phải đi từ
	// eris.stage v2; nếu không panel chỉ là ảnh chụp cũ mặc áo mới.
	private sealed class RuntimePresenceState
	{
		public readonly Dictionary<string, float> Affect = new(StringComparer.Ordinal);
		public string Source = "";
		public string StageSource = "";
		public string Stage = "idle";
		public string Emotion = "";
		public string Motion = "";
		public string TurnId = "";
		public string LastEventType = "";
		public string LifecycleState = "IDLE";
		public string StatusLabel = "";
		public string StatusTone = "active";
		public string RuntimeActivityKey = "";
		public string RuntimeActivityLabel = "";
		public string RuntimeActivityTone = "info";
		public string RuntimeActivityBaseLabel = "";
		public int RuntimeActivityPriority;
		public int RuntimeActivityWarningLevel;
		public bool RuntimeActivityActive;
		public ulong RuntimeActivityNoticeUntilMs;
		public ulong RuntimeActivityUpdatedAtMs;
		public string SessionId = "";
		public long EventSeq;
		public float ThoughtPressure;
		public float Confidence;
		public bool DiaryUpdated;
		public bool Speaking;
		public bool Thinking;
		public bool ReachedSpeaking;
		public ulong NoticeUntilMs;
		public ulong LastPacketAtMs;
		public ulong LastProgressAtMs;
		public int ProgressWarningLevel;
		public ulong LastAffectAtMs;
	}

	private readonly RuntimePresenceState _runtimePresence = new();
	private ulong _runtimePresenceLastUiRefreshMs;
	private PanelContainer _runtimePresenceIndicator;
	private Label _runtimePresenceIndicatorLabel;
	private StyleBoxFlat _runtimePresenceIndicatorStyle;
	private string _runtimePresenceAvatarReadySession = "";

	private void TryCaptureRuntimePresence(string raw)
	{
		if (string.IsNullOrWhiteSpace(raw))
			return;
		try
		{
			using JsonDocument doc = JsonDocument.Parse(raw);
			JsonElement root = doc.RootElement;
			if (root.ValueKind != JsonValueKind.Object)
				return;

			string protocol = RuntimePresenceString(root, "protocol");
			string eventType = RuntimePresenceString(root, "type").Trim().ToLowerInvariant();
			bool stageProtocol = string.Equals(protocol, "eris.stage", StringComparison.OrdinalIgnoreCase);
			if (!stageProtocol && !RuntimePresenceEventTypes.Contains(eventType))
				return;

			JsonElement data = RuntimePresenceObject(root, "data");
			string sessionId = RuntimePresenceString(root, "session_id");
			long eventSeq = RuntimePresenceLong(root, "event_seq", 0L);
			if (stageProtocol)
			{
				EnsureStageAvatarReady(sessionId);
				SendStageAck(sessionId, eventSeq, eventType);
				if (!AcceptStageEvent(sessionId, eventSeq))
					return;
			}

			_runtimePresence.LastPacketAtMs = Time.GetTicksMsec();
			_runtimePresence.LastEventType = eventType;
			string turnId = RuntimePresenceString(root, "turn_id");
			if (string.IsNullOrWhiteSpace(turnId))
				turnId = RuntimePresenceString(data, "turn_id");
			if (!string.IsNullOrWhiteSpace(turnId))
			{
				if (!string.Equals(_runtimePresence.TurnId, turnId, StringComparison.Ordinal))
				{
					_runtimePresence.ReachedSpeaking = false;
					_runtimePresence.NoticeUntilMs = 0;
				}
				_runtimePresence.TurnId = turnId;
			}

			string lifecycleState = RuntimePresenceString(data, "state").Trim().ToUpperInvariant();
			if (!string.IsNullOrWhiteSpace(lifecycleState))
				_runtimePresence.LifecycleState = lifecycleState;
			string statusLabel = RuntimePresenceString(data, "label").Trim();
			if (eventType != "runtime_status" && !string.IsNullOrWhiteSpace(statusLabel))
				_runtimePresence.StatusLabel = statusLabel;
			string topSource = RuntimePresenceString(root, "source");
			string dataSource = RuntimePresenceString(data, "source");
			string resolvedSource = !string.IsNullOrWhiteSpace(dataSource) ? dataSource : topSource;
			if (!string.IsNullOrWhiteSpace(resolvedSource))
				_runtimePresence.StageSource = resolvedSource;

			if (eventType is "affect_state" or "cognitive_affect" or "inner_affect")
			{
				JsonElement affect = RuntimePresenceObject(data, "affect");
				if (affect.ValueKind != JsonValueKind.Object)
					affect = RuntimePresenceObject(root, "affect");
				RuntimePresenceReadAffect(affect);
				_runtimePresence.ThoughtPressure = RuntimePresenceFloat(data, "thought_pressure", _runtimePresence.ThoughtPressure, signed: false);
				_runtimePresence.Confidence = RuntimePresenceFloat(data, "confidence", _runtimePresence.Confidence, signed: false);
				_runtimePresence.DiaryUpdated = RuntimePresenceBool(data, "diary_updated", false);
				if (!string.IsNullOrWhiteSpace(resolvedSource))
					_runtimePresence.Source = resolvedSource;
				_runtimePresence.LastAffectAtMs = _runtimePresence.LastPacketAtMs;
			}
			else if (data.ValueKind == JsonValueKind.Object && data.TryGetProperty("affect", out JsonElement nestedAffect))
			{
				RuntimePresenceReadAffect(nestedAffect);
				if (!string.IsNullOrWhiteSpace(resolvedSource))
					_runtimePresence.Source = resolvedSource;
				_runtimePresence.LastAffectAtMs = _runtimePresence.LastPacketAtMs;
			}

			string explicitEmotion = RuntimePresenceString(root, "emotion").Trim().ToLowerInvariant();
			string explicitMotion = RuntimePresenceString(root, "motion").Trim().ToLowerInvariant();
			if (!string.IsNullOrWhiteSpace(explicitEmotion))
				_runtimePresence.Emotion = explicitEmotion;
			if (!string.IsNullOrWhiteSpace(explicitMotion))
				_runtimePresence.Motion = explicitMotion;

			if (eventType == "runtime_status")
				ApplyRuntimeActivityStatus(data);

			switch (eventType)
			{
				case "received":
					SetRuntimePresenceThinking("received");
					break;
				case "thinking_started":
					SetRuntimePresenceThinking("thinking");
					break;
				case "content_committed":
					SetRuntimePresenceThinking("content_committed");
					break;
				case "audio_buffering":
				case "stage_segment_prepare":
				case "stage_segment_queued":
				case "tts_audio_ready":
					SetRuntimePresenceThinking("audio_buffering");
					break;
				case "thinking_finished":
					ApplyThinkingFinishedTarget(lifecycleState);
					break;
				case "speaking":
				case "audio_playback_started":
					_runtimePresence.Speaking = true;
					_runtimePresence.Thinking = false;
					_runtimePresence.ReachedSpeaking = true;
					_runtimePresence.NoticeUntilMs = 0;
					_runtimePresence.StatusLabel = "";
					_runtimePresence.StatusTone = "success";
					_runtimePresence.LastProgressAtMs = Time.GetTicksMsec();
					_runtimePresence.ProgressWarningLevel = 0;
					_runtimePresence.Stage = "speaking";
					break;
				case "degraded":
				{
					// V62: Surface/provider hụt một lượt không có nghĩa autonomous
					// "chết". Lifecycle data có channel nên UI phân biệt lỗi tạm
					// của lời chủ động với lỗi runtime thật.
					string degradedChannel = RuntimePresenceString(data, "channel").Trim().ToLowerInvariant();
					string degradedReason = RuntimePresenceString(root, "reason").Trim().ToLowerInvariant();
					if (degradedChannel == "autonomous" && degradedReason.StartsWith("surface_"))
						SetRuntimePresenceTerminal("autonomous_retry");
					else
						SetRuntimePresenceTerminal("degraded");
					break;
				}
				case "interrupted":
					SetRuntimePresenceTerminal("interrupted");
					break;
				case "audio_playback_done":
					SetRuntimePresenceTerminal("afterglow");
					break;
				case "done":
					SetRuntimePresenceTerminal("afterglow", showSilentNotice: !_runtimePresence.ReachedSpeaking);
					break;
				case "stage_segment_cancelled":
				case "stage_segment_failed":
				case "continuation_cancelled":
					SetRuntimePresenceTerminal("afterglow");
					break;
				case "stage_segment":
					if (!_runtimePresence.Speaking && !_runtimePresence.Thinking)
						_runtimePresence.Stage = "staged";
					break;
				case "show_plan":
					if (!_runtimePresence.Speaking && !_runtimePresence.Thinking)
						_runtimePresence.Stage = "planning";
					break;
			}

			ApplyRuntimePresencePose(eventType, lifecycleState);
			RefreshRuntimePresenceIndicator();
			_latestEmotionDebug = MergeRuntimePresence(_latestEmotionDebug);
			if (_emotionPanel != null && _emotionPanel.Visible)
				RefreshEmotionPanel();
		}
		catch (JsonException)
		{
			// Packet text/legacy không thuộc stage protocol, bỏ qua im lặng.
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[RuntimePresence] capture failed: {ex.Message}");
		}
	}

	private void TickRuntimePresencePanel()
	{
		ulong now = Time.GetTicksMsec();
		bool indicatorChanged = false;
		if (_runtimePresence.NoticeUntilMs > 0 && now >= _runtimePresence.NoticeUntilMs)
		{
			_runtimePresence.NoticeUntilMs = 0;
			_runtimePresence.StatusLabel = "";
			indicatorChanged = true;
		}
		if (_runtimePresence.RuntimeActivityNoticeUntilMs > 0 && now >= _runtimePresence.RuntimeActivityNoticeUntilMs)
		{
			_runtimePresence.RuntimeActivityNoticeUntilMs = 0;
			_runtimePresence.RuntimeActivityLabel = "";
			_runtimePresence.RuntimeActivityBaseLabel = "";
			_runtimePresence.RuntimeActivityKey = "";
			_runtimePresence.RuntimeActivityPriority = 0;
			_runtimePresence.RuntimeActivityWarningLevel = 0;
			indicatorChanged = true;
		}

		// Đã gỡ bỏ logic tự động báo "Có vẻ đang bị kẹt" theo thời gian
		// để chỉ báo lỗi khi thực sự có lỗi từ backend trả về.

		if (indicatorChanged)
			RefreshRuntimePresenceIndicator();

		if (_emotionPanel == null || !_emotionPanel.Visible)
			return;
		if (now - _runtimePresenceLastUiRefreshMs < 500)
			return;
		_runtimePresenceLastUiRefreshMs = now;

		ulong age = _runtimePresence.LastPacketAtMs > 0 ? now - _runtimePresence.LastPacketAtMs : ulong.MaxValue;
		if (!_runtimePresence.Speaking && !_runtimePresence.Thinking && age > 12000)
			_runtimePresence.Stage = "idle";
		if (age > 45000)
		{
			_runtimePresence.Emotion = "";
			_runtimePresence.Motion = "";
		}
		_latestEmotionDebug = MergeRuntimePresence(_latestEmotionDebug);
		RefreshEmotionPanel();
	}

	private void SetRuntimePresenceThinking(string stage)
	{
		_runtimePresence.Thinking = true;
		_runtimePresence.Speaking = false;
		_runtimePresence.NoticeUntilMs = 0;
		_runtimePresence.Stage = stage;
		_runtimePresence.LastProgressAtMs = Time.GetTicksMsec();
		_runtimePresence.ProgressWarningLevel = 0;
		_runtimePresence.StatusLabel = stage switch
		{
			"received" => "Đã nhận…",
			"content_committed" => "Đang chuẩn bị trả lời…",
			"audio_buffering" => "Đang chuẩn bị giọng…",
			_ => "Đang suy nghĩ…",
		};
		_runtimePresence.StatusTone = stage switch
		{
			"received" => "info",
			"content_committed" => "success",
			"audio_buffering" => "info",
			_ => "active",
		};
	}

	private void SetRuntimePresenceTerminal(string stage, bool showSilentNotice = false)
	{
		_runtimePresence.Speaking = false;
		_runtimePresence.Thinking = false;
		_runtimePresence.ProgressWarningLevel = 0;
		_runtimePresence.Stage = stage;
		_ttsAudioIsPlaying = false;
		ClearTtsBodyAudioMute();
		if (_runtimePresence.RuntimeActivityKey == "agent_turn"
			|| _runtimePresence.RuntimeActivityKey.StartsWith("agent_")
			|| _runtimePresence.RuntimeActivityLabel.Contains("đang nói")
			|| _runtimePresence.RuntimeActivityLabel.Contains("suy nghĩ"))
		{
			_runtimePresence.RuntimeActivityActive = false;
			_runtimePresence.RuntimeActivityLabel = "";
			_runtimePresence.RuntimeActivityBaseLabel = "";
			_runtimePresence.RuntimeActivityKey = "";
			_runtimePresence.RuntimeActivityPriority = 0;
			_runtimePresence.RuntimeActivityWarningLevel = 0;
			_runtimePresence.RuntimeActivityNoticeUntilMs = 0;
		}
		ulong now = Time.GetTicksMsec();
		if (showSilentNotice)
		{
			_runtimePresence.StatusLabel = "Đã chọn im lặng";
			_runtimePresence.StatusTone = "warning";
			_runtimePresence.NoticeUntilMs = now + 5200;
		}
		else if (stage == "autonomous_retry")
		{
			_runtimePresence.StatusLabel = "Lời chủ động hụt một nhịp, sẽ thử lại";
			_runtimePresence.StatusTone = "warning";
			_runtimePresence.NoticeUntilMs = now + 3600;
		}
		else if (stage == "degraded")
		{
			_runtimePresence.StatusLabel = "Có lỗi, nhưng vẫn còn chạy";
			_runtimePresence.StatusTone = "error";
			_runtimePresence.NoticeUntilMs = now + 8000;
		}
		else if (stage == "interrupted")
		{
			_runtimePresence.StatusLabel = "Đã bị ngắt";
			_runtimePresence.StatusTone = "warning";
			_runtimePresence.NoticeUntilMs = now + 3600;
		}
		else
		{
			_runtimePresence.StatusLabel = "";
			_runtimePresence.StatusTone = "muted";
			_runtimePresence.NoticeUntilMs = 0;
		}
	}

	private void ApplyThinkingFinishedTarget(string lifecycleState)
	{
		switch (lifecycleState)
		{
			case "SPEAKING":
				_runtimePresence.Speaking = true;
				_runtimePresence.Thinking = false;
				_runtimePresence.ReachedSpeaking = true;
				_runtimePresence.NoticeUntilMs = 0;
				_runtimePresence.StatusLabel = "";
				_runtimePresence.StatusTone = "success";
				_runtimePresence.LastProgressAtMs = Time.GetTicksMsec();
				_runtimePresence.ProgressWarningLevel = 0;
				_runtimePresence.Stage = "speaking";
				break;
			case "DEGRADED":
				SetRuntimePresenceTerminal("degraded");
				break;
			case "INTERRUPTED":
				SetRuntimePresenceTerminal("interrupted");
				break;
			case "DONE":
				SetRuntimePresenceTerminal("afterglow", showSilentNotice: !_runtimePresence.ReachedSpeaking);
				break;
			default:
				_runtimePresence.Thinking = false;
				_runtimePresence.ProgressWarningLevel = 0;
				_runtimePresence.Stage = "settling";
				_runtimePresence.StatusLabel = "Đang chờ…";
				_runtimePresence.StatusTone = "muted";
				_runtimePresence.NoticeUntilMs = Time.GetTicksMsec() + 2200;
				break;
		}
	}

	private void ApplyRuntimeActivityStatus(JsonElement data)
	{
		if (data.ValueKind != JsonValueKind.Object)
			return;
		string activity = RuntimePresenceString(data, "activity").Trim().ToLowerInvariant();
		string label = RuntimePresenceString(data, "label").Trim();
		string tone = RuntimePresenceString(data, "tone").Trim().ToLowerInvariant();
		string phase = RuntimePresenceString(data, "phase").Trim().ToLowerInvariant();
		bool active = RuntimePresenceBool(data, "active", true);
		int priority = RuntimePresenceInt(data, "priority", 50);
		int noticeMs = RuntimePresenceInt(data, "notice_ms", 0);
		ulong now = Time.GetTicksMsec();

		if (string.IsNullOrWhiteSpace(activity))
			activity = "runtime";
		if (string.IsNullOrWhiteSpace(tone))
			tone = active ? "info" : "muted";

		bool sameActivity = string.Equals(_runtimePresence.RuntimeActivityKey, activity, StringComparison.Ordinal);
		bool currentExpired = !_runtimePresence.RuntimeActivityActive
			&& _runtimePresence.RuntimeActivityNoticeUntilMs <= now;
		if (!sameActivity && !currentExpired && priority < _runtimePresence.RuntimeActivityPriority)
			return;

		_runtimePresence.RuntimeActivityKey = activity;
		_runtimePresence.RuntimeActivityPriority = Math.Clamp(priority, 0, 100);
		_runtimePresence.RuntimeActivityTone = NormalizeRuntimePresenceTone(tone);
		_runtimePresence.RuntimeActivityUpdatedAtMs = now;
		_runtimePresence.RuntimeActivityWarningLevel = 0;
		if (!string.IsNullOrWhiteSpace(label))
		{
			_runtimePresence.RuntimeActivityLabel = label;
			_runtimePresence.RuntimeActivityBaseLabel = label;
		}

		if (active)
		{
			_runtimePresence.RuntimeActivityActive = true;
			_runtimePresence.RuntimeActivityNoticeUntilMs = 0;
		}
		else
		{
			_runtimePresence.RuntimeActivityActive = false;
			_runtimePresence.RuntimeActivityNoticeUntilMs = noticeMs > 0
				? now + (ulong)Math.Clamp(noticeMs, 0, 30000)
				: 0;
			if (_runtimePresence.RuntimeActivityNoticeUntilMs == 0)
			{
				_runtimePresence.RuntimeActivityLabel = "";
				_runtimePresence.RuntimeActivityBaseLabel = "";
				_runtimePresence.RuntimeActivityKey = "";
				_runtimePresence.RuntimeActivityPriority = 0;
				_runtimePresence.RuntimeActivityWarningLevel = 0;
			}
		}

		if (activity == "startup" && !string.IsNullOrWhiteSpace(phase))
			_runtimePresence.StageSource = $"startup:{phase}";
	}

	private void ApplyRuntimePresencePose(string eventType, string lifecycleState)
	{
		if (_isLive2DMode || _motion == null || _body == null)
			return;
		bool thinkingEvent = eventType is "received" or "thinking_started" or "content_committed" or "audio_buffering";
		if (thinkingEvent)
		{
			_lastRemoteMotionAt = GetRuntimeSeconds();
			if (!string.Equals(_motion.CurrentAction, "read", StringComparison.Ordinal))
				_motion.ApplySilentPose("read", -1);
			return;
		}
		bool terminalEvent = eventType is "done" or "degraded" or "interrupted"
			or "audio_playback_done" or "stage_segment_cancelled" or "stage_segment_failed"
			or "continuation_cancelled";
		bool thinkingFinishedTerminal = eventType == "thinking_finished"
			&& (lifecycleState == "DEGRADED" || lifecycleState == "INTERRUPTED" || lifecycleState == "DONE");
		if ((terminalEvent || thinkingFinishedTerminal)
			&& !string.Equals(_motion.CurrentAction, "stand", StringComparison.Ordinal))
		{
			_motion.ApplySilentPose("stand");
		}
	}

	private void RefreshRuntimePresenceIndicator()
	{
		RefreshConversationPresenceFromRuntime();
		ulong now = Time.GetTicksMsec();
		bool bubbleVisible = ActiveSpeechBubble()?.Visible == true;
		bool thinkingVisible = _runtimePresence.Thinking && !_runtimePresence.Speaking;
		bool noticeVisible = _runtimePresence.NoticeUntilMs > now;
		bool activityVisible = (_runtimePresence.RuntimeActivityActive
			|| _runtimePresence.RuntimeActivityNoticeUntilMs > now)
			&& !_runtimePresence.Speaking
			&& !bubbleVisible;
		bool visible = (thinkingVisible || noticeVisible || activityVisible) && !bubbleVisible;
		if (!visible && _runtimePresenceIndicator == null)
			return;
		EnsureRuntimePresenceIndicator();
		if (_runtimePresenceIndicator == null || _runtimePresenceIndicatorLabel == null)
			return;

		string label;
		string tone;
		if (thinkingVisible || noticeVisible)
		{
			label = string.IsNullOrWhiteSpace(_runtimePresence.StatusLabel)
				? "Đang suy nghĩ…"
				: _runtimePresence.StatusLabel;
			tone = _runtimePresence.StatusTone;
		}
		else
		{
			label = string.IsNullOrWhiteSpace(_runtimePresence.RuntimeActivityLabel)
				? "Đang chạy…"
				: _runtimePresence.RuntimeActivityLabel;
			tone = _runtimePresence.RuntimeActivityTone;
		}

		bool wasVisible = _runtimePresenceIndicator.Visible;
		EnsureControlOnCanvasLayer(_runtimePresenceIndicator, 1000);
		_runtimePresenceIndicatorLabel.Text = label;
		ApplyRuntimePresenceIndicatorTone(tone);
		ApplyRuntimePresenceIndicatorWidth(label);
		_runtimePresenceIndicator.Visible = visible;
		if (visible)
			EnsureControlOnCanvasLayer(_runtimePresenceIndicator, 1000);

		// Visibility của indicator thuộc lifecycle backend, tuyệt đối không phụ thuộc
		// CharacterContextMenu. Refresh cả CanvasLayer và trạng thái click-through ngay.
		if (wasVisible != visible || visible)
			ScheduleTransientOverlayRefresh();
	}

	private void EnsureRuntimePresenceIndicator()
	{
		if (_runtimePresenceIndicator != null && GodotObject.IsInstanceValid(_runtimePresenceIndicator))
			return;
		Control root = UiOverlayRoot();
		if (root == null)
			return;

		var panel = new PanelContainer
		{
			Name = "RuntimePresenceIndicator",
			Visible = false,
			MouseFilter = Control.MouseFilterEnum.Ignore,
			ZIndex = 1000,
			ZAsRelative = false,
			ClipContents = false,
			CustomMinimumSize = new Vector2(196f, 38f),
		};
		panel.AnchorLeft = 0.5f;
		panel.AnchorRight = 0.5f;
		panel.AnchorTop = 0f;
		panel.AnchorBottom = 0f;
		panel.OffsetLeft = -98f;
		panel.OffsetRight = 98f;
		panel.OffsetTop = 18f;
		panel.OffsetBottom = 56f;
		var indicatorStyle = new StyleBoxFlat
		{
			BgColor = new Color(0.035f, 0.10f, 0.065f, 0.92f),
			BorderColor = new Color(0.21f, 0.79f, 0.47f, 0.82f),
			BorderWidthLeft = 1,
			BorderWidthTop = 1,
			BorderWidthRight = 1,
			BorderWidthBottom = 1,
			CornerRadiusTopLeft = 18,
			CornerRadiusTopRight = 18,
			CornerRadiusBottomLeft = 18,
			CornerRadiusBottomRight = 18,
			ContentMarginLeft = 16f,
			ContentMarginRight = 16f,
			ContentMarginTop = 7f,
			ContentMarginBottom = 7f,
			ShadowColor = new Color(0f, 0f, 0f, 0.28f),
			ShadowSize = 3,
		};
		panel.AddThemeStyleboxOverride("panel", indicatorStyle);

		var label = new Label
		{
			Text = "Đang suy nghĩ…",
			HorizontalAlignment = HorizontalAlignment.Center,
			VerticalAlignment = VerticalAlignment.Center,
			MouseFilter = Control.MouseFilterEnum.Ignore,
		};
		label.AddThemeColorOverride("font_color", new Color(0.91f, 0.98f, 0.94f, 1f));
		label.AddThemeFontSizeOverride("font_size", 14);
		panel.AddChild(label);
		root.AddChild(panel);
		_runtimePresenceIndicator = panel;
		_runtimePresenceIndicatorLabel = label;
		_runtimePresenceIndicatorStyle = indicatorStyle;
	}

	private void ApplyRuntimePresenceIndicatorTone(string tone)
	{
		if (_runtimePresenceIndicatorStyle == null || _runtimePresenceIndicatorLabel == null)
			return;
		string normalized = NormalizeRuntimePresenceTone(tone);
		Color background;
		Color border;
		Color text;
		switch (normalized)
		{
			case "info":
				background = new Color(0.035f, 0.085f, 0.12f, 0.94f);
				border = new Color(0.25f, 0.67f, 0.90f, 0.90f);
				text = new Color(0.91f, 0.97f, 1f, 1f);
				break;
			case "warning":
				background = new Color(0.14f, 0.085f, 0.025f, 0.95f);
				border = new Color(0.96f, 0.57f, 0.20f, 0.95f);
				text = new Color(1f, 0.94f, 0.83f, 1f);
				break;
			case "error":
				background = new Color(0.15f, 0.035f, 0.04f, 0.96f);
				border = new Color(0.94f, 0.27f, 0.30f, 0.96f);
				text = new Color(1f, 0.90f, 0.90f, 1f);
				break;
			case "muted":
				background = new Color(0.075f, 0.075f, 0.08f, 0.92f);
				border = new Color(0.47f, 0.48f, 0.52f, 0.82f);
				text = new Color(0.91f, 0.91f, 0.93f, 1f);
				break;
			default:
				background = new Color(0.035f, 0.10f, 0.065f, 0.94f);
				border = new Color(0.21f, 0.79f, 0.47f, 0.90f);
				text = new Color(0.91f, 0.98f, 0.94f, 1f);
				break;
		}
		_runtimePresenceIndicatorStyle.BgColor = background;
		_runtimePresenceIndicatorStyle.BorderColor = border;
		_runtimePresenceIndicatorLabel.AddThemeColorOverride("font_color", text);
	}

	private void ApplyRuntimePresenceIndicatorWidth(string text)
	{
		if (_runtimePresenceIndicator == null)
			return;
		float width = Mathf.Clamp(82f + Math.Max(0, (text ?? "").Length) * 7.15f, 196f, 430f);
		_runtimePresenceIndicator.CustomMinimumSize = new Vector2(width, 38f);
		_runtimePresenceIndicator.OffsetLeft = -width * 0.5f;
		_runtimePresenceIndicator.OffsetRight = width * 0.5f;
	}

	private static string NormalizeRuntimePresenceTone(string tone)
	{
		string normalized = (tone ?? "").Trim().ToLowerInvariant();
		return normalized is "info" or "active" or "success" or "warning" or "error" or "muted"
			? normalized
			: "active";
	}

	private bool AcceptStageEvent(string sessionId, long eventSeq)
	{
		if (!string.IsNullOrWhiteSpace(sessionId)
			&& !string.Equals(sessionId, _runtimePresence.SessionId, StringComparison.Ordinal))
		{
			_runtimePresence.SessionId = sessionId;
			_runtimePresence.EventSeq = 0;
			_runtimePresence.TurnId = "";
			_runtimePresence.Speaking = false;
			_runtimePresence.Thinking = false;
			_runtimePresence.ReachedSpeaking = false;
			_runtimePresence.NoticeUntilMs = 0;
			_runtimePresence.StatusLabel = "";
			_runtimePresence.ProgressWarningLevel = 0;
			_runtimePresence.RuntimeActivityKey = "";
			_runtimePresence.RuntimeActivityLabel = "";
			_runtimePresence.RuntimeActivityBaseLabel = "";
			_runtimePresence.RuntimeActivityPriority = 0;
			_runtimePresence.RuntimeActivityWarningLevel = 0;
			_runtimePresence.RuntimeActivityActive = false;
			_runtimePresence.RuntimeActivityNoticeUntilMs = 0;
		}
		if (eventSeq <= 0)
			return true;
		if (eventSeq <= _runtimePresence.EventSeq)
			return false;
		_runtimePresence.EventSeq = eventSeq;
		return true;
	}

	private void EnsureStageAvatarReady(string sessionId)
	{
		if (string.IsNullOrWhiteSpace(sessionId)
			|| string.Equals(_runtimePresenceAvatarReadySession, sessionId, StringComparison.Ordinal))
			return;
		_runtimePresenceAvatarReadySession = sessionId;
		SendStageInbound(new
		{
			protocol = "eris.stage",
			version = 2,
			type = "avatar_ready",
			session_id = sessionId,
			timestamp = Time.GetUnixTimeFromSystem(),
		});
	}

	private void SendStageAck(string sessionId, long eventSeq, string eventType)
	{
		if (eventSeq <= 0 || string.IsNullOrWhiteSpace(sessionId))
			return;
		if (string.Equals(eventType, "stage_ack_received", StringComparison.Ordinal)
			|| string.Equals(eventType, "avatar_ready_ack", StringComparison.Ordinal)
			|| eventType.EndsWith("_ack", StringComparison.Ordinal))
			return;
		SendStageInbound(new
		{
			protocol = "eris.stage",
			version = 2,
			type = "stage_ack",
			session_id = sessionId,
			event_seq = eventSeq,
			timestamp = Time.GetUnixTimeFromSystem(),
			data = new { event_seq = eventSeq },
		});
	}

	private void SendStageInbound(object payload)
	{
		try
		{
			string raw = JsonSerializer.Serialize(payload);
			_chatSend.PutPacket(Encoding.UTF8.GetBytes(raw));
		}
		catch (Exception ex)
		{
			GD.PrintErr($"[RuntimePresence] inbound stage event failed: {ex.Message}");
		}
	}

	private EmotionDebug MergeRuntimePresence(EmotionDebug baseline)
	{
		var items = new List<EmotionDebugItem>();
		var occupied = new HashSet<string>(StringComparer.Ordinal);

		void AddNumeric(string key, string label, float value, string group, string note = "")
		{
			float numeric = Mathf.Clamp(value, 0f, 1f);
			items.Add(new EmotionDebugItem
			{
				Key = key,
				Label = label,
				Group = group,
				ValueText = numeric.ToString("0.00"),
				NumericValue = numeric,
				HasNumericValue = true,
				Note = note,
			});
			occupied.Add(key);
		}

		void AddText(string key, string label, string value, string group, string note = "")
		{
			if (string.IsNullOrWhiteSpace(value))
				return;
			items.Add(new EmotionDebugItem
			{
				Key = key,
				Label = label,
				Group = group,
				ValueText = value,
				HasNumericValue = false,
				Note = note,
			});
			occupied.Add(key);
		}

		if (_runtimePresence.LastPacketAtMs > 0)
		{
			AddText("runtime_stage", "nhịp hiện tại", RuntimePresenceStageVi(_runtimePresence.Stage), "presence");
			AddText("runtime_event", "event gần nhất", _runtimePresence.LastEventType, "presence");
			AddText("runtime_source", "nguồn affect", RuntimePresenceSourceVi(_runtimePresence.Source), "presence");
			AddText("runtime_stage_source", "nguồn sân khấu", RuntimePresenceSourceVi(_runtimePresence.StageSource), "presence");
			AddText("runtime_idle_bias", "idle bias", RuntimePresenceIdleBias(), "presence", "Xu hướng vi chuyển động, không phải motion cưỡng bức.");
			AddText("runtime_emotion", "biểu cảm sân khấu", _runtimePresence.Emotion, "surface");
			AddText("runtime_motion", "motion sân khấu", _runtimePresence.Motion, "surface");
			if (_runtimePresence.DiaryUpdated)
				AddText("runtime_diary", "inner diary", "vừa cập nhật", "presence", "Chỉ nhận metadata; không lộ nội dung nhật ký.");
		}

		float Get(string key, float fallback = 0f) => _runtimePresence.Affect.TryGetValue(key, out float value) ? value : fallback;
		bool hasLiveAffect = _runtimePresence.Affect.Count > 0;
		if (hasLiveAffect)
		{
			float arousal = Get("arousal");
			float irritation = Get("irritation");
			float tension = Get("cognitive_tension");
			float tenderness = Get("tenderness");
			float attachment = Get("attachment");
			float curiosity = Get("curiosity");
			float socialEnergy = Get("social_energy");
			float thoughtPressure = Math.Max(_runtimePresence.ThoughtPressure, tension);
			float overload = Math.Max(tension, Math.Max(irritation, arousal * 0.55f));
			float stability = 1f - Math.Max(arousal, Math.Max(irritation, tension));
			float warmth = Math.Max(tenderness, attachment * 0.68f);

			AddNumeric("calm_aroused", "arousal sống", arousal, "live_affect");
			AddNumeric("emotional_stability", "ổn định hiện tại", stability, "live_affect");
			AddNumeric("overload", "quá tải hiện tại", overload, "live_affect");
			AddNumeric("curiosity", "tò mò", curiosity, "live_affect");
			AddNumeric("irritation", "khó chịu", irritation, "live_affect");
			AddNumeric("cognitive_tension", "căng nhận thức", tension, "live_affect");
			AddNumeric("thought_stream_pressure", "áp lực suy nghĩ", thoughtPressure, "live_affect");
			AddNumeric("thought_pressure", "thought pressure", _runtimePresence.ThoughtPressure, "live_affect");
			AddNumeric("confidence", "độ tin cậy affect", _runtimePresence.Confidence, "live_affect");
			AddNumeric("attachment", "gắn bó", attachment, "live_affect");
			AddNumeric("tenderness", "dịu dàng", tenderness, "live_affect");
			AddNumeric("warmth", "độ ấm", warmth, "live_affect");
			AddNumeric("intimacy", "độ gần", attachment * 0.78f, "live_affect");
			AddNumeric("social_energy", "năng lượng xã hội", socialEnergy, "live_affect");
			AddNumeric("speech_pressure", "muốn biểu đạt", Math.Max(socialEnergy, thoughtPressure * 0.62f), "live_affect");
			AddNumeric("social_pressure", "động lực giao tiếp", socialEnergy, "live_affect");
			foreach (string key in new[] { "boredom", "protectiveness", "fatigue" })
				if (_runtimePresence.Affect.ContainsKey(key))
					AddNumeric(key, RuntimePresenceAffectLabel(key), Get(key), "live_affect");
		}

		if (baseline != null && baseline.Items != null)
		{
			foreach (EmotionDebugItem item in baseline.Items)
			{
				if (item == null || string.IsNullOrWhiteSpace(item.Key)
					|| occupied.Contains(item.Key)
					|| item.Key.StartsWith("runtime_", StringComparison.Ordinal))
					continue;
				items.Add(item);
				occupied.Add(item.Key);
			}
		}

		float valence = hasLiveAffect ? Mathf.Clamp(Get("valence"), -1f, 1f) : (baseline?.SummaryValence ?? 0f);
		bool hasValence = hasLiveAffect || (baseline?.HasSummaryValence ?? false);
		float liveIrritation = Get("irritation");
		string mood = hasLiveAffect
			? (liveIrritation >= 0.50f || valence <= -0.28f ? "kho_chiu" : valence >= 0.28f ? "vui" : "trung_tinh")
			: (baseline?.SummaryValue ?? "trung_tinh");
		string state = _runtimePresence.LastPacketAtMs > 0
			? $"{RuntimePresenceStageVi(_runtimePresence.Stage)} · {RuntimePresenceSourceVi(_runtimePresence.Source)}"
			: (baseline?.SummaryState ?? "");

		return new EmotionDebug
		{
			SummaryLabel = hasLiveAffect ? "khí sắc sống" : (baseline?.SummaryLabel ?? "khí sắc"),
			SummaryValue = mood,
			SummaryState = state,
			SummaryValence = valence,
			HasSummaryValence = hasValence,
			Items = items,
		};
	}

	private void ResetRuntimePresenceState()
	{
		_runtimePresence.Affect.Clear();
		_runtimePresence.Source = "";
		_runtimePresence.StageSource = "";
		_runtimePresence.Stage = "idle";
		_runtimePresence.Emotion = "";
		_runtimePresence.Motion = "";
		_runtimePresence.TurnId = "";
		_runtimePresence.LastEventType = "";
		_runtimePresence.LifecycleState = "IDLE";
		_runtimePresence.StatusLabel = "";
		_runtimePresence.StatusTone = "active";
		_runtimePresence.RuntimeActivityKey = "";
		_runtimePresence.RuntimeActivityLabel = "";
		_runtimePresence.RuntimeActivityTone = "info";
		_runtimePresence.RuntimeActivityBaseLabel = "";
		_runtimePresence.RuntimeActivityPriority = 0;
		_runtimePresence.RuntimeActivityWarningLevel = 0;
		_runtimePresence.RuntimeActivityActive = false;
		_runtimePresence.RuntimeActivityNoticeUntilMs = 0;
		_runtimePresence.RuntimeActivityUpdatedAtMs = 0;
		_runtimePresence.SessionId = "";
		_runtimePresence.EventSeq = 0;
		_runtimePresenceAvatarReadySession = "";
		_runtimePresence.ThoughtPressure = 0f;
		_runtimePresence.Confidence = 0f;
		_runtimePresence.DiaryUpdated = false;
		_runtimePresence.Speaking = false;
		_runtimePresence.Thinking = false;
		_runtimePresence.NoticeUntilMs = 0;
		_runtimePresence.LastPacketAtMs = 0;
		_runtimePresence.LastProgressAtMs = 0;
		_runtimePresence.ProgressWarningLevel = 0;
		_runtimePresence.LastAffectAtMs = 0;
		_runtimePresenceLastUiRefreshMs = 0;
		if (_runtimePresenceIndicator != null)
			_runtimePresenceIndicator.Visible = false;
	}

	private void RuntimePresenceReadAffect(JsonElement affect)
	{
		if (affect.ValueKind != JsonValueKind.Object)
			return;
		foreach (JsonProperty property in affect.EnumerateObject())
		{
			if (property.Value.ValueKind != JsonValueKind.Number || !property.Value.TryGetSingle(out float value))
				continue;
			value = string.Equals(property.Name, "valence", StringComparison.Ordinal)
				? Mathf.Clamp(value, -1f, 1f)
				: Mathf.Clamp(value, 0f, 1f);
			_runtimePresence.Affect[property.Name] = value;
		}
	}

	private string RuntimePresenceIdleBias()
	{
		if (_runtimePresence.Speaking)
			return "giảm idle để ưu tiên lip-sync";
		if (_runtimePresence.Thinking)
			return "ponder nhẹ";
		float fatigue = _runtimePresence.Affect.TryGetValue("fatigue", out float f) ? f : 0f;
		float curiosity = _runtimePresence.Affect.TryGetValue("curiosity", out float c) ? c : 0f;
		float tension = _runtimePresence.Affect.TryGetValue("cognitive_tension", out float t) ? t : 0f;
		if (tension >= 0.58f || _runtimePresence.ThoughtPressure >= 0.58f)
			return "nghiêng đầu / giữ nét suy nghĩ";
		if (fatigue >= 0.58f)
			return "chớp chậm / chuyển động nhỏ";
		if (curiosity >= 0.52f)
			return "liếc nhẹ / tilt tò mò";
		return "ambient micro-motion";
	}

	private static readonly HashSet<string> RuntimePresenceEventTypes = new(StringComparer.Ordinal)
	{
		"affect_state", "cognitive_affect", "inner_affect", "stage_segment", "show_plan",
		"received", "thinking_started", "content_committed", "audio_buffering", "thinking_finished",
		"speaking", "done", "degraded", "interrupted", "continuation_cancelled",
		"stage_segment_prepare", "stage_segment_queued", "tts_audio_ready",
		"audio_playback_started", "audio_playback_done", "stage_segment_cancelled", "stage_segment_failed",
		"runtime_status",
	};

	private static JsonElement RuntimePresenceObject(JsonElement root, string key)
	{
		return root.ValueKind == JsonValueKind.Object
			&& root.TryGetProperty(key, out JsonElement value)
			&& value.ValueKind == JsonValueKind.Object ? value : default;
	}

	private static string RuntimePresenceString(JsonElement root, string key)
	{
		return root.ValueKind == JsonValueKind.Object
			&& root.TryGetProperty(key, out JsonElement value)
			&& value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";
	}

	private static bool RuntimePresenceBool(JsonElement root, string key, bool fallback)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value))
			return fallback;
		return value.ValueKind == JsonValueKind.True ? true : value.ValueKind == JsonValueKind.False ? false : fallback;
	}

	private static int RuntimePresenceInt(JsonElement root, string key, int fallback)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)
			|| value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out int number))
			return fallback;
		return number;
	}

	private static long RuntimePresenceLong(JsonElement root, string key, long fallback)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)
			|| value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out long number))
			return fallback;
		return number;
	}

	private static float RuntimePresenceFloat(JsonElement root, string key, float fallback, bool signed)
	{
		if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(key, out JsonElement value)
			|| value.ValueKind != JsonValueKind.Number || !value.TryGetSingle(out float number))
			return fallback;
		return signed ? Mathf.Clamp(number, -1f, 1f) : Mathf.Clamp(number, 0f, 1f);
	}

	private static string RuntimePresenceActivityVi(string activity) => (activity ?? "").Trim().ToLowerInvariant() switch
	{
		"startup" => "Khởi động Eris",
		"random_chat_seed" => "Tạo seed nền",
		"autonomous_deliberation" => "Cân nhắc tự chủ",
		"memory_bootstrap" => "Nạp bộ nhớ",
		_ => "Tác vụ runtime",
	};

	private static string RuntimePresenceStageVi(string stage) => stage switch
	{
		"received" => "đã nhận lượt",
		"thinking" => "đang suy nghĩ",
		"content_committed" => "đã chốt nội dung",
		"audio_buffering" => "đang chuẩn bị giọng",
		"speaking" => "đang nói",
		"degraded" => "đã hạ cấp",
		"interrupted" => "đã bị ngắt",
		"staged" => "đã xếp câu diễn",
		"planning" => "đang dựng nhịp",
		"settling" => "đang hạ nhịp",
		"stalled" => "chưa thấy tiến triển mới",
		"afterglow" => "dư âm sau câu nói",
		_ => "idle sống",
	};

	private static string RuntimePresenceSourceVi(string source)
	{
		if (string.IsNullOrWhiteSpace(source))
			return "runtime";
		return source.Replace("private_mind:", "não phụ · ", StringComparison.OrdinalIgnoreCase)
			.Replace("public_chat", "chat công khai", StringComparison.OrdinalIgnoreCase)
			.Replace('_', ' ');
	}

	private static string RuntimePresenceAffectLabel(string key) => key switch
	{
		"boredom" => "chán",
		"protectiveness" => "bảo vệ",
		"fatigue" => "mệt",
		_ => key.Replace('_', ' '),
	};
}
