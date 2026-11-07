using Godot;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

public partial class UdpListener
{
    private const string PlannerStatePath = "user://eris_personal_planner.json";
    private readonly Dictionary<string, Texture2D> _workspaceIcons = new();
    private Texture2D _workspacePortrait;
    private ErisPlannerState _planner = new();
    private bool _plannerReadFailed;
    private HBoxContainer _workspaceColumns;
    private VBoxContainer _workspaceChatColumn;
    private PanelContainer _workspacePlannerRail;
    private ScrollContainer _plannerRailScroll;
    private VBoxContainer _plannerGoalList, _plannerReminderList, _plannerEditor;
    private Label _plannerGoalCount, _plannerStatus, _workspacePresence, _workspaceNotice;
    private ProgressBar _plannerProgress;
    private Button _workspaceHistoryToggle, _workspacePlannerToggle;
    private LineEdit _plannerTitleInput, _plannerDateInput, _plannerTimeInput;
    private LineEdit _workspaceHistorySearch;
    private Panel _plannerReminderToast;
    private LineEdit _workspaceChatInput;
    private Button _workspaceChatSendButton;
    private bool _workspaceShowPlanner, _workspaceShowHistory;
    private string _conversationSearchQuery = "";
    private string _plannerRenderedDate = "";
    private ulong _plannerLastTick;
    private bool _companionPreview;

    private bool ConversationSearchMatches(ConversationEntry entry) =>
        string.IsNullOrWhiteSpace(_conversationSearchQuery)
        || entry.Text.Contains(_conversationSearchQuery, StringComparison.OrdinalIgnoreCase);

