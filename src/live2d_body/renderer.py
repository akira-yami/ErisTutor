# -*- coding: utf-8 -*-
"""Cửa sổ Live2D trong suốt dùng live2d-py + GLFW.

Renderer này cố ý không dùng ``model.Drag``. Model INFJ có bốn parameter mắt
tự đặt tên và mắt phải được rig theo trục X đối xứng, nên Cubism drag mặc định
vừa không hiểu đủ mapping vừa dễ kéo hai con ngươi vào nhau.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_LIVE2D_SCALE,
    MAX_LIVE2D_SCALE,
    MIN_LIVE2D_SCALE,
    Live2DBodyConfig,
    save_persisted_scale,
)
from .asset_playback import NativeAssetPlayback
from .gaze import GazeController, GazeSample
from .layer_visibility import Live2DLayerVisibility
from .parameter_profile import INFJ_PARAMETER_PROFILE
from .stage_state import Live2DStageState
from .udp_receiver import Live2DStageReceiver
from .window_bridge import DesktopWindowBridge, WindowGeometry


_NON_INTERACTIVE_HIT_PART_IDS = frozenset(
    {
        "artmesh_skinning",
        "artmesh0_skinning",
        "1(skinning)",
        "2(skinning)",
        "trong",
    }
)


class Live2DDependencyError(RuntimeError):
    pass


def _load_dependencies() -> tuple[Any, Any]:
    try:
        import glfw  # type: ignore
    except ImportError as exc:
        raise Live2DDependencyError(
            "Thiếu glfw. Chạy install_live2d_body.bat trước."
        ) from exc
    try:
        import live2d.v3 as live2d  # type: ignore
    except ImportError as exc:
        raise Live2DDependencyError(
            "Thiếu live2d-py cho Cubism 3+. Chạy install_live2d_body.bat trước."
        ) from exc
    return glfw, live2d


class Live2DRenderer:
    def __init__(
        self,
        config: Live2DBodyConfig,
        state: Live2DStageState,
        receiver: Live2DStageReceiver,
    ) -> None:
        self.config = config
        self.state = state
        self.receiver = receiver
        self.available_parameters: set[str] = set()
        self._parameter_indices: dict[str, int] = {}
        self._dragging_window = False
        self._drag_origin_cursor_screen = (0.0, 0.0)
        self._drag_origin_window = (0, 0)
        self._runtime_scale = float(config.scale)
        self._mouse_passthrough_active = False
        self._mouse_passthrough_supported = True
        self._mouse_replay_guard_until = 0.0
        self._bridge = DesktopWindowBridge(godot_pid=config.godot_pid)
        self._gaze = GazeController(
            strength_x=config.eye_strength_x,
            strength_y=config.eye_strength_y,
            right_x_sign=config.eye_right_x_sign,
            inner_eye_ratio=config.eye_inner_ratio,
            head_follow_degrees=config.head_follow_degrees,
            head_follow_left_degrees=config.head_follow_left_degrees,
            head_follow_right_degrees=config.head_follow_right_degrees,
        )
        self._last_geometry: WindowGeometry | None = None
        self._last_speaking = False
        self._last_stage_event_type = ""
        self._mouth_probe_times: list[float] = []
        # --- Idle sway (Neuro-like procedural animation) ---
        self._sway_t: float = 0.0  # thời gian tích luỹ riêng cho sway
        self._asset_playback = NativeAssetPlayback(config.model_path)
        self._layer_visibility = Live2DLayerVisibility()

    def run(self) -> None:
        glfw, live2d = _load_dependencies()
        model_path = Path(self.config.model_path).resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"Không thấy model3.json: {model_path}")

        if not glfw.init():
            raise RuntimeError("GLFW không khởi tạo được OpenGL window")

        window = None
        model = None
        try:
            glfw.window_hint(glfw.RESIZABLE, glfw.TRUE)
            glfw.window_hint(glfw.DECORATED, glfw.TRUE if self.config.decorated else glfw.FALSE)
            glfw.window_hint(glfw.FLOATING, glfw.TRUE if self.config.topmost else glfw.FALSE)
            if self.config.transparent:
                glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)

            window = glfw.create_window(
                self.config.width,
                self.config.height,
                "Eris Live2D",
                None,
                None,
            )
            if not window:
                raise RuntimeError("Không tạo được cửa sổ Live2D")
            glfw.make_context_current(window)
            glfw.swap_interval(1)
            screen_size, window_pos = self._position_window(glfw, window)
            self._show_window(glfw, window)

            live2d.init()
            live2d.glInit()
            model = live2d.LAppModel()
            model.LoadModelJson(str(model_path))
            fb_width, fb_height = glfw.get_framebuffer_size(window)
            model.Resize(max(1, fb_width), max(1, fb_height))
            self._apply_model_scale_and_offset(model, self._runtime_scale)
            model.SetAutoBlinkEnable(True)
            model.SetAutoBreathEnable(True)
            self._parameter_indices = self._read_parameter_indices(model)
            self.available_parameters = set(self._parameter_indices)
            canvas_size = self._safe_model_call(model, "GetCanvasSize")
            canvas_pixel_size = self._safe_model_call(model, "GetCanvasSizePixel")

            self._install_callbacks(glfw, window, model)
            # Cửa sổ Live2D là 520x900 nhưng phần model thật chỉ chiếm một phần.
            # Bật click-through mặc định; vòng render sẽ chỉ tắt nó khi con trỏ
            # thực sự nằm trên drawable/hit-part của model.
            self._set_mouse_passthrough(glfw, window, True)
            self._report_geometry(glfw, window, force=True)
            if self.config.z_order_sync:
                self._bridge.keep_live2d_below_godot(glfw, window)
            self.receiver.send_avatar_ready()
            print(
                f"[Live2D] Đã mở {model_path.name} | UDP {self.config.listen_host}:{self.config.listen_port}",
                flush=True,
            )
            print(
                "[Live2D] Window "
                f"pos={window_pos} size=({self.config.width},{self.config.height}) "
                f"fb=({fb_width},{fb_height}) screen={screen_size} "
                f"canvas={canvas_size} canvas_px={canvas_pixel_size} "
                f"scale={self.config.scale:.2f} offset=({self.config.offset_x:.2f},{self.config.offset_y:.2f}) "
                f"transparent={self.config.transparent} decorated={self.config.decorated} topmost={self.config.topmost}",
                flush=True,
            )
            self._print_model_profile(model_path)

            last = time.monotonic()
            fps_count = 0
            fps_at = last
            maintenance_at = 0.0
            window_visible = True
            while not glfw.window_should_close(window):
                # UDP control không phát GLFW event, vì vậy poll ngắn rồi tự kiểm tra
                # visibility. Khi ẩn, process vẫn giữ model/GPU context nhưng không vẽ.
                glfw.poll_events()
                if self.receiver.shutdown_requested:
                    glfw.set_window_should_close(window, True)
                    continue

                desired_visible = self.receiver.desired_visible
                if desired_visible != window_visible:
                    if desired_visible:
                        self._show_window(glfw, window)
                        self._report_geometry(glfw, window, force=True)
                        if self.config.z_order_sync:
                            self._bridge.keep_live2d_below_godot(glfw, window)
                        print("[Live2D] renderer resumed (resident process)", flush=True)
                    else:
                        self._dragging_window = False
                        try:
                            glfw.hide_window(window)
                        except Exception:
                            pass
                        print("[Live2D] renderer hidden (resident process)", flush=True)
                    window_visible = desired_visible

                requested_geometry = self.receiver.requested_window_geometry
                if requested_geometry is not None:
                    current_geometry = self._bridge.geometry(glfw, window)
                    target_geometry = WindowGeometry(
                        requested_geometry["x"],
                        requested_geometry["y"],
                        requested_geometry["width"],
                        requested_geometry["height"],
                    )
                    if target_geometry != current_geometry:
                        if (
                            current_geometry.width != target_geometry.width
                            or current_geometry.height != target_geometry.height
                        ):
                            glfw.set_window_size(
                                window,
                                target_geometry.width,
                                target_geometry.height,
                            )
                        glfw.set_window_pos(window, target_geometry.x, target_geometry.y)
                        self._report_geometry(glfw, window, force=True)

                scale_delta = self.receiver.consume_scale_delta()
                if scale_delta != 0.0:
                    self._apply_scale_delta(model, scale_delta)
                target_scale = self.receiver.consume_scale()
                if target_scale is not None:
                    self._apply_scale_delta(model, target_scale - self._runtime_scale)

                now = time.monotonic()
                dt = min(0.10, max(0.0, now - last))
                last = now
                snapshot = self.state.advance(dt, now=now)
                audio_start_edge = bool(
                    snapshot.speaking
                    and snapshot.last_event_type in {"audio_start", "audio_playback_started"}
                    and snapshot.last_event_type != self._last_stage_event_type
                )
                if snapshot.speaking and (not self._last_speaking or audio_start_edge):
                    # V62: probe cũ bám event ``speaking`` sớm hơn audio thật, nên
                    # có thể chụp đúng leading-silence rồi kết luận "miệng chết".
                    # Re-arm ở audio start và lấy ba mẫu để thấy cả nhịp giữa câu.
                    self._mouth_probe_times = [now + 0.18, now + 0.45, now + 0.90]
                if snapshot.speaking and self._mouth_probe_times and now >= self._mouth_probe_times[0]:
                    print(
                        "[Live2D][Mouth] "
                        f"ratio={snapshot.mouth_open_ratio:.3f} "
                        f"form={float(snapshot.parameters.get('ParamMouthForm', 0.0)):.3f} "
                        f"open={float(snapshot.parameters.get('ParamMouthOpenY', -1.0)):.3f} "
                        f"cue={snapshot.speech_cue}",
                        flush=True,
                    )
                    self._mouth_probe_times.pop(0)
                if not snapshot.speaking:
                    self._mouth_probe_times.clear()
                self._last_speaking = snapshot.speaking
                self._last_stage_event_type = snapshot.last_event_type
                if not window_visible:
                    # 20 Hz control check, gần như không tốn render CPU/GPU.
                    time.sleep(0.05)
                    continue

                self._update_window_drag(glfw, window)
                geometry = self._bridge.geometry(glfw, window)
                gaze = self._update_gaze(geometry, dt)

                self._update_model_frame(model, snapshot, gaze, dt)

                # Đừng để cả rectangle Live2D ăn chuột. Khi cursor ở vùng alpha rỗng,
                # click đi xuyên xuống Godot/app phía dưới; chỉ model thật mới nhận
                # right-click context menu và middle-drag.
                self._update_mouse_passthrough(glfw, window, model, geometry)

                if self.config.transparent:
                    live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
                else:
                    live2d.clearBuffer(0.08, 0.08, 0.10, 1.0)
                model.Draw()
                glfw.swap_buffers(window)

                if now >= maintenance_at:
                    self._report_geometry(glfw, window)
                    if self.config.z_order_sync:
                        self._bridge.keep_live2d_below_godot(glfw, window)
                    maintenance_at = now + 0.20

                fps_count += 1
                if now - fps_at >= 1.0:
                    if self.config.decorated:
                        glfw.set_window_title(
                            window,
                            f"Eris Live2D | {fps_count} FPS | {snapshot.emotion}",
                        )
                    fps_count = 0
                    fps_at = now
        finally:
            self.receiver.close()
            try:
                if model is not None:
                    model.StopAllMotions()
            except Exception:
                pass
            try:
                live2d.dispose()
            except Exception:
                pass
            if window is not None:
                try:
                    glfw.destroy_window(window)
                except Exception:
                    pass
            glfw.terminate()

    def _update_model_frame(self, model: Any, snapshot: Any, gaze: GazeSample, dt: float) -> None:
        """Seed a fresh baseline, let native assets/physics act, then apply audio ownership."""
        self._asset_playback.advance(model, snapshot)
        self._sway_t += dt
        sway = self._compute_idle_sway(self._sway_t, snapshot.speaking)
        # Reset every expression-owned channel, including absent stage values. Native
        # Add/Multiply expressions must never reuse their own previous-frame result.
        parameters = {
            "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
            "ParamBrowLAngle": 0.0, "ParamBrowRAngle": 0.0,
            "ParamMouthForm": 0.0,
            "ParamErisMouthCornerL": 0.0, "ParamErisMouthCornerR": 0.0,
            "ParamErisLipPress": 0.0, "ParamErisTongue": 0.0,
            **snapshot.parameters,
        }
        parameters["ParamAngleX"] = parameters.get("ParamAngleX", 0.0) + gaze.head_x_degrees + sway["angle_x"]
        parameters["ParamAngleZ"] = parameters.get("ParamAngleZ", 0.0) + sway["angle_z"]
        for parameter_id, value in parameters.items():
            if parameter_id in self.available_parameters:
                model.SetParameterValue(parameter_id, INFJ_PARAMETER_PROFILE.sanitize(parameter_id, float(value)))
        self._apply_gaze(model, gaze, bias_x=snapshot.gaze_bias_x, bias_y=snapshot.gaze_bias_y)
        # LAppModel setters already save in this installed wrapper. Save explicitly
        # for bindings exposing the lower-level native model too; Update loads these.
        core = getattr(model, "_model", model)
        save = getattr(core, "SaveParameters", None)
        if callable(save):
            save()
        model.Update()
        # Native motions temporarily override cursor gaze/head. Do not reapply them.
        # Audio owns the final mouth channels; native expressions retain mouth form.
        opening = INFJ_PARAMETER_PROFILE.clamp_mouth_open(
            snapshot.parameters.get("ParamMouthOpenY", 0.0) if snapshot.speaking else 0.0
        )
        if "ParamMouthOpenY" in self.available_parameters:
            model.SetParameterValue("ParamMouthOpenY", opening)
        if self._asset_playback.jaw_linked_tongue:
            if "ParamErisMouthCornerL" in self.available_parameters:
                # This ID is now the mouth shape axis, rather than an expression's
                # left corner. Fade it in with the jaw so a closed lip stays stable.
                shape = snapshot.mouth_shape * min(1.0, opening / .20)
                model.SetParameterValue("ParamErisMouthCornerL", max(0.0, min(1.0, shape)))
            if "ParamErisTongue" in self.available_parameters:
                model.SetParameterValue("ParamErisTongue", INFJ_PARAMETER_PROFILE.tongue_from_jaw(opening))
        elif snapshot.speaking and "ParamErisTongue" in self.available_parameters:
            # Legacy rigs use an external tongue overlay. Retract it during speech.
            index = self._parameter_indices.get("ParamErisTongue")
            if index is not None:
                tongue = float(model.GetParameterValue(index))
                progress = max(0.0, min(1.0, (opening - 0.12) / 0.24))
                retract = progress * progress * (3.0 - 2.0 * progress)
                model.SetParameterValue("ParamErisTongue", tongue * (1.0 - retract))
        if self._asset_playback.terminal:
            for parameter_id in ("ParamErisMouthCornerL", "ParamErisMouthCornerR", "ParamErisLipPress", "ParamErisTongue"):
                if parameter_id in self.available_parameters:
                    model.SetParameterValue(parameter_id, 0.0)
        self._apply_eyelids(model, left_scale=snapshot.eye_open_left * gaze.left_eye_open_scale, right_scale=snapshot.eye_open_right * gaze.right_eye_open_scale)
        # Chỉ can thiệp vào Part có ID khớp model thật; ArtMesh không được
        # giả định là Part và không được ẩn cả mặt để mô phỏng hiệu ứng.
        self._layer_visibility.apply(
            model,
            emotion=snapshot.emotion,
            weight=snapshot.explicit_emotion_weight,
            terminal=self._asset_playback.terminal,
        )

    def _update_gaze(self, geometry: WindowGeometry, dt: float) -> GazeSample:
        if not self.config.eye_tracking:
            cursor = None
        else:
            cursor = self._bridge.cursor_screen()
        face = (
            geometry.x + geometry.width * 0.5,
            geometry.y + geometry.height * self.config.face_anchor_y,
        )
        return self._gaze.update(
            cursor_screen=cursor,
            face_screen=face,
            span_x=max(180.0, geometry.width * 0.85),
            span_y=max(180.0, geometry.height * 0.38),
            dt=dt,
        )

    def _apply_gaze(
        self,
        model: Any,
        gaze: GazeSample,
        *,
        bias_x: float = 0.0,
        bias_y: float = 0.0,
    ) -> None:
        # ``bias_x`` dùng trục màn hình chung: âm nhìn trái, dương nhìn phải.
        # Chuyển nó sang hai trục riêng của model, vẫn giữ mắt phía trong có
        # biên nhỏ hơn để idle glance không tạo cảm giác lé.
        screen_bias_x = max(-0.16, min(0.16, float(bias_x)))
        vertical_bias = max(-0.12, min(0.12, float(bias_y)))
        if screen_bias_x >= 0.0:
            left_bias = screen_bias_x
            right_bias = -screen_bias_x * self.config.eye_inner_ratio
        else:
            left_bias = screen_bias_x * self.config.eye_inner_ratio
            right_bias = -screen_bias_x

        eye_values = {
            "eyeballxl": gaze.left_x + left_bias,
            "eyeballxr": gaze.right_x + right_bias,
            "eyebally": gaze.left_y + vertical_bias,
            "eyeballyr": gaze.right_y + vertical_bias,
        }
        if "ParamEyeBallX" in self.available_parameters:
            eye_values["ParamEyeBallX"] = gaze.left_x
        if "ParamEyeBallY" in self.available_parameters:
            eye_values["ParamEyeBallY"] = gaze.left_y
        for parameter_id, value in eye_values.items():
            if parameter_id in self.available_parameters:
                model.SetParameterValue(
                    parameter_id,
                    INFJ_PARAMETER_PROFILE.sanitize(parameter_id, float(value)),
                )

    def _apply_eyelids(self, model: Any, *, left_scale: float, right_scale: float) -> None:
        """Giữ auto-blink nhưng giới hạn độ mở mí theo mood và hướng nhìn."""
        self._scale_eye_parameter(model, "ParamEyeLOpen", left_scale)
        self._scale_eye_parameter(model, "ParamEyeROpen", right_scale)

    def _scale_eye_parameter(self, model: Any, parameter_id: str, scale: float) -> None:
        """Cap native eyelids at the stage's absolute opening for this frame.

        Native expressions and the stage both describe the mood's opening.
        Multiplying them squares that mood (e.g. teasing .72 becomes .5184).
        A cap keeps the smaller blink value without applying the same mood twice.

        ``GetParameter()`` có thể trả một object snapshot. Cache object đó từ lúc
        load model rồi đọc ``.value`` ở các frame sau sẽ ghi đè auto-blink bằng
        giá trị cũ (thường là 1.0), khiến nhân vật mở mắt như đang trực ca đêm.
        Vì vậy chỉ cache index và đọc lại bằng ``GetParameterValue(index)`` sau
        ``model.Update()``.
        """

        index = self._parameter_indices.get(parameter_id)
        if index is None:
            return
        try:
            current = float(model.GetParameterValue(index))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            # Fallback cho build live2d-py cũ: lấy object mới ở chính frame này,
            # tuyệt đối không dùng object đã cache từ lúc load model.
            try:
                current = float(model.GetParameter(index).value)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                return
        normalized_scale = max(0.0, min(1.0, float(scale)))
        target = INFJ_PARAMETER_PROFILE.clamp_eye_open(min(current, normalized_scale))
        model.SetParameterValue(parameter_id, target)

    def _report_geometry(self, glfw: Any, window: Any, *, force: bool = False) -> None:
        geometry = self._bridge.geometry(glfw, window)
        if not force and geometry == self._last_geometry:
            return
        self._last_geometry = geometry
        self.receiver.send_window_geometry(
            screen_x=geometry.x,
            screen_y=geometry.y,
            width=geometry.width,
            height=geometry.height,
        )

    def _print_model_profile(self, model_path: Path) -> None:
        try:
            payload = json.loads(model_path.read_text(encoding="utf-8-sig"))
        except Exception:
            payload = {}
        refs = payload.get("FileReferences", {}) if isinstance(payload, dict) else {}
        expressions = refs.get("Expressions", []) if isinstance(refs, dict) else []
        motions = refs.get("Motions", {}) if isinstance(refs, dict) else {}
        custom_eye = all(
            item in self.available_parameters
            for item in ("eyeballxl", "eyeballxr", "eyebally", "eyeballyr")
        )
        print(
            "[Live2D] Model profile: "
            f"parameters={len(self.available_parameters)} "
            f"head={'X/Z' if {'ParamAngleX', 'ParamAngleZ'} <= self.available_parameters else 'partial'} "
            f"eyes={'custom mirrored pair' if custom_eye else 'standard/partial'} "
            f"mouth={'yes' if 'ParamMouthOpenY' in self.available_parameters else 'no'} "
            f"physics={'yes' if isinstance(refs, dict) and bool(refs.get('Physics')) else 'no'} "
            f"expressions={len(expressions) if isinstance(expressions, list) else 0} "
            f"motion_groups={len(motions) if isinstance(motions, dict) else 0}",
            flush=True,
        )

    @staticmethod
    def _read_parameter_indices(model: Any) -> dict[str, int]:
        indices: dict[str, int] = {}
        try:
            for index in range(int(model.GetParameterCount())):
                parameter = model.GetParameter(index)
                parameter_id = str(getattr(parameter, "id", "") or "")
                if parameter_id:
                    indices[parameter_id] = index
        except Exception as exc:
            print(f"[Live2D][WARN] Không đọc được parameter list: {exc}", flush=True)
        return indices

    @staticmethod
    def _safe_model_call(model: Any, method_name: str) -> Any:
        try:
            return getattr(model, method_name)()
        except Exception as exc:
            return f"unavailable:{exc}"

    def _position_window(self, glfw: Any, window: Any) -> tuple[tuple[int, int], tuple[int, int]]:
        screen_size = (0, 0)
        window_pos = (0, 0)
        try:
            target_x = self.config.x
            target_y = self.config.y

            monitors = None
            try:
                monitors = glfw.get_monitors()
            except Exception:
                monitors = None

            chosen_monitor = None
            if monitors and (target_x is not None or target_y is not None):
                best_dist = float("inf")
                for m in monitors:
                    try:
                        mx, my = glfw.get_monitor_pos(m)
                        mode = glfw.get_video_mode(m)
                        mw = int(mode.size.width)
                        mh = int(mode.size.height)
                    except Exception:
                        continue

                    inside_x = (target_x is None) or (mx <= target_x <= mx + mw)
                    inside_y = (target_y is None) or (my <= target_y <= my + mh)
                    if inside_x and inside_y:
                        chosen_monitor = m
                        break

                    dx = 0
                    if target_x is not None:
                        if target_x < mx:
                            dx = mx - target_x
                        elif target_x > mx + mw:
                            dx = target_x - (mx + mw)
                    dy = 0
                    if target_y is not None:
                        if target_y < my:
                            dy = my - target_y
                        elif target_y > my + mh:
                            dy = target_y - (my + mh)
                    dist = dx * dx + dy * dy
                    if dist < best_dist:
                        best_dist = dist
                        chosen_monitor = m

            if chosen_monitor is None:
                try:
                    chosen_monitor = glfw.get_primary_monitor()
                except Exception:
                    chosen_monitor = None
                if chosen_monitor is None and monitors:
                    chosen_monitor = monitors[0]

            if chosen_monitor is not None:
                mx, my = glfw.get_monitor_pos(chosen_monitor)
                mode = glfw.get_video_mode(chosen_monitor)
                screen_width = int(mode.size.width)
                screen_height = int(mode.size.height)
                screen_size = (screen_width, screen_height)

                max_x = max(mx, mx + screen_width - self.config.width)
                if self.config.x is not None:
                    x = max(mx, min(int(self.config.x), max_x))
                else:
                    x = mx + max(0, screen_width - self.config.width - 30)

                max_y = max(my, my + screen_height - self.config.height)
                if self.config.y is not None:
                    y = max(my, min(int(self.config.y), max_y))
                else:
                    y = my + max(0, screen_height - self.config.height - 70)
            else:
                x = self.config.x if self.config.x is not None else 0
                y = self.config.y if self.config.y is not None else 0

            glfw.set_window_pos(window, int(x), int(y))
            window_pos = (int(x), int(y))
        except Exception:
            pass
        return screen_size, window_pos

    @staticmethod
    def _show_window(glfw: Any, window: Any) -> None:
        try:
            glfw.show_window(window)
        except Exception:
            pass
        # Không focus cưỡng bức. Việc giật focus vừa khó chịu vừa đẩy Live2D lên
        # trên bubble/context menu Godot trong topmost band.

    def _set_mouse_passthrough(self, glfw: Any, window: Any, enabled: bool) -> bool:
        """Toggle GLFW mouse passthrough without changing transparency/rendering."""
        if not self._mouse_passthrough_supported:
            return False
        if self._mouse_passthrough_active == bool(enabled):
            return True

        attrib = getattr(glfw, "MOUSE_PASSTHROUGH", None)
        setter = getattr(glfw, "set_window_attrib", None)
        if attrib is None or setter is None:
            self._mouse_passthrough_supported = False
            print(
                "[Live2D][WARN] GLFW build không có MOUSE_PASSTHROUGH; "
                "vùng trong suốt của cửa sổ có thể vẫn chặn chuột.",
                flush=True,
            )
            return False
        try:
            setter(window, attrib, glfw.TRUE if enabled else glfw.FALSE)
            self._mouse_passthrough_active = bool(enabled)
            return True
        except Exception as exc:
            self._mouse_passthrough_supported = False
            print(f"[Live2D][WARN] Không đổi được mouse passthrough: {exc}", flush=True)
            return False

    def _update_mouse_passthrough(
        self,
        glfw: Any,
        window: Any,
        model: Any,
        geometry: WindowGeometry,
    ) -> None:
        if not self.config.transparent or not self._mouse_passthrough_supported:
            return

        # Đang middle-drag phải giữ capture đến mouse-up, kể cả cursor rời model.
        if self._dragging_window:
            self._set_mouse_passthrough(glfw, window, False)
            return

        cursor = self._bridge.cursor_screen()
        if cursor is None:
            self._set_mouse_passthrough(glfw, window, False)
            return

        local_x = float(cursor[0] - geometry.x)
        local_y = float(cursor[1] - geometry.y)
        if self._cursor_over_godot_ui(cursor):
            self._set_mouse_passthrough(glfw, window, True)
            return
        if local_x < 0.0 or local_y < 0.0 or local_x >= geometry.width or local_y >= geometry.height:
            self._set_mouse_passthrough(glfw, window, True)
            return

        hit = self._model_hit_test(
            model,
            local_x,
            local_y,
            window_width=geometry.width,
            window_height=geometry.height,
        )
        self._set_mouse_passthrough(glfw, window, not hit)

    def _cursor_over_godot_ui(self, cursor: tuple[float, float]) -> bool:
        receiver = getattr(self, "receiver", None)
        return any(
            x <= cursor[0] < x + width and y <= cursor[1] < y + height
            for x, y, width, height in getattr(receiver, "input_exclusion_rects", ())
        )

    @staticmethod
    def _model_hit_test(
        model: Any,
        x: float,
        y: float,
        *,
        window_width: int | float | None = None,
        window_height: int | float | None = None,
    ) -> bool:
        """Prefer precise drawable hit testing; degrade safely on older live2d-py."""
        try:
            parts = model.HitPart(float(x), float(y))
            if Live2DRenderer._hit_parts_block_mouse(parts):
                return True
        except Exception:
            pass

        # HitArea names commonly used by Cubism samples. This is only a fallback;
        # HitPart above is the normal path and supports precise part-level testing.
        for name in ("Head", "Body"):
            try:
                if model.HitTest(name, float(x), float(y)):
                    return True
            except Exception:
                continue

        return Live2DRenderer._fallback_model_body_hit_test(
            x,
            y,
            window_width=window_width,
            window_height=window_height,
        )

    @staticmethod
    def _hit_parts_block_mouse(parts: Any) -> bool:
        for part in parts or ():
            part_id = str(part).strip().lower()
            if part_id and part_id not in _NON_INTERACTIVE_HIT_PART_IDS:
                return True
        return False

    @staticmethod
    def _fallback_model_body_hit_test(
        x: float,
        y: float,
        *,
        window_width: int | float | None,
        window_height: int | float | None,
    ) -> bool:
        if not window_width or not window_height:
            return False
        width = float(window_width)
        height = float(window_height)
        if width <= 0.0 or height <= 0.0:
            return False
        nx = float(x) / width
        ny = float(y) / height
        if nx < 0.0 or ny < 0.0 or nx > 1.0 or ny > 1.0:
            return False

        def inside_ellipse(cx: float, cy: float, rx: float, ry: float) -> bool:
            dx = (nx - cx) / rx
            dy = (ny - cy) / ry
            return dx * dx + dy * dy <= 1.0

        return (
            inside_ellipse(0.50, 0.23, 0.20, 0.17)
            or inside_ellipse(0.50, 0.53, 0.27, 0.34)
            or inside_ellipse(0.50, 0.78, 0.23, 0.18)
        )

    def _update_window_drag(self, glfw: Any, window: Any) -> None:
        if not self._dragging_window:
            return
        cursor = self._bridge.cursor_screen()
        if cursor is None:
            return
        dx = cursor[0] - self._drag_origin_cursor_screen[0]
        dy = cursor[1] - self._drag_origin_cursor_screen[1]
        glfw.set_window_pos(
            window,
            int(self._drag_origin_window[0] + dx),
            int(self._drag_origin_window[1] + dy),
        )

    def _forward_stale_mouse_press(
        self,
        glfw: Any,
        window: Any,
        model: Any,
        button: int,
    ) -> bool:
        """Recover a click that arrived after the cursor already left the model."""
        if not self.config.transparent or not self._mouse_passthrough_supported:
            return False
        now = time.monotonic()
        if now < self._mouse_replay_guard_until or self._dragging_window:
            return False

        cursor = self._bridge.cursor_screen()
        if cursor is None:
            return False
        geometry = self._bridge.geometry(glfw, window)
        local_x = float(cursor[0] - geometry.x)
        local_y = float(cursor[1] - geometry.y)
        if (
            not self._cursor_over_godot_ui(cursor)
            and 0.0 <= local_x < geometry.width
            and 0.0 <= local_y < geometry.height
            and (
                button == glfw.MOUSE_BUTTON_RIGHT
                or self._model_hit_test(
                    model,
                    local_x,
                    local_y,
                    window_width=geometry.width,
                    window_height=geometry.height,
                )
            )
        ):
            return False

        button_name = None
        if button == glfw.MOUSE_BUTTON_LEFT:
            button_name = "left"
        elif button == glfw.MOUSE_BUTTON_RIGHT:
            button_name = "right"
        elif button == glfw.MOUSE_BUTTON_MIDDLE:
            button_name = "middle"
        if button_name is None:
            return False

        # The stale press already landed on the Live2D rectangle. Release the
        # rectangle first, then replay only mouse-down to the underlying app.
        self._set_mouse_passthrough(glfw, window, True)
        self._mouse_replay_guard_until = now + 0.08
        return self._bridge.replay_mouse_button_down(button_name)

    def _compute_head_anchor_offset_y(self, scale: float) -> float:
        """Bù trừ tọa độ Y theo tỉ lệ scale để đỉnh đầu không bị cắt ngang cửa sổ khi phóng to."""
        base_offset_y = getattr(getattr(self, "config", None), "offset_y", -0.02)
        head_compensation = -0.65 * (float(scale) - DEFAULT_LIVE2D_SCALE)
        return float(base_offset_y) + head_compensation

    def _apply_model_scale_and_offset(self, model: Any, scale: float) -> None:
        if model is None:
            return
        target_scale = max(MIN_LIVE2D_SCALE, min(MAX_LIVE2D_SCALE, round(float(scale), 3)))
        target_offset_y = self._compute_head_anchor_offset_y(target_scale)
        base_offset_x = float(getattr(getattr(self, "config", None), "offset_x", 0.0))
        try:
            model.SetScale(target_scale)
            if hasattr(model, "SetOffset"):
                model.SetOffset(base_offset_x, target_offset_y)
        except Exception as exc:
            print(f"[Live2D][WARN] Không cập nhật được scale/offset model: {exc}", flush=True)

    def _apply_scale_delta(self, model: Any, delta: float) -> None:
        target = max(MIN_LIVE2D_SCALE, min(MAX_LIVE2D_SCALE, round(self._runtime_scale + delta, 3)))
        if abs(target - self._runtime_scale) < 1e-4:
            return
        self._runtime_scale = target
        self._apply_model_scale_and_offset(model, self._runtime_scale)
        save_persisted_scale(self._runtime_scale)

    def _install_callbacks(self, glfw: Any, window: Any, model: Any) -> None:
        def on_framebuffer_size(_window: Any, width: int, height: int) -> None:
            if width > 0 and height > 0:
                model.Resize(width, height)
                self._apply_model_scale_and_offset(model, self._runtime_scale)

        def on_key(_window: Any, key: int, _scancode: int, action: int, _mods: int) -> None:
            if action != glfw.PRESS:
                return
            if key == glfw.KEY_ESCAPE:
                glfw.set_window_should_close(window, True)
            elif key == glfw.KEY_R:
                model.StopAllMotions()
                model.ResetPose()
                model.ResetParameters()
                self._runtime_scale = DEFAULT_LIVE2D_SCALE
                self._apply_model_scale_and_offset(model, self._runtime_scale)
                save_persisted_scale(self._runtime_scale)
            elif key in (glfw.KEY_EQUAL, glfw.KEY_KP_ADD):
                self._apply_scale_delta(model, 0.1)
            elif key in (glfw.KEY_MINUS, glfw.KEY_KP_SUBTRACT):
                self._apply_scale_delta(model, -0.1)

        def on_scroll(_window: Any, _xoffset: float, yoffset: float) -> None:
            if yoffset == 0 or self._dragging_window:
                return
            delta = max(-0.15, min(0.15, yoffset * 0.05))
            self._apply_scale_delta(model, delta)

        def on_mouse_button(_window: Any, button: int, action: int, _mods: int) -> None:
            if action == glfw.PRESS and self._forward_stale_mouse_press(glfw, window, model, button):
                return

            if button == glfw.MOUSE_BUTTON_RIGHT and action == glfw.PRESS:
                cursor = self._bridge.cursor_screen()
                if cursor is None:
                    cursor_x, cursor_y = glfw.get_cursor_pos(window)
                    window_x, window_y = glfw.get_window_pos(window)
                    cursor = (window_x + cursor_x, window_y + cursor_y)
                print(f"[Live2D] Chuột phải tại ({int(cursor[0])}, {int(cursor[1])}), đang mở context menu...", flush=True)
                self.receiver.send_context_menu_request(
                    screen_x=int(cursor[0]),
                    screen_y=int(cursor[1]),
                )
                return

            if button != glfw.MOUSE_BUTTON_MIDDLE:
                return
            if action == glfw.PRESS:
                cursor = self._bridge.cursor_screen()
                if cursor is None:
                    local_x, local_y = glfw.get_cursor_pos(window)
                    win_x, win_y = glfw.get_window_pos(window)
                    cursor = (win_x + local_x, win_y + local_y)
                self._dragging_window = True
                self._drag_origin_cursor_screen = cursor
                self._drag_origin_window = glfw.get_window_pos(window)
            elif action == glfw.RELEASE:
                self._dragging_window = False

        glfw.set_framebuffer_size_callback(window, on_framebuffer_size)
        glfw.set_key_callback(window, on_key)
        glfw.set_mouse_button_callback(window, on_mouse_button)
        glfw.set_scroll_callback(window, on_scroll)

    @staticmethod
    def _compute_idle_sway(t: float, speaking: bool) -> dict[str, float]:
        """Sinh dao động sine nhỏ để model trông có sức sống (Neuro-like sway).

        - AngleZ (nghiêng đầu): sóng chậm ~0.18 Hz, biên độ ±1.4 deg idle / ±0.7 deg khi nói.
        - AngleX (xoay trái/phải): sóng siêu chậm ~0.07 Hz, biên độ ±0.8 deg.
          Kết hợp 2 sóng lệch pha nhau tạo chuyển động Lissajous nhỏ — trông
          tự nhiên hơn dao động đơn thuần.
        Khi đang nói, biên độ giảm 50% để không lấn át lipsync.
        """
        # Hệ số biên độ: giảm khi speaking để ưu tiên lipsync
        amp = 0.5 if speaking else 1.0

        # --- AngleZ: drift nghiêng đầu trái/phải ---
        # Hai sóng cộng lại → không lặp đều đặn → trông sinh động hơn
        z = (
            math.sin(2 * math.pi * 0.18 * t) * 1.4
            + math.sin(2 * math.pi * 0.11 * t + 1.1) * 0.5
        ) * amp

        # --- AngleX: drift xoay trái/phải siêu nhỏ ---
        x = (
            math.sin(2 * math.pi * 0.07 * t + 0.7) * 0.8
            + math.sin(2 * math.pi * 0.13 * t + 2.3) * 0.3
        ) * amp

        return {"angle_z": z, "angle_x": x}


def run_renderer(config: Live2DBodyConfig) -> None:
    state = Live2DStageState()
    receiver = Live2DStageReceiver(
        state,
        host=config.listen_host,
        port=config.listen_port,
        backend_host=config.backend_host,
        backend_port=config.backend_port,
        body_host=config.body_host,
        body_port=config.body_port,
    )
    receiver.start()
    renderer = Live2DRenderer(config, state, receiver)
    renderer.run()
