using Godot;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using RandomNumberGenerator = System.Security.Cryptography.RandomNumberGenerator;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

public partial class UdpListener
{
    // Rich Chat runs in the user's regular browser, but operates on Eris' LIVE
    // single-writer transcript and existing SendUserChatMessage. No second LLM,
    // no second conversation store, no Python/socket changes.
    private TcpListener _richChatServer;
    private CancellationTokenSource _richChatCancellation;
    private readonly ConcurrentQueue<string> _richChatOutbox = new();
    // Yêu cầu xóa transcript được xử lý trên Godot main thread, cùng nơi ghi lịch sử.
    private sealed class RichChatDeleteRequest
    {
        public string Scope { get; init; } = "";
        public string Date { get; init; } = "";
        public TaskCompletionSource<string> Completion { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        // 0 = queued, 1 = main thread xử lý, 2 = hủy vì quá hạn trước khi chạy.
        public int Lifecycle;
    }
    private readonly ConcurrentQueue<RichChatDeleteRequest> _richChatDeleteOutbox = new();
    private readonly ConcurrentQueue<(string PathId, int Page)> _learnerPortraitOutbox = new();
    private string _learnerPortraitSnapshot = "{\"status\":\"loading\"}";
    private readonly Dictionary<string, (byte[] Bytes, string Mime)> _richChatAssets = new();
    private string _richChatToken = "";
    private string _richChatUrl = "";
    private string _richChatSnapshot = "{\"messages\":[],\"status\":\"Đang kết nối\"}";
    private int _richChatEventClients;
    private ulong _richChatLastSnapshotMs;
    // The same loopback-only, token-protected browser server is shared by
    // Rich Chat and Learner Portrait. No second HTTP listener or public port.
    private void EnsureRichChatPortal()
    {
        if (_richChatServer != null) return;
        LoadRichChatAssets();
        _richChatToken = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        _richChatServer = new TcpListener(IPAddress.Loopback, 0);
        _richChatServer.Start(16);
        int port = ((IPEndPoint)_richChatServer.LocalEndpoint).Port;
        _richChatUrl = $"http://127.0.0.1:{port}/{_richChatToken}/";
        _richChatCancellation = new CancellationTokenSource();
        _ = Task.Run(() => RichChatAcceptLoop(_richChatCancellation.Token));
        GD.Print("[RichChat] Browser portal started on loopback only");
    }

    private void OpenRichChatPortal() => OpenRichChatPortal("");

    private void OpenRichChatPortal(string page)
    {
        if (_companionPreview) return;
        try
        {
            EnsureRichChatPortal();
            UpdateRichChatSnapshot();
            Error launch = OS.ShellOpen(_richChatUrl + page);
            if (launch != Error.Ok)
                throw new InvalidOperationException("Không mở được trình duyệt: " + launch);
        }
        catch (Exception exc)
        {
            GD.PrintErr("[RichChat] Không mở được: " + exc.Message);
            StopRichChatPortal();
            if (_conversationHistoryPanel != null && !_conversationHistoryPanel.Visible)
                ShowConversationHistoryPanel();
        }
    }

    private void OpenLearnerPortraitPortal()
    {
        if (_companionPreview) return;
        CloseCharacterContextMenu();
        try
        {
            EnsureRichChatPortal();
            SendLearnerPortraitRequest();
            if (OS.ShellOpen(_richChatUrl + "portrait/index.html") != Error.Ok)
                throw new InvalidOperationException("Trình duyệt chưa mở được.");
        }
        catch (Exception exc)
        {
            GD.PrintErr("[LearnerPortrait] Fallback Godot: " + exc.Message);
            // Preserve the old read-only panel as a usable offline fallback.
            ShowNativeCapabilityPanel();
        }
    }

