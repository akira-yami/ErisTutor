using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

public sealed class UdpDecisionPayload
{
	public string Text { get; init; } = "";
	public bool ParsedJson { get; init; }
	public bool HasTextField { get; init; }
	public bool IsControlEnvelope { get; init; }
	public bool ShouldSpeak { get; init; } = true;
	public bool HasRemoteMotion { get; init; }
	public bool HasExplicitMotion { get; init; }
	public MotionCommand MotionCommand { get; init; }
	public BodyBias BodyBias { get; init; }
	public bool HasBodyBias { get; init; }
	public string ParseMode { get; init; } = "";
	public string DebugTrace { get; init; } = "";
	public string ParserTrace { get; init; } = "";
	public EmotionDebug EmotionDebug { get; init; }
	public bool HasEmotionDebug { get; init; }
	public string PrivateMindDebugJson { get; init; } = "";
	public bool HasPrivateMindDebug { get; init; }
	public BodyTimeline BodyTimeline { get; init; }
	public bool HasBodyTimeline { get; init; }
}

public sealed class EmotionDebugItem
{
	public string Key { get; init; } = "";
	public string Label { get; init; } = "";
	public string Group { get; init; } = "";
	public string ValueText { get; init; } = "";
	public float NumericValue { get; init; }
	public bool HasNumericValue { get; init; }
	public string Note { get; init; } = "";
}

public sealed class EmotionDebug
{
	public string SummaryLabel { get; init; } = "";
	public string SummaryValue { get; init; } = "";
	public string SummaryState { get; init; } = "";
	public float SummaryValence { get; init; }
	public bool HasSummaryValence { get; init; }
	public List<EmotionDebugItem> Items { get; init; } = new();

	public static bool TryFromJson(JsonElement root, out EmotionDebug debug)
	{
		debug = null;
		JsonElement emotionRoot;
		if (!root.TryGetProperty("emotion_debug", out emotionRoot) || emotionRoot.ValueKind != JsonValueKind.Object)
		{
			// Architecture C mới coi identity_runtime là contract panel chính.
			// Chuyển summary/appraisal sang EmotionDebug để UI cũ vẫn render được
			// mà không bắt backend phải gửi compatibility block mãi mãi.
			if (TryFromIdentityRuntime(root, out debug))
				return true;
			return false;
		}

		string summaryLabel = "";
		string summaryValue = "";
		string summaryState = "";
		float summaryValence = 0f;
		bool hasSummaryValence = false;
		if (emotionRoot.TryGetProperty("summary", out var summary) && summary.ValueKind == JsonValueKind.Object)
		{
			summaryLabel = ReadString(summary, "label");
			summaryValue = ReadString(summary, "value");
			summaryState = ReadString(summary, "state");
			if (summary.TryGetProperty("valence", out var valence) && valence.ValueKind == JsonValueKind.Number)
			{
				summaryValence = valence.GetSingle();
				hasSummaryValence = true;
			}
		}

		var items = new List<EmotionDebugItem>();
		if (emotionRoot.TryGetProperty("items", out var itemsRoot) && itemsRoot.ValueKind == JsonValueKind.Object)
		{
			foreach (var property in itemsRoot.EnumerateObject())
			{
				var itemRoot = property.Value;
				if (itemRoot.ValueKind != JsonValueKind.Object)
					continue;

				string valueText = "";
				float numericValue = 0f;
				bool hasNumericValue = false;
				if (itemRoot.TryGetProperty("value", out var valueElement))
				{
					if (valueElement.ValueKind == JsonValueKind.Number)
					{
						numericValue = valueElement.GetSingle();
						valueText = numericValue.ToString("0.00");
						hasNumericValue = true;
					}
					else if (valueElement.ValueKind == JsonValueKind.String)
					{
						valueText = valueElement.GetString() ?? "";
					}
					else if (valueElement.ValueKind == JsonValueKind.True || valueElement.ValueKind == JsonValueKind.False)
					{
						valueText = valueElement.GetBoolean() ? "true" : "false";
					}
					else
					{
						valueText = valueElement.GetRawText();
					}
				}

				items.Add(new EmotionDebugItem
				{
					Key = property.Name,
					Label = ReadString(itemRoot, "label"),
					Group = ReadString(itemRoot, "group"),
					ValueText = valueText,
					NumericValue = numericValue,
					HasNumericValue = hasNumericValue,
					Note = ReadString(itemRoot, "note"),
				});
			}
		}

		if (items.Count == 0
			&& string.IsNullOrWhiteSpace(summaryLabel)
			&& string.IsNullOrWhiteSpace(summaryValue)
			&& TryFromIdentityRuntime(root, out EmotionDebug identityDebug))
		{
			debug = identityDebug;
			return true;
		}

		debug = new EmotionDebug
		{
			SummaryLabel = summaryLabel,
			SummaryValue = summaryValue,
			SummaryState = summaryState,
			SummaryValence = summaryValence,
			HasSummaryValence = hasSummaryValence,
			Items = items,
		};
		return true;
	}

