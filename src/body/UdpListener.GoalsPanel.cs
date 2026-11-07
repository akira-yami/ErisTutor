using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

public partial class UdpListener
{
    // V3: Goals Panel chỉ hiển thị projection; không phải nơi duyệt hay quyết định mục tiêu.
    // Giao thức riêng `parse_mode=autonomy_goals`, không đi qua Speech/TTS parser.
    private PanelContainer _autonomyGoalsPanel;
    private VBoxContainer _autonomyGoalRows;
    private readonly Dictionary<string, int> _autonomyGoalRevisions = new();

    private void EnsureAutonomyGoalsPanel()
    {
        if (_autonomyGoalsPanel != null)
            return;
        var parent = GetNode<Control>("CanvasLayer/Control");
        _autonomyGoalsPanel = new PanelContainer
        {
            Name = "AutonomyGoalsPanel",
            Visible = false,
            Size = new Vector2(410, 390),
            Position = new Vector2(40, 100),
            ZIndex = 1020,
            MouseFilter = Control.MouseFilterEnum.Stop,
        };
        var body = new VBoxContainer();
        var header = new HBoxContainer();
        var title = new Label { Text = "Mục tiêu của Eris", SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
        var close = new Button { Text = "Ẩn" };
        close.Pressed += () =>
        {
            _autonomyGoalsPanel.Visible = false;
            UpdateMousePassthrough();
        };
        header.AddChild(title);
        header.AddChild(close);
        body.AddChild(header);
        var scroll = new ScrollContainer { SizeFlagsVertical = Control.SizeFlags.ExpandFill };
        _autonomyGoalRows = new VBoxContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
        scroll.AddChild(_autonomyGoalRows);
        body.AddChild(scroll);
        _autonomyGoalsPanel.AddChild(body);
        parent.AddChild(_autonomyGoalsPanel);
        MakePanelDraggable(_autonomyGoalsPanel);
    }

    private bool TryHandleAutonomyGoalsPacket(string raw)
    {
        if (string.IsNullOrEmpty(raw) || !raw.Contains("autonomy_goals", StringComparison.Ordinal))
            return false;
        try
        {
            using JsonDocument doc = JsonDocument.Parse(raw);
            JsonElement root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                !root.TryGetProperty("parse_mode", out var pm) || pm.GetString() != "autonomy_goals")
                return false;
            if (!root.TryGetProperty("goals", out var goals) || goals.ValueKind != JsonValueKind.Array)
                return true;
            EnsureAutonomyGoalsPanel();
            // Nếu packet tới trễ với revision cũ, không ghi đè UI bằng trạng thái cũ.
            foreach (var item in goals.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object) continue;
                string id = GoalText(item, "goal_id", 160);
                if (string.IsNullOrEmpty(id)) continue;
                int rev = item.TryGetProperty("revision", out var r) && r.TryGetInt32(out var rv) ? rv : 0;
                if (_autonomyGoalRevisions.TryGetValue(id, out var previous) && rev < previous)
                    return true;
            }
            foreach (Node child in _autonomyGoalRows.GetChildren())
            {
                _autonomyGoalRows.RemoveChild(child);
                child.QueueFree();
            }
            int count = 0;
            foreach (var item in goals.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object || count++ >= 12) break;
                string id = GoalText(item, "goal_id", 160);
                string title = GoalText(item, "title", 210);
                string status = GoalText(item, "status", 40);
                string outcome = GoalText(item, "last_outcome_status", 40);
                string reflection = GoalText(item, "last_reflection", 45);
                int rev = item.TryGetProperty("revision", out var r) && r.TryGetInt32(out var rv) ? rv : 0;
                if (!string.IsNullOrEmpty(id)) _autonomyGoalRevisions[id] = rev;
                _autonomyGoalRows.AddChild(new Label
                {
                    Text = $"• {title}\n  {status} · phiên bản {rev}" +
                        (string.IsNullOrEmpty(outcome) ? "" : $" · kết quả: {outcome}") +
                        (reflection == "workspace_verified" ? " · Đã kiểm tra bàn làm việc" :
                         reflection == "needs_review" ? " · Cần xem lại kết quả" : ""),
                    AutowrapMode = TextServer.AutowrapMode.WordSmart,
                });
            }
            if (root.TryGetProperty("show", out var show) && show.ValueKind == JsonValueKind.True && goals.GetArrayLength() > 0)
                _autonomyGoalsPanel.Visible = true;
            UpdateMousePassthrough();
            return true;
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[autonomy goals] Gói dữ liệu không hợp lệ: {ex.Message}");
            return true;
        }
    }

    private static string GoalText(JsonElement item, string key, int maxLength)
    {
        if (!item.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.String)
            return "";
        string result = value.GetString() ?? "";
        return result.Length > maxLength ? result.Substring(0, maxLength) : result;
    }
}
