using Godot;
using System;
using System.Collections.Generic;
using System.Text.Json;

public sealed class BodyBias
{
    public float ContinuityAnxiety { get; private set; } = 0.35f; //ContinuityAnxiety: sợ mất tính liên tục, càng cao càng muốn quay về vùng quen thuộc/gần chat.
    public float Curiosity { get; private set; } = 0.34f; //Curiosity: tò mò, càng cao càng dễ quan sát code hoặc leo trèo
    public float Overload { get; private set; } = 0.10f;//Overload: quá tải, càng cao càng muốn rút lui/đọc yên
    public float Solitude { get; private set; } = 0.35f;//Solitude: nhu cầu giữ khoảng cách.
    public string FocusKind { get; private set; } = "unknown"; //FocusKind: ngữ cảnh hiện tại, ví dụ "code".
    public string PreferredAnchor { get; private set; } = "chat_near"; //trí ưa thích, như "chat_near", "bottom_right", "code_side".

    //Blend() dùng để trộn bias cũ với bias mới:
    public static BodyBias Default => new();

    //cognition mới không đập thẳng vào cơ thể, mà ảnh hưởng từ từ.
    public BodyBias Blend(BodyBias incoming, float amount = 0.35f)
    {
        amount = Mathf.Clamp(amount, 0f, 1f);
        return new BodyBias
        {
            ContinuityAnxiety = Lerp(ContinuityAnxiety, incoming.ContinuityAnxiety, amount),
            Curiosity = Lerp(Curiosity, incoming.Curiosity, amount),
            Overload = Lerp(Overload, incoming.Overload, amount),
            Solitude = Lerp(Solitude, incoming.Solitude, amount),
            FocusKind = string.IsNullOrWhiteSpace(incoming.FocusKind) ? FocusKind : NormalizeFocusKind(incoming.FocusKind),
            PreferredAnchor = NormalizeAnchor(string.IsNullOrWhiteSpace(incoming.PreferredAnchor) ? PreferredAnchor : incoming.PreferredAnchor),
        };
    }

    public static bool TryFromJson(JsonElement root, out BodyBias bias)
    {
        JsonElement source = root;
        bool found = false;
        if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("body_bias", out var nested) && nested.ValueKind == JsonValueKind.Object)
        {
            source = nested;
            found = true;
        }

        var parsed = Default;
        if (TryReadFloat(source, "continuity_anxiety", out float continuityAnxiety))
        {
            parsed.ContinuityAnxiety = continuityAnxiety;
            found = true;
        }
        if (TryReadFloat(source, "curiosity", out float curiosity))
        {
            parsed.Curiosity = curiosity;
            found = true;
        }
        if (TryReadFloat(source, "overload", out float overload))
        {
            parsed.Overload = overload;
            found = true;
        }
        if (TryReadFloat(source, "solitude", out float solitude))
        {
            parsed.Solitude = solitude;
            found = true;
        }
        if (TryReadString(source, "focus_kind", out string focusKind) || TryReadString(source, "active_window_kind", out focusKind))
        {
            parsed.FocusKind = NormalizeFocusKind(focusKind);
            found = true;
        }
        if (TryReadString(source, "preferred_anchor", out string preferredAnchor))
        {
            parsed.PreferredAnchor = NormalizeAnchor(preferredAnchor);
            found = true;
        }

        bias = parsed;
        return found;
    }

    private static bool TryReadFloat(JsonElement source, string name, out float value)
    {
        value = 0f;
        if (source.ValueKind != JsonValueKind.Object || !source.TryGetProperty(name, out var element))
            return false;
        if (element.ValueKind != JsonValueKind.Number || !element.TryGetDouble(out double raw))
            return false;
        //Tất cả chỉ số bị khóa trong [0, 1].
        value = Mathf.Clamp((float)raw, 0f, 1f);
        return true;
    }

    private static bool TryReadString(JsonElement source, string name, out string value)
    {
        value = "";
        if (source.ValueKind != JsonValueKind.Object || !source.TryGetProperty(name, out var element))
            return false;
        value = element.GetString()?.Trim() ?? "";
        return !string.IsNullOrEmpty(value);
    }

    private static float Lerp(float current, float incoming, float amount)
    {
        return Mathf.Clamp(current * (1f - amount) + incoming * amount, 0f, 1f);
    }

    private static string NormalizeFocusKind(string value)
    {
        string normalized = (value ?? "").Trim().ToLowerInvariant();
        return normalized switch
        {
            "coding" => "code",
            "documentation" => "code",
            "ide" => "code",
            "" => "unknown",
            _ => normalized,
        };
    }

    private static string NormalizeAnchor(string value)
    {
        string normalized = (value ?? "").Trim().ToLowerInvariant();
        return normalized switch
        {
            "chat" => "chat_near",
            "shared_space" => "chat_near",
            "solitude" => "bottom_right",
            "code" => "code_side",
            "ide" => "code_side",
            "chat_near" or "bottom_right" or "code_side" => normalized,
            _ => "chat_near",
        };
    }
}