	private static bool TryFromIdentityRuntime(JsonElement root, out EmotionDebug debug)
	{
		debug = null;
		if (!root.TryGetProperty("identity_runtime", out JsonElement identity) || identity.ValueKind != JsonValueKind.Object)
			return false;
		JsonElement summary = identity.TryGetProperty("summary", out JsonElement summaryNode) && summaryNode.ValueKind == JsonValueKind.Object
			? summaryNode : default;
		JsonElement appraisal = identity.TryGetProperty("appraisal", out JsonElement appraisalNode) && appraisalNode.ValueKind == JsonValueKind.Object
			? appraisalNode : default;

		var items = new List<EmotionDebugItem>();
		if (appraisal.ValueKind == JsonValueKind.Object)
		{
			foreach (JsonProperty property in appraisal.EnumerateObject())
			{
				if (property.Value.ValueKind != JsonValueKind.Number || !property.Value.TryGetSingle(out float number))
					continue;
				number = Math.Clamp(number, 0f, 1f);
				items.Add(new EmotionDebugItem
				{
					Key = property.Name,
					Label = property.Name.Replace('_', ' '),
					Group = "appraisal",
					ValueText = number.ToString("0.00"),
					NumericValue = number,
					HasNumericValue = true,
				});
			}
		}

		float valence = 0f;
		bool hasValence = summary.ValueKind == JsonValueKind.Object
			&& summary.TryGetProperty("valence", out JsonElement valenceNode)
			&& valenceNode.ValueKind == JsonValueKind.Number
			&& valenceNode.TryGetSingle(out valence);
		string mood = summary.ValueKind == JsonValueKind.Object ? ReadString(summary, "mood") : "";
		if (summary.ValueKind == JsonValueKind.Object
			&& summary.TryGetProperty("arousal", out JsonElement arousalNode)
			&& arousalNode.ValueKind == JsonValueKind.Number
			&& arousalNode.TryGetSingle(out float arousal))
		{
			arousal = Math.Clamp(arousal, 0f, 1f);
			items.Insert(0, new EmotionDebugItem
			{
				Key = "calm_aroused",
				Label = "arousal",
				Group = "appraisal",
				ValueText = arousal.ToString("0.00"),
				NumericValue = arousal,
				HasNumericValue = true,
			});
		}
		string architecture = ReadString(identity, "architecture");
		debug = new EmotionDebug
		{
			SummaryLabel = "identity runtime",
			SummaryValue = string.IsNullOrWhiteSpace(mood) ? "trung_tinh" : mood,
			SummaryState = architecture,
			SummaryValence = hasValence ? Math.Clamp(valence, -1f, 1f) : 0f,
			HasSummaryValence = hasValence,
			Items = items,
		};
		return true;
	}

	private static string ReadString(JsonElement root, string propertyName)
	{
		return root.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
			? value.GetString() ?? ""
			: "";
	}
}

public sealed class UdpDecisionParser
{
	private static readonly Dictionary<string, string> AnimAliases = new()
	{
		{ "walk", "stand_walk" },
		{ "walking", "stand_walk" },
		{ "run", "stand_walk" },
		{ "running", "stand_walk" },
		{ "divide", "divine" },
		{ "wave", "stand" },
		{ "idle", "stand" },
	};

