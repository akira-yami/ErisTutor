using Godot;
using System;
using System.Globalization;

public readonly struct MotionCommand
{
    //MotionCommand là gói lệnh đầu vào:
    public MotionCommand(
        string intent,
        string poseAnimation,
        string nextAnimation,
        string bounceAction,
        int facingDir,
        float targetX,
        float impulseX = 0f,
        float impulseY = 0f,
        float clingChance = 0f)
    {
        Intent = intent ?? "";
        PoseAnimation = poseAnimation ?? "stand";
        NextAnimation = nextAnimation ?? "";
        BounceAction = bounceAction ?? "";
        FacingDir = facingDir;
        TargetX = targetX;
        ImpulseX = impulseX;
        ImpulseY = impulseY;
        ClingChance = clingChance;
    }

    public string Intent { get; }
    public string PoseAnimation { get; }
    public string NextAnimation { get; }
    public string BounceAction { get; }
    public int FacingDir { get; }
    public float TargetX { get; }
    public float ImpulseX { get; }
    public float ImpulseY { get; }
    public float ClingChance { get; }
}

public sealed class MotionController
{
    private enum ClimbPhase { None, GoingToWall, Up, Top, TopHold, Down }

    private readonly Action<string> _playSound;
    private readonly float _moveSpeed;
    private readonly float _gravity;
    private readonly float _climbSpeed;

    private float _moveDir = 1f; //hướng di chuyển vật lý, -1 trái, 1 phải.
    private int _facingDir = -1; //hướng mặt nhân vật.
    private bool _isWalking;
    private string _currentAction = "stand";
    private float _velocityX; //vận tốc khi rơi/bị ném
    private float _velocityY; //vận tốc khi rơi/bị ném
    private bool _isFalling;
    private Vector2 _spriteHalfSize;
    private Vector2 _bubbleBasePos;
    private string _nextAction = "";
    private string _bounceAction = ""; //animation/phản ứng khi đụng tường
    private float _targetX = float.NaN;
    private float _clingArmedSeconds; //cửa sổ còn cho phép bám tường sau cú ném
    private bool _clingAttempted; //đã roll bám cho cú ném hiện tại chưa

    private ClimbPhase _climbPhase = ClimbPhase.None;
    private float _climbUpWallX;
    private float _climbDownWallX;
    private float _climbTopTargetX = float.NaN;

    public MotionController(Action<string> playSound, float moveSpeed = 60f, float gravity = 980f, float climbSpeed = 50f)
    {
        _playSound = playSound;
        _moveSpeed = moveSpeed;
        _gravity = gravity;
        _climbSpeed = climbSpeed;
    }

    public string CurrentAction => _currentAction;
    public bool IsWalking => _isWalking;
    public bool IsClimbing => _climbPhase != ClimbPhase.None;
    public bool IsTopPerched => _climbPhase == ClimbPhase.TopHold;
    public bool IsThrown => Math.Abs(_velocityX) > 0.1f && _isFalling;
    public bool IsBusy => _isWalking || IsThrown || (_climbPhase != ClimbPhase.None && _climbPhase != ClimbPhase.TopHold);

    public void Configure(Vector2 spriteHalfSize, Vector2 bubbleBasePos)
    {
        _spriteHalfSize = spriteHalfSize;
        _bubbleBasePos = bubbleBasePos;
    }

    public void PlaceOnFloor(ref Vector2 pos, Vector2 screenSize)
    {
        pos.Y = screenSize.Y - _spriteHalfSize.Y;
        _velocityX = 0f;
        _velocityY = 0f;
        _isFalling = false;
    }