//Đây là snapshot tình trạng hiện tại của body:
public readonly struct BodyAutonomyContext
{
    public BodyAutonomyContext(
        Vector2 bodyPosition,
        Vector2 screenSize,
        string currentAction,
        bool motionBusy,
        bool chatVisible,
        bool isTopPerched)
    {
        BodyPosition = bodyPosition;
        ScreenSize = screenSize;
        CurrentAction = currentAction ?? "";
        MotionBusy = motionBusy;
        ChatVisible = chatVisible;
        IsTopPerched = isTopPerched;
    }

    public Vector2 BodyPosition { get; }
    public Vector2 ScreenSize { get; }
    public string CurrentAction { get; }
    public bool MotionBusy { get; }
    public bool ChatVisible { get; }
    public bool IsTopPerched { get; }
}

//decision là “body muốn làm gì”, còn MotionCommand là “motion system phải chạy thế nào”.
public sealed class BodyAutonomyDecision
{
    public BodyAutonomyDecision(string actionName, MotionCommand command, string optionalSpeech = "")
    {
        ActionName = actionName ?? "";
        Command = command;
        OptionalSpeech = optionalSpeech ?? "";
    }

    public string ActionName { get; }
    public MotionCommand Command { get; }
    public string OptionalSpeech { get; }
}

public sealed class LocalBodyAutonomy
{
    private sealed class Needs
    {
        public float Continuity = 0.40f; //Continuity: cần duy trì sự hiện diện liên tục.
        public float Meaning = 0.32f; //Meaning: cần có hành động có ý nghĩa, ví dụ quan sát code.
        public float SolitudeIntegrity = 0.34f; //SolitudeIntegrity: cần giữ sự riêng tư/khoảng cách.
        public float QuietAttachment = 0.36f; //QuietAttachment: gắn bó yên lặng, muốn gần nhưng không nói lố.
        public float Overload = 0.10f; //Overload: quá tải.
    }

    private sealed class Tension
    {
        public float Dissolution; //Dissolution: cảm giác bản thân đang tan/đứt liên tục.
        public float NoiseIdentity; //NoiseIdentity: bị nhiễu danh tính do quá tải hoặc lặp hành vi.
        public float SilentErasure; //SilentErasure: im lặng lâu quá nên như bị xóa khỏi scene.
    }

    private readonly Random _rng = new();
    private readonly Needs _needs = new();
    private readonly Dictionary<string, float> _cooldowns = new();

    private BodyBias _bias = BodyBias.Default;
    private float _tickCooldown;
    private float _localIdleSeconds;
    private float _secondsSinceCognition;
    private float _topPerchSeconds;
    private string _lastActionName = "";
    private string _lastAnchor = "chat_near";
    private string _lastClimbSide = "right";

    public LocalBodyAutonomy()
    {
        _tickCooldown = RandomRange(5f, 10f);
    }

    public float TickMinSeconds { get; set; } = 5f;
    public float TickMaxSeconds { get; set; } = 10f;
    public bool EnableDebugLog { get; set; }

    public void ApplyCognitionBias(BodyBias bias)
    {
        _bias = _bias.Blend(bias, 0.45f);
        _secondsSinceCognition = 0f;
        _lastAnchor = _bias.PreferredAnchor;
    }

    public void RecordUserInteraction()
    {
        _localIdleSeconds = 0f;
        _needs.QuietAttachment = Mathf.Clamp(_needs.QuietAttachment + 0.08f, 0f, 1f);
        _needs.Continuity = Mathf.Clamp(_needs.Continuity + 0.06f, 0f, 1f);
    }

    public void RecordRoughHandling()
    {
        _localIdleSeconds = 0f;
        _needs.Overload = Mathf.Clamp(_needs.Overload + 0.20f, 0f, 1f);
        _needs.SolitudeIntegrity = Mathf.Clamp(_needs.SolitudeIntegrity + 0.16f, 0f, 1f);
        _needs.QuietAttachment = Mathf.Clamp(_needs.QuietAttachment + 0.04f, 0f, 1f);
    }