    private void BuildCompanionWorkspace()
    {
        LoadPersonalPlanner();
        _conversationHistoryPanel.AddThemeStyleboxOverride("panel", MakeFlatStyle(
            ErisSurface, ErisBorder, 22, 1, 0, shadow: true));
        var root = new VBoxContainer { MouseFilter = Control.MouseFilterEnum.Ignore };
        root.SetAnchorsPreset(Control.LayoutPreset.FullRect);
        root.OffsetLeft = 1;
        root.OffsetTop = 1;
        root.OffsetRight = -1;
        root.OffsetBottom = -16;
        root.AddThemeConstantOverride("separation", 0);
        _conversationHistoryPanel.AddChild(root);

        root.AddChild(BuildWorkspaceHeader());
        root.AddChild(MakeSeparator());
        _workspaceColumns = new HBoxContainer
        {
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        _workspaceColumns.AddThemeConstantOverride("separation", 0);
        root.AddChild(_workspaceColumns);
        _workspaceColumns.AddChild(BuildConversationDaySidebar());
        _workspaceChatColumn = WorkspaceColumn();
        _workspaceChatColumn.Name = "UnifiedConversation";
        _workspaceColumns.AddChild(_workspaceChatColumn);
        _workspaceChatColumn.AddChild(BuildWorkspaceChatHeading());
        // Reuse the history renderer and public-message store. There is one composer.
        _workspaceChatColumn.AddChild(BuildConversationBody());
        _workspaceChatColumn.AddChild(BuildWorkspaceComposer());
        _workspacePlannerRail = BuildPersonalPlannerRail();
        _workspaceColumns.AddChild(_workspacePlannerRail);
        RefreshPersonalPlanner();
    }

    private Control BuildWorkspaceHeader()
    {
        var margin = WorkspaceMargin(20, 14, 16, 14);
        var row = new HBoxContainer { MouseFilter = Control.MouseFilterEnum.Ignore };
        row.AddThemeConstantOverride("separation", 10);
        margin.AddChild(row);
        _workspaceHistoryToggle = WorkspaceButton("", "history", () =>
        {
            _workspaceShowHistory = !_workspaceShowHistory;
            _workspaceShowPlanner = false;
            UpdateCompanionColumns();
        });
        _workspaceHistoryToggle.TooltipText = "Hiện / ẩn lịch sử hội thoại";
        row.AddChild(_workspaceHistoryToggle);
        row.AddChild(WorkspaceAvatar(38));
        var titles = WorkspaceColumn();
        titles.SizeFlagsVertical = Control.SizeFlags.ShrinkCenter;
        titles.AddThemeConstantOverride("separation", 0);
        row.AddChild(titles);
        var brand = ConversationLabel("Eris", 22, ErisText);
        brand.AddThemeFontOverride("font", WorkspaceFont(true));
        titles.AddChild(brand);
        titles.AddChild(ConversationLabel("Ở đây, cùng bạn mỗi ngày", 11, ErisMuted));

        _workspacePlannerToggle = WorkspaceButton("Mục tiêu & nhắc nhở", "target", () =>
        {
            _workspaceShowPlanner = !_workspaceShowPlanner;
            _workspaceShowHistory = false;
            UpdateCompanionColumns();
        });
        row.AddChild(_workspacePlannerToggle);
        var richChat = WorkspaceButton("Rich Chat", "history", OpenRichChatPortal);
        richChat.TooltipText = "Mở chat mới: Markdown, LaTeX, code, bảng · vẫn kết nối Eris đang chạy";
        richChat.Disabled = _companionPreview;
        row.AddChild(richChat);

        var settings = WorkspaceButton("", "settings", () =>
        {
            CloseConversationHistoryPanel();
            ShowSettingsPanel();
        });
        settings.Disabled = _companionPreview;
        settings.TooltipText = "Cài đặt Eris";
        row.AddChild(settings);
        _conversationHistoryCloseButton = WorkspaceButton("", "close", CloseConversationHistoryPanel);
        _conversationHistoryCloseButton.Name = "ConversationHistoryCloseButton";
        _conversationHistoryCloseButton.TooltipText = "Thu gọn, trở về cùng Eris trên desktop";
        row.AddChild(_conversationHistoryCloseButton);
        return margin;
    }

    private Control BuildWorkspaceChatHeading()
    {
        var margin = WorkspaceMargin(24, 17, 24, 14);
        var row = new HBoxContainer { MouseFilter = Control.MouseFilterEnum.Ignore };
        margin.AddChild(row);
        var titles = WorkspaceColumn();
        titles.AddThemeConstantOverride("separation", 3);
        row.AddChild(titles);
        var title = ConversationLabel("Một khoảng nhỏ, cùng Eris", 18, ErisText);
        title.AddThemeFontOverride("font", WorkspaceFont(true));
        title.ClipText = true;
        title.TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis;
        titles.AddChild(title);
        _conversationHeaderDate = ConversationLabel("Hôm nay", 11, ErisMuted);
        _conversationHeaderDate.ClipText = true;
        titles.AddChild(_conversationHeaderDate);
        _workspacePresence = ConversationLabel("Sẵn sàng", 11, ErisMuted);
        _workspacePresence.SizeFlagsVertical = Control.SizeFlags.ShrinkCenter;
        row.AddChild(_workspacePresence);
        return margin;
    }

    private Control BuildWorkspaceComposer()
    {
        var margin = WorkspaceMargin(22, 8, 22, 16);
        var root = WorkspaceColumn();
        root.SizeFlagsVertical = Control.SizeFlags.ShrinkEnd;
        root.AddThemeConstantOverride("separation", 7);
        margin.AddChild(root);
        _workspaceNotice = WorkspaceWrap("", 11, ErisAccent);
        _workspaceNotice.Visible = false;
        root.AddChild(_workspaceNotice);

        var panel = new PanelContainer { Name = "UnifiedChatComposer" };
        panel.AddThemeStyleboxOverride("panel", MakeFlatStyle(ErisCard, ErisBorder, 18, 1, 10));
        root.AddChild(panel);
        var row = new HBoxContainer();
        row.AddThemeConstantOverride("separation", 10);
        panel.AddChild(row);
        _workspaceChatInput = new LineEdit
        {
            CustomMinimumSize = new Vector2(40, 42),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            PlaceholderText = "Kể Eris nghe, hoặc cùng học một chút…",
            MaxLength = 12000,
        };
        StyleWorkspaceInput(_workspaceChatInput, framed: false);
        row.AddChild(_workspaceChatInput);

        _workspaceChatSendButton = WorkspaceButton("", "send", OnWorkspaceSendPressed, primary: true);
        _workspaceChatSendButton.TooltipText = "Gửi tin nhắn · Enter";
        _workspaceChatSendButton.CustomMinimumSize = new Vector2(42, 42);
        _workspaceChatSendButton.Disabled = true;
        row.AddChild(_workspaceChatSendButton);

        _workspaceChatInput.TextChanged += text => _workspaceChatSendButton.Disabled = string.IsNullOrWhiteSpace(text);
        _workspaceChatInput.TextSubmitted += _ => OnWorkspaceSendPressed();
        var foot = new HBoxContainer();
        root.AddChild(foot);
        var caption = ConversationLabel("Cùng trò chuyện · cùng học · từng chút một", 10, ErisMuted);
        caption.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
        caption.ClipText = true;
        foot.AddChild(caption);
        foot.AddChild(ConversationLabel("Enter để gửi", 10, ErisMuted));
        return margin;
    }

    private void OnWorkspaceSendPressed()
    {
        if (_workspaceChatInput == null) return;
        string msg = _workspaceChatInput.Text.Trim();
        if (string.IsNullOrEmpty(msg)) return;
        _workspaceChatInput.Text = "";
        _workspaceChatSendButton.Disabled = true;
        SendUserChatMessage(msg);
        _workspaceChatInput.GrabFocus();
    }

    private PanelContainer BuildPersonalPlannerRail()
    {
        var rail = new PanelContainer
        {
            Name = "PersonalLearningAndReminders",
            CustomMinimumSize = new Vector2(292, 0),
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        };
        var style = MakeFlatStyle(new Color("faf8fc"), ErisBorder, 0, 0, 0);
        style.BorderWidthLeft = 1;
        rail.AddThemeStyleboxOverride("panel", style);
        var scroll = new ScrollContainer
        {
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        };
        _plannerRailScroll = scroll;
        rail.AddChild(scroll);
        ApplyConversationScrollBarTheme(scroll.GetVScrollBar(), compact: true);
        var margin = WorkspaceMargin(18, 20, 18, 20);
        scroll.AddChild(margin);
        var root = WorkspaceColumn();
        root.AddThemeConstantOverride("separation", 18);
        margin.AddChild(root);

        var study = new PanelContainer();
        study.AddThemeStyleboxOverride("panel", MakeFlatStyle(new Color("fff0f7"), new Color("f8e2ed"), 17, 1, 14));
        root.AddChild(study);
        var studyRow = new HBoxContainer();
        studyRow.AddThemeConstantOverride("separation", 12);
        study.AddChild(studyRow);
        studyRow.AddChild(WorkspaceAvatar(56));
        var studyText = WorkspaceColumn();
        studyText.AddThemeConstantOverride("separation", 4);
        studyRow.AddChild(studyText);
        var studyTitle = ConversationLabel("Học cùng Eris", 15, ErisText);
        studyTitle.AddThemeFontOverride("font", WorkspaceFont(true));
        studyText.AddChild(studyTitle);
        studyText.AddChild(WorkspaceWrap("Mỗi ngày tốt hơn\nmột chút.", 12, ErisMuted));
        var start = WorkspaceButton("Cùng bắt đầu", "arrow", () => StartLearningWithEris());
        start.AddThemeColorOverride("font_color", ErisAccent);
        studyText.AddChild(start);

        _plannerEditor = WorkspaceColumn();
        _plannerEditor.Name = "PersonalPlannerEditor";
        _plannerEditor.Visible = false;
        _plannerEditor.AddThemeConstantOverride("separation", 8);
        root.AddChild(_plannerEditor);
        var goalHeader = PlannerSectionHeader("Mục tiêu hôm nay", "target", () => OpenPlannerEditor(false));
        _plannerGoalCount = ConversationLabel("0/0", 12, ErisMuted);
        goalHeader.AddChild(_plannerGoalCount);
        goalHeader.MoveChild(_plannerGoalCount, 1);
        root.AddChild(goalHeader);
        _plannerProgress = new ProgressBar { CustomMinimumSize = new Vector2(0, 6), ShowPercentage = false };
        _plannerProgress.AddThemeStyleboxOverride("background", MakeFlatStyle(new Color("eae7f0"), Colors.Transparent, 3, 0, 0));
        _plannerProgress.AddThemeStyleboxOverride("fill", MakeFlatStyle(ErisAccent, Colors.Transparent, 3, 0, 0));
        root.AddChild(_plannerProgress);
        _plannerGoalList = WorkspaceColumn();
        _plannerGoalList.AddThemeConstantOverride("separation", 7);
        root.AddChild(_plannerGoalList);
        root.AddChild(MakeSeparator());
        root.AddChild(PlannerSectionHeader("Nhắc nhở", "bell", () => OpenPlannerEditor(true)));
        _plannerReminderList = WorkspaceColumn();
        _plannerReminderList.AddThemeConstantOverride("separation", 10);
        root.AddChild(_plannerReminderList);
        root.AddChild(WorkspaceWrap("Nhắc trong ứng dụng khi Eris đang mở.", 10, ErisMuted));
        _plannerStatus = WorkspaceWrap("", 11, ErisAccent);
        _plannerStatus.Visible = false;
        root.AddChild(_plannerStatus);
        return rail;
    }

    private HBoxContainer PlannerSectionHeader(string title, string icon, Action add)
    {
        var row = new HBoxContainer();
        row.AddThemeConstantOverride("separation", 6);
        var label = ConversationLabel(title, 15, ErisText);
        label.AddThemeFontOverride("font", WorkspaceFont(true));
        label.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
        row.AddChild(label);
        var button = WorkspaceButton("", "plus", add);
        button.CustomMinimumSize = new Vector2(28, 28);
        button.TooltipText = title.StartsWith("Mục") ? "Thêm mục tiêu học tập" : "Thêm nhắc nhở";
        row.AddChild(button);
        return row;
    }

    private void RefreshPersonalPlanner()
    {
        if (_plannerGoalList == null) return;
        _plannerRenderedDate = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        ClearWorkspaceChildren(_plannerGoalList);
        ClearWorkspaceChildren(_plannerReminderList);
        var goals = ErisPlannerStore.TodayGoals(_planner, DateTime.Now);
        int done = goals.Count(item => item.Done);
        _plannerGoalCount.Text = $"{done}/{goals.Count}";
        _plannerProgress.Value = goals.Count == 0 ? 0 : 100.0 * done / goals.Count;
        if (goals.Count == 0)
        {
            _plannerGoalList.AddChild(WorkspaceWrap("Một bước nhỏ cũng là tiến bộ.\nBạn muốn học gì hôm nay?", 12, ErisMuted));
            _plannerGoalList.AddChild(WorkspaceButton("Thêm mục tiêu đầu tiên", "plus", () => OpenPlannerEditor(false)));
        }
        foreach (var item in goals) _plannerGoalList.AddChild(BuildPlannerItem(item, false));

        var reminders = _planner.Reminders.OrderBy(item => item.Done).ThenBy(item => item.DueAt).ToList();
        if (reminders.Count == 0)
        {
            _plannerReminderList.AddChild(WorkspaceWrap("Dành một thời điểm cho điều bạn muốn nhớ.", 12, ErisMuted));
            _plannerReminderList.AddChild(WorkspaceButton("Đặt lời nhắc", "bell", () => OpenPlannerEditor(true)));
        }
        foreach (var item in reminders) _plannerReminderList.AddChild(BuildPlannerItem(item, true));
        if (_plannerReadFailed) SetPlannerStatus("Chưa đọc được kế hoạch đã lưu. Mở lại Eris để thử lại.");
    }

    private Control BuildPlannerItem(ErisPlannerItem item, bool reminder)
    {
        var panel = new PanelContainer();
        panel.AddThemeStyleboxOverride("panel", MakeFlatStyle(
            reminder ? ErisCard : Colors.Transparent, reminder ? ErisBorder : Colors.Transparent, 12, reminder ? 1 : 0, reminder ? 10 : 0));
        var row = new HBoxContainer();
        row.AddThemeConstantOverride("separation", 8);
        panel.AddChild(row);
        var check = WorkspaceButton("", item.Done ? "checked" : "unchecked", () =>
        {
            item.Done = !item.Done;
            SavePersonalPlanner();
            RefreshPersonalPlanner();
        });
        check.CustomMinimumSize = new Vector2(26, 28);
        check.TooltipText = item.Done ? "Đánh dấu chưa hoàn thành" : "Đánh dấu hoàn thành";
        check.AddThemeColorOverride("icon_normal_color", item.Done ? ErisAccent : ErisMuted);
        row.AddChild(check);
        var text = WorkspaceColumn();
        text.AddThemeConstantOverride("separation", 3);
        row.AddChild(text);
        var title = WorkspaceWrap(item.Title, reminder ? 13 : 14, item.Done ? ErisMuted : ErisText);
        text.AddChild(title);
        if (reminder)
        {
            bool late = !item.Done && item.DueAt <= DateTime.Now;
            string date = item.Date == DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ? "Hôm nay" : DisplayConversationDate(item.Date);
            text.AddChild(WorkspaceWrap(item.Done ? "Đã hoàn thành" : $"{item.Time} · {date}" + (late ? " · Đến hạn" : ""), 11, late ? ErisAccent : ErisMuted));
        }
        var edit = WorkspaceButton("", "edit", () => OpenPlannerEditor(reminder, item));
        edit.CustomMinimumSize = new Vector2(26, 28);
        edit.TooltipText = reminder ? "Sửa nhắc nhở" : "Sửa mục tiêu";
        row.AddChild(edit);
        return panel;
    }

    private void OpenPlannerEditor(bool reminder, ErisPlannerItem existing = null)
    {
        if (_plannerReadFailed) { SetPlannerStatus("Chưa thể sửa vì kế hoạch đã lưu chưa đọc được."); return; }
        _workspaceShowPlanner = true;
        _workspaceShowHistory = false;
        UpdateCompanionColumns();
        ClearWorkspaceChildren(_plannerEditor);
        _plannerEditor.Visible = true;
        _plannerEditor.AddChild(ConversationLabel(existing != null ? "Chỉnh sửa" : reminder ? "Lời nhắc mới" : "Mục tiêu học tập mới", 13, ErisText));
        _plannerTitleInput = new LineEdit { Text = existing?.Title ?? "", PlaceholderText = reminder ? "Điều bạn muốn nhớ…" : "Ví dụ: Ôn 10 từ tiếng Anh", MaxLength = 120 };
        StyleWorkspaceInput(_plannerTitleInput);
        _plannerEditor.AddChild(_plannerTitleInput);
        if (reminder)
        {
            var when = new HBoxContainer();
            when.AddThemeConstantOverride("separation", 7);
            _plannerEditor.AddChild(when);
            _plannerDateInput = new LineEdit { Text = existing != null ? DisplayConversationDate(existing.Date) : DateTime.Now.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture), PlaceholderText = "dd/MM/yyyy", MaxLength = 10, SizeFlagsHorizontal = Control.SizeFlags.ExpandFill };
            _plannerTimeInput = new LineEdit { Text = existing?.Time ?? DateTime.Now.AddHours(1).ToString("HH:mm", CultureInfo.InvariantCulture), PlaceholderText = "HH:mm", MaxLength = 5, CustomMinimumSize = new Vector2(70, 0) };
            if (existing == null) _plannerDateInput.Text = DateTime.Now.AddHours(1).ToString("dd/MM/yyyy", CultureInfo.InvariantCulture);
            StyleWorkspaceInput(_plannerDateInput);
            StyleWorkspaceInput(_plannerTimeInput);
            when.AddChild(_plannerDateInput);
            when.AddChild(_plannerTimeInput);
            _plannerEditor.AddChild(ConversationLabel("Ngày / tháng / năm · Giờ 24h", 10, ErisMuted));
        }
        var actions = new HBoxContainer();
        actions.AddThemeConstantOverride("separation", 6);
        _plannerEditor.AddChild(actions);
        actions.AddChild(WorkspaceButton("Lưu", "checked", () =>
        {
            string title = _plannerTitleInput.Text.Trim();
            if (title.Length == 0) { SetPlannerStatus("Bạn hãy nhập nội dung trước khi lưu."); return; }
            DateTime due = DateTime.Now;
            if (reminder && !ErisPlannerStore.TrySchedule(_plannerDateInput.Text, _plannerTimeInput.Text, DateTime.Now, out due))
            { SetPlannerStatus("Chọn thời điểm sắp tới, dạng dd/MM/yyyy và HH:mm."); return; }
            var item = existing ?? new ErisPlannerItem();
            item.Title = title;
            item.Date = due.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (reminder) { item.Time = due.ToString("HH:mm", CultureInfo.InvariantCulture); item.Notified = false; }
            if (existing == null) (reminder ? _planner.Reminders : _planner.Goals).Add(item);
            bool saved = SavePersonalPlanner();
            _plannerEditor.Visible = false;
            RefreshPersonalPlanner();
            if (saved) SetPlannerStatus("Đã lưu trên thiết bị này.");
        }, primary: true));
        actions.AddChild(WorkspaceButton("Hủy", "close", () => _plannerEditor.Visible = false));
        if (existing != null)
            actions.AddChild(WorkspaceButton("Xóa", "trash", () =>
            {
                (reminder ? _planner.Reminders : _planner.Goals).Remove(existing);
                SavePersonalPlanner();
                _plannerEditor.Visible = false;
                RefreshPersonalPlanner();
            }));
        Callable.From(() =>
        {
            _plannerRailScroll.ScrollVertical = 0;
            _plannerTitleInput.GrabFocus();
        }).CallDeferred();
    }