    //Đây là lõi runtime.
    public void Step(ref Vector2 pos, Vector2 screenSize, float delta)
    {
        //Tức là khi leo, controller bỏ qua gravity bình thường và dùng logic leo riêng.
        if (_climbPhase != ClimbPhase.None)
        {
            UpdateClimbMovement(ref pos, screenSize, delta);
            return;
        }

        _velocityY += _gravity * delta; //tăng vận tốc rơi theo gravity
        pos.X += _velocityX * delta;
        pos.Y += _velocityY * delta; //cộng vận tốc vào vị trí

        bool clingArmed = _clingArmedSeconds > 0f;
        float airDecay = clingArmed ? 120f : 260f; //đang armed thì giữ đà ngang lâu hơn để với tới tường
        _velocityX = Mathf.MoveToward(_velocityX, 0f, airDecay * delta); //giảm dần vận tốc ngang
        if (clingArmed)
            _clingArmedSeconds = Math.Max(0f, _clingArmedSeconds - delta);

        if (pos.Y >= screenSize.Y - _spriteHalfSize.Y)
        {
            pos.Y = screenSize.Y - _spriteHalfSize.Y;
            float floorDecay = clingArmed ? 200f : 420f; //tiếp đất sát chân tường vẫn trượt tới được mép
            _velocityX = Mathf.MoveToward(_velocityX, 0f, floorDecay * delta);
            _velocityY = 0f;
            _isFalling = false;
            if (!_isWalking && _climbPhase == ClimbPhase.None && (_currentAction == "air_swing_l" || _currentAction == "air_swing_r"))
                _currentAction = "stand";
        }
        else
        {
            _isFalling = true;
        }

        if (pos.Y <= _spriteHalfSize.Y)
        {
            pos.Y = _spriteHalfSize.Y;
            _velocityY = 0f;
        }

        if (!_isWalking)
        {
            HandleAirborneWallContact(ref pos, screenSize);
            return;
        }

        pos.X += _moveSpeed * _moveDir * delta;

        if (!float.IsNaN(_targetX))
        {
            bool reached = (_moveDir > 0f && pos.X >= _targetX) || (_moveDir < 0f && pos.X <= _targetX);
            if (reached)
            {
                pos.X = _targetX;
                _targetX = float.NaN;
                if (!string.IsNullOrEmpty(_nextAction))
                {
                    string action = _nextAction;
                    _nextAction = "";
                    ApplyStaticAction(action, 0);
                }
                else
                {
                    ApplyStaticAction("stand", 0);
                }
            }
            return;
        }

        //Nếu không có target, đi tới tường thì gọi:
        if (pos.X >= screenSize.X - _spriteHalfSize.X)
        {
            pos.X = screenSize.X - _spriteHalfSize.X;
            HandleWallHit(nextWalkDir: -1);
        }
        else if (pos.X <= _spriteHalfSize.X)
        {
            pos.X = _spriteHalfSize.X;
            HandleWallHit(nextWalkDir: 1);
        }
    }

    //Nó dịch MotionCommand thành state nội bộ
    public void ApplyCommand(MotionCommand command, Vector2 currentPos, Vector2 screenSize)
    {
        string intent = (command.Intent ?? "").Trim().ToLowerInvariant();
        string pose = NormalizePose(command.PoseAnimation);
        string nextAnimation = NormalizePose(command.NextAnimation);
        string bounceAction = NormalizePose(command.BounceAction, allowEmpty: true);
        int facingDir = command.FacingDir < 0 ? -1 : command.FacingDir > 0 ? 1 : 0;
        float absoluteTargetX = ResolveAbsoluteTargetX(command.TargetX, screenSize);

        if (facingDir != 0)
        {
            _moveDir = facingDir;
            _facingDir = facingDir;
        }

        switch (intent)
        {
            case "walk_to":
                if (float.IsNaN(absoluteTargetX))
                {
                    ApplyStaticAction(pose == "stand_walk" ? "stand" : pose, facingDir);
                    return;
                }
                if (facingDir == 0)
                    facingDir = absoluteTargetX >= currentPos.X ? 1 : -1;
                _moveDir = facingDir;
                _facingDir = facingDir;
                BeginWalk(absoluteTargetX, nextAnimation, bounceAction);
                return;

            case "walk":
                BeginWalk(float.NaN, nextAnimation, bounceAction);
                return;

            case "climb_to_top":
                _targetX = absoluteTargetX;
                _nextAction = "climb_top";
                _bounceAction = "";
                BeginClimb(currentPos, screenSize);
                return;

            case "climb_loop":
                _targetX = float.NaN;
                _nextAction = "";
                _bounceAction = "";
                BeginClimb(currentPos, screenSize);
                return;

            case "drop":
                CancelClimbState();
                _targetX = float.NaN;
                _nextAction = "";
                _bounceAction = "";
                _isWalking = false;
                _currentAction = pose is "air_swing_l" or "air_swing_r"
                    ? pose
                    : (facingDir < 0 ? "air_swing_l" : "air_swing_r");
                _velocityX = 0f;
                _velocityY = 120f;
                _isFalling = true;
                _playSound?.Invoke(_currentAction);
                return;

            case "throw":
                CancelClimbState();
                _targetX = float.NaN;
                _nextAction = "";
                _isWalking = false;
                _currentAction = pose is "air_swing_l" or "air_swing_r"
                    ? pose
                    : (facingDir < 0 ? "air_swing_l" : "air_swing_r");
                _velocityX = command.ImpulseX;
                _velocityY = command.ImpulseY;
                _bounceAction = BuildClingMarker(command.ClingChance);
                _clingArmedSeconds = 4f;
                _clingAttempted = false;
                _isFalling = true;
                _playSound?.Invoke(_currentAction);
                return;

            case "face":
                ApplyStaticAction(pose == "stand_walk" ? "stand" : pose, facingDir);
                return;

            case "pose":
            case "":
            default:
                ApplyStaticAction(pose == "stand_walk" ? "stand" : pose, facingDir);
                return;
        }
    }

