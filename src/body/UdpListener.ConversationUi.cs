using Godot;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;

public partial class UdpListener
{
    private sealed class ConversationEntry
    {
        public string Role { get; set; } = "";
        public string Text { get; set; } = "";
        public string Date { get; set; } = "";
        public string Time { get; set; } = "";
        public string StructuredDocumentJson { get; set; } = "";
        public string TurnId { get; set; } = "";
    }

    // Schema cũ chỉ lưu một ngày. Giữ lại để tự động di chuyển dữ liệu hiện có.
    private sealed class ConversationDayStore
    {
        public string Date { get; set; } = "";
        public List<ConversationEntry> Entries { get; set; } = new();
    }

    private sealed class ConversationHistoryStore
    {
        public int Version { get; set; } = 2;
        public List<ConversationEntry> Entries { get; set; } = new();
    }

    private const string ConversationHistoryStatePath = "user://eris_conversation_history.json";
    private const string ConversationLegacyTodayStatePath = "user://eris_conversation_today.json";
    private const int ConversationHistoryLimit = 400;
    private static readonly Vector2 ConversationHistoryPreferredSize = new(1380f, 820f);
    private static readonly Vector2 ConversationHistoryCompactSize = new(620f, 480f);

    // Hồng chỉ giữ vai trò điểm nhấn; phần còn lại dùng nền trung tính để lịch sử dễ đọc hơn.
    private static readonly Color ErisAccent = new("ed408e");
    private static readonly Color ErisSurface = new("fdfcfe");
    private static readonly Color ErisCard = new(1.00f, 1.00f, 1.00f, 0.985f);
    private static readonly Color ErisUserCard = new(1.00f, 0.925f, 0.963f, 0.995f);
    private static readonly Color ErisText = new("302c46");
    private static readonly Color ErisMuted = new("797288");
    private static readonly Color ErisBorder = new("eeeaf2");

    private readonly List<ConversationEntry> _conversationEntries = new();
    private string _conversationIncomingTurnId = "";
    private Panel _conversationHistoryPanel;
    private Label _conversationHeaderDate;
    private PanelContainer _conversationDaySidebar;
    private ScrollContainer _conversationDayScroll;
    private VBoxContainer _conversationDayList;
    private Label _conversationDayCountLabel;
    private string _conversationSelectedDate = "";
    private bool _conversationDateSelectionInitialized;
    private ScrollContainer _conversationScroll;
    private VBoxContainer _conversationMessageList;
    private PanelContainer _conversationEmptyState;
    private Control _conversationScrollDecorationLayer;
    private Texture2D _conversationScrollCapTexture;
    private Texture2D _conversationScrollTrackTexture;
    private Texture2D _conversationScrollFillTexture;
    private TextureRect _conversationScrollCapRect;
    private TextureRect _conversationScrollTrackRect;
    private TextureRect _conversationScrollFillRect;
    private PanelContainer _conversationThinkingRow;
    private Label _conversationThinkingLabel;
    private Button _conversationDeleteButton;
    private Button _conversationHistoryCloseButton;
    private bool _conversationDeleteArmed;
    private ulong _conversationDeleteArmedAtMs;
    private bool _conversationHistoryUserPositioned;
    private ulong _conversationLastDotTickMs;
    private ulong _conversationStateChangedAtMs;
    private int _conversationDotPhase;
    private string _conversationState = "idle";
    private string _conversationStateLabel = "Sẵn sàng";
    private string _lastAssistantConversationText = "";
    private ulong _lastAssistantConversationAtMs;
    private string _conversationStoreDate = "";
    private Vector2 _conversationLastViewportSize = Vector2.Zero;

    private void BuildConversationUi()
    {
        if (_conversationHistoryPanel != null)
            return;

        LoadConversationHistory();
        _conversationHistoryPanel = new Panel
        {
            Name = "ConversationHistoryPanel",
            Visible = false,
            CustomMinimumSize = Vector2.Zero,
            Size = ConversationHistoryPreferredSize,
            ZIndex = 1010,
            ClipContents = true,
        };
        GetNode<Control>("CanvasLayer/Control").AddChild(_conversationHistoryPanel);
        _conversationHistoryPanel.Theme = new Theme { DefaultFont = WorkspaceFont(), DefaultFontSize = 13 };
        BuildConversationPanelTree();
        RebuildConversationMessages();
        RefreshConversationHeaderDate();
        RefreshConversationPresence();
        UpdateConversationUiLayout(force: true);
    }

    private void OnContextConversationHistoryPressed()
    {
        if (_conversationHistoryPanel == null)
            return;
        _bodyAutonomy?.RecordUserInteraction();
        CloseCharacterContextMenu();
        // Windows: use Rich Chat as the main conversation surface.
        // Keep the Godot panel for planner/reminders and non-Windows builds.
        if (OS.GetName() == "Windows" && !_companionPreview)
        {
            OpenRichChatPortal();
            return;
        }
        if (_conversationHistoryPanel.Visible)
            CloseConversationHistoryPanel();
        else
            ShowConversationHistoryPanel();
    }

    private void ShowConversationHistoryPanel()
    {
        if (_conversationHistoryPanel == null)
            return;

        EnsureConversationDateCurrent();
        CloseCharacterContextMenu();
        _conversationHistoryPanel.Visible = true;
        _chatPanel.Visible = false;
        _conversationHistoryPanel.ZIndex = 1010;
        _conversationHistoryPanel.MoveToFront();
        UpdateConversationUiLayout(force: true);
        RefreshConversationPresenceFromRuntime();
        RefreshConversationHeaderDate();
        RefreshConversationEmptyState();
        UpdateMousePassthrough();
        CallDeferred(nameof(ScrollConversationToBottom));
    }

    private void CloseConversationHistoryPanel()
    {
        if (_conversationHistoryPanel == null)
            return;
        if (_draggingPanel == _conversationHistoryPanel)
            _draggingPanel = null;
        _conversationHistoryPanel.Visible = false;
        _plannerEditor.Visible = false;
        // A visible history window does not own the runtime turn. Drafts survive closing.
        ResetConversationDeleteConfirmation();
        UpdateMousePassthrough();
    }

    private void BuildConversationPanelTree()
    {
        BuildCompanionWorkspace();
    }