	private static readonly HashSet<string> ValidAnimations = new()
	{
		"stand", "sit_down", "sit", "sit_up", "read", "stand_walk",
		"piano", "clone", "divine", "hop", "jump", "sleep", "sad",
		"food", "affection", "climb", "climb_top", "air_swing_l", "air_swing_r",
		"air", "shime", "idle",
	};

	public UdpDecisionPayload Parse(string raw)
	{
		string text = "";
		bool parsedJson = false;
		bool hasTextField = false;
		bool isControlEnvelope = false;
		bool shouldSpeak = true;
		string animation = "stand";
		int direction = 0;
		string nextAnimation = "";
		string bounceAction = "";
		string debugTrace = "";
		string parserTrace = "";
		string parseMode = "";
		string motionIntent = "";
		int motionFacing = 0;
		float motionTargetX = -1f;
		bool explicitMotion = false;
		BodyBias bodyBias = null;
		bool hasBodyBias = false;
		EmotionDebug emotionDebug = null;
		bool hasEmotionDebug = false;
		string privateMindDebugJson = "";
		bool hasPrivateMindDebug = false;
		BodyTimeline bodyTimeline = null;
		bool hasBodyTimeline = false;

		try
		{
			string cleaned = StripCodeFence(raw.Trim());
			using var doc = JsonDocument.Parse(cleaned);
			parsedJson = true;
			var root = doc.RootElement;
			string envelopeType = root.TryGetProperty("type", out var typeElement) && typeElement.ValueKind == JsonValueKind.String
				? typeElement.GetString() ?? ""
				: "";
			string protocol = root.TryGetProperty("protocol", out var protocolElement) && protocolElement.ValueKind == JsonValueKind.String
				? protocolElement.GetString() ?? ""
				: "";
			isControlEnvelope = string.Equals(protocol, "eris.stage", StringComparison.OrdinalIgnoreCase)
				|| envelopeType.StartsWith("maintenance_", StringComparison.Ordinal)
				|| envelopeType.StartsWith("settings_", StringComparison.Ordinal)
				|| string.Equals(envelopeType, "panel_state_update", StringComparison.Ordinal)
				// Coding-agent events vẫn có thể đi chung UDP lane trong giai đoạn chuyển tiếp,
				// nhưng Godot body không còn Agent Console UI nên chỉ coi chúng là control traffic.
				|| string.Equals(envelopeType, "agent_event", StringComparison.Ordinal)
				|| envelopeType.EndsWith("_ack", StringComparison.Ordinal);
			if (isControlEnvelope)
				shouldSpeak = false;

			if (BodyBias.TryFromJson(root, out bodyBias))
				hasBodyBias = true;
			if (EmotionDebug.TryFromJson(root, out emotionDebug))
				hasEmotionDebug = true;
			if (TryReadPrivateMindDebugJson(root, out privateMindDebugJson))
				hasPrivateMindDebug = true;
			if (BodyTimeline.TryFromJson(root, out bodyTimeline))
				hasBodyTimeline = true;
			if (root.TryGetProperty("text", out var t))
			{
				text = t.GetString() ?? "";
				hasTextField = true;
			}
			if (root.TryGetProperty("should_speak", out var ss) && ss.ValueKind == JsonValueKind.False)
				shouldSpeak = false;
			if (root.TryGetProperty("animation", out var a))
				animation = a.GetString() ?? "stand";
			if (root.TryGetProperty("direction", out var d) && d.ValueKind == JsonValueKind.Number)
				direction = d.GetInt32();
			if (root.TryGetProperty("next_animation", out var na))
				nextAnimation = na.GetString() ?? "";
			if (root.TryGetProperty("bounce_action", out var ba))
				bounceAction = ba.GetString() ?? "";
			if (root.TryGetProperty("debug_trace", out var dt))
				debugTrace = dt.GetString() ?? "";
			if (root.TryGetProperty("parser_trace", out var pt))
				parserTrace = pt.GetString() ?? "";
			if (root.TryGetProperty("parse_mode", out var pm))
				parseMode = pm.GetString() ?? "";
			if (root.TryGetProperty("target_x", out var tx) && tx.ValueKind == JsonValueKind.Number)
				motionTargetX = tx.GetSingle();
			if (root.TryGetProperty("motion_intent", out var mi))
			{
				motionIntent = mi.GetString() ?? "";
				if (!string.IsNullOrWhiteSpace(motionIntent))
					explicitMotion = true;
			}
			if (root.TryGetProperty("motion_facing", out var mf) && mf.ValueKind == JsonValueKind.Number)
			{
				motionFacing = mf.GetInt32();
				if (motionFacing != 0)
					explicitMotion = true;
			}
			if (root.TryGetProperty("motion_target_x", out var mtx) && mtx.ValueKind == JsonValueKind.Number)
			{
				motionTargetX = mtx.GetSingle();
				if (motionTargetX >= 0f)
					explicitMotion = true;
			}
		}
		catch (Exception ex)
		{
			string trimmed = raw.TrimStart();
			bool rawIsPlainText = !trimmed.StartsWith("{", StringComparison.Ordinal) && !trimmed.StartsWith("```", StringComparison.Ordinal);
			GD.PrintErr($"[Parse] Khung du lieu bi hong: {ex.Message}");
			text = rawIsPlainText ? raw : "Loi duong truyen tin hieu.";
			hasTextField = true;
			animation = "stand";
		}

		int resolvedFacing = motionFacing != 0 ? motionFacing : direction;
		animation = NormalizeAnimationName(animation, resolvedFacing);
		nextAnimation = NormalizeAnimationName(nextAnimation, resolvedFacing, allowEmpty: true);
		bounceAction = NormalizeAnimationName(bounceAction, resolvedFacing, allowEmpty: true);
		if (string.IsNullOrWhiteSpace(motionIntent))
			motionIntent = InferMotionIntent(animation, nextAnimation, resolvedFacing, motionTargetX);
		if (motionFacing == 0)
			motionFacing = resolvedFacing;

		bool hasRemoteMotion = HasRemoteMotionCommand(motionIntent, animation, nextAnimation, motionFacing, motionTargetX);
		return new UdpDecisionPayload
		{
			Text = text,
			ParsedJson = parsedJson,
			HasTextField = hasTextField,
			IsControlEnvelope = isControlEnvelope,
			ShouldSpeak = shouldSpeak,
			HasRemoteMotion = hasRemoteMotion,
			HasExplicitMotion = explicitMotion,
			MotionCommand = new MotionCommand(motionIntent, animation, nextAnimation, bounceAction, motionFacing, motionTargetX),
			BodyBias = bodyBias,
			HasBodyBias = hasBodyBias,
			ParseMode = parseMode,
			DebugTrace = debugTrace,
			ParserTrace = parserTrace,
			EmotionDebug = emotionDebug,
			HasEmotionDebug = hasEmotionDebug,
			PrivateMindDebugJson = privateMindDebugJson,
			HasPrivateMindDebug = hasPrivateMindDebug,
			BodyTimeline = bodyTimeline,
			HasBodyTimeline = hasBodyTimeline,
		};
	}