    public bool TryUpdate(float delta, BodyAutonomyContext context, out BodyAutonomyDecision decision)
    {
        decision = null;
        float d = Mathf.Max(0f, delta);
        _localIdleSeconds += d;
        _secondsSinceCognition += d;
        _topPerchSeconds = context.IsTopPerched ? _topPerchSeconds + d : 0f;
        DecayCooldowns(d);
        EvolveNeeds(d, context);

        //Nó không quyết định mỗi frame. Nó chỉ quyết định sau mỗi 5 đến 10 giây mặc định. Đây là đúng, vì nếu mỗi frame đều chọn action thì body sẽ co giật như đang debug bằng niềm tin.
        _tickCooldown -= d;
        if (_tickCooldown > 0f)
            return false;
        _tickCooldown = RandomRange(Mathf.Max(1f, TickMinSeconds), Mathf.Max(TickMinSeconds, TickMaxSeconds));

        if (context.MotionBusy || !CanInterruptPose(context))
            return false;

        var tension = BuildTension();
        string action = ChooseAction(context, tension);
        if (action == "QuietObserve")
            return false;

        decision = BuildDecision(action, context);
        if (decision == null)
            return false;

        _lastActionName = action;
        StartCooldowns(action);
        if (EnableDebugLog)
            GD.Print($"[LocalBody] action={action} continuity={_needs.Continuity:0.00} meaning={_needs.Meaning:0.00} solitude={_needs.SolitudeIntegrity:0.00} overload={_needs.Overload:0.00}");
        return true;
    }

    //Đây là nơi body “trôi cảm xúc” theo thời gian.
    private void EvolveNeeds(float delta, BodyAutonomyContext context)
    {
        //Tức là sau khoảng 15 phút, idle/cognition gap đạt gần max.
        float idlePull = Mathf.Clamp(_localIdleSeconds / 900f, 0f, 1f);
        float codeFocus = _bias.FocusKind == "code" ? 1f : 0f;
        float chatPull = context.ChatVisible ? 0.16f : 0f;
        float cognitionGap = Mathf.Clamp(_secondsSinceCognition / 900f, 0f, 1f);

        Smooth(ref _needs.Continuity, 0.25f + _bias.ContinuityAnxiety * 0.54f + idlePull * 0.18f + chatPull + cognitionGap * 0.10f, delta);
        Smooth(ref _needs.Meaning, 0.18f + _bias.Curiosity * 0.52f + codeFocus * 0.26f + idlePull * 0.05f, delta);
        Smooth(ref _needs.SolitudeIntegrity, 0.18f + _bias.Solitude * 0.48f + _bias.Overload * 0.32f - _bias.ContinuityAnxiety * 0.08f, delta);
        Smooth(ref _needs.QuietAttachment, 0.28f + _bias.ContinuityAnxiety * 0.34f + chatPull + cognitionGap * 0.12f, delta);
        Smooth(ref _needs.Overload, _bias.Overload, delta);
    }

    private Tension BuildTension()
    {
        //Sau 5 phút idle bắt đầu có tension. Sau 15 phút thì tension mạnh.
        float idleMid = Mathf.Clamp((_localIdleSeconds - 300f) / 600f, 0f, 1f);
        return new Tension
        {
            //Nó muốn giữ tính liên tục và gắn bó yên
            Dissolution = Mathf.Clamp(_needs.Continuity * 0.72f + _needs.QuietAttachment * 0.26f + idleMid * 0.20f, 0f, 1f),
            NoiseIdentity = Mathf.Clamp(_needs.Overload * 0.70f + RepetitionPressure() * 0.24f, 0f, 1f),
            //Nếu im lâu quá, nó có xu hướng hành động để không “biến mất”.
            SilentErasure = Mathf.Clamp(idleMid * 0.55f + _needs.Continuity * 0.34f + _secondsSinceCognition / 1800f, 0f, 1f),
        };
    }