    private void StyleConversationDeleteButton(bool armed)
    {
        if (_conversationDeleteButton == null)
            return;

        Color background = armed
            ? new Color(0.72f, 0.10f, 0.20f, 0.98f)
            : new Color(1f, 1f, 1f, 0f);
        Color border = armed
            ? new Color(0.86f, 0.28f, 0.38f, 0.96f)
            : new Color(1f, 1f, 1f, 0f);
        Color text = armed ? Colors.White : ErisMuted;

        StyleBoxFlat normal = MakeFlatStyle(background, border, 9, armed ? 1 : 0, 7f);
        StyleBoxFlat hover = (StyleBoxFlat)normal.Duplicate();
        hover.BgColor = armed
            ? new Color(0.80f, 0.14f, 0.25f, 0.99f)
            : new Color(1.00f, 0.93f, 0.965f, 0.96f);
        hover.BorderColor = armed ? border : new Color(0.94f, 0.78f, 0.86f, 0.52f);
        hover.BorderWidthLeft = hover.BorderWidthTop = hover.BorderWidthRight = hover.BorderWidthBottom = 1;

        StyleBoxFlat pressed = (StyleBoxFlat)hover.Duplicate();
        pressed.BgColor = armed
            ? new Color(0.62f, 0.06f, 0.16f, 0.99f)
            : new Color(0.99f, 0.88f, 0.94f, 0.99f);

        _conversationDeleteButton.AddThemeStyleboxOverride("normal", normal);
        _conversationDeleteButton.AddThemeStyleboxOverride("hover", hover);
        _conversationDeleteButton.AddThemeStyleboxOverride("pressed", pressed);
        _conversationDeleteButton.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());
        _conversationDeleteButton.AddThemeStyleboxOverride("disabled", new StyleBoxEmpty());
        _conversationDeleteButton.AddThemeColorOverride("font_color", text);
        _conversationDeleteButton.AddThemeColorOverride("font_hover_color", armed ? Colors.White : ErisAccent);
        _conversationDeleteButton.AddThemeColorOverride("font_pressed_color", armed ? Colors.White : ErisText);
        _conversationDeleteButton.AddThemeColorOverride("font_disabled_color", new Color(0.66f, 0.58f, 0.62f, 0.48f));
        _conversationDeleteButton.AddThemeFontSizeOverride("font_size", 11);
    }

    private void OnDeleteConversationHistoryPressed()
    {
        if (_conversationEntries.Count == 0)
        {
            ResetConversationDeleteConfirmation();
            return;
        }

        ulong now = Time.GetTicksMsec();
        if (!_conversationDeleteArmed || now - _conversationDeleteArmedAtMs > 4000)
        {
            _conversationDeleteArmed = true;
            _conversationDeleteArmedAtMs = now;
            _conversationDeleteButton.Text = "Xoá hết?";
            _conversationDeleteButton.TooltipText = "Nhấn lần nữa trong 4 giây để xác nhận";
            StyleConversationDeleteButton(armed: true);
            return;
        }

        ClearConversationHistory();
    }

    private void ResetConversationDeleteConfirmation()
    {
        _conversationDeleteArmed = false;
        _conversationDeleteArmedAtMs = 0;
        if (_conversationDeleteButton == null)
            return;
        _conversationDeleteButton.Text = "Xóa lịch sử trên máy";
        _conversationDeleteButton.TooltipText = "Xoá toàn bộ lịch sử hội thoại trên máy này";
        StyleConversationDeleteButton(armed: false);
    }

    private void ClearConversationHistory()
    {
        _conversationEntries.Clear();
        _workspacePendingDocuments.Clear();
        _workspaceSeenDocuments.Clear();
        _lastAssistantConversationText = "";
        _lastAssistantConversationAtMs = 0;
        _conversationStoreDate = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        _conversationSelectedDate = "";
        _conversationDateSelectionInitialized = false;
        if (!_companionPreview)
        {
            DeleteConversationStoreFile(ConversationHistoryStatePath);
            DeleteConversationStoreFile(ConversationLegacyTodayStatePath);
        }
        ResetConversationDeleteConfirmation();
        RebuildConversationMessages();
        RefreshConversationHeaderDate();
        RefreshConversationEmptyState();
    }

    private static void DeleteConversationStoreFile(string resourcePath)
    {
        try
        {
            string path = ProjectSettings.GlobalizePath(resourcePath);
            if (System.IO.File.Exists(path))
                System.IO.File.Delete(path);
            string tempPath = path + ".tmp";
            if (System.IO.File.Exists(tempPath))
                System.IO.File.Delete(tempPath);
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[ConversationUi] Không xoá được lịch sử: {ex.Message}");
        }
    }

    private Control BuildConversationBody()
    {
        var body = new VBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        body.AddThemeConstantOverride("separation", 0);
        body.AddChild(MakeSeparator());

        var contentRow = new HBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        contentRow.AddThemeConstantOverride("separation", 0);
        body.AddChild(contentRow);

        var conversationArea = new VBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        conversationArea.AddThemeConstantOverride("separation", 0);
        contentRow.AddChild(conversationArea);

        _conversationScroll = new ScrollContainer
        {
            Name = "ConversationHistoryScroll",
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            VerticalScrollMode = ScrollContainer.ScrollMode.Auto,
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Stop,
            ClipContents = true,
        };
        _conversationScroll.AddThemeConstantOverride("v_scrollbar_separation", 0);
        conversationArea.AddChild(_conversationScroll);

        var scrollMargin = new MarginContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        scrollMargin.AddThemeConstantOverride("margin_left", 20);
        scrollMargin.AddThemeConstantOverride("margin_right", 18);
        scrollMargin.AddThemeConstantOverride("margin_top", 18);
        scrollMargin.AddThemeConstantOverride("margin_bottom", 18);
        _conversationScroll.AddChild(scrollMargin);

        _conversationMessageList = new VBoxContainer
        {
            Name = "ConversationHistoryMessageList",
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        _conversationMessageList.AddThemeConstantOverride("separation", 14);
        scrollMargin.AddChild(_conversationMessageList);

        _conversationEmptyState = BuildConversationEmptyState();
        _conversationMessageList.AddChild(_conversationEmptyState);

        _conversationThinkingRow = BuildThinkingRow();
        _conversationMessageList.AddChild(_conversationThinkingRow);

        VScrollBar bar = _conversationScroll.GetVScrollBar();
        ApplyConversationScrollBarTheme(bar);
        if (bar != null)
            bar.ValueChanged += _ => UpdateConversationScrollDecor();
        _conversationScroll.Resized += UpdateConversationScrollDecor;
        return body;
    }

    private PanelContainer BuildConversationDaySidebar()
    {
        _conversationDaySidebar = new PanelContainer
        {
            Name = "ConversationHistoryDaySidebar",
            CustomMinimumSize = new Vector2(252, 0),
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        };
        var style = MakeFlatStyle(new Color("faf8fc"), ErisBorder, 0, 0, 0);
        style.BorderWidthRight = 1;
        _conversationDaySidebar.AddThemeStyleboxOverride("panel", style);
        var margin = WorkspaceMargin(16, 20, 16, 18);
        _conversationDaySidebar.AddChild(margin);
        var root = WorkspaceColumn();
        root.AddThemeConstantOverride("separation", 12);
        margin.AddChild(root);
        var heading = ConversationLabel("Hội thoại", 17, ErisText);
        heading.AddThemeFontOverride("font", WorkspaceFont(true));
        root.AddChild(heading);
        _conversationDayCountLabel = ConversationLabel("Những câu chuyện của mình", 11, ErisMuted);
        root.AddChild(_conversationDayCountLabel);
        _workspaceHistorySearch = new LineEdit { PlaceholderText = "Tìm trong hội thoại…", ClearButtonEnabled = true };
        StyleWorkspaceInput(_workspaceHistorySearch);
        root.AddChild(_workspaceHistorySearch);
        _workspaceHistorySearch.TextChanged += text =>
        {
            _conversationSearchQuery = text.Trim();
            _conversationSelectedDate = "";
            _conversationDateSelectionInitialized = true;
            RebuildConversationMessages();
            RefreshConversationHeaderDate();
        };
        root.AddChild(WorkspaceButton("Trở về hôm nay", "history", () =>
        {
            _conversationSearchQuery = "";
            _workspaceHistorySearch.Text = "";
            SelectConversationDate(DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
            (_workspaceChatInput ?? _chatInput)?.GrabFocus();
        }, primary: true));
        root.AddChild(ConversationLabel("LỊCH SỬ THEO NGÀY", 10, ErisMuted));
        _conversationDayScroll = new ScrollContainer
        {
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        };
        root.AddChild(_conversationDayScroll);
        _conversationDayList = WorkspaceColumn();
        _conversationDayList.SizeFlagsVertical = Control.SizeFlags.ShrinkBegin;
        _conversationDayList.AddThemeConstantOverride("separation", 6);
        _conversationDayScroll.AddChild(_conversationDayList);
        ApplyConversationScrollBarTheme(_conversationDayScroll.GetVScrollBar(), compact: true);
        root.AddChild(MakeSeparator());
        root.AddChild(WorkspaceWrap("Một nơi để tiếp nối\nnhững điều còn dang dở.", 11, ErisMuted));
        _conversationDeleteButton = WorkspaceButton("Xóa lịch sử trên máy", "trash", OnDeleteConversationHistoryPressed);
        _conversationDeleteButton.Name = "ConversationHistoryDeleteButton";
        StyleConversationDeleteButton(false);
        root.AddChild(_conversationDeleteButton);
        return _conversationDaySidebar;
    }

    private void RebuildConversationDayList()
    {
        if (_conversationDayList == null)
            return;

        foreach (Node child in _conversationDayList.GetChildren())
        {
            _conversationDayList.RemoveChild(child);
            child.QueueFree();
        }

        List<IGrouping<string, ConversationEntry>> groups = _conversationEntries
            .Where(ConversationSearchMatches)
            .GroupBy(entry => NormalizeConversationDate(entry.Date, _conversationStoreDate), StringComparer.Ordinal)
            .OrderByDescending(group => group.Key, StringComparer.Ordinal)
            .ToList();

        EnsureConversationSelectedDate(_conversationEntries.Select(entry => NormalizeConversationDate(entry.Date, _conversationStoreDate)).Distinct().OrderByDescending(date => date).ToList());
        if (_conversationDayCountLabel != null)
        {
            _conversationDayCountLabel.Text = !string.IsNullOrWhiteSpace(_conversationSearchQuery)
                ? $"{_conversationEntries.Count(ConversationSearchMatches)} kết quả · {groups.Count} ngày"
                : groups.Count == 0
                ? "Chưa có dữ liệu"
                : groups.Count == 1
                    ? $"1 ngày · {_conversationEntries.Count} tin nhắn"
                    : $"{groups.Count} ngày · {_conversationEntries.Count} tin nhắn";
        }

        _conversationDayList.AddChild(BuildConversationDayButton(
            "",
            "Tất cả",
            groups.Count == 0 ? "Chưa có dữ liệu" : $"{groups.Count} ngày",
            _conversationEntries.Count(ConversationSearchMatches),
            string.IsNullOrWhiteSpace(_conversationSelectedDate)));

        foreach (IGrouping<string, ConversationEntry> group in groups)
        {
            string date = group.Key;
            int count = group.Count();
            string preview = group.Last().Text.Replace("\n", " ");
            _conversationDayList.AddChild(BuildConversationDayButton(
                date,
                ConversationDaySidebarTitle(date),
                preview,
                count,
                string.Equals(_conversationSelectedDate, date, StringComparison.Ordinal)));
        }
    }

    private Button BuildConversationDayButton(
        string date,
        string title,
        string subtitle,
        int count,
        bool selected)
    {
        var button = new Button
        {
            Text = "",
            CustomMinimumSize = new Vector2(0f, 68f),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            FocusMode = Control.FocusModeEnum.None,
            MouseFilter = Control.MouseFilterEnum.Stop,
            MouseDefaultCursorShape = Control.CursorShape.PointingHand,
            TooltipText = string.IsNullOrWhiteSpace(date)
                ? "Hiện toàn bộ lịch sử hội thoại"
                : $"Xem hội thoại ngày {DisplayConversationDate(date)}",
        };

        StyleBoxFlat normal = MakeFlatStyle(
            selected ? new Color(1.00f, 0.925f, 0.963f, 0.98f) : new Color(1f, 1f, 1f, 0f),
            selected ? ErisAccent : new Color(1f, 1f, 1f, 0f),
            9,
            0,
            0f);
        normal.BorderWidthLeft = selected ? 3 : 0;

        StyleBoxFlat hover = (StyleBoxFlat)normal.Duplicate();
        hover.BgColor = selected
            ? new Color(1.00f, 0.90f, 0.95f, 0.99f)
            : new Color(1.00f, 0.955f, 0.978f, 0.96f);
        StyleBoxFlat pressed = (StyleBoxFlat)normal.Duplicate();
        pressed.BgColor = new Color(0.99f, 0.88f, 0.94f, 0.99f);

        button.AddThemeStyleboxOverride("normal", normal);
        button.AddThemeStyleboxOverride("hover", hover);
        button.AddThemeStyleboxOverride("pressed", pressed);
        button.AddThemeStyleboxOverride("focus", new StyleBoxEmpty());

        var contentMargin = new MarginContainer
        {
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        contentMargin.SetAnchorsPreset(Control.LayoutPreset.FullRect);
        contentMargin.OffsetLeft = 11f;
        contentMargin.OffsetTop = 7f;
        contentMargin.OffsetRight = -9f;
        contentMargin.OffsetBottom = -7f;
        button.AddChild(contentMargin);

        var row = new HBoxContainer { MouseFilter = Control.MouseFilterEnum.Ignore };
        row.AddThemeConstantOverride("separation", 6);
        contentMargin.AddChild(row);

        var labels = new VBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        labels.AddThemeConstantOverride("separation", 0);
        row.AddChild(labels);

        Label titleLabel = ConversationLabel(title, 13, selected ? ErisText : new Color(0.39f, 0.25f, 0.31f, 1f));
        titleLabel.ClipText = true;
        titleLabel.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        labels.AddChild(titleLabel);

        Label subtitleLabel = ConversationLabel(subtitle, 11, ErisMuted);
        subtitleLabel.ClipText = true;
        subtitleLabel.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        labels.AddChild(subtitleLabel);

        Label countLabel = ConversationLabel(count.ToString(CultureInfo.InvariantCulture), 11, selected ? ErisAccent : ErisMuted);
        countLabel.CustomMinimumSize = new Vector2(24f, 0f);
        countLabel.HorizontalAlignment = HorizontalAlignment.Right;
        countLabel.VerticalAlignment = VerticalAlignment.Center;
        row.AddChild(countLabel);

        button.Pressed += () => SelectConversationDate(date);
        return button;
    }

    private void EnsureConversationSelectedDate(IReadOnlyList<string> datesDescending)
    {
        if (datesDescending == null || datesDescending.Count == 0)
        {
            _conversationSelectedDate = "";
            _conversationDateSelectionInitialized = false;
            return;
        }

        if (!_conversationDateSelectionInitialized)
        {
            string today = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            _conversationSelectedDate = datesDescending.Contains(today, StringComparer.Ordinal)
                ? today
                : datesDescending[0];
            _conversationDateSelectionInitialized = true;
            return;
        }

        if (!string.IsNullOrWhiteSpace(_conversationSelectedDate)
            && _conversationSelectedDate != DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
            && !datesDescending.Contains(_conversationSelectedDate, StringComparer.Ordinal))
            _conversationSelectedDate = datesDescending[0];
    }

    private void SelectConversationDate(string date)
    {
        _conversationDateSelectionInitialized = true;
        _conversationSelectedDate = string.IsNullOrWhiteSpace(date)
            ? ""
            : NormalizeConversationDate(date, _conversationStoreDate);
        _workspaceShowHistory = false;
        UpdateCompanionColumns();
        RebuildConversationDayList();
        RebuildConversationMessages(rebuildDayList: false);
        RefreshConversationHeaderDate();
        RefreshConversationPresence();
        CallDeferred(nameof(ScrollConversationToTop));
    }

    private static string ConversationDaySidebarTitle(string date)
    {
        string normalized = NormalizeConversationDate(date, DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        if (!DateTime.TryParseExact(normalized, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out DateTime parsed))
            return DisplayConversationDate(normalized);

        if (parsed.Date == DateTime.Today)
            return "Hôm nay";
        if (parsed.Date == DateTime.Today.AddDays(-1))
            return "Hôm qua";
        return parsed.ToString("dddd", new CultureInfo("vi-VN"));
    }

    private void BuildConversationScrollDecoration()
    {
        if (_conversationHistoryPanel == null || _conversationScrollDecorationLayer != null)
            return;

        _conversationScrollCapTexture ??= GD.Load<Texture2D>("res://asset/Resource/topcap.png");
        _conversationScrollTrackTexture ??= GD.Load<Texture2D>("res://asset/Resource/scrolltrack.png");
        _conversationScrollFillTexture ??= GD.Load<Texture2D>("res://asset/Resource/scrollfill.png");

        _conversationScrollDecorationLayer = new Control
        {
            Name = "ConversationHistoryScrollDecoration",
            MouseFilter = Control.MouseFilterEnum.Ignore,
            ZIndex = 2,
        };
        _conversationScrollDecorationLayer.SetAnchorsPreset(Control.LayoutPreset.FullRect);
        _conversationHistoryPanel.AddChild(_conversationScrollDecorationLayer);

        _conversationScrollTrackRect = CreateDecorTextureRect(_conversationScrollTrackTexture, new Vector2(14f, 220f));
        _conversationScrollFillRect = CreateDecorTextureRect(_conversationScrollFillTexture, new Vector2(14f, 72f));
        _conversationScrollCapRect = CreateDecorTextureRect(_conversationScrollCapTexture, new Vector2(28f, 28f));
        _conversationScrollDecorationLayer.AddChild(_conversationScrollTrackRect);
        _conversationScrollDecorationLayer.AddChild(_conversationScrollFillRect);
        _conversationScrollDecorationLayer.AddChild(_conversationScrollCapRect);
        Callable.From(UpdateConversationScrollDecor).CallDeferred();
    }

    private void UpdateConversationScrollDecor()
    {
        if (_conversationScroll == null || _conversationHistoryPanel == null)
            return;

        VScrollBar scrollBar = _conversationScroll.GetVScrollBar();
        if (scrollBar == null)
            return;

        Vector2 panelOrigin = _conversationHistoryPanel.GlobalPosition;
        Vector2 scPos = _conversationScroll.GlobalPosition - panelOrigin;
        Vector2 scSize = _conversationScroll.Size;

        float railWidth = 12f;
        float trackX = scPos.X + scSize.X - railWidth - 7f;
        float trackTop = scPos.Y + 8f;
        float trackHeight = Mathf.Max(52f, scSize.Y - 16f);
        bool canScroll = scrollBar.MaxValue > scrollBar.Page + 0.5;

        if (_conversationScrollTrackRect != null)
        {
            _conversationScrollTrackRect.Position = new Vector2(trackX, trackTop);
            _conversationScrollTrackRect.Size = new Vector2(railWidth, trackHeight);
            _conversationScrollTrackRect.Visible = canScroll;
        }

        float thumbMinHeight = 54f;
        float thumbMaxHeight = Mathf.Min(trackHeight, 150f);
        float thumbHeight = thumbMinHeight;
        if (scrollBar.MaxValue > 0.001 && scrollBar.Page > 0)
        {
            float pageRatio = Mathf.Clamp((float)(scrollBar.Page / scrollBar.MaxValue), 0.12f, 1f);
            thumbHeight = Mathf.Clamp(trackHeight * pageRatio, thumbMinHeight, thumbMaxHeight);
        }

        float scrollRange = Mathf.Max(0f, (float)(scrollBar.MaxValue - scrollBar.Page));
        float scrollRatio = scrollRange > 0.001f
            ? Mathf.Clamp((float)(scrollBar.Value / scrollRange), 0f, 1f)
            : 0f;
        float thumbY = trackTop + scrollRatio * Mathf.Max(0f, trackHeight - thumbHeight);

        if (_conversationScrollFillRect != null)
        {
            _conversationScrollFillRect.Position = new Vector2(trackX, thumbY);
            _conversationScrollFillRect.Size = new Vector2(railWidth, thumbHeight);
            _conversationScrollFillRect.Visible = canScroll;
        }

        if (_conversationScrollCapRect != null)
        {
            float capWidth = railWidth + 14f;
            float capHeight = capWidth;
            if (_conversationScrollCapTexture != null)
            {
                Vector2 textureSize = _conversationScrollCapTexture.GetSize();
                if (textureSize.X > 0f && textureSize.Y > 0f)
                    capHeight = capWidth * textureSize.Y / textureSize.X;
            }

            _conversationScrollCapRect.Position = new Vector2(
                trackX + (railWidth - capWidth) * 0.5f,
                thumbY - capHeight * 0.40f);
            _conversationScrollCapRect.Size = new Vector2(capWidth, capHeight);
            _conversationScrollCapRect.Visible = canScroll;
        }
    }

    private PanelContainer BuildConversationEmptyState()
    {
        var panel = new PanelContainer
        {
            Name = "ConversationHistoryEmptyState",
            CustomMinimumSize = new Vector2(0, 260),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
        };
        panel.AddThemeStyleboxOverride("panel", new StyleBoxEmpty());
        var box = WorkspaceColumn();
        box.Alignment = BoxContainer.AlignmentMode.Center;
        box.AddThemeConstantOverride("separation", 12);
        panel.AddChild(box);
        var avatar = WorkspaceAvatar(72);
        avatar.SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter;
        box.AddChild(avatar);
        var title = WorkspaceWrap("Hôm nay của bạn thế nào?", 22, ErisText);
        title.Name = "EmptyTitle";
        title.HorizontalAlignment = HorizontalAlignment.Center;
        title.AddThemeFontOverride("font", WorkspaceFont(true));
        box.AddChild(title);
        var hint = WorkspaceWrap("Kể Eris nghe một câu chuyện, một ý tưởng,\nhoặc cùng dành chút thời gian học điều mới.", 13, ErisMuted);
        hint.Name = "EmptyHint";
        hint.HorizontalAlignment = HorizontalAlignment.Center;
        box.AddChild(hint);
        var start = WorkspaceButton("Cùng học một chút", "arrow", () => StartLearningWithEris());
        start.Name = "EmptyStart";
        start.SizeFlagsHorizontal = Control.SizeFlags.ShrinkCenter;
        box.AddChild(start);
        return panel;
    }

    private PanelContainer BuildThinkingRow()
    {
        var row = new PanelContainer
        {
            Name = "ThinkingRow",
            Visible = false,
            CustomMinimumSize = new Vector2(220f, 40f),
            SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        row.AddThemeStyleboxOverride("panel", MakeFlatStyle(
            new Color(1.00f, 0.94f, 0.97f, 0.96f),
            new Color(1f, 1f, 1f, 0f),
            11,
            0,
            11f));
        _conversationThinkingLabel = ConversationLabel("Eris đang suy nghĩ...", 13, ErisMuted);
        _conversationThinkingLabel.VerticalAlignment = VerticalAlignment.Center;
        row.AddChild(_conversationThinkingLabel);
        return row;
    }

    private void UpdateConversationUiLayout(bool force = false)
    {
        if (_conversationHistoryPanel == null)
            return;

        Vector2 viewport = GetViewportRect().Size;
        if (!force && viewport.DistanceTo(_conversationLastViewportSize) < 1f)
            return;
        _conversationLastViewportSize = viewport;

        float availableWidth = Mathf.Max(1f, viewport.X - 24f);
        float availableHeight = Mathf.Max(1f, viewport.Y - 24f);
        float width = Mathf.Min(ConversationHistoryPreferredSize.X, availableWidth);
        float height = Mathf.Min(ConversationHistoryPreferredSize.Y, availableHeight);
        if (availableWidth >= ConversationHistoryCompactSize.X)
            width = Mathf.Max(width, ConversationHistoryCompactSize.X);
        if (availableHeight >= ConversationHistoryCompactSize.Y)
            height = Mathf.Max(height, ConversationHistoryCompactSize.Y);

        Vector2 target = new(width, height);
        _conversationHistoryPanel.CustomMinimumSize = Vector2.Zero;
        _conversationHistoryPanel.Size = target;

        if (_conversationHistoryUserPositioned)
            ClampConversationHistoryPanelToScreen();
        else
            _conversationHistoryPanel.Position = new Vector2(
                Mathf.Max(0f, (viewport.X - target.X) * 0.5f),
                Mathf.Max(0f, (viewport.Y - target.Y) * 0.5f));

        if (_conversationHeaderDate != null)
            _conversationHeaderDate.Visible = target.X >= 560f;
        UpdateCompanionColumns();
        Callable.From(UpdateConversationScrollDecor).CallDeferred();
    }

    private void ClampConversationHistoryPanelToScreen()
    {
        if (_conversationHistoryPanel == null)
            return;
        Vector2 screen = GetViewportRect().Size;
        Vector2 pos = _conversationHistoryPanel.Position;
        pos.X = Mathf.Clamp(pos.X, 0f, Mathf.Max(0f, screen.X - _conversationHistoryPanel.Size.X));
        pos.Y = Mathf.Clamp(pos.Y, 0f, Mathf.Max(0f, screen.Y - _conversationHistoryPanel.Size.Y));
        _conversationHistoryPanel.Position = pos;
    }

    private void RecordConversationUserMessage(string text)
    {
        _conversationSelectedDate = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        _conversationDateSelectionInitialized = true;
        _conversationSearchQuery = "";
        if (_workspaceHistorySearch != null) _workspaceHistorySearch.Text = "";
        ConversationAppendMessage("user", text);
        ConversationSetState("thinking", "Eris đang suy nghĩ...");
    }

    private void RecordConversationAssistantMessage(string text)
    {
        if ((text ?? "").Trim().StartsWith("[Chủ nhân]:", StringComparison.OrdinalIgnoreCase))
            return;
        string cleaned = NormalizeConversationText(text);
        if (string.IsNullOrWhiteSpace(cleaned))
            return;
        ulong now = Time.GetTicksMsec();
        if (string.Equals(cleaned, _lastAssistantConversationText, StringComparison.Ordinal)
            && _lastAssistantConversationAtMs > 0
            && now - _lastAssistantConversationAtMs < 5000)
            return;
        _lastAssistantConversationText = cleaned;
        _lastAssistantConversationAtMs = now;
        ConversationAppendMessage("assistant", cleaned);
    }

    private void ConversationAppendMessage(string role, string text)
    {
        EnsureConversationDateCurrent();
        string cleaned = NormalizeConversationText(text);
        if (string.IsNullOrWhiteSpace(cleaned))
            return;

        DateTime now = DateTime.Now;
        var entry = new ConversationEntry
        {
            Role = role == "user" ? "user" : "assistant",
            Text = cleaned,
            Date = now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            Time = now.ToString("HH:mm", CultureInfo.InvariantCulture),
            TurnId = role == "assistant" ? _conversationIncomingTurnId : "",
        };
        TryAttachPendingWorkspaceDocument(entry);
        TryAttachPendingLiveWorkspace(entry);
        _conversationEntries.Add(entry);
        while (_conversationEntries.Count > ConversationHistoryLimit)
            _conversationEntries.RemoveAt(0);

        SaveConversationHistory();
        RebuildConversationMessages();
        RefreshConversationEmptyState();
        RefreshConversationHeaderDate();
        // UDP có thể gửi document trước bubble: vẫn tự mở panel cho lượt mới
        // sau khi entry đã được commit, không mở vì gói nội dung chưa có lời thoại.
        if (entry.Role == "assistant" && entry.Text.Length > 480 &&
            !string.IsNullOrWhiteSpace(entry.StructuredDocumentJson) &&
            _conversationHistoryPanel != null && !_conversationHistoryPanel.Visible)
            ShowConversationHistoryPanel();
        if ((_conversationHistoryPanel?.Visible ?? false) && ConversationShowsLiveDay())
            CallDeferred(nameof(ScrollConversationToBottom));
    }

    private Control BuildConversationMessageRow(ConversationEntry entry)
    {
        bool user = string.Equals(entry.Role, "user", StringComparison.Ordinal);
        var row = new HBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        row.AddThemeConstantOverride("separation", 12);

        var spacer = new Control
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };

        var cluster = new VBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        cluster.AddThemeConstantOverride("separation", 4);

        Label meta = ConversationLabel(user ? $"Bạn · {entry.Time}" : $"Eris · {entry.Time}", 11,
            user ? new Color(0.61f, 0.24f, 0.42f, 1f) : ErisAccent);
        meta.HorizontalAlignment = user ? HorizontalAlignment.Right : HorizontalAlignment.Left;
        var metaRow = new HBoxContainer();
        meta.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
        metaRow.AddChild(meta);
        var copy = WorkspaceButton("", "copy", () => DisplayServer.ClipboardSet(entry.Text));
        copy.CustomMinimumSize = new Vector2(24, 24);
        copy.TooltipText = "Sao chép tin nhắn";
        copy.AddThemeStyleboxOverride("normal", new StyleBoxEmpty());
        copy.AddThemeStyleboxOverride("hover", MakeFlatStyle(new Color("f8edf5"), Colors.Transparent, 6, 0, 0));
        metaRow.AddChild(copy);
        cluster.AddChild(metaRow);

        var bubble = new PanelContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        bubble.AddThemeStyleboxOverride("panel", MakeFlatStyle(
            user ? ErisUserCard : ErisCard,
            user ? new Color(1f, 1f, 1f, 0f) : new Color(0.94f, 0.86f, 0.90f, 0.90f),
            11,
            user ? 0 : 1,
            12f));

        // Nếu đã có tài liệu V5, hiển thị trực tiếp Markdown/toán/code tại đây.
        // Không động tới text nguyên bản trong lịch sử và TTS.
        if (!user && !string.IsNullOrWhiteSpace(entry.StructuredDocumentJson))
        {
            bubble.AddChild(RenderStructuredConversationDocument(entry.StructuredDocumentJson, entry.Text));
            cluster.AddChild(bubble);
            row.AddChild(cluster);
            row.AddChild(spacer);
            return row;
        }
        // Dữ liệu đời cũ: giữ fallback thu gọn, không xóa lịch sử.
        bool longReply = !user && entry.Text.Length > 450;
        bool openFullFromCompanion = longReply && _expandRecentAssistantFromCompanion
            && string.Equals(entry.Text, NormalizeConversationText(_lastSpeechBubbleText), StringComparison.Ordinal);
        var content = new VBoxContainer();
        string preview = longReply && !openFullFromCompanion
            ? entry.Text.Substring(0, Math.Min(420, entry.Text.Length)).TrimEnd() + "…"
            : entry.Text;
        var message = ConversationLabel(preview, 15, ErisText);
        message.AutowrapMode = TextServer.AutowrapMode.WordSmart;
        message.AddThemeConstantOverride("line_spacing", 5);
        message.CustomMinimumSize = new Vector2(EstimateConversationBubbleWidth(entry.Text), 0f);
        content.AddChild(message);
        if (longReply)
        {
            bool expanded = openFullFromCompanion;
            var toggle = WorkspaceButton(expanded ? "Thu gọn" : "Xem toàn bộ lời giải", "history", () => { });
            toggle.Pressed += () =>
            {
                expanded = !expanded;
                message.Text = expanded ? entry.Text : preview;
                toggle.Text = expanded ? "Thu gọn" : "Xem toàn bộ lời giải";
            };
            content.AddChild(toggle);
        }
        bubble.AddChild(content);
        cluster.AddChild(bubble);

        if (user)
        {
            row.AddChild(spacer);
            row.AddChild(cluster);
        }
        else
        {
            row.AddChild(cluster);
            row.AddChild(spacer);
        }
        return row;
    }

    private void RebuildConversationMessages(bool rebuildDayList = true)
    {
        if (_conversationMessageList == null)
            return;
        if (rebuildDayList)
            RebuildConversationDayList();

        foreach (Node child in _conversationMessageList.GetChildren())
        {
            if (child == _conversationThinkingRow || child == _conversationEmptyState)
                continue;
            _conversationMessageList.RemoveChild(child);
            child.QueueFree();
        }

        string previousDate = "";
        foreach (ConversationEntry entry in ConversationVisibleEntries())
        {
            string entryDate = NormalizeConversationDate(entry.Date, _conversationStoreDate);
            if (!string.Equals(previousDate, entryDate, StringComparison.Ordinal))
            {
                Control dateSeparator = BuildConversationDateSeparator(entryDate);
                _conversationMessageList.AddChild(dateSeparator);
                if (_conversationThinkingRow != null)
                    _conversationMessageList.MoveChild(dateSeparator, _conversationThinkingRow.GetIndex());
                previousDate = entryDate;
            }

            Control row = BuildConversationMessageRow(entry);
            _conversationMessageList.AddChild(row);
            if (_conversationThinkingRow != null)
                _conversationMessageList.MoveChild(row, _conversationThinkingRow.GetIndex());
        }
        RefreshConversationEmptyState();
        RefreshConversationPresence();
        Callable.From(UpdateConversationScrollDecor).CallDeferred();
    }

    private IEnumerable<ConversationEntry> ConversationVisibleEntries()
    {
        if (string.IsNullOrWhiteSpace(_conversationSelectedDate))
            return _conversationEntries.Where(ConversationSearchMatches);

        return _conversationEntries.Where(entry => string.Equals(
            NormalizeConversationDate(entry.Date, _conversationStoreDate),
            _conversationSelectedDate,
            StringComparison.Ordinal) && ConversationSearchMatches(entry));
    }

    private bool ConversationShowsLiveDay()
    {
        return string.IsNullOrWhiteSpace(_conversationSelectedDate)
            || string.Equals(_conversationSelectedDate, DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), StringComparison.Ordinal);
    }

    private Control BuildConversationDateSeparator(string date)
    {
        var row = new HBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        row.AddThemeConstantOverride("separation", 10);

        Color lineColor = new Color(0.94f, 0.86f, 0.90f, 0.90f);
        ColorRect left = new()
        {
            Color = lineColor,
            CustomMinimumSize = new Vector2(0f, 1f),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        row.AddChild(left);

        Label label = ConversationLabel(ConversationDateHeading(date), 11, ErisMuted);
        label.HorizontalAlignment = HorizontalAlignment.Center;
        label.CustomMinimumSize = new Vector2(142f, 26f);
        label.VerticalAlignment = VerticalAlignment.Center;
        row.AddChild(label);

        ColorRect right = new()
        {
            Color = lineColor,
            CustomMinimumSize = new Vector2(0f, 1f),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        row.AddChild(right);
        return row;
    }

    private void RefreshConversationEmptyState()
    {
        bool historyEmpty = _conversationEntries.Count == 0;
        bool visibleEmpty = !ConversationVisibleEntries().Any();
        if (_conversationEmptyState != null)
        {
            _conversationEmptyState.Visible = visibleEmpty;
            var content = _conversationEmptyState.GetChild<VBoxContainer>(0);
            bool searching = !string.IsNullOrWhiteSpace(_conversationSearchQuery);
            content.GetNode<Label>("EmptyTitle").Text = searching ? "Chưa tìm thấy tin nhắn" : "Hôm nay của bạn thế nào?";
            content.GetNode<Label>("EmptyHint").Text = searching
                ? "Thử một từ khóa khác hoặc chọn Tất cả trong lịch sử."
                : "Kể Eris nghe một câu chuyện, một ý tưởng,\nhoặc cùng dành chút thời gian học điều mới.";
            content.GetNode<Button>("EmptyStart").Visible = !searching;
        }
        if (_conversationDeleteButton != null)
            _conversationDeleteButton.Disabled = historyEmpty;
    }

    private void ConversationSetState(string state, string label = "")
    {
        _conversationState = string.IsNullOrWhiteSpace(state) ? "idle" : state.Trim().ToLowerInvariant();
        _conversationStateChangedAtMs = Time.GetTicksMsec();
        _conversationStateLabel = string.IsNullOrWhiteSpace(label) ? ConversationStateLabel(_conversationState) : label.Trim();
        RefreshConversationPresence();
    }

    private void RefreshConversationPresence()
    {
        if (_workspacePresence != null)
            _workspacePresence.Text = _conversationStateLabel;
        bool thinking = _conversationState is "thinking" or "preparing" or "buffering";
        if (_conversationThinkingRow != null)
            _conversationThinkingRow.Visible = thinking && ConversationShowsLiveDay();
        if (_conversationThinkingLabel != null)
            _conversationThinkingLabel.Text = thinking ? _conversationStateLabel : "";

        if (thinking && (_conversationHistoryPanel?.Visible ?? false) && ConversationShowsLiveDay())
            CallDeferred(nameof(ScrollConversationToBottom));
    }

    private void RefreshConversationPresenceFromRuntime()
    {
        if (_runtimePresence.Speaking)
        {
            ConversationSetState("speaking", "Eris đang nói");
            return;
        }
        if (_runtimePresence.Thinking)
        {
            string label = string.IsNullOrWhiteSpace(_runtimePresence.StatusLabel)
                ? "Eris đang suy nghĩ..."
                : PrefixErisStatus(_runtimePresence.StatusLabel);
            string state = _runtimePresence.Stage == "audio_buffering" ? "buffering" : "thinking";
            ConversationSetState(state, label);
            return;
        }

        bool localPending = _conversationState is "thinking" or "preparing" or "buffering";
        if (localPending
            && _conversationStateChangedAtMs > _runtimePresence.LastPacketAtMs
            && Time.GetTicksMsec() - _conversationStateChangedAtMs < 45000)
            return;
        ConversationSetState("idle", "Sẵn sàng");
    }

    private void TickConversationUi()
    {
        TickPersonalPlanner();
        EnsureConversationDateCurrent();
        if (_conversationHistoryPanel?.Visible ?? false)
            UpdateConversationUiLayout();

        ulong now = Time.GetTicksMsec();
        if (_conversationDeleteArmed && now - _conversationDeleteArmedAtMs > 4000)
            ResetConversationDeleteConfirmation();

        bool thinking = _conversationState is "thinking" or "preparing" or "buffering";
        if (!thinking || _conversationThinkingLabel == null)
            return;
        if (!_runtimePresence.Thinking
            && !_runtimePresence.Speaking
            && _conversationStateChangedAtMs > 0
            && now - _conversationStateChangedAtMs > 45000)
        {
            ConversationSetState("idle", "Chưa nhận được phản hồi");
            return;
        }
        if (now - _conversationLastDotTickMs < 360)
            return;
        _conversationLastDotTickMs = now;
        _conversationDotPhase = (_conversationDotPhase + 1) % 4;
        string baseText = string.IsNullOrWhiteSpace(_conversationStateLabel)
            ? (_conversationState == "buffering" ? "Eris đang chuẩn bị giọng nói" : "Eris đang suy nghĩ")
            : _conversationStateLabel.TrimEnd('.', '…');
        _conversationThinkingLabel.Text = baseText + new string('.', _conversationDotPhase + 1);
    }

    private void ScrollConversationToTop()
    {
        if (_conversationScroll == null)
            return;
        VScrollBar bar = _conversationScroll.GetVScrollBar();
        if (bar != null)
            bar.Value = 0;
        UpdateConversationScrollDecor();
    }

    private void ScrollConversationToBottom()
    {
        if (_conversationScroll == null)
            return;
        VScrollBar bar = _conversationScroll.GetVScrollBar();
        if (bar != null)
            bar.Value = bar.MaxValue;
        UpdateConversationScrollDecor();
    }

    private float EstimateConversationBubbleWidth(string text)
    {
        string clean = (text ?? "").Replace("\r", "");
        int longestLine = clean.Split('\n').Select(line => line.Length).DefaultIfEmpty(0).Max();
        float ideal = Mathf.Clamp(34f + longestLine * 7.1f, 92f, 560f);
        if (clean.Length > 84)
            ideal = Mathf.Max(ideal, 320f);

        // Derive from the target layout: old bubble minimum widths must not keep
        // the container wider than a newly narrowed viewport.
        float chatWidth = _conversationHistoryPanel?.Size.X ?? 600f;
        if (_conversationDaySidebar?.Visible == true) chatWidth -= _conversationDaySidebar.CustomMinimumSize.X;
        if (_workspacePlannerRail?.Visible == true) chatWidth -= _workspacePlannerRail.CustomMinimumSize.X;
        float available = Mathf.Clamp(chatWidth - 100f, 80f, 560f);
        return Mathf.Min(ideal, available);
    }

    private void ApplyConversationScrollBarTheme(VScrollBar scrollBar, bool compact = false)
    {
        if (scrollBar == null)
            return;

        float width = compact ? 4f : 6f;
        scrollBar.CustomMinimumSize = new Vector2(width, 0f);
        scrollBar.Modulate = Colors.White;

        StyleBoxFlat track = MakeFlatStyle(
            new Color(0.94f, 0.86f, 0.90f, compact ? 0.18f : 0.28f),
            new Color(1f, 1f, 1f, 0f),
            compact ? 2 : 3,
            0,
            0f);
        StyleBoxFlat grabber = MakeFlatStyle(
            new Color(0.91f, 0.46f, 0.67f, compact ? 0.48f : 0.64f),
            new Color(1f, 1f, 1f, 0f),
            compact ? 2 : 3,
            0,
            0f);
        StyleBoxFlat grabberHover = (StyleBoxFlat)grabber.Duplicate();
        grabberHover.BgColor = new Color(0.91f, 0.36f, 0.61f, 0.82f);

        scrollBar.AddThemeStyleboxOverride("scroll", track);
        scrollBar.AddThemeStyleboxOverride("scroll_focus", track);
        scrollBar.AddThemeStyleboxOverride("grabber", grabber);
        scrollBar.AddThemeStyleboxOverride("grabber_highlight", grabberHover);
        scrollBar.AddThemeStyleboxOverride("grabber_pressed", grabberHover);
    }

    private Control ActiveSpeechBubble()
    {
        if (_isLive2DMode && _live2DSpeechBubble != null)
            return _live2DSpeechBubble;
        return _bubble;
    }

    private RichTextLabel ActiveSpeechLabel()
    {
        if (_isLive2DMode && _live2DSpeechLabel != null)
            return _live2DSpeechLabel;
        return _label;
    }

    private Label ActiveSpeechStatusLabel()
    {
        if (_isLive2DMode && _live2DSpeechStatus != null)
            return _live2DSpeechStatus;
        return null;
    }

    private void SyncSpeechBubbleMode()
    {
        bool shouldRemainVisible = (_bubble?.Visible ?? false) || (_live2DSpeechBubble?.Visible ?? false);
        string fullText = _lastSpeechBubbleText;
        if (string.IsNullOrWhiteSpace(fullText))
        {
            // Only relevant during an early renderer switch, before the first committed utterance.
            fullText = _label?.Text ?? _live2DSpeechLabel?.Text ?? "";
        }
        _speechIsBeingRead = _speechIsLong && !_isLive2DMode;

        if (_isLive2DMode)
        {
            BuildLive2DSpeechBubble();
            SetLive2DSpeechPreview(fullText, _ttsAudioIsPlaying ? _currentSpeechSentenceIndex : -1);
            if (_live2DSpeechStatus != null)
                _live2DSpeechStatus.Text = _ttsAudioIsPlaying ? "đang nói…" : "trò chuyện";
            if (_bubble != null)
                _bubble.Visible = false;
            if (_live2DSpeechBubble != null)
            {
                _live2DSpeechBubble.Visible = shouldRemainVisible && !string.IsNullOrWhiteSpace(fullText);
                if (_live2DSpeechBubble.Visible)
                    ApplyLive2DBubbleVisualState();
            }
        }
        else
        {
            if (_label != null && !string.IsNullOrWhiteSpace(fullText))
                SetSpeechLabelText(_label, EscapeBbcode(fullText));
            if (_live2DSpeechBubble != null)
                _live2DSpeechBubble.Visible = false;
            HideCompanionBubbleTail();
            if (_bubble != null)
            {
                _bubble.Visible = shouldRemainVisible && !string.IsNullOrWhiteSpace(fullText);
                if (_bubble.Visible)
                {
                    PromoteLegacySpeechBubbleToUiOverlay();
                    SyncLegacySpeechBubbleOverlayTransform();
                }
            }
        }
        ScheduleTransientOverlayRefresh();
    }

    private void HideAllSpeechBubbles()
    {
        if (_bubble != null)
            _bubble.Visible = false;
        if (_live2DSpeechBubble != null)
            _live2DSpeechBubble.Visible = false;
        HideCompanionBubbleTail();
        if (_label != null)
            _label.Text = "";
        if (_live2DSpeechLabel != null)
            _live2DSpeechLabel.Text = "";
        if (_live2DSpeechStatus != null)
            _live2DSpeechStatus.Text = "";
        ScheduleTransientOverlayRefresh();
    }

    private Control Live2DSpeechBubbleControl()
    {
        return _live2DSpeechBubble;
    }

    private void EnsureConversationDateCurrent()
    {
        string today = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (string.Equals(_conversationStoreDate, today, StringComparison.Ordinal))
            return;

        _conversationStoreDate = today;
        if (_conversationDayList != null)
        {
            RebuildConversationDayList();
            RebuildConversationMessages(rebuildDayList: false);
        }
        RefreshConversationHeaderDate();
    }

    private void LoadConversationHistory()
    {
        if (_companionPreview) return;
        _conversationEntries.Clear();
        _conversationStoreDate = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        bool loaded = TryLoadConversationHistoryFile();
        if (!loaded)
            loaded = TryMigrateLegacyConversationFile();

        if (loaded)
        {
            _lastAssistantConversationText = _conversationEntries.LastOrDefault(e => e.Role == "assistant")?.Text ?? "";
            _lastAssistantConversationAtMs = 0;
        }
    }

    private bool TryLoadConversationHistoryFile()
    {
        string path = ProjectSettings.GlobalizePath(ConversationHistoryStatePath);
        try
        {
            if (!System.IO.File.Exists(path))
                return false;

            string raw = System.IO.File.ReadAllText(path);
            ConversationHistoryStore store = JsonSerializer.Deserialize<ConversationHistoryStore>(raw);
            if (store?.Entries == null)
                return false;

            AddNormalizedConversationEntries(store.Entries, _conversationStoreDate);
            return true;
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[ConversationUi] Không đọc được lịch sử hội thoại: {ex.Message}");
            return false;
        }
    }

    private bool TryMigrateLegacyConversationFile()
    {
        string path = ProjectSettings.GlobalizePath(ConversationLegacyTodayStatePath);
        try
        {
            if (!System.IO.File.Exists(path))
                return false;

            string raw = System.IO.File.ReadAllText(path);
            ConversationDayStore store = JsonSerializer.Deserialize<ConversationDayStore>(raw);
            if (store?.Entries == null)
                return false;

            string fallbackDate = NormalizeConversationDate(store.Date, _conversationStoreDate);
            AddNormalizedConversationEntries(store.Entries, fallbackDate);
            SaveConversationHistory();
            return true;
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[ConversationUi] Không di chuyển được lịch sử cũ: {ex.Message}");
            return false;
        }
    }

    private void AddNormalizedConversationEntries(IEnumerable<ConversationEntry> entries, string fallbackDate)
    {
        foreach (ConversationEntry entry in entries.TakeLast(ConversationHistoryLimit))
        {
            if (entry == null || string.IsNullOrWhiteSpace(entry.Text))
                continue;

            entry.Role = entry.Role == "user" ? "user" : "assistant";
            entry.Text = NormalizeConversationText(entry.Text);
            if (entry.StructuredDocumentJson != null && entry.StructuredDocumentJson.Length > 48_000)
                entry.StructuredDocumentJson = "";
            entry.Date = NormalizeConversationDate(entry.Date, fallbackDate);
            entry.Time = NormalizeConversationTime(entry.Time);
            if (!string.IsNullOrWhiteSpace(entry.Text))
                _conversationEntries.Add(entry);
        }
    }

    private void SaveConversationHistory()
    {
        if (_companionPreview) return;
        string path = ProjectSettings.GlobalizePath(ConversationHistoryStatePath);
        try
        {
            string directory = System.IO.Path.GetDirectoryName(path) ?? "";
            if (!string.IsNullOrWhiteSpace(directory))
                System.IO.Directory.CreateDirectory(directory);

            var store = new ConversationHistoryStore
            {
                Version = 2,
                Entries = _conversationEntries.TakeLast(ConversationHistoryLimit).ToList(),
            };
            string json = JsonSerializer.Serialize(store, new JsonSerializerOptions { WriteIndented = true });
            string tempPath = path + ".tmp";
            System.IO.File.WriteAllText(tempPath, json);
            System.IO.File.Move(tempPath, path, true);
        }
        catch (Exception ex)
        {
            GD.PrintErr($"[ConversationUi] Không lưu được lịch sử hội thoại: {ex.Message}");
        }
    }

    private void RefreshConversationHeaderDate()
    {
        if (_conversationHeaderDate == null)
            return;

        if (!string.IsNullOrWhiteSpace(_conversationSelectedDate))
        {
            string title = ConversationDaySidebarTitle(_conversationSelectedDate);
            int count = ConversationVisibleEntries().Count();
            _conversationHeaderDate.Text = $"{title} · {DisplayConversationDate(_conversationSelectedDate)} · {count} tin nhắn";
            return;
        }

        List<string> dates = _conversationEntries
            .Select(e => NormalizeConversationDate(e.Date, _conversationStoreDate))
            .Where(d => !string.IsNullOrWhiteSpace(d))
            .Distinct(StringComparer.Ordinal)
            .OrderBy(d => d, StringComparer.Ordinal)
            .ToList();

        if (dates.Count == 0)
        {
            _conversationHeaderDate.Text = "Chưa có tin nhắn";
            return;
        }

        string first = dates.First();
        string last = dates.Last();
        string range = string.Equals(first, last, StringComparison.Ordinal)
            ? DisplayConversationDate(last)
            : $"{DisplayConversationDate(first)} – {DisplayConversationDate(last)}";
        _conversationHeaderDate.Text = $"{range} · {_conversationEntries.Count} tin nhắn";
    }

    private static string NormalizeConversationDate(string date, string fallbackDate)
    {
        string candidate = (date ?? "").Trim();
        if (DateTime.TryParseExact(candidate, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out DateTime parsed))
            return parsed.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        string fallback = (fallbackDate ?? "").Trim();
        if (DateTime.TryParseExact(fallback, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed))
            return parsed.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

        return DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    }

    private static string NormalizeConversationTime(string time)
    {
        string candidate = (time ?? "").Trim();
        if (DateTime.TryParseExact(candidate, new[] { "HH:mm", "H:mm", "HH:mm:ss", "H:mm:ss" },
                CultureInfo.InvariantCulture, DateTimeStyles.None, out DateTime parsed))
            return parsed.ToString("HH:mm", CultureInfo.InvariantCulture);
        return "--:--";
    }

    private static string DisplayConversationDate(string date)
    {
        string normalized = NormalizeConversationDate(date, DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        if (DateTime.TryParseExact(normalized, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out DateTime parsed))
            return parsed.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture);
        return normalized;
    }

    private static string ConversationDateHeading(string date)
    {
        string normalized = NormalizeConversationDate(date, DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        DateTime today = DateTime.Today;
        if (DateTime.TryParseExact(normalized, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out DateTime parsed))
        {
            if (parsed.Date == today)
                return $"Hôm nay · {parsed.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture)}";
            if (parsed.Date == today.AddDays(-1))
                return $"Hôm qua · {parsed.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture)}";
            string weekday = parsed.ToString("dddd", new CultureInfo("vi-VN"));
            if (!string.IsNullOrWhiteSpace(weekday))
                weekday = char.ToUpperInvariant(weekday[0]) + weekday.Substring(1);
            return $"{weekday} · {parsed.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture)}";
        }
        return DisplayConversationDate(normalized);
    }

    private static string NormalizeConversationText(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return "";
        string cleaned = text.Replace("\r\n", "\n").Trim();
        if (cleaned.StartsWith("[Chủ nhân]:", StringComparison.OrdinalIgnoreCase))
            cleaned = cleaned.Substring("[Chủ nhân]:".Length).Trim();
        return cleaned;
    }

    private static string ConversationStateLabel(string state)
    {
        return state switch
        {
            "thinking" => "Eris đang suy nghĩ...",
            "preparing" => "Eris đang chuẩn bị trả lời...",
            "buffering" => "Eris đang chuẩn bị giọng nói...",
            "speaking" => "Eris đang nói",
            _ => "Sẵn sàng",
        };
    }

    private static string PrefixErisStatus(string label)
    {
        string clean = (label ?? "").Trim();
        if (string.IsNullOrWhiteSpace(clean))
            return "Eris đang suy nghĩ...";
        if (clean.StartsWith("Eris", StringComparison.OrdinalIgnoreCase))
            return clean;
        return "Eris " + char.ToLowerInvariant(clean[0]) + clean.Substring(1);
    }

    private static Label ConversationLabel(string text, int fontSize, Color color)
    {
        var label = new Label
        {
            Text = text,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        label.AddThemeColorOverride("font_color", color);
        label.AddThemeFontSizeOverride("font_size", fontSize);
        return label;
    }

    private static Control MakeSeparator()
    {
        return new ColorRect
        {
            Color = new Color(0.94f, 0.86f, 0.90f, 0.90f),
            CustomMinimumSize = new Vector2(0f, 1f),
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
    }

    private static StyleBoxFlat MakeFlatStyle(
        Color background,
        Color border,
        int radius,
        int borderWidth,
        float padding,
        bool shadow = false)
    {
        return new StyleBoxFlat
        {
            BgColor = background,
            BorderColor = border,
            BorderWidthLeft = borderWidth,
            BorderWidthTop = borderWidth,
            BorderWidthRight = borderWidth,
            BorderWidthBottom = borderWidth,
            CornerRadiusTopLeft = radius,
            CornerRadiusTopRight = radius,
            CornerRadiusBottomLeft = radius,
            CornerRadiusBottomRight = radius,
            ContentMarginLeft = padding,
            ContentMarginTop = padding,
            ContentMarginRight = padding,
            ContentMarginBottom = padding,
            ShadowColor = shadow ? new Color(0.28f, 0.10f, 0.19f, 0.14f) : new Color(0f, 0f, 0f, 0f),
            ShadowSize = shadow ? 14 : 0,
            ShadowOffset = shadow ? new Vector2(0f, 6f) : Vector2.Zero,
        };
    }
}