    public void ApplySilentPose(string poseAnimation, int facingDir = 0)
    {
        string pose = NormalizePose(poseAnimation);
        int facing = facingDir < 0 ? -1 : facingDir > 0 ? 1 : 0;
        ApplyStaticAction(pose == "stand_walk" ? "stand" : pose, facing, playSound: false);
    }

    public void ApplyVisualState(AnimatedSprite2D avatar, Control bubble, Vector2 bodyPosition, Vector2 screenSize)
    {
        if (_climbPhase == ClimbPhase.GoingToWall)
        {
            if (avatar.Animation != "stand_walk")
                avatar.Play("stand_walk");
            float scaleX = _climbUpWallX > bodyPosition.X ? -1f : 1f;
            ApplyFlip(avatar, bubble, scaleX);
            return;
        }

        if (_climbPhase == ClimbPhase.Up || _climbPhase == ClimbPhase.Down)
        {
            if (avatar.Animation != "climb")
                avatar.Play("climb");
            float wallX = _climbPhase == ClimbPhase.Up ? _climbUpWallX : _climbDownWallX;
            float scaleX = wallX < screenSize.X / 2f ? 1f : -1f;
            ApplyFlip(avatar, bubble, scaleX);
            return;
        }

        if (_climbPhase == ClimbPhase.Top || _climbPhase == ClimbPhase.TopHold)
        {
            if (avatar.Animation != "climb_top")
                avatar.Play("climb_top");
            ApplyFlip(avatar, bubble, TopFacingScale(bodyPosition, screenSize));
            return;
        }

        if (_isFalling)
        {
            if (avatar.Animation != "air" && _currentAction != "air_swing_l" && _currentAction != "air_swing_r")
                avatar.Play("air");
            else if (avatar.Animation != _currentAction && (_currentAction == "air_swing_l" || _currentAction == "air_swing_r"))
                avatar.Play(_currentAction);
            if (_currentAction == "air_swing_l")
                ApplyHorizontalFacing(avatar, bubble, -1);
            else if (_currentAction == "air_swing_r")
                ApplyHorizontalFacing(avatar, bubble, 1);
            return;
        }

        if (avatar.Animation != _currentAction)
            avatar.Play(_currentAction);

        if (_isWalking)
        {
            ApplyHorizontalFacing(avatar, bubble, _moveDir >= 0f ? 1 : -1);
            return;
        }

        if (_currentAction == "air_swing_l")
        {
            ApplyHorizontalFacing(avatar, bubble, -1);
            return;
        }

        if (_currentAction == "air_swing_r")
        {
            ApplyHorizontalFacing(avatar, bubble, 1);
            return;
        }

        ApplyHorizontalFacing(avatar, bubble, _facingDir);
    }

    public void NotifyAnimationFinished()
    {
        if (!string.IsNullOrEmpty(_bounceAction) && _currentAction == _bounceAction && !_isWalking)
        {
            _currentAction = "stand_walk";
            _isWalking = true;
            _playSound?.Invoke("stand_walk");
        }
    }

    private void BeginWalk(float targetX, string nextAction, string bounceAction)
    {
        CancelClimbState();
        _targetX = targetX;
        _nextAction = nextAction;
        _bounceAction = bounceAction;
        _currentAction = "stand_walk";
        _isWalking = true;
        _velocityX = 0f;
        _playSound?.Invoke("stand_walk");
    }