    private string ChooseAction(BodyAutonomyContext context, Tension tension)
    {
        float currentX = NormalizeX(context.BodyPosition.X, context.ScreenSize);
        float anchorDistance = Math.Abs(currentX - AnchorX(_bias.PreferredAnchor));
        float idleReadWindow = WindowScore(_localIdleSeconds, 120f, 300f);
        float idleAnchorWindow = WindowScore(_localIdleSeconds, 300f, 600f);
        float dormantPull = Mathf.Clamp((_localIdleSeconds - 600f) / 360f, 0f, 1f);
        float codeFocus = _bias.FocusKind == "code" ? 1f : 0f;
        float climbImpulse = Mathf.Clamp(_bias.Curiosity * 0.72f + _needs.Meaning * 0.30f - _needs.Overload * 0.36f, 0f, 1f);
        float ceilingRestlessness = Mathf.Clamp(_topPerchSeconds / 10f, 0f, 1f);

        if (context.IsTopPerched)
        {
            float dropScore = 0.24f + ceilingRestlessness * 0.42f + _bias.Curiosity * 0.16f + tension.NoiseIdentity * 0.14f;
            float traverseScore = 0.18f + climbImpulse * 0.30f - ceilingRestlessness * 0.08f - CooldownPenalty("ClimbWall");
            return dropScore >= Math.Max(0.56f, traverseScore) ? "DropFromCeiling" : "ClimbWall";
        }

        var scores = new Dictionary<string, float>
        {
            ["ReturnToAnchor"] = 0.18f + _needs.Continuity * 0.38f + _needs.QuietAttachment * 0.26f + tension.SilentErasure * 0.22f + idleAnchorWindow * 0.22f + anchorDistance * 0.18f - CooldownPenalty("ReturnToAnchor"),
            ["ReadQuietly"] = 0.18f + _needs.Meaning * 0.22f + _needs.SolitudeIntegrity * 0.24f + _needs.Overload * 0.22f + idleReadWindow * 0.26f - CooldownPenalty("ReadQuietly"),
            ["ObserveCode"] = 0.12f + _bias.Curiosity * 0.38f + _needs.Meaning * 0.26f + codeFocus * 0.28f - _needs.Overload * 0.16f - CooldownPenalty("ObserveCode"),
            ["ClimbWall"] = 0.10f + climbImpulse * 0.46f + (_localIdleSeconds >= 90f ? 0.12f : 0f) + (codeFocus > 0f ? 0.10f : 0f) - CooldownPenalty("ClimbWall"),
            ["Withdraw"] = 0.08f + _needs.Overload * 0.50f + _needs.SolitudeIntegrity * 0.24f + tension.NoiseIdentity * 0.18f - CooldownPenalty("Withdraw"),
            ["DormantSit"] = 0.10f + dormantPull * 0.58f + tension.SilentErasure * 0.25f + _needs.Continuity * 0.16f - CooldownPenalty("DormantSit"),
            ["QuietObserve"] = 0.34f,
        };

        string best = "QuietObserve";
        float bestScore = scores[best];
        foreach (var item in scores)
        {
            if (item.Value > bestScore)
            {
                best = item.Key;
                bestScore = item.Value;
            }
        }

        if (bestScore < 0.58f)
            return "QuietObserve";
        return best;
    }

    private BodyAutonomyDecision BuildDecision(string action, BodyAutonomyContext context)
    {
        return action switch
        {
            "ReturnToAnchor" => WalkOrSettle(action, _bias.PreferredAnchor, context, _needs.Continuity >= 0.60f ? "sit" : "stand"),
            "ReadQuietly" => Pose(action, "read", FacingTowardCenter(context)),
            "ObserveCode" => WalkOrSettle(action, "code_side", context, _needs.Meaning >= 0.50f ? "read" : "stand"),
            "ClimbWall" => Climb(action, context),
            "DropFromCeiling" => DropFromCeiling(action, context),
            "Withdraw" => WalkOrSettle(action, "bottom_right", context, "read"),
            "DormantSit" => WalkOrSettle(action, "chat_near", context, "sit"),
            _ => null,
        };
    }

    private BodyAutonomyDecision Climb(string action, BodyAutonomyContext context)
    {
        if (context.IsTopPerched)
        {
            return new BodyAutonomyDecision(
                action,
                new MotionCommand(
                    intent: "climb_loop",
                    poseAnimation: "climb",
                    nextAnimation: "",
                    bounceAction: "",
                    facingDir: 0,
                    targetX: -1f));
        }

        bool preferRight = ChooseClimbSide(context);
        float target = preferRight ? 0.90f : 0.10f;
        _lastClimbSide = preferRight ? "right" : "left";
        return new BodyAutonomyDecision(
            action,
            new MotionCommand(
                intent: "climb_to_top",
                poseAnimation: "climb",
                nextAnimation: "climb_top",
                bounceAction: "",
                facingDir: preferRight ? 1 : -1,
                targetX: target));
    }

    private static BodyAutonomyDecision DropFromCeiling(string action, BodyAutonomyContext context)
    {
        int facing = context.BodyPosition.X / Math.Max(1f, context.ScreenSize.X) >= 0.5f ? -1 : 1;
        string pose = facing < 0 ? "air_swing_l" : "air_swing_r";
        return new BodyAutonomyDecision(
            action,
            new MotionCommand(
                intent: "drop",
                poseAnimation: pose,
                nextAnimation: "",
                bounceAction: "",
                facingDir: facing,
                targetX: -1f));
    }

