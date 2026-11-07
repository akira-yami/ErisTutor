using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;

public partial class UdpListener
{
    // P1: one-way P0 sidecar -> existing Godot main-thread state -> Rich Chat.
    // Feedback is a receipt from the browser, NOT a claim that Root saw it.
    private sealed class RichChatArtifactSlot
    {
        public string Intent = "";
        public string Artifact = "";
        public int Revision;
    }
    private sealed class RichChatImageSlot
    {
        public string Path = "";
        public string Sha256 = "";
    }
    private readonly object _richChatFeedbackWriteLock = new();
    private Dictionary<string, RichChatArtifactSlot> _richChatReceiptIndex = new();
    private Dictionary<string, RichChatImageSlot> _richChatImageIndex = new();
    private const int RichChatMaxBundleBytes = 256_000;
    private ulong _richChatArtifactsLastScan;
    private string _richChatArtifactsTurnSignature = "";
    private object[] _richChatCachedBundles = Array.Empty<object>();
    private static readonly Regex RichChatId = new(@"^[A-Za-z0-9_:/.-]{1,160}$", RegexOptions.Compiled);

    private static string RichChatValue(JsonElement value, string key)
    {
        return value.ValueKind == JsonValueKind.Object && value.TryGetProperty(key, out JsonElement item) &&
            item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : "";
    }

    private static bool RichChatSafeId(string value) => RichChatId.IsMatch(value);

