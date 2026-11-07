using Godot;
using System;
using System.Text.RegularExpressions;

// Live2D companion speech presentation only. The full text/TTS/history contract
// stays with UdpListener and is not shortened or re-written here.
public partial class UdpListener
{
    private const int CompanionSpeechPreviewChars = 176;
    private const float CompanionSpeechMaxWidth = 384f;
    private const float CompanionSpeechMinWidth = 288f;
    private static readonly Color CompanionBubbleBackground = new("192724");
    private static readonly Color CompanionBubbleBorder = new("708b7f");
    private static readonly Color CompanionBubbleText = new("f4f7f3");
    private static readonly Color CompanionBubbleSecondary = new("b2c3b9");
    private static readonly Color CompanionBubbleAccent = new("7bd0a2");

    private Button _companionSpeechMoreButton;
    private Polygon2D _companionSpeechTail;
    private bool? _companionSpeechTailRightOfModel;
    private bool _companionSpeechLayoutQueued;
    private bool _expandRecentAssistantFromCompanion;
    private float _companionBubbleEntranceOffsetY;
    private Tween _companionBubbleEntranceTween;

    private void BuildCompanionSpeechBubble()
    {
        Control overlay = UiOverlayRoot();
        if (overlay == null || _live2DSpeechBubble != null)
            return;

        float width = CompanionBubbleWidth();
        var bubble = new PanelContainer
        {
            Name = "Live2DCompanionSpeechBubble",
            Visible = false,
            AnchorLeft = 0f,
            AnchorTop = 0f,
            AnchorRight = 0f,
            AnchorBottom = 0f,
            SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
            Size = new Vector2(width, 94f),
            CustomMinimumSize = new Vector2(width, 0f),
            MouseFilter = Control.MouseFilterEnum.Pass,
            ZIndex = 999,
            ZAsRelative = false,
        };
        StyleBoxFlat style = MakeFlatStyle(
            CompanionBubbleBackground,
            new Color(CompanionBubbleBorder.R, CompanionBubbleBorder.G, CompanionBubbleBorder.B, 0.62f),
            23, 1, 15f, shadow: true);
        style.ShadowColor = new Color(0f, 0f, 0f, 0.28f);
        style.ShadowSize = 17;
        style.ShadowOffset = new Vector2(0f, 6f);
        bubble.AddThemeStyleboxOverride("panel", style);
        overlay.AddChild(bubble);
        _live2DSpeechBubble = bubble;

        var content = new VBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Pass,
        };
        content.AddThemeConstantOverride("separation", 9);
        bubble.AddChild(content);

        var header = new HBoxContainer
        {
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            MouseFilter = Control.MouseFilterEnum.Pass,
        };
        header.AddThemeConstantOverride("separation", 8);
        content.AddChild(header);