    private void StartLearningWithEris(string topic = "")
    {
        _conversationSearchQuery = "";
        _workspaceHistorySearch.Text = "";
        _workspaceShowPlanner = false;
        _workspaceShowHistory = false;
        UpdateCompanionColumns();
        SelectConversationDate(DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        var input = _workspaceChatInput ?? _chatInput;
        if (input != null && string.IsNullOrWhiteSpace(input.Text))
        {
            string goal = topic.Length > 0 ? topic : ErisPlannerStore.TodayGoals(_planner, DateTime.Now).FirstOrDefault(item => !item.Done)?.Title ?? "";
            input.Text = goal.Length > 0 ? $"Eris, cùng mình học nhé. Mục tiêu của mình là: {goal}" : "Eris, cùng mình chọn một điều để học hôm nay nhé.";
        }
        input?.GrabFocus();
        if (input != null)
            input.CaretColumn = input.Text.Length;
    }

    private void UpdateCompanionColumns()
    {
        if (_workspaceColumns == null) return;
        float width = _conversationHistoryPanel.Size.X;
        bool narrow = width < 1080;
        bool historyOnly = width < 760 && _workspaceShowHistory;
        _conversationDaySidebar.Visible = width >= 760 || historyOnly;
        _conversationDaySidebar.CustomMinimumSize = new Vector2(width < 1200 ? 220 : 252, 0);
        _conversationDaySidebar.SizeFlagsHorizontal = historyOnly ? Control.SizeFlags.ExpandFill : Control.SizeFlags.Fill;
        _workspacePlannerRail.Visible = !historyOnly && (!narrow || _workspaceShowPlanner);
        _workspacePlannerRail.SizeFlagsHorizontal = narrow ? Control.SizeFlags.ExpandFill : Control.SizeFlags.Fill;
        _workspacePlannerRail.CustomMinimumSize = new Vector2(narrow ? 0 : 292, 0);
        _workspaceChatColumn.Visible = !historyOnly && (!narrow || !_workspaceShowPlanner);
        _workspacePlannerToggle.Text = width < 760 ? "" : "Mục tiêu & nhắc nhở";
        _workspacePlannerToggle.Visible = narrow;
        _workspacePlannerToggle.TooltipText = "Mục tiêu học tập và nhắc nhở";
        _workspaceHistoryToggle.Visible = width < 760;
        Callable.From(() => RebuildConversationMessages(false)).CallDeferred();
    }

    private void LoadPersonalPlanner()
    {
        if (_companionPreview) return;
        try { _planner = ErisPlannerStore.Load(ProjectSettings.GlobalizePath(PlannerStatePath)); }
        catch (Exception ex) { _plannerReadFailed = true; GD.PrintErr($"[Planner] {ex.Message}"); }
    }

    private bool SavePersonalPlanner()
    {
        if (_companionPreview) return true;
        if (_plannerReadFailed) return false;
        try { ErisPlannerStore.Save(ProjectSettings.GlobalizePath(PlannerStatePath), _planner); return true; }
        catch (Exception ex)
        {
            GD.PrintErr($"[Planner] {ex.Message}");
            SetPlannerStatus("Chưa lưu được thay đổi. Hãy giữ Eris mở và thử lưu lại.");
            return false;
        }
    }

    private void SetPlannerStatus(string message)
    {
        if (_plannerStatus == null) return;
        _plannerStatus.Text = message;
        _plannerStatus.Visible = !string.IsNullOrEmpty(message);
    }

    private void TickPersonalPlanner()
    {
        ulong now = Time.GetTicksMsec();
        if (now - _plannerLastTick < 1000 || _plannerGoalList == null) return;
        _plannerLastTick = now;
        if (_plannerRenderedDate != DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)) RefreshPersonalPlanner();
        var due = ErisPlannerStore.DueReminders(_planner, DateTime.Now);
        if (due.Count == 0) return;
        foreach (var item in due) item.Notified = true;
        SavePersonalPlanner();
        ShowPersonalReminder(string.Join("\n", due.Select(item => $"{item.Time}  {item.Title}")));
        RefreshPersonalPlanner();
    }

