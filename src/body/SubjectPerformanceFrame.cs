using System;
using System.Collections.Generic;
using System.Text.Json;

/// <summary>
/// Versioned body-only contract emitted by the Python performance adapter.
/// It intentionally contains no beliefs, raw need activations, hidden evidence,
/// narrative hypotheses or other private Subject Core state.
/// </summary>
public sealed class SubjectPerformanceFrame
{
    public const string Protocol = "eris.subject.performance";
    public const int CurrentSchemaVersion = 1;
    public const int MaxBodyCues = 32;
    public const int MaxTextField = 256;

    public int SchemaVersion { get; init; } = CurrentSchemaVersion;
    public string PlanId { get; init; } = "";
    public string DecisionId { get; init; } = "";
    public string DeliveryId { get; init; } = "";
    public int Sequence { get; init; }
    public float Energy { get; init; } = 0.5f;
    public float Pace { get; init; } = 0.5f;
    public float Vulnerability { get; init; }
    public float Restraint { get; init; } = 0.5f;
    public string GazeTarget { get; init; } = "user";
    public IReadOnlyList<string> BodyCues { get; init; } = Array.Empty<string>();

    public static bool TryParse(string raw, out SubjectPerformanceFrame frame)
    {
        frame = null;
        if (string.IsNullOrWhiteSpace(raw) || raw.Length > 64 * 1024)
            return false;

        try
        {
            using JsonDocument document = JsonDocument.Parse(raw);
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("protocol", out JsonElement protocol)
                || !string.Equals(protocol.GetString(), Protocol, StringComparison.Ordinal))
                return false;

            int schemaVersion = ReadInt(root, "schema_version", CurrentSchemaVersion);
            int sequence = ReadInt(root, "sequence", 0);
            string planId = ReadString(root, "plan_id");
            if (schemaVersion != CurrentSchemaVersion || sequence < 0 || string.IsNullOrWhiteSpace(planId))
                return false;

            var cues = new List<string>();
            if (root.TryGetProperty("body_cues", out JsonElement cueRoot))
            {
                if (cueRoot.ValueKind != JsonValueKind.Array || cueRoot.GetArrayLength() > MaxBodyCues)
                    return false;
                foreach (JsonElement item in cueRoot.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.String)
                        return false;
                    string cue = item.GetString() ?? "";
                    if (string.IsNullOrWhiteSpace(cue) || cue.Length > MaxTextField)
                        return false;
                    cues.Add(cue);
                }
            }

            frame = new SubjectPerformanceFrame
            {
                SchemaVersion = schemaVersion,
                PlanId = planId,
                DecisionId = ReadString(root, "decision_id"),
                DeliveryId = ReadString(root, "delivery_id"),
                Sequence = sequence,
                Energy = ReadScore(root, "energy", 0.5f),
                Pace = ReadScore(root, "pace", 0.5f),
                Vulnerability = ReadScore(root, "vulnerability", 0f),
                Restraint = ReadScore(root, "restraint", 0.5f),
                GazeTarget = ReadString(root, "gaze_target", "user"),
                BodyCues = cues,
            };
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    private static string ReadString(JsonElement root, string name, string fallback = "")
    {
        if (!root.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String)
            return fallback;
        string result = value.GetString() ?? fallback;
        return result.Length <= MaxTextField ? result : result.Substring(0, MaxTextField);
    }

    private static int ReadInt(JsonElement root, string name, int fallback)
    {
        return root.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt32(out int result)
            ? result
            : fallback;
    }

    private static float ReadScore(JsonElement root, string name, float fallback)
    {
        if (!root.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.Number
            || !value.TryGetSingle(out float result) || float.IsNaN(result) || float.IsInfinity(result))
            return fallback;
        return Math.Clamp(result, 0f, 1f);
    }
}