    private void LoadRichChatAssets()
    {
        Assembly assembly = typeof(UdpListener).Assembly;
        var resources = assembly.GetManifestResourceNames();
        string[] files = {
            "index.html", "chat.css", "chat.js", "studio.css", "studio.js", "artifacts.js", "canvas.html", "canvas.css", "canvas.js", "vendor/mathjax.js", "vendor/prism.css",
            "vendor/prism.js", "vendor/prism-clike.js", "vendor/prism-python.js",
            "vendor/prism-javascript.js", "vendor/prism-typescript.js", "vendor/prism-csharp.js",
            "vendor/prism-c.js", "vendor/prism-cpp.js", "vendor/prism-java.js", "vendor/prism-css.js",
            "vendor/prism-markup.js", "vendor/prism-bash.js", "vendor/prism-json.js",
            "portrait/index.html", "portrait/portrait.css", "portrait/portrait.js"
        };
        foreach (string file in files)
        {
            string suffix = "richchat." + file.Replace('/', '.');
            string resource = resources.FirstOrDefault(name => name.EndsWith(suffix, StringComparison.OrdinalIgnoreCase));
            if (resource == null) throw new FileNotFoundException("Embedded Rich Chat asset absent: " + file);
            using Stream stream = assembly.GetManifestResourceStream(resource);
            using var buffer = new MemoryStream();
            stream.CopyTo(buffer);
            string mime = file.EndsWith(".js", StringComparison.Ordinal) ? "text/javascript; charset=utf-8"
                : file.EndsWith(".css", StringComparison.Ordinal) ? "text/css; charset=utf-8"
                : "text/html; charset=utf-8";
            _richChatAssets[file] = (buffer.ToArray(), mime);
        }
    }

    private async Task RichChatAcceptLoop(CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested)
        {
            try
            {
                TcpClient client = await _richChatServer.AcceptTcpClientAsync(cancellation);
                _ = Task.Run(() => ServeRichChatClient(client), cancellation);
            }
            catch (OperationCanceledException) { break; }
            catch (ObjectDisposedException) { break; }
            catch (Exception exc)
            {
                if (!cancellation.IsCancellationRequested)
                    GD.PrintErr("[RichChat] Accept error: " + exc.Message);
                break;
            }
        }
    }

