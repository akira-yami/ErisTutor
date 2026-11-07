using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

public partial class UdpListener
{
    // V5: Không chỉnh nội dung của Root. Đây là bộ trình bày đọc riêng cho panel.
    // Dữ liệu được gắn vào đúng tin nhắn theo SHA-256; không chèn thêm lời nói.
    private readonly Dictionary<string, string> _workspacePendingDocuments = new();
    private readonly HashSet<string> _workspaceSeenDocuments = new();

    private static string ConversationDocumentHash(string text)
    {
        string normalized = (text ?? "").Replace("\r\n", "\n").Trim();
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(normalized))).ToLowerInvariant();
    }

    private static string DocumentString(JsonElement node, string key, int limit = 8000)
    {
        if (node.ValueKind != JsonValueKind.Object ||
            !node.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.String)
            return "";
        string data = value.GetString() ?? "";
        return data.Length <= limit ? data : data.Substring(0, limit);
    }

    private void AcknowledgeWorkspaceDocument(string documentId, string status)
    {
        // Phản hồi từ chính socket vừa gửi datagram, không đổi cổng nhận của Eris.
        try
        {
            string ip = _udp.GetPacketIP();
            int port = _udp.GetPacketPort();
            if (ip != "127.0.0.1" && ip != "::1" || port < 1 || port > 65535)
                return;
            using var sender = new PacketPeerUdp();
            if (sender.SetDestAddress(ip, port) != Error.Ok) return;
            byte[] data = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                parse_mode = "workspace_document_ack", document_id = documentId, status,
            }));
            sender.PutPacket(data);
        }
        catch (Exception ex)
        {
            GD.PrintErr("[V5] Không gửi được ACK: " + ex.Message);
        }
    }

    private static string ExtractConversationTurnId(string raw)
    {
        try
        {
            using JsonDocument parsed = JsonDocument.Parse(raw);
            var root = parsed.RootElement;
            if (root.TryGetProperty("stage", out var stage) && stage.ValueKind == JsonValueKind.Object)
                return DocumentString(stage, "turn_id", 150);
        }
        catch (JsonException) { }
        return "";
    }

    private bool TryHandleWorkspaceDocumentPacket(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw) || !raw.Contains("workspace_document", StringComparison.Ordinal))
            return false;
        try
        {
            using JsonDocument parsed = JsonDocument.Parse(raw);
            JsonElement root = parsed.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                DocumentString(root, "parse_mode", 40) != "workspace_document")
                return false;
            if (!root.TryGetProperty("workspace_version", out var version) ||
                !version.TryGetInt32(out int v) || v != 1)
                return true;
            if (!root.TryGetProperty("blocks", out var blocks) || blocks.ValueKind != JsonValueKind.Array ||
                blocks.GetArrayLength() > 80 || raw.Length > 46000)
                return true;
            string documentId = DocumentString(root, "document_id", 64);
            string textHash = DocumentString(root, "text_hash", 64);
            if (!Regex.IsMatch(documentId, "^[a-f0-9]{32}$") ||
                !Regex.IsMatch(textHash, "^[a-f0-9]{64}$"))
                return true;
            if (_workspaceSeenDocuments.Contains(documentId))
            {
                AcknowledgeWorkspaceDocument(documentId, "rendered");
                return true;
            }
            string turnId = DocumentString(root, "turn_id", 150);
            if (AttachWorkspaceDocument(textHash, turnId, raw))
            {
                if (_workspaceSeenDocuments.Count > 1500) _workspaceSeenDocuments.Clear();
                _workspaceSeenDocuments.Add(documentId);
                AcknowledgeWorkspaceDocument(documentId, "rendered");
            }
            else
            {
                // UDP không bảo đảm thứ tự; giữ tài liệu đến khi bubble được commit.
                if (_workspacePendingDocuments.Count >= 12) _workspacePendingDocuments.Clear();
                string pendingKey = turnId.Length > 0 ? "turn:" + turnId : "hash:" + textHash;
                _workspacePendingDocuments[pendingKey] = raw;
            }
            return true;
        }
        catch (Exception ex)
        {
            GD.PrintErr("[V5] Lỗi tài liệu có cấu trúc: " + ex.Message);
            return true;
        }
    }

    private bool AttachWorkspaceDocument(string hash, string turnId, string raw)
    {
        // Cập nhật chính tin nhắn đã tồn tại, không tạo tin assistant trùng.
        for (int i = _conversationEntries.Count - 1; i >= Math.Max(0, _conversationEntries.Count - 30); i--)
        {
            var entry = _conversationEntries[i];
            if (entry.Role != "assistant" || ConversationDocumentHash(entry.Text) != hash)
                continue;
            // turn_id có mặt thì bắt buộc khớp, không gắn nhầm hai câu giống nhau.
            if (!string.IsNullOrEmpty(turnId) && entry.TurnId != turnId)
                continue;
            if (string.IsNullOrEmpty(entry.StructuredDocumentJson))
            {
                entry.StructuredDocumentJson = raw;
                SaveConversationHistory();
                if (_conversationHistoryPanel != null)
                {
                    RebuildConversationMessages();
                    bool latest = i == _conversationEntries.Count - 1;
                    if (latest && entry.Text.Length > 480 && !_conversationHistoryPanel.Visible)
                        ShowConversationHistoryPanel();
                }
            }
            return true;
        }
        return false;
    }

    private void TryAttachPendingWorkspaceDocument(ConversationEntry entry)
    {
        if (entry.Role != "assistant") return;
        string hash = ConversationDocumentHash(entry.Text);
        string turnKey = "turn:" + (entry.TurnId ?? "");
        string hashKey = "hash:" + hash;
        if ((!string.IsNullOrEmpty(entry.TurnId) && _workspacePendingDocuments.Remove(turnKey, out var doc))
            || _workspacePendingDocuments.Remove(hashKey, out doc))
        {
            entry.StructuredDocumentJson = doc;
            try
            {
                using JsonDocument parsed = JsonDocument.Parse(doc);
                string id = DocumentString(parsed.RootElement, "document_id", 64);
                if (id.Length != 0) _workspaceSeenDocuments.Add(id);
            }
            catch (JsonException) { }
        }
    }

    private static string SafeBbText(string text)
    {
        // Nội dung do model sinh không được trở thành BBCode tùy ý.
        var result = new StringBuilder();
        foreach (char symbol in text ?? "")
        {
            if (symbol == '[') result.Append("[lb]");
            else if (symbol == ']') result.Append("[rb]");
            else result.Append(symbol);
        }
        return result.ToString();
    }

    private static string InlineMarkup(string text)
    {
        // Chỉ các dạng Markdown thông dụng; mọi BBCode do model sinh đều escape.
        string escaped = SafeBbText(text);
        escaped = Regex.Replace(escaped, @"\*\*(.+?)\*\*", "[b]$1[/b]");
        escaped = Regex.Replace(escaped, @"(?<!\*)\*([^*\n]+)\*(?!\*)", "[i]$1[/i]");
        escaped = Regex.Replace(escaped, @"`([^`\n]+)`", "[code]$1[/code]");
        return escaped;
    }

    private RichTextLabel RenderRichParagraph(string text, int size = 15, bool muted = false)
    {
        var label = new RichTextLabel
        {
            BbcodeEnabled = true, FitContent = true, ScrollActive = false,
            SelectionEnabled = true, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            CustomMinimumSize = new Vector2(0, 28),
        };
        label.AddThemeFontSizeOverride("normal_font_size", size);
        label.AddThemeColorOverride("default_color", muted ? ErisMuted : ErisText);
        label.Text = InlineMarkup(text);
        return label;
    }

    private void RenderStructuredBlock(VBoxContainer column, JsonElement block)
    {
        string type = DocumentString(block, "type", 40);
        string text = DocumentString(block, "text", 12000);
        switch (type)
        {
            case "heading":
                int level = 2;
                if (block.TryGetProperty("level", out var l) && l.TryGetInt32(out int lv)) level = lv;
                // Tiêu đề là mốc điều hướng, không phải một dòng chữ bị hòa vào đoạn văn.
                var section = new PanelContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
                var sectionStyle = new StyleBoxFlat {
                    BgColor = new Color("fff4f9"), BorderColor = new Color("f4cce1"),
                    BorderWidthLeft = 3, ContentMarginLeft = 14, ContentMarginRight = 12,
                    ContentMarginTop = 9, ContentMarginBottom = 9,
                    CornerRadiusTopLeft = 7, CornerRadiusTopRight = 7,
                    CornerRadiusBottomLeft = 7, CornerRadiusBottomRight = 7,
                };
                section.AddThemeStyleboxOverride("panel", sectionStyle);
                section.AddChild(RenderRichParagraph("**" + text + "**", level <= 2 ? 19 : 16));
                column.AddChild(section);
                break;
            case "paragraph":
                column.AddChild(RenderRichParagraph(text));
                break;
            case "quote":
                column.AddChild(RenderRichParagraph("“" + text + "”", 14, true));
                break;
            case "list":
                if (block.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
                    foreach (var item in items.EnumerateArray())
                        if (item.ValueKind == JsonValueKind.String)
                            column.AddChild(RenderRichParagraph("• " + (item.GetString() ?? "")));
                break;
            case "code":
                var heading = new HBoxContainer();
                string language = DocumentString(block, "language", 30);
                heading.AddChild(ConversationLabel("CODE " + language, 11, ErisMuted));
                var copy = new Button { Text = "Sao chép code" };
                copy.Pressed += () => DisplayServer.ClipboardSet(text);
                heading.AddChild(copy);
                column.AddChild(heading);
                var code = new CodeEdit
                {
                    Text = text, Editable = false,
                    CustomMinimumSize = new Vector2(640, Math.Min(330, 65 + text.Split('\n').Length * 22)),
                    SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
                };
                column.AddChild(code);
                break;
            case "formula":
                string latex = DocumentString(block, "latex", 1800);
                // Ưu tiên kết quả toán đã được dựng thành ảnh, không phơi raw LaTeX
                // ở đầu mỗi công thức khiến bài giải trông như một đoạn log.
                bool hasFormulaPicture = block.TryGetProperty("path", out _) && TryAddArtifactImage(column, block, inlineFormula: true);
                if (!hasFormulaPicture)
                    column.AddChild(RenderRichParagraph(latex, 15));
                var formulaFooter = new HBoxContainer();
                var copyMath = new Button { Text = "Sao chép công thức" };
                copyMath.Pressed += () => DisplayServer.ClipboardSet(latex);
                formulaFooter.AddChild(copyMath);
                if (hasFormulaPicture)
                {
                    var showRaw = new Button { Text = "Xem LaTeX" };
                    var raw = RenderRichParagraph(latex, 12, true);
                    raw.Visible = false;
                    showRaw.Pressed += () => { raw.Visible = !raw.Visible; };
                    formulaFooter.AddChild(showRaw);
                    column.AddChild(formulaFooter);
                    column.AddChild(raw);
                }
                else column.AddChild(formulaFooter);
                break;
            case "table":
                if (!block.TryGetProperty("headers", out var headers) || headers.ValueKind != JsonValueKind.Array)
                    break;
                int cols = Math.Max(1, Math.Min(12, headers.GetArrayLength()));
                var grid = new GridContainer { Columns = cols, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
                foreach (var h in headers.EnumerateArray()) grid.AddChild(RenderRichParagraph("**" + h.ToString() + "**", 13));
                if (block.TryGetProperty("rows", out var rows) && rows.ValueKind == JsonValueKind.Array)
                    foreach (var row in rows.EnumerateArray())
                        if (row.ValueKind == JsonValueKind.Array)
                        {
                            int k=0;
                            foreach (var cell in row.EnumerateArray())
                            {
                                if (k++ >= cols) break;
                                grid.AddChild(RenderRichParagraph(cell.ToString(), 13));
                            }
                            while (k++ < cols) grid.AddChild(new Label { Text = "" });
                        }
                column.AddChild(grid);
                break;
            default:
                if (!string.IsNullOrEmpty(text)) column.AddChild(RenderRichParagraph(text));
                break;
        }
    }

    private Control RenderStructuredConversationDocument(string raw, string original)
    {
        var column = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(620, 0) };
        column.AddThemeConstantOverride("separation", 11);
        try
        {
            using JsonDocument doc = JsonDocument.Parse(raw);
            JsonElement root = doc.RootElement;
            if (root.TryGetProperty("blocks", out var blocks) && blocks.ValueKind == JsonValueKind.Array)
            {
                int count = 0;
                foreach (var block in blocks.EnumerateArray())
                {
                    if (++count > 80) break;
                    if (block.ValueKind == JsonValueKind.Object) RenderStructuredBlock(column, block);
                }
                if (count > 0) return column;
                // Tài liệu sống có thể bắt đầu trước khi nhánh trình bày gửi
                // khối đầu tiên; đừng đổ nguyên wall-of-text ở trạng thái này.
                if (root.TryGetProperty("revision", out var liveRev) && liveRev.TryGetInt32(out int r) && r > 0)
                {
                    column.AddChild(RenderRichParagraph("Eris đang viết lên bảng…", 14, true));
                    return column;
                }
            }
        }
        catch (Exception ex) { GD.PrintErr("[V5] Fallback text do lỗi render: " + ex.Message); }
        column.AddChild(RenderRichParagraph(original));
        return column;
    }
}