        var dot = new Panel
        {
            CustomMinimumSize = new Vector2(9f, 9f),
            SizeFlagsVertical = Control.SizeFlags.ShrinkCenter,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        dot.AddThemeStyleboxOverride("panel", MakeFlatStyle(CompanionBubbleAccent, Colors.Transparent, 5, 0, 0));
        header.AddChild(dot);

        var name = new Label { Text = "Eris", MouseFilter = Control.MouseFilterEnum.Ignore };
        name.AddThemeColorOverride("font_color", CompanionBubbleText);
        name.AddThemeFontSizeOverride("font_size", 15);
        header.AddChild(name);

        _live2DSpeechStatus = new Label
        {
            Text = "trò chuyện",
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            VerticalAlignment = VerticalAlignment.Center,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        _live2DSpeechStatus.AddThemeColorOverride("font_color", CompanionBubbleSecondary);
        _live2DSpeechStatus.AddThemeFontSizeOverride("font_size", 12);
        header.AddChild(_live2DSpeechStatus);

        var close = new Button
        {
            Text = "×",
            TooltipText = "Đóng bong bóng (lịch sử hội thoại vẫn được lưu)",
            Flat = true,
            CustomMinimumSize = new Vector2(24f, 23f),
            MouseFilter = Control.MouseFilterEnum.Stop,
            FocusMode = Control.FocusModeEnum.None,
        };
        close.AddThemeColorOverride("font_color", CompanionBubbleSecondary);
        close.AddThemeColorOverride("font_hover_color", Colors.White);
        close.AddThemeFontSizeOverride("font_size", 18);
        close.Pressed += DismissSpeechBubble;
        header.AddChild(close);

        _live2DSpeechLabel = new RichTextLabel
        {
            BbcodeEnabled = false,
            FitContent = false,
            ScrollActive = false,
            ScrollFollowing = false,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            CustomMinimumSize = new Vector2(0f, 26f),
            SizeFlagsHorizontal = Control.SizeFlags.ExpandFill,
            SizeFlagsVertical = Control.SizeFlags.ShrinkBegin,
            MouseFilter = Control.MouseFilterEnum.Ignore,
        };
        _live2DSpeechLabel.AddThemeColorOverride("default_color", CompanionBubbleText);
        _live2DSpeechLabel.AddThemeFontSizeOverride("normal_font_size", 16);
        _live2DSpeechLabel.AddThemeFontSizeOverride("bold_font_size", 16);
        content.AddChild(_live2DSpeechLabel);

        _companionSpeechMoreButton = new Button
        {
            Text = "Xem đầy đủ  ↗",
            Visible = false,
            Flat = true,
            FocusMode = Control.FocusModeEnum.None,
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(0f, 25f),
            MouseFilter = Control.MouseFilterEnum.Stop,
        };
        _companionSpeechMoreButton.AddThemeFontSizeOverride("font_size", 13);
        _companionSpeechMoreButton.AddThemeColorOverride("font_color", CompanionBubbleAccent);
        _companionSpeechMoreButton.AddThemeColorOverride("font_hover_color", Colors.White);
        _companionSpeechMoreButton.Pressed += OpenCompanionSpeechHistory;
        content.AddChild(_companionSpeechMoreButton);

        // Draw the small tail behind the bubble so it feels anchored to Live2D.
        // It's decorative and does not capture desktop mouse input.
        _companionSpeechTail = new Polygon2D
        {
            Name = "CompanionSpeechTail",
            Color = CompanionBubbleBackground,
            Visible = false,
            ZIndex = 998,
            ZAsRelative = false,
        };
        overlay.AddChild(_companionSpeechTail);
        ConfigureLive2DSpeechBubbleVisual();
    }

    private float CompanionBubbleWidth()
    {
        float viewportWidth = GetViewportRect().Size.X;
        if (viewportWidth <= 0f)
            return CompanionSpeechMaxWidth;
        return Mathf.Min(CompanionSpeechMaxWidth,
            Mathf.Max(180f, Mathf.Min(CompanionSpeechMinWidth, viewportWidth - 24f))
                + Mathf.Max(0f, Mathf.Min(96f, (viewportWidth - 840f) * 0.15f)));
    }

    // Only this display is shortened. The TTS text, transcript, conversation
    // history, and model output retain the original committed message.
    private void SetLive2DSpeechPreview(string committedText, int activeSentence = -1)
    {
        if (_live2DSpeechLabel == null)
            return;

        string fullText = CollapseCompanionWhitespace(committedText ?? "");
        int charLimit = Mathf.Clamp(Mathf.RoundToInt(CompanionBubbleWidth() * 0.46f), 84, CompanionSpeechPreviewChars);
        bool needsMore = fullText.Length > charLimit;
        string candidate = fullText;
        if (needsMore && activeSentence >= 0 && activeSentence < _speechSentences.Count)
            candidate = CollapseCompanionWhitespace(_speechSentences[activeSentence]);

        string preview = TruncateCompanionSpeech(candidate, charLimit);
        _live2DSpeechLabel.Text = preview;
        if (_companionSpeechMoreButton != null)
            _companionSpeechMoreButton.Visible = needsMore || preview.Length < candidate.Length;
        ScheduleCompanionSpeechLayout();
    }

    private static string CollapseCompanionWhitespace(string input)
    {
        return Regex.Replace(input ?? "", @"\s+", " ").Trim();
    }

    private static string TruncateCompanionSpeech(string input, int charLimit)
    {
        if (input.Length <= charLimit)
            return input;
        int cut = input.LastIndexOf(' ', charLimit);
        if (cut < charLimit / 2)
            cut = charLimit;
        return input[..cut].TrimEnd(' ', '.', '…') + "…";
    }

    private void ScheduleCompanionSpeechLayout()
    {
        if (_companionSpeechLayoutQueued || !IsInsideTree())
            return;
        _companionSpeechLayoutQueued = true;
        CallDeferred(nameof(RefreshCompanionSpeechLayoutDeferred));
    }

    private void RefreshCompanionSpeechLayoutDeferred()
    {
        _companionSpeechLayoutQueued = false;
        if (_live2DSpeechBubble == null || _live2DSpeechLabel == null || !IsInsideTree())
            return;

        float width = CompanionBubbleWidth();
        _live2DSpeechBubble.CustomMinimumSize = new Vector2(width, 0f);
        float innerWidth = Mathf.Max(60f, width - 32f);
        _live2DSpeechLabel.Size = new Vector2(innerWidth, _live2DSpeechLabel.Size.Y);
        // RichTextLabel provides its actual wrapped height after the container layout.
        float contentHeight = _live2DSpeechLabel.GetContentHeight();
        float textHeight = Mathf.Clamp(contentHeight, 26f, 160f);
        _live2DSpeechLabel.CustomMinimumSize = new Vector2(0f, textHeight);
        _live2DSpeechBubble.ResetSize();
        float targetHeight = Mathf.Clamp(_live2DSpeechBubble.GetCombinedMinimumSize().Y, 50f, 260f);
        _live2DSpeechBubble.Size = new Vector2(width, targetHeight);
        if (_isLive2DMode && _live2DSpeechBubble.Visible)
            ApplyLive2DBubbleVisualState();
        UpdateMousePassthrough();
    }

    private void OpenCompanionSpeechHistory()
    {
        // The full original message is displayed by Rich Chat, not the short TTS bubble.
        _expandRecentAssistantFromCompanion = true;
        if (OS.GetName() == "Windows" && !_companionPreview)
            OpenRichChatPortal();
        else
        {
            ShowConversationHistoryPanel();
            RebuildConversationMessages();
            CallDeferred(nameof(ScrollConversationToBottom));
        }
        HideAllSpeechBubbles();
        _bubbleHideAtMs = 0;
    }

    private void ScheduleCompanionBubbleAutoHide()
    {
        if (BubbleReadSeconds <= 0f)
        {
            _bubbleHideAtMs = 0;
            return;
        }
        // The preview is brief; the full message is available in history.
        float seconds = Mathf.Max(BubbleReadSeconds, 5f);
        _bubbleHideAtMs = Time.GetTicksMsec() + (ulong)(seconds * 1000f);
    }

    private void AnimateCompanionBubbleEntrance()
    {
        if (_live2DSpeechBubble == null)
            return;
        if (_companionBubbleEntranceTween != null && _companionBubbleEntranceTween.IsValid())
            _companionBubbleEntranceTween.Kill();
        _companionBubbleEntranceOffsetY = 8f;
        _live2DSpeechBubble.Modulate = new Color(1f, 1f, 1f, 0f);
        _companionBubbleEntranceTween = CreateTween();
        _companionBubbleEntranceTween.SetParallel(true);
        _companionBubbleEntranceTween.TweenProperty(_live2DSpeechBubble, "modulate:a", 1f, 0.18f);
        _companionBubbleEntranceTween.TweenMethod(
            Callable.From<float>(offset => _companionBubbleEntranceOffsetY = offset),
            8f, 0f, 0.18f);
    }

    private void PositionCompanionBubbleTail(Control bubble, bool besideModel, bool rightOfModel)
    {
        if (_companionSpeechTail == null)
            return;
        bool visible = _isLive2DMode && bubble.Visible && besideModel;
        _companionSpeechTail.Visible = visible;
        if (!visible)
            return;
        _companionSpeechTail.Modulate = new Color(1f, 1f, 1f, bubble.Modulate.A);

        float top = bubble.GlobalPosition.Y + Mathf.Min(46f, bubble.Size.Y * 0.42f);
        if (_companionSpeechTailRightOfModel != rightOfModel)
        {
            // The polygon geometry only changes when Eris switches sides,
            // rather than allocating new vertices on every render frame.
            _companionSpeechTail.Polygon = rightOfModel
                ? new Vector2[] { new(19f, 0f), new(0f, 10f), new(19f, 20f) }
                : new Vector2[] { new(0f, 0f), new(19f, 10f), new(0f, 20f) };
            _companionSpeechTailRightOfModel = rightOfModel;
        }
        _companionSpeechTail.GlobalPosition = new Vector2(
            rightOfModel ? bubble.GlobalPosition.X - 18f : bubble.GlobalPosition.X + bubble.Size.X - 1f,
            top);
    }

    private void HideCompanionBubbleTail()
    {
        if (_companionSpeechTail != null)
            _companionSpeechTail.Visible = false;
    }
}
