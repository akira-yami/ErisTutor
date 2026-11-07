using Godot;
using System;
using System.Reflection;

// Offline checks against the real Godot menu and viewport transforms; no backend.
public partial class Live2DGeometryPreview : UdpListener
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private void Set(string name, object value) => typeof(UdpListener).GetField(name, Private)!.SetValue(this, value);
    private object Invoke(string name, params object[] args) => typeof(UdpListener).GetMethod(name, Private)!.Invoke(this, args);

    public override async void _Ready()
    {
        try
        {
            EnableDesktopOverlayWindow = false;
            GetWindow().Size = new Vector2I(1720, 1080);
            var canvas = new CanvasLayer { Name = "CanvasLayer" };
            AddChild(canvas);
            var root = new Control { Name = "Control", Position = new Vector2(8, 0) };
            canvas.AddChild(root);
            var menu = new Panel { Name = "CharacterContextMenu", Visible = false };
            root.AddChild(menu);
            var box = new VBoxContainer { Name = "VBoxContainer" };
            menu.AddChild(box);
            var button = new Button { Text = "Learning Canvas (web) · Java DI", CustomMinimumSize = new Vector2(280, 34) };
            box.AddChild(button);
            int pressed = 0;
            button.Pressed += () => pressed++;
            var emotion = new Panel { Visible = false };
            AddChild(emotion);
            Set("_emotionPanel", emotion);
            Set("_characterContextMenu", menu);
            Set("_isLive2DMode", true);
            Set("_hasLive2DWindowRect", true);
            var body = new CharacterBody2D();
            AddChild(body);
            Set("_body", body);

            foreach (float scale in new[] { 1f, 1.25f, 1.5f })
            {
                GetWindow().ContentScaleFactor = scale;
                await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
                Transform2D toScreen = GetViewport().GetScreenTransform();
                GD.Print($"viewport={GetViewportRect()}, screenTransform={toScreen}");
                Vector2 modelStart = toScreen * new Vector2(900, 60);
                Vector2 modelEnd = toScreen * new Vector2(1200, 600);
                var rect = new Rect2I((Vector2I)modelStart, (Vector2I)(modelEnd - modelStart));
                Set("_live2DWindowRectScreen", rect);
                foreach (bool staleCursor in new[] { false, true })
                {
                    Vector2 cursor = staleCursor ? toScreen * new Vector2(-500, 100) : toScreen * new Vector2(1000, 200);
                    Invoke("OpenContextMenuFromLive2D", cursor.X, cursor.Y);
                    await ToSignal(GetTree(), SceneTree.SignalName.ProcessFrame);
                    if (menu.GlobalPosition.X < 850 || menu.GlobalPosition.X > 1200)
                        throw new Exception($"Menu detached: scale={scale}, stale={staleCursor}, pos={menu.GlobalPosition}");
                    if (menu.Size.X < 292)
                        throw new Exception("Menu does not contain its button width");
                    Vector2 point = button.GetGlobalRect().GetCenter();
                    if (!(bool)Invoke("IsMouseOverInteractiveOverlay", point))
                        throw new Exception("Menu button does not reserve Godot mouse capture");
                    int before = pressed;
                    GetViewport().PushInput(new InputEventMouseMotion { Position = point }, true);
                    GetViewport().PushInput(new InputEventMouseButton { Position = point, ButtonIndex = MouseButton.Left, Pressed = true }, true);
                    GetViewport().PushInput(new InputEventMouseButton { Position = point, ButtonIndex = MouseButton.Left, Pressed = false }, true);
                    if (pressed != before + 1)
                        throw new Exception("Menu button did not receive exactly one click");
                    Invoke("SyncLive2DBodyAnchor");
                    if (Mathf.Abs(body.GlobalPosition.X - 1050f) > 2f)
                        throw new Exception("Body anchor differs from renderer rect");
                    GD.Print($"PASS scale={scale}, stale={staleCursor}, menu={menu.GlobalPosition}, size={menu.Size}");
                }
            }
            GetTree().Quit();
        }
        catch (Exception ex) { GD.PrintErr(ex); GetTree().Quit(1); }
    }

    public override void _Process(double delta) { }
    public override void _Input(InputEvent input) => base._Input(input);
    public override void _UnhandledInput(InputEvent input) => base._UnhandledInput(input);
    public override void _ExitTree() { }
}