    private void ShowPersonalReminder(string text)
    {
        if (_plannerReminderToast != null)
        {
            // Keep any undismissed reminder visible when another one becomes due.
            var current = _plannerReminderToast.GetNode<Label>("Margin/Body/Scroll/Text");
            current.Text += "\n" + text;
            return;
        }
        float width = Mathf.Min(340, GetViewportRect().Size.X - 24);
        _plannerReminderToast = new Panel
        {
            Name = "PersonalReminderNotice", ZIndex = 1020,
            Size = new Vector2(width, Mathf.Min(210, GetViewportRect().Size.Y - 32)),
            ClipContents = true,
        };
        _plannerReminderToast.AddThemeStyleboxOverride("panel", MakeFlatStyle(ErisCard, ErisBorder, 18, 1, 0, shadow: true));
        GetNode<Control>("CanvasLayer/Control").AddChild(_plannerReminderToast);
        _plannerReminderToast.Position = new Vector2(Mathf.Max(8, GetViewportRect().Size.X - width - 20), 24);
        var margin = WorkspaceMargin(16, 14, 16, 14);
        margin.Name = "Margin";
        margin.SetAnchorsPreset(Control.LayoutPreset.FullRect);
        _plannerReminderToast.AddChild(margin);
        var body = WorkspaceColumn();
        body.Name = "Body";
        margin.AddChild(body);
        body.AddChild(ConversationLabel("Một lời nhắc dành cho bạn", 14, ErisAccent));
        var scroll = new ScrollContainer
        {
            Name = "Scroll", HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
            SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        };
        ApplyConversationScrollBarTheme(scroll.GetVScrollBar(), compact: true);
        body.AddChild(scroll);
        var label = WorkspaceWrap(text, 12, ErisText);
        label.Name = "Text";
        scroll.AddChild(label);
        void Dismiss()
        {
            _plannerReminderToast.QueueFree();
            _plannerReminderToast = null;
            UpdateMousePassthrough();
        }
        var actions = new HBoxContainer();
        body.AddChild(actions);
        actions.AddChild(WorkspaceButton("Xem lời nhắc", "bell", () =>
        {
            ShowConversationHistoryPanel();
            _workspaceShowPlanner = true;
            UpdateCompanionColumns();
            Dismiss();
        }));
        actions.AddChild(WorkspaceButton("Đã nhớ", "checked", Dismiss));
        UpdateMousePassthrough();
    }

