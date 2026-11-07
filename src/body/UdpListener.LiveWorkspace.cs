using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

public partial class UdpListener
{
    // V6: mỗi tài liệu là một chuỗi thay đổi; không bao giờ chỉnh transcript TTS.
    // Revision được kiểm tra trước khi sửa để tránh worker cũ ghi đè kết quả mới.
    private sealed class LiveWorkspaceState
    {
        public string DocumentId = "";
        public string TurnId = "";
        public string TextHash = "";
        public string SessionId = "";
        public int Generation;
        public int Revision;
        public bool Finished;
        public readonly List<string> Order = new();
        public readonly Dictionary<string, JsonElement> Blocks = new();

        public string ToDocumentJson()
        {
            var result = Order.Where(key => Blocks.ContainsKey(key))
                .Select(key => Blocks[key]).ToArray();
            return JsonSerializer.Serialize(new {
                parse_mode = "workspace_document", workspace_version = 1,
                document_id = DocumentId, turn_id = TurnId,
                text_hash = TextHash, revision = Revision, finished = Finished, blocks = result,
            });
        }
    }

    private bool _liveWorkspaceRenderDirty;
    private ulong _liveWorkspaceLastRenderAt;
    private readonly Dictionary<string, LiveWorkspaceState> _liveWorkspaceDocuments = new();
    private readonly Dictionary<string, int> _liveLatestGeneration = new();