    private void BeginClimb(Vector2 currentPos, Vector2 screenSize)
    {
        CancelClimbState();
        _isWalking = false;
        _currentAction = "climb";
        _velocityX = 0f;

        float wallXRight = screenSize.X - _spriteHalfSize.X;
        float wallXLeft = _spriteHalfSize.X;

        if (wallXRight - currentPos.X <= currentPos.X - wallXLeft)
        {
            _climbUpWallX = wallXRight;
            _climbDownWallX = wallXLeft;
        }
        else
        {
            _climbUpWallX = wallXLeft;
            _climbDownWallX = wallXRight;
        }

        _climbTopTargetX = _nextAction == "climb_top"
            ? (!float.IsNaN(_targetX) ? _targetX : _climbUpWallX)
            : float.NaN;

        _climbPhase = ClimbPhase.GoingToWall;
        _playSound?.Invoke("climb");
    }

    private void ApplyStaticAction(string action, int facingDir, bool playSound = true)
    {
        CancelClimbState();
        _targetX = float.NaN;
        _nextAction = "";
        _bounceAction = "";
        _isWalking = false;
        _velocityX = 0f;
        _currentAction = string.IsNullOrEmpty(action) ? "stand" : action;
        if (facingDir != 0)
            _facingDir = facingDir;
        if (playSound)
            _playSound?.Invoke(_currentAction);
    }

    //Va tường khi đi bộ
    private void HandleWallHit(int nextWalkDir)
    {
        if (!string.IsNullOrEmpty(_bounceAction))
        {
            _moveDir = nextWalkDir;
            _currentAction = _bounceAction;
            _isWalking = false;
            _playSound?.Invoke(_bounceAction);
            return;
        }

        if (!string.IsNullOrEmpty(_nextAction))
        {
            string action = _nextAction;
            _nextAction = "";
            if (action == "stand_walk")
            {
                _moveDir = nextWalkDir;
                _facingDir = nextWalkDir;
                _currentAction = "stand_walk";
                _isWalking = true;
                _playSound?.Invoke("stand_walk");
                return;
            }

            ApplyStaticAction(action, 0);
            return;
        }

        _moveDir = nextWalkDir;
        _facingDir = nextWalkDir;
    }

    private void CancelClimbState()
    {
        _climbPhase = ClimbPhase.None;
        _climbTopTargetX = float.NaN;
        _velocityY = 0f;
        _isFalling = false;
        _clingArmedSeconds = 0f;
        _clingAttempted = false;
    }

    private void HandleAirborneWallContact(ref Vector2 pos, Vector2 screenSize)
    {
        float clingChance = ParseClingChance(_bounceAction);
        bool clingArmed = _clingArmedSeconds > 0f && clingChance > 0f && !_clingAttempted;

        //Trường hợp không phải throw-cling vẫn giữ hành vi cũ: phải đang bay và còn lực ngang.
        if (!clingArmed && (!_isFalling || Math.Abs(_velocityX) < 40f))
            return;

        float rightWall = screenSize.X - _spriteHalfSize.X;
        float leftWall = _spriteHalfSize.X;
        bool hitRight = pos.X >= rightWall;
        bool hitLeft = pos.X <= leftWall;
        if (!hitRight && !hitLeft)
            return;

        pos.X = hitRight ? rightWall : leftWall;
        _facingDir = hitRight ? -1 : 1;
        _velocityX = 0f;

        if (!clingArmed)
            return;

        //Mỗi cú ném chỉ roll bám đúng một lần, ngay khi chạm được tường.
        _clingAttempted = true;
        _clingArmedSeconds = 0f;
        if (GD.Randf() <= clingChance)
        {
            _nextAction = "climb_top";
            _targetX = hitRight ? 0.90f : 0.10f;
            _bounceAction = "";
            BeginClimb(pos, screenSize);
        }
    }