	private static bool TryReadFirstString(JsonElement root, out string text, params string[] propertyNames)
	{
		foreach (string propertyName in propertyNames)
		{
			if (root.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String)
			{
				text = value.GetString() ?? "";
				return true;
			}
		}

		text = "";
		return false;
	}

	private static bool TryReadPrivateMindDebugJson(JsonElement root, out string json)
	{
		json = "";
		if (root.TryGetProperty("private_mind_digest", out var topLevel) && topLevel.ValueKind == JsonValueKind.Object)
		{
			json = topLevel.GetRawText();
			return true;
		}
		if (
			root.TryGetProperty("emotion_debug", out var emotionRoot)
			&& emotionRoot.ValueKind == JsonValueKind.Object
			&& emotionRoot.TryGetProperty("private_mind", out var privateMind)
			&& privateMind.ValueKind == JsonValueKind.Object
		)
		{
			json = privateMind.GetRawText();
			return true;
		}
		return false;
	}

	private static string StripCodeFence(string cleaned)
	{
		if (!cleaned.StartsWith("```", StringComparison.Ordinal))
			return cleaned;
		int start = cleaned.IndexOf('\n') + 1;
		int end = cleaned.LastIndexOf("```", StringComparison.Ordinal);
		return start > 0 && end > start ? cleaned.Substring(start, end - start).Trim() : cleaned;
	}