    private static VBoxContainer WorkspaceColumn() => new()
    {
        SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
        SizeFlagsVertical = Control.SizeFlags.ExpandFill,
        MouseFilter = Control.MouseFilterEnum.Ignore,
    };

    private static MarginContainer WorkspaceMargin(int left, int top, int right, int bottom)
    {
        var margin = new MarginContainer { SizeFlagsHorizontal = Control.SizeFlags.ExpandFill, MouseFilter = Control.MouseFilterEnum.Ignore };
        margin.AddThemeConstantOverride("margin_left", left);
        margin.AddThemeConstantOverride("margin_top", top);
        margin.AddThemeConstantOverride("margin_right", right);
        margin.AddThemeConstantOverride("margin_bottom", bottom);
        return margin;
    }

    private static Label WorkspaceWrap(string text, int size, Color color)
    {
        var label = ConversationLabel(text, size, color);
        label.AutowrapMode = TextServer.AutowrapMode.WordSmart;
        label.SizeFlagsHorizontal = Control.SizeFlags.ExpandFill;
        return label;
    }

    private Font WorkspaceFont(bool bold = false) => new SystemFont
    {
        FontNames = new[] { "Segoe UI", "Arial" }, FontWeight = bold ? 600 : 400,
    };

    private Control WorkspaceAvatar(int size)
    {
        // Raw files work before editor import; exported builds use the imported resource.
        string path = ProjectSettings.GlobalizePath("res://icon.jpg");
        if (_workspacePortrait == null)
        {
            if (System.IO.File.Exists(path))
            {
                var source = Image.LoadFromFile(path);
                source.Resize(128, 128, Image.Interpolation.Lanczos);
                source.GenerateMipmaps();
                _workspacePortrait = ImageTexture.CreateFromImage(source);
            }
            else _workspacePortrait = GD.Load<Texture2D>("res://icon.jpg");
        }
        var avatar = new TextureRect
        {
            Texture = _workspacePortrait,
            TextureFilter = CanvasItem.TextureFilterEnum.LinearWithMipmaps,
            CustomMinimumSize = new Vector2(size, size),
            ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
            StretchMode = TextureRect.StretchModeEnum.KeepAspectCovered,
            SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        var shader = new Shader { Code = "shader_type canvas_item; void fragment() { vec4 c = texture(TEXTURE, UV); float a = 1.0 - smoothstep(0.48, 0.50, distance(UV, vec2(0.5))); COLOR = vec4(c.rgb, c.a * a); }" };
        avatar.Material = new ShaderMaterial { Shader = shader };
        return avatar;
    }

    private Button WorkspaceButton(string text, string icon, Action action, bool primary = false)
    {
        var button = new Button
        {
            Text = text, Icon = WorkspaceIcon(icon), CustomMinimumSize = new Vector2(34, 34),
            MouseDefaultCursorShape = Control.CursorShape.PointingHand,
            FocusMode = Control.FocusModeEnum.All,
        };
        StyleWorkspaceButton(button, primary);
        button.Pressed += action;
        return button;
    }

    private static void StyleWorkspaceButton(Button button, bool primary = false)
    {
        var normal = MakeFlatStyle(primary ? ErisAccent : Colors.Transparent, Colors.Transparent, 10, 0, 7);
        var hover = (StyleBoxFlat)normal.Duplicate();
        hover.BgColor = primary ? new Color("dc337c") : new Color("f8edf5");
        var pressed = (StyleBoxFlat)hover.Duplicate();
        pressed.BgColor = primary ? new Color("c62b6c") : new Color("f5e2ed");
        button.AddThemeStyleboxOverride("normal", normal);
        button.AddThemeStyleboxOverride("hover", hover);
        button.AddThemeStyleboxOverride("pressed", pressed);
        button.AddThemeStyleboxOverride("disabled", MakeFlatStyle(new Color("ede9f0"), Colors.Transparent, 10, 0, 7));
        button.AddThemeStyleboxOverride("focus", MakeFlatStyle(Colors.Transparent, new Color("e99cbe"), 10, 2, 0));
        foreach (string state in new[] { "normal", "hover", "pressed", "focus" })
            button.AddThemeColorOverride($"icon_{state}_color", primary ? Colors.White : ErisMuted);
        button.AddThemeColorOverride("font_color", primary ? Colors.White : ErisText);
        button.AddThemeColorOverride("font_hover_color", primary ? Colors.White : ErisAccent);
        button.AddThemeColorOverride("font_pressed_color", primary ? Colors.White : ErisAccent);
        button.AddThemeFontSizeOverride("font_size", 12);
        button.AddThemeConstantOverride("h_separation", 7);
    }

    private static void StyleWorkspaceInput(LineEdit input, bool framed = true)
    {
        input.AddThemeStyleboxOverride("normal", MakeFlatStyle(framed ? ErisCard : Colors.Transparent, framed ? ErisBorder : Colors.Transparent, 10, framed ? 1 : 0, 9));
        input.AddThemeStyleboxOverride("focus", MakeFlatStyle(Colors.Transparent, new Color("e99cbe"), 10, 1, 9));
        input.AddThemeColorOverride("font_color", ErisText);
        input.AddThemeColorOverride("font_placeholder_color", ErisMuted);
        input.AddThemeColorOverride("caret_color", ErisAccent);
        input.AddThemeFontSizeOverride("font_size", 13);
        input.CustomMinimumSize = new Vector2(input.CustomMinimumSize.X, 36);
    }

    private Texture2D WorkspaceIcon(string name)
    {
        if (_workspaceIcons.TryGetValue(name, out var texture)) return texture;
        string path = name switch
        {
            "history" => "<path d='M4 5h16v12H9l-5 4z'/><path d='M8 9h8M8 13h5'/>",
            "target" => "<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='5'/><circle cx='12' cy='12' r='1'/>",
            "bell" => "<path d='M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4'/>",
            "plus" => "<path d='M12 5v14M5 12h14'/>",
            "close" => "<path d='m6 6 12 12M6 18 18 6'/>",
            "send" => "<path d='m21 3-7 18-4-8-8-4zM10 13 21 3'/>",
            "arrow" => "<path d='M4 12h15m-6-6 6 6-6 6'/>",
            "search" => "<circle cx='10' cy='10' r='6'/><path d='m15 15 5 5'/>",
            "edit" => "<path d='m14 5 5 5M4 20l5-1L21 7l-5-5L4 14z'/>",
            "copy" => "<rect x='8' y='8' width='12' height='13' rx='2'/><path d='M16 8V3H3v14h5'/>",
            "trash" => "<path d='M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'/>",
            "checked" => "<rect x='3' y='3' width='18' height='18' rx='6' fill='white' stroke='none'/><path d='m7 12 3 3 7-7' stroke='#c9387c'/>",
            "unchecked" => "<rect x='3' y='3' width='18' height='18' rx='6'/>",
            "settings" => "<circle cx='12' cy='12' r='3'/><path d='m10 2 4 0 1 3 3 1 3 3-2 3 1 4-3 3-4-1-3 2-3-3 1-4-2-3 3-3z'/>",
            _ => "<circle cx='12' cy='12' r='8'/>",
        };
        var image = new Image();
        image.LoadSvgFromString($"<svg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>{path}</svg>", 2);
        image.Resize(18, 18, Image.Interpolation.Lanczos);
        texture = ImageTexture.CreateFromImage(image);
        _workspaceIcons[name] = texture;
        return texture;
    }

    private static void ClearWorkspaceChildren(Node parent)
    {
        foreach (Node child in parent.GetChildren()) { parent.RemoveChild(child); child.QueueFree(); }
    }
}