    private object[] ReadRichChatBundles(HashSet<string> activeTurns)
    {
        string turnSignature = string.Join(";", activeTurns.OrderBy(t => t, StringComparer.Ordinal));
        ulong now = Time.GetTicksMsec();
        if (turnSignature == _richChatArtifactsTurnSignature && now - _richChatArtifactsLastScan < 1800)
            return _richChatCachedBundles;
        _richChatArtifactsTurnSignature = turnSignature;
        _richChatArtifactsLastScan = now;
        var output = new List<object>();
        var receipts = new Dictionary<string, RichChatArtifactSlot>(StringComparer.Ordinal);
        var images = new Dictionary<string, RichChatImageSlot>(StringComparer.Ordinal);
        string directory = Path.Combine(ArtifactWorkspaceRoot(), "presentation_v1");
        if (!Directory.Exists(directory))
        {
            Volatile.Write(ref _richChatReceiptIndex, receipts);
            Volatile.Write(ref _richChatImageIndex, images);
            _richChatCachedBundles = output.ToArray();
            return _richChatCachedBundles;
        }
        try
        {
            // Bounded, read-only discovery. Dot-temp, feedback and symlinks are ignored.
            var files = Directory.EnumerateFiles(directory, "pi_*.json", SearchOption.TopDirectoryOnly)
                .Select(p => new FileInfo(p)).Where(f => f.Length > 0 && f.Length <= RichChatMaxBundleBytes &&
                    (f.Attributes & FileAttributes.ReparsePoint) == 0)
                .OrderByDescending(f => f.LastWriteTimeUtc).Take(36).ToArray();
            int provisionalCount = 0;
            foreach (FileInfo file in files)
            {
                try
                {
                    using JsonDocument document = JsonDocument.Parse(File.ReadAllBytes(file.FullName));
                    JsonElement root = document.RootElement;
                    if (RichChatValue(root, "schema") != "eris.presentation.bundle.v1") continue;
                    string intent = RichChatValue(root, "intent_id"), turn = RichChatValue(root, "turn_id");
                    if (!RichChatSafeId(intent) || !RichChatSafeId(turn) ||
                        file.Name != intent + ".json" || !root.TryGetProperty("artifacts", out var items) ||
                        items.ValueKind != JsonValueKind.Array || items.GetArrayLength() > 120) continue;
                    bool bound = activeTurns.Contains(turn);
                    // Provisional job artifacts must NEVER be attached to a conversation turn.
                    // Recently produced jobs are shown in their own gallery.
                    bool job = turn.StartsWith("job:", StringComparison.Ordinal) &&
                        (DateTime.UtcNow - file.LastWriteTimeUtc).TotalHours <= 3;
                    if (!bound && (!job || provisionalCount >= 6)) continue;
                    var artifacts = new List<JsonElement>();
                    foreach (JsonElement art in items.EnumerateArray())
                    {
                        string id = RichChatValue(art, "artifact_id"), kind = RichChatValue(art, "kind");
                        if (!RichChatSafeId(id) || RichChatValue(art, "schema") != "eris.artifact.v1" ||
                            RichChatValue(art, "turn_id") != turn ||
                            !art.TryGetProperty("revision", out var rev) || rev.ValueKind != JsonValueKind.Number ||
                            !rev.TryGetInt32(out int version) || version < 1 ||
                            !new[] { "math", "chart", "code", "table", "document" }.Contains(kind) ||
                            !art.TryGetProperty("content", out JsonElement content) || content.ValueKind != JsonValueKind.Object ||
                            !art.TryGetProperty("presentation", out var pres) ||
                            !pres.TryGetProperty("target", out var target) || target.ValueKind != JsonValueKind.Array ||
                            !target.EnumerateArray().Any(t => t.ValueKind == JsonValueKind.String && t.GetString() == "rich_chat")) continue;
                        var clone = art.Clone();
                        artifacts.Add(clone);
                        receipts[id] = new RichChatArtifactSlot { Artifact = id, Intent = intent, Revision = version };
                        if (kind == "document" && content.TryGetProperty("blocks", out var blocks) &&
                            blocks.ValueKind == JsonValueKind.Array)
                        {
                            int n = 0;
                            foreach (var block in blocks.EnumerateArray())
                            {
                                if (RichChatValue(block, "type") == "image")
                                    images[id + "/" + n] = new RichChatImageSlot {
                                        Path = RichChatValue(block, "path"), Sha256 = RichChatValue(block, "sha256") };
                                n++;
                            }
                        }
                    }
                    if (artifacts.Count > 0)
                    {
                        output.Add(new { intent_id = intent, turn_id = turn, provisional = !bound,
                            artifacts = artifacts.ToArray() });
                        if (!bound) provisionalCount++;
                    }
                }
                catch (Exception ex) when (ex is IOException || ex is JsonException || ex is UnauthorizedAccessException || ex is InvalidOperationException)
                { /* Sidecar may be incomplete/stale or external; never break conversation. */ }
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        Volatile.Write(ref _richChatReceiptIndex, receipts);
        Volatile.Write(ref _richChatImageIndex, images);
        _richChatCachedBundles = output.ToArray();
        return _richChatCachedBundles;
    }

    // Only a recently exposed (intent, artifact, revision) pair can be acknowledged.
    private bool RecordRichChatFeedback(JsonElement feedback)
    {
        if (feedback.ValueKind != JsonValueKind.Object ||
            RichChatValue(feedback, "type") != "renderer.feedback.v1" ||
            RichChatValue(feedback, "renderer_id") != "richchat-main") return false;
        string intent = RichChatValue(feedback, "intent_id"), artifact = RichChatValue(feedback, "artifact_id");
        string code = RichChatValue(feedback, "code"), status = RichChatValue(feedback, "status");
        if (!RichChatSafeId(intent) || !RichChatSafeId(artifact) ||
            !feedback.TryGetProperty("revision", out var revision) || !revision.TryGetInt32(out int version) ||
            !feedback.TryGetProperty("retryable", out var retry) ||
            (retry.ValueKind != JsonValueKind.True && retry.ValueKind != JsonValueKind.False)) return false;
        bool valid = (code == "OK_RENDERED" && status == "rendered") ||
                     (code == "E_RENDER" && status == "failed") ||
                     (code == "E_UNSUPPORTED_KIND" && status == "failed");
        if (!valid) return false;
        var index = Volatile.Read(ref _richChatReceiptIndex);
        if (!index.TryGetValue(artifact, out var slot) || slot.Intent != intent || slot.Revision != version)
            return false;
        string directory = Path.Combine(ArtifactWorkspaceRoot(), "presentation_v1", "feedback");
        try
        {
            Directory.CreateDirectory(directory);
            string filename = artifact + ".r" + version + ".json";
            // Artifact IDs are created by the hashed P0 builder (no path components).
            if (!Regex.IsMatch(artifact, "^art_[a-f0-9]{24}$")) return false;
            string target = Path.Combine(directory, filename);
            lock (_richChatFeedbackWriteLock)
            {
                // P3: ACK progression is monotonic. A late failed render must
                // never erase a verified success from the same artifact revision.
                // Guard the read-modify-write path against parallel HTTP requests.
                if (File.Exists(target))
                {
                    try
                    {
                        using JsonDocument previous = JsonDocument.Parse(File.ReadAllBytes(target));
                        JsonElement old = previous.RootElement;
                        if (RichChatValue(old, "intent_id") == intent &&
                            RichChatValue(old, "artifact_id") == artifact &&
                            RichChatValue(old, "status") == "rendered")
                            return true;
                    }
                    catch (Exception ex) when (ex is IOException || ex is JsonException) { }
                }
                var receipt = new {
                    type = "renderer.feedback.v1", renderer_id = "richchat-main", intent_id = intent,
                    artifact_id = artifact, revision = version, status, code,
                    retryable = retry.GetBoolean(),
                    // Server timestamp is a transport receipt, not a visual correctness proof.
                    received_at = DateTime.UtcNow.ToString("O"),
                };
                byte[] encoded = JsonSerializer.SerializeToUtf8Bytes(receipt);
                if (encoded.Length > 4096) return false;
                string temporary = Path.Combine(directory, ".ack-" + Guid.NewGuid().ToString("N") + ".tmp");
                File.WriteAllBytes(temporary, encoded);
                try { File.Move(temporary, target, overwrite: true); }
                finally { if (File.Exists(temporary)) File.Delete(temporary); }
                return true;
            }
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { return false; }
    }

    private bool TryReadRichChatArtifactImage(string relative, out byte[] bytes)
    {
        bytes = Array.Empty<byte>();
        if (!relative.StartsWith("api/image/", StringComparison.Ordinal)) return false;
        var lookup = relative.Substring("api/image/".Length);
        if (!Volatile.Read(ref _richChatImageIndex).TryGetValue(lookup, out var slot)) return false;
        if (!Regex.IsMatch(slot.Sha256, "^[a-fA-F0-9]{64}$") || string.IsNullOrWhiteSpace(slot.Path) ||
            slot.Path.Contains('\\') || slot.Path.StartsWith('/') || slot.Path.Split('/').Any(p => p == ".." || p == ""))
            return false;
        string workspace = ArtifactWorkspaceRoot();
        string actual = Path.GetFullPath(Path.Combine(workspace, slot.Path.Replace('/', Path.DirectorySeparatorChar)));
        if (!actual.StartsWith(workspace.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
            StringComparison.OrdinalIgnoreCase)) return false;
        try
        {
            var file = new FileInfo(actual);
            if (!file.Exists || file.Length < 8 || file.Length > 3_000_000 || (file.Attributes & FileAttributes.ReparsePoint) != 0) return false;
            byte[] data = File.ReadAllBytes(actual);
            if (!Convert.ToHexString(SHA256.HashData(data)).Equals(slot.Sha256, StringComparison.OrdinalIgnoreCase) ||
                data.Length < 8 || !data.Take(8).SequenceEqual(new byte[] {137,80,78,71,13,10,26,10})) return false;
            bytes = data;
            return true;
        }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { return false; }
    }
}