	private static string NormalizeAnimationName(string animation, int facingDir, bool allowEmpty = false)
	{
		string value = (animation ?? "").Trim();
		if (string.IsNullOrEmpty(value))
			return allowEmpty ? "" : "stand";
		if (AnimAliases.TryGetValue(value, out var mapped))
		{
			GD.Print($"[Anim] Alias '{value}' -> '{mapped}'");
			value = mapped;
		}
		if (ValidAnimations.Contains(value))
			return value;
		string fallback = (facingDir == 1 || facingDir == -1) ? "stand_walk" : "stand";
		GD.PrintErr($"[Anim] Animation khong hop le: '{value}', fallback ve '{fallback}'.");
		return allowEmpty ? "" : fallback;
	}

	private static string InferMotionIntent(string animation, string nextAnimation, int facingDir, float targetX)
	{
		if (animation == "climb" && nextAnimation == "climb_top")
			return "climb_to_top";
		if (animation == "climb_top")
			return "climb_to_top";
		if (animation == "climb")
			return "climb_loop";
		if (animation == "air_swing_l" || animation == "air_swing_r")
			return "drop";
		if (targetX >= 0f)
			return "walk_to";
		if (animation == "stand_walk")
			return "walk";
		if (facingDir == 1 || facingDir == -1)
			return "face";
		return "";
	}

	private static bool HasRemoteMotionCommand(string motionIntent, string animation, string nextAnimation, int motionFacing, float motionTargetX)
	{
		if (!string.IsNullOrWhiteSpace(motionIntent))
			return true;
		if (motionFacing != 0)
			return true;
		if (motionTargetX >= 0f)
			return true;
		if (!string.IsNullOrWhiteSpace(nextAnimation))
			return true;
		return !string.Equals(animation, "stand", StringComparison.Ordinal);
	}
}

public sealed class BodyTimelineEvent
{
	public int AtMs { get; init; }
	public string Kind { get; init; } = "motion";
	public string Name { get; init; } = "stand";
	public int DurationMs { get; init; }
	public string Intent { get; init; } = "pose";
	public int Facing { get; init; }
	public float TargetX { get; init; } = -1f;
}

public sealed class BodyTimeline
{
	private static readonly Dictionary<string, string> AnimAliases = new()
	{
		{ "walk", "stand_walk" },
		{ "walking", "stand_walk" },
		{ "run", "stand_walk" },
		{ "running", "stand_walk" },
		{ "wave", "stand" },
		{ "idle", "stand" },
	};

	private static readonly HashSet<string> ValidAnimations = new()
	{
		"stand", "sit_down", "sit", "sit_up", "read", "stand_walk",
		"piano", "clone", "divine", "hop", "jump", "sleep", "sad",
		"food", "affection", "climb", "climb_top", "air_swing_l", "air_swing_r",
		"air", "shime", "idle",
	};

	public string TurnId { get; init; } = "";
	public int DurationMs { get; init; }
	public string Source { get; init; } = "";
	public List<BodyTimelineEvent> Events { get; init; } = new();
	public bool HasEvents => Events.Count > 0;

	public static bool TryFromJson(JsonElement root, out BodyTimeline timeline)
	{
		timeline = null;
		if (!TryGetTimelineRoot(root, out var timelineRoot))
			return false;
		if (!timelineRoot.TryGetProperty("events", out var eventsRoot) || eventsRoot.ValueKind != JsonValueKind.Array)
			return false;

		var events = new List<BodyTimelineEvent>();
		foreach (var item in eventsRoot.EnumerateArray())
		{
			if (item.ValueKind != JsonValueKind.Object)
				continue;

			string name = NormalizeAnimationName(ReadString(item, "name"));
			if (string.IsNullOrWhiteSpace(name))
				continue;

			events.Add(new BodyTimelineEvent
			{
				AtMs = ClampInt(ReadInt(item, "at_ms", 0), 0, 30000),
				Kind = NormalizeKind(ReadString(item, "kind")),
				Name = name,
				DurationMs = ClampInt(ReadInt(item, "duration_ms", 0), 0, 30000),
				Intent = NormalizeIntent(ReadString(item, "intent")),
				Facing = ClampFacing(ReadInt(item, "facing", 0)),
				TargetX = ClampTarget(ReadFloat(item, "target_x", -1f)),
			});
		}

		if (events.Count == 0)
			return false;

		events.Sort((a, b) => a.AtMs.CompareTo(b.AtMs));
		int durationMs = ClampInt(ReadInt(timelineRoot, "duration_ms", 0), 0, 30000);
		if (durationMs <= 0)
		{
			foreach (var e in events)
				durationMs = Math.Max(durationMs, e.AtMs + Math.Max(120, e.DurationMs));
		}

		timeline = new BodyTimeline
		{
			TurnId = ReadString(timelineRoot, "turn_id"),
			DurationMs = durationMs,
			Source = ReadString(timelineRoot, "source"),
			Events = events,
		};
		return true;
	}