    private void ServeRichChatClient(TcpClient client)
    {
        using (client)
        {
            try
            {
                client.ReceiveTimeout = 4000;
                client.SendTimeout = 4000;
                using NetworkStream net = client.GetStream();
                // Bound HTTP request size and close each connection, no websocket/external routing.
                using var data = new MemoryStream();
                byte[] chunk = new byte[4096];
                int headerEnd = -1;
                while (data.Length < 16_384 && headerEnd == -1)
                {
                    int read = net.Read(chunk, 0, chunk.Length);
                    if (read <= 0) return;
                    data.Write(chunk, 0, read);
                    headerEnd = FindHttpSeparator(data.GetBuffer(), (int)data.Length);
                }
                if (headerEnd < 0) { Reply(net, 400, "text/plain", Encoding.UTF8.GetBytes("Bad Request")); return; }
                byte[] bytes = data.ToArray();
                string header = Encoding.ASCII.GetString(bytes, 0, headerEnd);
                string[] lines = header.Split("\r\n");
                string[] request = lines[0].Split(' ');
                if (request.Length != 3) { Reply(net, 400, "text/plain", Array.Empty<byte>()); return; }
                string method = request[0], path = request[1].Split('?')[0];
                // Refuse DNS rebinding and cross-origin writes even when the token leaks.
                string expectedHost = "127.0.0.1:" + ((IPEndPoint)_richChatServer.LocalEndpoint).Port;
                if (!lines.Any(line => line.Equals("Host: " + expectedHost, StringComparison.OrdinalIgnoreCase)))
                { Reply(net, 403, "text/plain", Array.Empty<byte>()); return; }
                string routePrefix = "/" + _richChatToken + "/";
                if (!path.StartsWith(routePrefix, StringComparison.Ordinal))
                { Reply(net, 404, "text/plain", Array.Empty<byte>()); return; }
                string relative = path.Substring(routePrefix.Length);
                if (method == "GET" && relative == "api/canvas/state")
                {
                    Reply(net, 200, "application/json; charset=utf-8",
                        Encoding.UTF8.GetBytes(Volatile.Read(ref _webCanvasStateJson)));
                    return;
                }
                if (method == "GET" && relative == "api/state")
                {
                    string snapshot = Volatile.Read(ref _richChatSnapshot);
                    Reply(net, 200, "application/json; charset=utf-8", Encoding.UTF8.GetBytes(snapshot));
                    return;
                }
                if (method == "GET" && relative == "api/events")
                {
                    // SSE phản ánh snapshot đã được main thread xác nhận.
                    // Không tự bịa token-stream hoặc tạo agent thứ hai.
                    // Giới hạn kết nối để tab đóng/mở không giữ thread vô hạn.
                    if (Interlocked.Increment(ref _richChatEventClients) > 8)
                    {
                        Interlocked.Decrement(ref _richChatEventClients);
                        Reply(net, 409, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}"));
                        return;
                    }
                    try { StreamRichChatEvents(net); }
                    finally { Interlocked.Decrement(ref _richChatEventClients); }
                    return;
                }
                if (method == "GET" && relative == "api/portrait/state")
                {
                    Reply(net, 200, "application/json; charset=utf-8",
                          Encoding.UTF8.GetBytes(Volatile.Read(ref _learnerPortraitSnapshot)));
                    return;
                }
                if (method == "POST" && (relative == "api/portrait/refresh" || relative == "api/portrait/select" || relative == "api/portrait/page"))
                {
                    string expectedOrigin = "Origin: http://" + expectedHost;
                    if (!lines.Any(line => line.Equals(expectedOrigin, StringComparison.OrdinalIgnoreCase)))
                    { Reply(net, 403, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    int length = -1;
                    foreach (string line in lines.Skip(1))
                        if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase) &&
                            int.TryParse(line.Substring(15).Trim(), out int size)) length = size;
                    if (length < 0 || length > 1024)
                    { Reply(net, 413, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    int offset = headerEnd + 4;
                    while (data.Length - offset < length && data.Length < 2048)
                    {
                        int read = net.Read(chunk, 0, Math.Min(chunk.Length, length - (int)data.Length + offset));
                        if (read <= 0) return;
                        data.Write(chunk, 0, read);
                    }
                    string pathId = "";
                    int pageIndex = -1;
                    if (relative == "api/portrait/page")
                    {
                        using JsonDocument navigation = JsonDocument.Parse(data.ToArray().AsMemory(offset, length));
                        if (!navigation.RootElement.TryGetProperty("page", out var pageValue) ||
                            pageValue.ValueKind != JsonValueKind.Number ||
                            !pageValue.TryGetInt32(out pageIndex) || pageIndex < 0 || pageIndex > 20)
                        { Reply(net, 400, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    }
                    if (relative == "api/portrait/select")
                    {
                        using JsonDocument selection = JsonDocument.Parse(data.ToArray().AsMemory(offset, length));
                        pathId = selection.RootElement.TryGetProperty("path_id", out var pid) &&
                                 pid.ValueKind == JsonValueKind.String ? pid.GetString() ?? "" : "";
                        if (pathId.Length == 0 || pathId.Length > 120 || pathId.Any(char.IsControl))
                        { Reply(net, 400, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    }
                    if (_learnerPortraitOutbox.Count >= 5)
                    { Reply(net, 409, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    _learnerPortraitOutbox.Enqueue((pathId, pageIndex));
                    Reply(net, 202, "application/json", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                    return;
                }
                if (method == "POST" && relative == "api/history/delete")
                {
                    // Chỉ trang Rich Chat cùng origin được quyền yêu cầu hành động phá hủy.
                    string expectedOrigin = "Origin: http://" + expectedHost;
                    if (!lines.Any(line => line.Equals(expectedOrigin, StringComparison.OrdinalIgnoreCase)))
                    { Reply(net, 403, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false,\"reason\":\"origin\"}")); return; }
                    int length = -1;
                    foreach (string line in lines.Skip(1))
                        if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase) &&
                            int.TryParse(line.Substring(15).Trim(), out int size)) length = size;
                    if (length < 2 || length > 384)
                    { Reply(net, 413, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false,\"reason\":\"payload\"}")); return; }
                    int offset = headerEnd + 4;
                    while (data.Length - offset < length && data.Length < 1024)
                    {
                        int read = net.Read(chunk, 0, Math.Min(chunk.Length, length - (int)data.Length + offset));
                        if (read <= 0) return;
                        data.Write(chunk, 0, read);
                    }
                    using JsonDocument deletionJson = JsonDocument.Parse(data.ToArray().AsMemory(offset, length));
                    JsonElement deletion = deletionJson.RootElement;
                    string scope = deletion.ValueKind == JsonValueKind.Object && deletion.TryGetProperty("scope", out var scopeElement) && scopeElement.ValueKind == JsonValueKind.String
                        ? scopeElement.GetString() ?? "" : "";
                    string date = deletion.ValueKind == JsonValueKind.Object && deletion.TryGetProperty("date", out var dateElement) && dateElement.ValueKind == JsonValueKind.String
                        ? dateElement.GetString() ?? "" : "";
                    string confirmation = deletion.ValueKind == JsonValueKind.Object && deletion.TryGetProperty("confirmation", out var confirmElement) && confirmElement.ValueKind == JsonValueKind.String
                        ? confirmElement.GetString() ?? "" : "";
                    bool valid = confirmation == "DELETE_ERIS_HISTORY" && (scope == "all" && date.Length == 0 ||
                        scope == "day" && DateTime.TryParseExact(date, "yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture,
                            System.Globalization.DateTimeStyles.None, out _) && date.Length == 10);
                    if (!valid)
                    { Reply(net, 400, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false,\"reason\":\"invalid_request\"}")); return; }
                    if (_richChatDeleteOutbox.Count >= 2)
                    { Reply(net, 409, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false,\"reason\":\"queued\"}")); return; }
                    var requestDelete = new RichChatDeleteRequest { Scope = scope, Date = date };
                    _richChatDeleteOutbox.Enqueue(requestDelete);
                    // Không báo xóa thành công khi main thread chưa xác nhận đã lưu.
                    string outcome;
                    try
                    {
                        if (!requestDelete.Completion.Task.Wait(TimeSpan.FromSeconds(3)))
                        {
                            // Không để yêu cầu hết hạn bất ngờ xóa lịch sử sau khi web báo lỗi.
                            bool cancelled = Interlocked.CompareExchange(ref requestDelete.Lifecycle, 2, 0) == 0;
                            Reply(net, 503, "application/json", Encoding.UTF8.GetBytes(cancelled
                                ? "{\"ok\":false,\"reason\":\"timeout\"}"
                                : "{\"ok\":false,\"reason\":\"timeout_processing\"}"));
                            return;
                        }
                        outcome = requestDelete.Completion.Task.Result;
                    }
                    catch (Exception)
                    { Reply(net, 503, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false,\"reason\":\"error\"}")); return; }
                    int status = outcome == "ok" ? 200 : outcome is "busy" or "empty" ? 409 : 500;
                    Reply(net, status, "application/json; charset=utf-8",
                        Encoding.UTF8.GetBytes("{\"ok\":" + (outcome == "ok" ? "true" : "false") + ",\"reason\":\"" + outcome + "\"}"));
                    return;
                }
                if (method == "GET" && relative.StartsWith("api/image/", StringComparison.Ordinal))
                {
                    if (TryReadRichChatArtifactImage(relative, out var png))
                        Reply(net, 200, "image/png", png);
                    else Reply(net, 404, "text/plain", Array.Empty<byte>());
                    return;
                }
                if (method == "POST" && (relative == "api/send" || relative == "api/feedback" || relative == "api/artifact/edit" || relative == "api/canvas/request"))
                {
                    int length = -1;
                    foreach (string line in lines.Skip(1))
                        if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase) &&
                            int.TryParse(line.Substring(15).Trim(), out int value)) length = value;
                    if (length < 1 || length > (relative == "api/artifact/edit" ? 80_000 : relative == "api/canvas/request" ? 20_000 : 12_000))
                    { Reply(net, 413, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    int contentOffset = headerEnd + 4;
                    while (data.Length - contentOffset < length && data.Length < 100_000)
                    {
                        int read = net.Read(chunk, 0, Math.Min(chunk.Length, length - (int)data.Length + contentOffset));
                        if (read <= 0) return;
                        data.Write(chunk, 0, read);
                    }
                    bytes = data.ToArray();
                    using JsonDocument json = JsonDocument.Parse(bytes.AsMemory(contentOffset, length));
                    if (relative == "api/canvas/request")
                    {
                        string expectedOrigin = "Origin: http://" + expectedHost;
                        bool sameOrigin = lines.Any(line => line.Equals(expectedOrigin, StringComparison.OrdinalIgnoreCase));
                        if (!sameOrigin)
                        {
                            Reply(net, 403, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}"));
                            return;
                        }
                        bool accepted = TryQueueWebCanvasRequest(json.RootElement);
                        Reply(net, accepted ? 202 : 400, "application/json; charset=utf-8",
                            Encoding.UTF8.GetBytes(accepted ? "{\"ok\":true}" : "{\"ok\":false}"));
                        return;
                    }
                    if (relative == "api/feedback")
                    {
                        // The page MUST be served by this listener, not a cross-origin page.
                        string expectedOrigin = "Origin: http://" + expectedHost;
                        if (!lines.Any(line => line.Equals(expectedOrigin, StringComparison.OrdinalIgnoreCase)) ||
                            !RecordRichChatFeedback(json.RootElement))
                            Reply(net, 400, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}"));
                        else Reply(net, 202, "application/json", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                        return;
                    }
                    if (relative == "api/artifact/edit")
                    {
                        string expectedOrigin = "Origin: http://" + expectedHost;
                        if (!lines.Any(line => line.Equals(expectedOrigin, StringComparison.OrdinalIgnoreCase)))
                        { Reply(net, 403, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                        // The workspace editor backend is not implemented yet.
                        Reply(net, 501, "application/json; charset=utf-8",
                            Encoding.UTF8.GetBytes("{\"ok\":false,\"error\":\"artifact_edit_not_implemented\"}"));
                        return;
                    }
                    string message = json.RootElement.TryGetProperty("message", out var msg) && msg.ValueKind == JsonValueKind.String
                        ? msg.GetString()?.Trim() ?? "" : "";
                    if (message.Length == 0 || message.Length > 6000 || _richChatOutbox.Count >= 15)
                    { Reply(net, 400, "application/json", Encoding.UTF8.GetBytes("{\"ok\":false}")); return; }
                    _richChatOutbox.Enqueue(message);
                    Reply(net, 202, "application/json", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                    return;
                }
                if (method != "GET") { Reply(net, 405, "text/plain", Array.Empty<byte>()); return; }
                string asset = relative.Length == 0 ? "index.html" : relative;
                if (!_richChatAssets.TryGetValue(asset, out var payload))
                { Reply(net, 404, "text/plain", Array.Empty<byte>()); return; }
                Reply(net, 200, payload.Mime, payload.Bytes);
            }
            catch (Exception) { /* Browser closed while requesting: do not disrupt Eris. */ }
        }
    }

    // Snapshot streaming qua HTTP trên localhost (text/event-stream).
    // Trình duyệt có fallback GET /api/state nếu SSE không khả dụng.
    private void StreamRichChatEvents(NetworkStream net)
    {
        byte[] header = Encoding.ASCII.GetBytes(
            "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n" +
            "Cache-Control: no-cache, no-store\r\nConnection: close\r\n" +
            "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n" +
            "X-Frame-Options: DENY\r\n\r\n");
        net.WriteTimeout = 4000;
        net.Write(header, 0, header.Length);
        string previous = null;
        DateTime started = DateTime.UtcNow;
        DateTime heartbeat = started;
        while (_richChatServer != null && _richChatCancellation?.IsCancellationRequested == false
               && (DateTime.UtcNow - started).TotalSeconds < 45)
        {
            string current = Volatile.Read(ref _richChatSnapshot);
            if (!string.Equals(current, previous, StringComparison.Ordinal))
            {
                // JsonSerializer là một dòng; escape CR/LF dự phòng nếu format đổi.
                string compact = current.Replace("\r", "").Replace("\n", "");
                byte[] frame = Encoding.UTF8.GetBytes("event: state\ndata: " + compact + "\n\n");
                net.Write(frame, 0, frame.Length);
                previous = current;
                heartbeat = DateTime.UtcNow;
            }
            else if ((DateTime.UtcNow - heartbeat).TotalSeconds > 11)
            {
                byte[] ping = Encoding.ASCII.GetBytes(": ping\n\n");
                net.Write(ping, 0, ping.Length);
                heartbeat = DateTime.UtcNow;
            }
            Thread.Sleep(250);
        }
    }

    private static int FindHttpSeparator(byte[] bytes, int length)
    {
        for (int i = 0; i < length - 3; i++)
            if (bytes[i] == 13 && bytes[i + 1] == 10 && bytes[i + 2] == 13 && bytes[i + 3] == 10)
                return i;
        return -1;
    }

    private static void Reply(NetworkStream net, int status, string mime, byte[] payload)
    {
        string label = status switch { 200 => "OK", 202 => "Accepted", 409 => "Conflict", 400 => "Bad Request",
            403 => "Forbidden", 404 => "Not Found", 405 => "Method Not Allowed", 413 => "Payload Too Large",
            501 => "Not Implemented", 503 => "Service Unavailable", _ => "Error" };
        string headers = $"HTTP/1.1 {status} {label}\r\nContent-Type: {mime}\r\nContent-Length: {payload.Length}\r\n" +
            "Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n" +
            "Referrer-Policy: no-referrer\r\nX-Frame-Options: DENY\r\n" +
            "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
            "font-src 'self' data:; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'\r\n\r\n";
        byte[] prefix = Encoding.ASCII.GetBytes(headers);
        net.Write(prefix, 0, prefix.Length);
        net.Write(payload, 0, payload.Length);
    }

    private void UpdateRichChatSnapshot()
    {
        if (_richChatServer == null) return;
        // Main thread only. Snapshot is handed atomically to the HTTP reader thread.
        var visible = _conversationEntries.TakeLast(ConversationHistoryLimit).Select((entry, index) => new {
            id = index, role = entry.Role, text = entry.Text, date = entry.Date, time = entry.Time,
            turnId = entry.Role == "assistant" ? entry.TurnId : "",
        }).ToArray();
        var activeTurns = _conversationEntries.Where(e => e.Role == "assistant" && !string.IsNullOrEmpty(e.TurnId))
            .Select(e => e.TurnId).ToHashSet(StringComparer.Ordinal);
        var bundles = ReadRichChatBundles(activeTurns);
        // Read the existing stage/TTS authority: no independent browser-side guess
        // about which turn is speaking. A stale TTS completion never selects a
        // different transcript row. The page may remain open after Eris exits.
        bool speaking = _ttsAudioIsPlaying || _runtimePresence.Speaking;
        bool thinking = !speaking && (_runtimePresence.Thinking ||
            _conversationState is "thinking" or "preparing" or "buffering");
        string speechTurnId = !string.IsNullOrWhiteSpace(_ttsBodyAudioTurnId)
            ? _ttsBodyAudioTurnId : _runtimePresence.TurnId;
        string phase = speaking ? "speaking" : thinking ? "thinking" : "idle";
        // Chỉ công bố metadata quan sát được, không lộ chuỗi suy luận nội bộ.
        string activity = _runtimePresence.RuntimeActivityActive
            ? _runtimePresence.RuntimeActivityLabel : "";
        string stageLabel = !string.IsNullOrWhiteSpace(_runtimePresence.StatusLabel)
            ? _runtimePresence.StatusLabel : _conversationStateLabel;
        // Revision đơn điệu: snapshot xóa không thể bị polling/SSE cũ ghi đè trong cùng mili-giây.
        ulong snapshotRevision = Math.Max(Time.GetTicksMsec(), _richChatLastSnapshotMs + 1);
        string next = JsonSerializer.Serialize(new {
            revision = snapshotRevision,
            messages = visible, bundles, status = _conversationStateLabel, online = true,
            speech = new { phase, turnId = speaking ? speechTurnId : "" },
            presence = new {
                stage = _runtimePresence.Stage, label = stageLabel,
                activity, emotion = _runtimePresence.Emotion,
                motion = _runtimePresence.Motion,
                isActive = _runtimePresence.RuntimeActivityActive,
                warning = _runtimePresence.ProgressWarningLevel > 0,
                turnId = _runtimePresence.TurnId,
            },
        });
        Volatile.Write(ref _richChatSnapshot, next);
        _richChatLastSnapshotMs = snapshotRevision;
    }

    private void TickRichChatPortal()
    {
        if (_richChatServer == null) return;
        // Tác vụ xóa lịch sử đứng trước enqueue tin mới để tránh đua với transcript.
        while (_richChatDeleteOutbox.TryDequeue(out RichChatDeleteRequest deletion))
        {
            if (Interlocked.CompareExchange(ref deletion.Lifecycle, 1, 0) != 0) continue;
            string outcome;
            try
            {
                outcome = DeleteRichChatHistoryOnMainThread(deletion.Scope, deletion.Date);
                if (outcome == "ok") UpdateRichChatSnapshot();
            }
            catch (Exception exc)
            {
                GD.PrintErr("[RichChat] Delete transcript failed: " + exc.Message);
                outcome = "error";
            }
            deletion.Completion.TrySetResult(outcome);
        }
        int processed = 0;
        while (processed++ < 5 && _richChatOutbox.TryDequeue(out string message))
            SendUserChatMessage(message);
        // UI requests stay on the Godot main thread; the web server never calls UDP.
        if (_learnerPortraitOutbox.TryDequeue(out var portraitTask))
            SendLearnerPortraitRequest(portraitTask.PathId, portraitTask.Page);
        if (_learnerPortraitPending && Time.GetTicksMsec() - _learnerPortraitSentAtMs > 20000)
        {
            _learnerPortraitPending = false;
            Volatile.Write(ref _learnerPortraitSnapshot,
                "{\"status\":\"error\",\"message\":\"Eris chưa phản hồi sau 20 giây. Hãy thử Làm mới.\"}");
        }
        if (Time.GetTicksMsec() >= _richChatLastSnapshotMs + 500)
            UpdateRichChatSnapshot();
    }

    private void StopRichChatPortal()
    {
        try { _richChatCancellation?.Cancel(); } catch (ObjectDisposedException) { }
        try { _richChatServer?.Stop(); } catch (ObjectDisposedException) { }
        _richChatServer = null;
        _richChatCancellation?.Dispose();
        _richChatCancellation = null;
        _richChatUrl = "";
        ResetWebCanvasPortal();
    }
}
