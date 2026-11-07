using Godot;
using System;
using System.Linq;
using System.Globalization;
using System.Threading.Tasks;

// Runs the real UI without the desktop body, backend, missing legacy art, or user data.
public partial class CompanionPreview : UdpListener
{
    public override async void _Ready()
    {
        try
        {
            InitializeCompanionPreview();
            if (OS.GetCmdlineUserArgs().Contains("--verify"))
            {
                await VerifyCompanionPreview();
                GetTree().Quit();
            }
        }
        catch (Exception ex) { GD.PrintErr(ex); GetTree().Quit(1); }
    }

    public override void _Process(double delta) { }
    public override void _Input(InputEvent input) { }
    public override void _UnhandledInput(InputEvent input) { }
    public override void _ExitTree() { }
}

public partial class UdpListener
{
    protected void InitializeCompanionPreview()
    {
        _companionPreview = true;
        EnableDesktopOverlayWindow = false;
        EnableMousePassthrough = false;
        GetViewport().TransparentBg = false;
        DisplayServer.WindowSetFlag(DisplayServer.WindowFlags.Transparent, false);
        DisplayServer.WindowSetFlag(DisplayServer.WindowFlags.Borderless, false);
        GetWindow().Title = "Eris · UI preview · Dữ liệu minh họa";
        var canvas = new CanvasLayer { Name = "CanvasLayer" };
        AddChild(canvas);
        var control = new Control { Name = "Control", MouseFilter = Control.MouseFilterEnum.Ignore };
        control.SetAnchorsPreset(Control.LayoutPreset.FullRect);
        canvas.AddChild(control);
        _chatPanel = new Panel { Visible = false };
        control.AddChild(_chatPanel);
        _chatInput = new LineEdit();
        _chatSendButton = new Button();
        _chatPanel.AddChild(_chatInput);
        _chatPanel.AddChild(_chatSendButton);
        _conversationStoreDate = DateTime.Today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        _conversationDateSelectionInitialized = true;
        _conversationSelectedDate = _conversationStoreDate;
        _planner = new ErisPlannerState();
        if (!OS.GetCmdlineUserArgs().Contains("--empty"))
        {
            void Message(int days, string role, string text, string time) => _conversationEntries.Add(new ConversationEntry
            { Role = role, Text = text, Date = DateTime.Today.AddDays(days).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), Time = time });
            Message(-3, "assistant", "Mình cùng chia đồ án thành từng bước nhỏ nhé.", "09:15");
            Message(-1, "user", "Mình muốn sắp xếp việc học cho tuần này.", "20:30");
            Message(-1, "assistant", "Được, mình bắt đầu từ thời gian bạn có thể dành cho việc học nhé.", "20:31");
            Message(0, "user", "Hôm nay mình muốn ôn tiếng Anh, nhưng hơi khó tập trung.", "16:20");
            Message(0, "assistant", "Vậy bắt đầu thật nhỏ nhé. Mình chọn một chủ đề quen thuộc, học 5 từ rồi thử dùng chúng trong một câu. Bạn muốn bắt đầu với chủ đề nào?", "16:20");
            Message(0, "user", "Công việc đi. Mình có khoảng 20 phút.", "16:21");
            Message(0, "assistant", "Được. Mình sẽ đi từng câu cùng bạn. Trước tiên: khi nói về một việc bạn làm hằng ngày ở công ty, bạn sẽ bắt đầu thế nào?", "16:21");
            foreach (var (title, done) in new[] { ("Ôn 10 từ tiếng Anh", true), ("Đọc 5 trang tài liệu", true), ("Luyện nói cùng Eris", false), ("Hoàn thành bài tập", false) })
                _planner.Goals.Add(new ErisPlannerItem { Title = title, Date = _conversationStoreDate, Done = done });
            foreach (var (title, hours) in new[] { ("Ôn lại từ vựng", 2), ("Dành 15 phút đọc sách", 4) })
            {
                var due = DateTime.Now.AddHours(hours);
                _planner.Reminders.Add(new ErisPlannerItem { Title = title, Date = due.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), Time = due.ToString("HH:mm", CultureInfo.InvariantCulture) });
            }
        }
        BuildConversationUi();
        _conversationHistoryPanel.Visible = true;
        _chatSendButton.Pressed += OnSendPressed;
        _chatInput.TextSubmitted += _ => OnSendPressed();
        // Never connect to the real runtime during visual QA.
        _chatSend.ConnectToHost("127.0.0.1", 14243);
    }

    protected async Task VerifyCompanionPreview()
    {
        void Check(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
            GD.Print("PASS: " + message);
        }
        async Task Settle()
        {
            for (int i = 0; i < 5; i++) await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
        }
        string directory = ProjectSettings.GlobalizePath("res://scratch/ui-qa");
        System.IO.Directory.CreateDirectory(directory);
        foreach (var size in new[] { new Vector2I(1440, 900), new Vector2I(1000, 760), new Vector2I(720, 760), new Vector2I(480, 760) })
        {
            GetWindow().Size = size;
            _workspaceShowHistory = false;
            _workspaceShowPlanner = false;
            UpdateConversationUiLayout(true);
            await Settle();
            UpdateConversationUiLayout(true);
            await Settle();
            var panel = _conversationHistoryPanel.GetGlobalRect();
            Check(panel.Size.X <= size.X && panel.Size.Y <= size.Y, $"Workspace fits {size}");
            Check(_workspaceChatColumn.Size.X >= 300, $"Readable conversation at {size}");
            Check(panel.Encloses(_chatInput.GetGlobalRect()) && panel.Encloses(_chatSendButton.GetGlobalRect()), $"Composer contained at {size}");
            await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
            string prefix = OS.GetCmdlineUserArgs().Contains("--empty") ? "empty" : "companion";
            GetViewport().GetTexture().GetImage().SavePng($"{directory}/{prefix}-{size.X}.png");
            if (size.X < 1080)
            {
                _workspaceShowPlanner = true;
                UpdateCompanionColumns();
                await Settle();
                Check(_workspacePlannerRail.Visible && !_workspaceChatColumn.Visible, $"Planner accessible at {size}");
                Check(panel.Encloses(_workspacePlannerRail.GetGlobalRect()), $"Planner contained at {size}");
                await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
                GetViewport().GetTexture().GetImage().SavePng($"{directory}/{prefix}-planner-{size.X}.png");
            }
        }
        if (OS.GetCmdlineUserArgs().Contains("--empty"))
        {
            GD.Print("ALL EMPTY UI CHECKS PASSED");
            return;
        }

        _workspaceShowPlanner = false;
        _workspaceShowHistory = true;
        UpdateCompanionColumns();
        await Settle();
        Check(_conversationDaySidebar.Visible, "History accessible on narrow viewport");
        SelectConversationDate(DateTime.Today.AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        Check(ConversationVisibleEntries().Count() == 2, "History selects the requested day");
        _workspaceHistorySearch.Text = "sắp xếp";
        _workspaceHistorySearch.EmitSignal(LineEdit.SignalName.TextChanged, _workspaceHistorySearch.Text);
        Check(ConversationVisibleEntries().Count() == 1, "Search filters actual message content");
        _chatInput.Text = "Tin nhắn kiểm tra UI";
        _chatInput.EmitSignal(LineEdit.SignalName.TextChanged, _chatInput.Text);
        Check(!_chatSendButton.Disabled, "Nonempty draft enables send");
        _chatSendButton.EmitSignal(Button.SignalName.Pressed);
        Check(_conversationHistoryPanel.Visible && !_chatPanel.Visible, "Sending keeps the unified conversation open");
        Check(_chatInput.Text == "" && ConversationShowsLiveDay() && _conversationSearchQuery == "", "Sending returns to today and clears search");
        Check(_conversationEntries.Last().Text == "Tin nhắn kiểm tra UI", "Sending records exactly the submitted text");

        OpenPlannerEditor(false);
        _plannerTitleInput.Text = "Mục tiêu kiểm tra";
        ((Button)_plannerEditor.GetChildren().OfType<HBoxContainer>().Last().GetChild(0)).EmitSignal(Button.SignalName.Pressed);
        Check(_planner.Goals.Any(item => item.Title == "Mục tiêu kiểm tra"), "Goal form creates an item");
        var goal = _planner.Goals.Last();
        var goalRow = _plannerGoalList.GetChildren().Last();
        ((Button)goalRow.GetChild(0).GetChild(0)).EmitSignal(Button.SignalName.Pressed);
        Check(goal.Done, "Goal checkbox changes completion");
        OpenPlannerEditor(false, goal);
        _plannerTitleInput.Text = "Mục tiêu đã sửa";
        ((Button)_plannerEditor.GetChildren().OfType<HBoxContainer>().Last().GetChild(0)).EmitSignal(Button.SignalName.Pressed);
        Check(goal.Title == "Mục tiêu đã sửa", "Goal form edits the existing item");
        OpenPlannerEditor(true);
        _plannerTitleInput.Text = "Lời nhắc kiểm tra";
        _plannerDateInput.Text = "31/02/2026";
        _plannerTimeInput.Text = "25:90";
        var save = (Button)_plannerEditor.GetChildren().OfType<HBoxContainer>().Last().GetChild(0);
        int count = _planner.Reminders.Count;
        save.EmitSignal(Button.SignalName.Pressed);
        Check(_planner.Reminders.Count == count && _plannerEditor.Visible, "Reminder form rejects invalid date/time");
        var due = DateTime.Now.AddHours(1);
        _plannerDateInput.Text = due.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture);
        _plannerTimeInput.Text = due.ToString("HH:mm", CultureInfo.InvariantCulture);
        save.EmitSignal(Button.SignalName.Pressed);
        Check(_planner.Reminders.Count == count + 1, $"Reminder form accepts a future time ({_plannerDateInput.Text} {_plannerTimeInput.Text}; {_plannerStatus.Text})");
        OpenPlannerEditor(true, _planner.Reminders.Last());
        await Settle();
        await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
        GetViewport().GetTexture().GetImage().SavePng($"{directory}/reminder-form-480.png");
        ((Button)_plannerEditor.GetChildren().OfType<HBoxContainer>().Last().GetChild(2)).EmitSignal(Button.SignalName.Pressed);
        Check(_planner.Reminders.Count == count, "Reminder form deletes only the selected item");

        var overdue = new ErisPlannerItem { Title = "Đến hạn", Date = DateTime.Now.AddHours(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), Time = DateTime.Now.AddHours(-1).ToString("HH:mm", CultureInfo.InvariantCulture) };
        _planner.Reminders.Add(overdue);
        _plannerLastTick = 0;
        TickPersonalPlanner();
        await Settle();
        Check(overdue.Notified && _plannerReminderToast?.Visible == true, "Due reminder surfaces an in-app notice");
        Check(ErisPlannerStore.DueReminders(_planner, DateTime.Now).Count == 0, "Due reminder does not fire repeatedly");
        await ToSignal(RenderingServer.Singleton, RenderingServer.SignalName.FramePostDraw);
        GetViewport().GetTexture().GetImage().SavePng($"{directory}/reminder-notice-480.png");
        _plannerReminderToast.QueueFree();
        _plannerReminderToast = null;

        // Durable behavior: round trip, missed reminders, day boundary, corrupt-file safety.
        string storePath = $"{directory}/planner-behavior-check.json";
        var clock = new DateTime(2026, 10, 17, 12, 0, 0);
        var state = new ErisPlannerState();
        state.Goals.Add(new ErisPlannerItem { Title = "Hôm nay", Date = "2026-10-17", Done = true });
        state.Goals.Add(new ErisPlannerItem { Title = "Hôm qua", Date = "2026-10-16" });
        state.Reminders.Add(new ErisPlannerItem { Title = "Bỏ lỡ khi đóng app", Date = "2026-10-16", Time = "21:00" });
        state.Reminders.Add(new ErisPlannerItem { Title = "Sắp tới", Date = "2026-10-17", Time = "13:00" });
        state.Reminders.Add(new ErisPlannerItem { Title = "Xong rồi", Date = "2026-10-17", Time = "10:00", Done = true });
        ErisPlannerStore.Save(storePath, state);
        state = ErisPlannerStore.Load(storePath);
        Check(ErisPlannerStore.TodayGoals(state, clock).Single().Done, "Goal completion survives reopening, scoped to its day");
        var missed = ErisPlannerStore.DueReminders(state, clock);
        Check(missed.Count == 1 && missed[0].Title == "Bỏ lỡ khi đóng app", "Missed reminders survive reopening; future and done items stay quiet");
        missed[0].Notified = true;
        ErisPlannerStore.Save(storePath, state);
        Check(ErisPlannerStore.DueReminders(ErisPlannerStore.Load(storePath), clock).Count == 0, "Notification acknowledgment survives reopening");
        Check(ErisPlannerStore.TodayGoals(state, clock.AddDays(1)).Count == 0, "Next day starts its own goal checklist");
        Check(!ErisPlannerStore.TrySchedule("31/02/2026", "12:00", clock, out _)
            && !ErisPlannerStore.TrySchedule("17/10/2026", "11:00", clock, out _), "Scheduling rejects impossible dates and past times");
        System.IO.File.WriteAllText(storePath, "{bad json");
        bool rejected = false;
        try { ErisPlannerStore.Load(storePath); } catch (System.Text.Json.JsonException) { rejected = true; }
        Check(rejected && System.IO.File.ReadAllText(storePath) == "{bad json", "Invalid saved data is preserved, never silently replaced");
        System.IO.File.Delete(storePath);
        _chatInput.Text = "Bản nháp cần giữ";
        CloseConversationHistoryPanel();
        Check(_chatInput.Text == "Bản nháp cần giữ", "Closing preserves an unsent draft");
        GD.Print("ALL COMPANION UI CHECKS PASSED");
    }
}