	private static bool TryGetTimelineRoot(JsonElement root, out JsonElement timelineRoot)
	{
		// Payload legacy đặt timeline ở `stage.body_timeline` hoặc top-level.
		// Stage protocol v2 lại bọc metadata trong `data.body_timeline`.
		// Đọc đủ cả hai hình dạng để body không đứng im đúng lúc Eris đang "nghĩ".
		if (
			root.TryGetProperty("stage", out var stage)
			&& stage.ValueKind == JsonValueKind.Object
			&& stage.TryGetProperty("body_timeline", out timelineRoot)
			&& timelineRoot.ValueKind == JsonValueKind.Object)
		{
			return true;
		}
		if (root.TryGetProperty("body_timeline", out timelineRoot) && timelineRoot.ValueKind == JsonValueKind.Object)
			return true;

		if (root.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Object)
		{
			if (data.TryGetProperty("body_timeline", out timelineRoot)
				&& timelineRoot.ValueKind == JsonValueKind.Object)
			{
				return true;
			}

			// Chấp nhận thêm dạng `data.stage.body_timeline` để protocol có thể
			// mở rộng mà frontend không lại phải đoán cấu trúc bằng thần giao cách cảm.
			if (data.TryGetProperty("stage", out var dataStage)
				&& dataStage.ValueKind == JsonValueKind.Object
				&& dataStage.TryGetProperty("body_timeline", out timelineRoot)
				&& timelineRoot.ValueKind == JsonValueKind.Object)
			{
				return true;
			}
		}

		timelineRoot = default;
		return false;
	}

	private static string NormalizeAnimationName(string animation)
	{
		string value = (animation ?? "").Trim();
		if (string.IsNullOrEmpty(value))
			return "";
		if (AnimAliases.TryGetValue(value, out var mapped))
			value = mapped;
		return ValidAnimations.Contains(value) ? value : "stand";
	}

	private static string NormalizeKind(string kind)
	{
		string value = (kind ?? "").Trim().ToLowerInvariant();
		return value == "motion" ? "motion" : "motion";
	}

	private static string NormalizeIntent(string intent)
	{
		string value = (intent ?? "").Trim().ToLowerInvariant();
		return value is "pose" or "face" or "walk" or "walk_to" ? value : "pose";
	}

	private static int ClampFacing(int value)
	{
		return value < 0 ? -1 : value > 0 ? 1 : 0;
	}

	private static float ClampTarget(float value)
	{
		if (value < 0f)
			return -1f;
		return Mathf.Clamp(value, 0f, 1f);
	}

	private static int ClampInt(int value, int min, int max)
	{
		return Math.Max(min, Math.Min(max, value));
	}

	private static string ReadString(JsonElement root, string propertyName)
	{
		return root.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
			? value.GetString() ?? ""
			: "";
	}

	private static int ReadInt(JsonElement root, string propertyName, int defaultValue)
	{
		if (!root.TryGetProperty(propertyName, out var value))
			return defaultValue;
		if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int parsed))
			return parsed;
		return defaultValue;
	}

	private static float ReadFloat(JsonElement root, string propertyName, float defaultValue)
	{
		if (!root.TryGetProperty(propertyName, out var value))
			return defaultValue;
		if (value.ValueKind == JsonValueKind.Number && value.TryGetSingle(out float parsed))
			return parsed;
		return defaultValue;
	}
}