    private void UpdateClimbMovement(ref Vector2 pos, Vector2 screenSize, float delta)
    {
        float ceilingY = _spriteHalfSize.Y;
        float floorY = screenSize.Y - _spriteHalfSize.Y;

        switch (_climbPhase)
        {
            case ClimbPhase.GoingToWall:
                float dx = _climbUpWallX - pos.X;
                pos.X += Mathf.Sign(dx) * _moveSpeed * delta;
                if (Mathf.Abs(dx) < _moveSpeed * delta + 2f)
                {
                    pos.X = _climbUpWallX;
                    _climbPhase = ClimbPhase.Up;
                }
                break;

            case ClimbPhase.Up:
                pos.X = _climbUpWallX;
                pos.Y -= _climbSpeed * delta;
                if (pos.Y <= ceilingY)
                {
                    pos.Y = ceilingY;
                    _climbPhase = ClimbPhase.Top;
                }
                break;

            case ClimbPhase.Top:
                pos.Y = ceilingY;
                float topTargetX = float.IsNaN(_climbTopTargetX) ? _climbDownWallX : _climbTopTargetX;
                float dx2 = topTargetX - pos.X;
                pos.X += Mathf.Sign(dx2) * _moveSpeed * delta;
                if (Mathf.Abs(dx2) < _moveSpeed * delta + 2f)
                {
                    pos.X = topTargetX;
                    if (float.IsNaN(_climbTopTargetX))
                    {
                        _climbPhase = ClimbPhase.Down;
                    }
                    else
                    {
                        _climbPhase = ClimbPhase.TopHold;
                        _currentAction = "climb_top";
                        _nextAction = "";
                        _targetX = float.NaN;
                    }
                }
                break;

            case ClimbPhase.TopHold:
                pos.Y = ceilingY;
                if (!float.IsNaN(_climbTopTargetX))
                    pos.X = _climbTopTargetX;
                _velocityY = 0f;
                _isFalling = false;
                break;

            case ClimbPhase.Down:
                pos.X = _climbDownWallX;
                pos.Y += _climbSpeed * delta;
                if (pos.Y >= floorY)
                {
                    pos.Y = floorY;
                    _climbPhase = ClimbPhase.None;
                    _velocityX = 0f;
                    _velocityY = 0f;
                    _isFalling = false;
                    _currentAction = "stand";
                    _isWalking = false;
                    _climbTopTargetX = float.NaN;
                }
                break;
        }
    }

    private float ResolveAbsoluteTargetX(float targetX, Vector2 screenSize)
    {
        if (targetX < 0f)
            return float.NaN;
        float frac = Mathf.Clamp(targetX, 0f, 1f);
        return Mathf.Clamp(frac * screenSize.X, _spriteHalfSize.X, screenSize.X - _spriteHalfSize.X);
    }

    private static string NormalizePose(string animation, bool allowEmpty = false)
    {
        string value = (animation ?? "").Trim();
        if (string.IsNullOrEmpty(value))
            return allowEmpty ? "" : "stand";
        return value;
    }

    private float TopFacingScale(Vector2 bodyPosition, Vector2 screenSize)
    {
        int topFacing = _facingDir;
        if (_climbPhase == ClimbPhase.Top)
        {
            float topTargetX = float.IsNaN(_climbTopTargetX) ? _climbDownWallX : _climbTopTargetX;
            topFacing = topTargetX >= bodyPosition.X ? 1 : -1;
        }
        else if (_climbPhase == ClimbPhase.TopHold)
        {
            if (Mathf.IsEqualApprox(bodyPosition.X, _spriteHalfSize.X))
                topFacing = 1;
            else if (Mathf.IsEqualApprox(bodyPosition.X, screenSize.X - _spriteHalfSize.X))
                topFacing = -1;
        }

        return topFacing == 1 ? -1f : 1f;
    }

    private void ApplyHorizontalFacing(AnimatedSprite2D avatar, Control bubble, int facingDir)
    {
        _facingDir = facingDir >= 0 ? 1 : -1;
        ApplyFlip(avatar, bubble, _facingDir == 1 ? -1f : 1f);
    }

    private void ApplyFlip(AnimatedSprite2D avatar, Control bubble, float scaleX)
    {
        float scaleY = Mathf.Abs(avatar.Scale.Y);
        if (Mathf.IsZeroApprox(scaleY)) scaleY = 1f;
        avatar.Scale = new Vector2(scaleX * scaleY, scaleY);
        bubble.Scale = new Vector2(scaleX, 1f);
        bubble.Position = new Vector2(_bubbleBasePos.X * scaleX * scaleY, _bubbleBasePos.Y * scaleY);
    }

    private static string BuildClingMarker(float clingChance)
    {
        return "cling:" + Mathf.Clamp(clingChance, 0f, 1f).ToString("0.000", CultureInfo.InvariantCulture);
    }

    private static float ParseClingChance(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || !value.StartsWith("cling:", StringComparison.Ordinal))
            return 0f;
        return float.TryParse(value.Substring(6), NumberStyles.Float, CultureInfo.InvariantCulture, out float parsed)
            ? Mathf.Clamp(parsed, 0f, 1f)
            : 0f;
    }
}