    private void AcknowledgeLiveWorkspace(string documentId, int revision, string status)
    {
        try
        {
            string ip = _udp.GetPacketIP();
            int port = _udp.GetPacketPort();
            if ((ip != "127.0.0.1" && ip != "::1") || port < 1 || port > 65535)
                return;
            using var sender = new PacketPeerUdp();
            if (sender.SetDestAddress(ip, port) != Error.Ok) return;
            byte[] raw = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                parse_mode = "workspace_live_ack", document_id = documentId, revision, status,
            }));
            sender.PutPacket(raw);
        }
        catch (Exception exc)
        {
            GD.PrintErr("[V6] Không gửi được ACK: " + exc.Message);
        }
    }

    private bool TryHandleWorkspaceLivePacket(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw) || !raw.Contains("workspace_live_op", StringComparison.Ordinal))
            return false;
        try
        {
            if (Encoding.UTF8.GetByteCount(raw) > 42000) return true;
            using JsonDocument parsed = JsonDocument.Parse(raw);
            JsonElement packet = parsed.RootElement;
            if (DocumentString(packet, "parse_mode", 40) != "workspace_live_op") return false;
            if (!packet.TryGetProperty("workspace_version", out var version) ||
                !version.TryGetInt32(out int schema) || schema != 2) return true;
            string documentId = DocumentString(packet, "document_id", 64);
            string textHash = DocumentString(packet, "text_hash", 64);
            string turnId = DocumentString(packet, "turn_id", 150);
            string sessionId = DocumentString(packet, "session_id", 150);
            string operation = DocumentString(packet, "operation", 30);
            string blockId = DocumentString(packet, "block_id", 64);
            string afterId = DocumentString(packet, "after_id", 64);
            if (!Regex.IsMatch(documentId, "^[a-f0-9]{32}$") ||
                !Regex.IsMatch(textHash, "^[a-f0-9]{64}$") ||
                string.IsNullOrWhiteSpace(turnId) || string.IsNullOrWhiteSpace(sessionId) ||
                !packet.TryGetProperty("revision", out var revElement) ||
                !revElement.TryGetInt32(out int revision) ||
                !packet.TryGetProperty("previous_revision", out var prevElement) ||
                !prevElement.TryGetInt32(out int previous) ||
                !packet.TryGetProperty("generation", out var genElement) ||
                !genElement.TryGetInt32(out int generation) || generation < 0 ||
                revision < 1 || revision > 10000)
                return true;

            if (_liveLatestGeneration.TryGetValue(sessionId, out int latest) && generation < latest)
            {
                AcknowledgeLiveWorkspace(documentId, revision, "stale");
                return true;
            }

            if (!_liveWorkspaceDocuments.TryGetValue(documentId, out var state))
            {
                if (operation != "begin" || previous != 0 || revision != 1)
                {
                    AcknowledgeLiveWorkspace(documentId, revision, "out_of_order");
                    return true;
                }
                state = new LiveWorkspaceState {
                    DocumentId = documentId, TextHash = textHash, TurnId = turnId,
                    SessionId = sessionId, Generation = generation,
                };
                _liveWorkspaceDocuments[documentId] = state;
                if (_liveWorkspaceDocuments.Count > 40)
                    _liveWorkspaceDocuments.Remove(_liveWorkspaceDocuments.Keys.First());
            }
            else if (state.TurnId != turnId || state.TextHash != textHash ||
                     state.SessionId != sessionId || state.Generation != generation)
            {
                AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                return true;
            }

            if (revision <= state.Revision)
            {
                AcknowledgeLiveWorkspace(documentId, revision, "duplicate");
                return true;
            }
            if (previous != state.Revision || revision != state.Revision + 1)
            {
                AcknowledgeLiveWorkspace(documentId, revision, "out_of_order");
                return true;
            }

            switch (operation)
            {
                case "begin":
                    if (revision != 1) return true;
                    break;
                case "insert_block":
                case "replace_block":
                    if (!Regex.IsMatch(blockId, "^[a-zA-Z0-9_-]{1,64}$") ||
                        !packet.TryGetProperty("block", out var block) ||
                        block.ValueKind != JsonValueKind.Object ||
                        block.GetRawText().Length > 11000)
                    {
                        AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                        return true;
                    }
                    string kind = DocumentString(block, "type", 30);
                    if (kind != "heading" && kind != "paragraph" && kind != "formula" &&
                        kind != "code" && kind != "list" && kind != "quote" &&
                        kind != "table" && kind != "image")
                    {
                        AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                        return true;
                    }
                    if (operation == "insert_block")
                    {
                        if (state.Blocks.ContainsKey(blockId) || state.Order.Count >= 80 ||
                            (!string.IsNullOrEmpty(afterId) && !state.Order.Contains(afterId)))
                        {
                            AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                            return true;
                        }
                        int position = string.IsNullOrEmpty(afterId) ? state.Order.Count : state.Order.IndexOf(afterId) + 1;
                        state.Order.Insert(position, blockId);
                    }
                    else if (!state.Blocks.ContainsKey(blockId))
                    {
                        AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                        return true;
                    }
                    state.Blocks[blockId] = block.Clone();
                    break;
                case "remove_block":
                    if (!state.Blocks.Remove(blockId))
                    {
                        AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                        return true;
                    }
                    state.Order.Remove(blockId);
                    break;
                case "finalize":
                    state.Finished = true;
                    break;
                default:
                    AcknowledgeLiveWorkspace(documentId, revision, "rejected");
                    return true;
            }
            state.Revision = revision;
            _liveLatestGeneration[sessionId] = Math.Max(generation,
                _liveLatestGeneration.TryGetValue(sessionId, out int known) ? known : generation);
            // ACK xác nhận đã áp dụng vào state; không ngụ ý Panel đang mở.
            AttachLiveWorkspaceState(state);
            AcknowledgeLiveWorkspace(documentId, revision, "applied");
            return true;
        }
        catch (Exception ex)
        {
            GD.PrintErr("[V6] Không nhận được thao tác tài liệu: " + ex.Message);
            return true;
        }
    }

    private bool AttachLiveWorkspaceState(LiveWorkspaceState state)
    {
        // Chỉ gắn vào tin cùng turn_id VÀ hash. Không thay đổi hội thoại gốc.
        for (int i = _conversationEntries.Count - 1; i >= Math.Max(0, _conversationEntries.Count - 60); i--)
        {
            var entry = _conversationEntries[i];
            if (entry.Role != "assistant" || entry.TurnId != state.TurnId ||
                ConversationDocumentHash(entry.Text) != state.TextHash)
                continue;
            entry.StructuredDocumentJson = state.ToDocumentJson();
            SaveConversationHistory();
            // Không rebuild mọi datagram: một bài có thể gửi hàng chục patch,
            // redraw liên tục gây giật, mất vị trí cuộn và nặng Godot UI.
            _liveWorkspaceRenderDirty = true;
            return true;
        }
        return false;
    }

    private void TickLiveWorkspaceUi()
    {
        if (!_liveWorkspaceRenderDirty) return;
        if (_conversationHistoryPanel == null || !_conversationHistoryPanel.Visible) return;
        ulong now = Time.GetTicksMsec();
        if (now - _liveWorkspaceLastRenderAt < 125) return;
        _liveWorkspaceRenderDirty = false;
        _liveWorkspaceLastRenderAt = now;
        int scroll = _conversationScroll?.ScrollVertical ?? 0;
        RebuildConversationMessages(rebuildDayList: false);
        // Giữ góc nhìn đọc hiện tại, không đẩy người dùng về cuối khi Writer sửa.
        Callable.From(() => {
            if (_conversationScroll != null && GodotObject.IsInstanceValid(_conversationScroll))
                _conversationScroll.ScrollVertical = scroll;
        }).CallDeferred();
    }

    private void TryAttachPendingLiveWorkspace(ConversationEntry entry)
    {
        if (entry.Role != "assistant" || string.IsNullOrEmpty(entry.TurnId)) return;
        string hash = ConversationDocumentHash(entry.Text);
        foreach (var state in _liveWorkspaceDocuments.Values)
        {
            if (state.TurnId != entry.TurnId || state.TextHash != hash) continue;
            entry.StructuredDocumentJson = state.ToDocumentJson();
            break;
        }
    }
}