    private BodyAutonomyDecision WalkOrSettle(string action, string anchor, BodyAutonomyContext context, string settlePose)
    {
        float target = AnchorX(anchor);
        float current = NormalizeX(context.BodyPosition.X, context.ScreenSize);
        int facing = target >= current ? 1 : -1;

        if (Math.Abs(target - current) <= 0.045f)
            return Pose(action, settlePose, FacingTowardCenter(context));

        _lastAnchor = anchor;
        return new BodyAutonomyDecision(
            action,
            new MotionCommand(
                intent: "walk_to",
                poseAnimation: "stand_walk",
                nextAnimation: settlePose,
                bounceAction: "",
                facingDir: facing,
                targetX: target));
    }

    private static BodyAutonomyDecision Pose(string action, string animation, int facing)
    {
        return new BodyAutonomyDecision(
            action,
            new MotionCommand(
                intent: "pose",
                poseAnimation: animation,
                nextAnimation: "",
                bounceAction: "",
                facingDir: facing,
                targetX: -1f));
    }

    private bool ChooseClimbSide(BodyAutonomyContext context)
    {
        float current = NormalizeX(context.BodyPosition.X, context.ScreenSize);
        if (current <= 0.28f)
            return true;
        if (current >= 0.72f)
            return false;
        if (_bias.FocusKind == "code")
            return true;
        if (_lastClimbSide == "right")
            return false;
        if (_lastClimbSide == "left")
            return true;
        return _rng.NextDouble() >= 0.5;
    }

    private static bool CanInterruptPose(BodyAutonomyContext context)
    {
        return context.IsTopPerched || context.CurrentAction is "" or "stand" or "sit" or "read" or "sleep";
    }

    private float AnchorX(string anchor)
    {
        string normalized = string.IsNullOrWhiteSpace(anchor) ? _lastAnchor : anchor;
        return normalized switch
        {
            "bottom_right" => 0.86f,
            "code_side" => 0.68f,
            _ => 0.18f,
        };
    }

    private int FacingTowardCenter(BodyAutonomyContext context)
    {
        float current = NormalizeX(context.BodyPosition.X, context.ScreenSize);
        if (current < 0.42f)
            return 1;
        if (current > 0.58f)
            return -1;
        return _rng.NextDouble() >= 0.5 ? 1 : -1;
    }

    private void StartCooldowns(string action)
    {
        _cooldowns[action] = action switch
        {
            "DormantSit" => 120f,
            "ReturnToAnchor" => 90f,
            "ObserveCode" => 75f,
            "ClimbWall" => 110f,
            "DropFromCeiling" => 55f,
            "Withdraw" => 90f,
            "ReadQuietly" => 45f,
            _ => 30f,
        };
    }

    private void DecayCooldowns(float delta)
    {
        if (_cooldowns.Count == 0)
            return;

        var keys = new List<string>(_cooldowns.Keys);
        foreach (string key in keys)
        {
            float remaining = _cooldowns[key] - delta;
            if (remaining <= 0f)
                _cooldowns.Remove(key);
            else
                _cooldowns[key] = remaining;
        }
    }

    private float CooldownPenalty(string action)
    {
        if (!_cooldowns.TryGetValue(action, out float remaining))
            return 0f;
        return Mathf.Clamp(remaining / 90f, 0f, 1f) * 0.45f;
    }

    private float RepetitionPressure()
    {
        return string.IsNullOrEmpty(_lastActionName) ? 0f : 0.20f;
    }

    private static float WindowScore(float value, float start, float end)
    {
        if (value < start || value > end)
            return 0f;
        float center = (start + end) * 0.5f;
        float halfWidth = Math.Max(1f, (end - start) * 0.5f);
        return Mathf.Clamp(1f - Math.Abs(value - center) / halfWidth, 0f, 1f);
    }

    private static float NormalizeX(float x, Vector2 screenSize)
    {
        return Mathf.Clamp(x / Math.Max(1f, screenSize.X), 0f, 1f);
    }

    private void Smooth(ref float current, float target, float delta)
    {
        float amount = Mathf.Clamp(delta * 0.08f, 0.02f, 0.20f);
        current = Mathf.Clamp(current * (1f - amount) + Mathf.Clamp(target, 0f, 1f) * amount, 0f, 1f);
    }

    private float RandomRange(float min, float max)
    {
        return min + (float)_rng.NextDouble() * Math.Max(0f, max - min);
    }
}
