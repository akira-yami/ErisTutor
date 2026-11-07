# -*- coding: utf-8 -*-
"""Chess.com bridge service wired to the durable engagement runtime."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from runtime.cognition.normalization import normalize_vi
from runtime.engagements import EvidenceBinding
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .contracts import BoardSnapshot, ChessObservation, ChessObservationPhase
from .analyzer import ChessAnalyzer, HybridChessAnalyzer, LocalChessAnalyzer
from .engine import Position, move_san
from .stockfish_adapter import StockfishConfig, StockfishUCIAnalyzer
from .tracker import ChessGameTracker
from .surface import ChessSurfaceState, chess_move_question, chess_turn_relevant, describe_chess_move_vi
from .companion_policy import build_companion_signal


class ChessCompanionService:
    # Contract generic cho StructuredEvidenceProvider. Chat pipeline chỉ biết
    # đây là một nguồn structured state, không cần hard-code ChessCompanionService.
    structured_source_id = "chess_bridge"
    engagement_kind = "chess"

    def __init__(
        self,
        engagement_runtime,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str = "",
        analysis_depth: int = 2,
        direct_move_rate: float = 0.18,
        stockfish_enabled: bool = True,
        stockfish_path: str = "",
        stockfish_threads: int = 1,
        stockfish_hash_mb: int = 96,
        stockfish_multipv: int = 3,
        stockfish_movetime_ms: int = 180,
        stockfish_on_demand_movetime_ms: int = 700,
        stockfish_startup_timeout_seconds: float = 4.0,
        stockfish_analysis_timeout_seconds: float = 2.5,
        analyzer: ChessAnalyzer | None = None,
        max_body_bytes: int = 65536,
        logger: Any | None = None,
        health_registry: SubsystemHealthRegistry | None = None,
        wake_callback: Any | None = None,
        semantic_context_callback: Any | None = None,
        clock=None,
    ) -> None:
        self.engagement_runtime = engagement_runtime
        self.host = str(host or "127.0.0.1")
        self.port = int(port)
        self.token = str(token or "")
        self.max_body_bytes = max(1024, int(max_body_bytes))
        self.logger = logger
        self.health_registry = health_registry
        self.wake_callback = wake_callback if callable(wake_callback) else None
        # Callback typed sang Semantic Tutor. Bridge chỉ chuyển state; nó không
        # tự gọi LLM và không để OCR phân tích lại bàn cờ đã có FEN.
        self.semantic_context_callback = semantic_context_callback if callable(semantic_context_callback) else None
        self.clock = clock or time.time

        # Stockfish là dependency tùy chọn. Không có binary thì Hybrid analyzer
        # dùng engine nội bộ ngay, không làm bridge khởi động thất bại.
        if analyzer is None:
            local_analyzer = LocalChessAnalyzer(depth=analysis_depth)
            stockfish_analyzer = StockfishUCIAnalyzer(
                StockfishConfig(
                    enabled=stockfish_enabled,
                    path=stockfish_path,
                    threads=stockfish_threads,
                    hash_mb=stockfish_hash_mb,
                    multipv=stockfish_multipv,
                    movetime_ms=stockfish_movetime_ms,
                    on_demand_movetime_ms=stockfish_on_demand_movetime_ms,
                    startup_timeout_seconds=stockfish_startup_timeout_seconds,
                    analysis_timeout_seconds=stockfish_analysis_timeout_seconds,
                ),
                logger=logger,
            )
            analyzer = HybridChessAnalyzer(
                primary=stockfish_analyzer if stockfish_analyzer.available else None,
                fallback=local_analyzer,
                logger=logger,
            )
        self.tracker = ChessGameTracker(
            depth=analysis_depth,
            direct_move_rate=direct_move_rate,
            analyzer=analyzer,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._last_snapshot_summary: dict[str, Any] = {}
        self._last_wakeup_reason = ""
        # Candidate được analyzer chuẩn bị cho đúng lượt user hỏi. Chỉ khi câu
        # public thật sự chứa nước này nó mới trở thành suggestion đã giao.
        self._suggestion_candidate: dict[str, Any] = {}
        self._delivered_suggestion: dict[str, Any] = {}
        self._last_suggestion_outcome: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> "ChessCompanionService":
        with self._lock:
            if self.running:
                return self
            service = self

            class Handler(BaseHTTPRequestHandler):
                server_version = "ERISChessBridge/1.0"

                def log_message(self, fmt, *args):
                    if service.logger is not None:
                        try:
                            service.logger.debug("[chess-bridge] " + fmt, *args)
                        except (AttributeError, TypeError, ValueError, RuntimeError):
                            pass

                def _headers(self, status: int = 200):
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "https://www.chess.com")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Eris-Bridge-Token")
                    self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
                    self.end_headers()

                def _json(self, payload: Mapping[str, Any], status: int = 200):
                    self._headers(status)
                    self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

                def do_OPTIONS(self):
                    self._headers(204)

                def do_GET(self):
                    if self.path.rstrip("/") in {"", "/health", "/v1/chess/health"}:
                        self._json(service.diagnostics())
                    else:
                        self._json({"ok": False, "error": "not_found"}, 404)

                def do_POST(self):
                    if self.path.rstrip("/") != "/v1/chess/snapshot":
                        self._json({"ok": False, "error": "not_found"}, 404)
                        return
                    if service.token and self.headers.get("X-Eris-Bridge-Token", "") != service.token:
                        self._json({"ok": False, "error": "unauthorized"}, 401)
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0") or 0)
                    except ValueError:
                        self._json({"ok": False, "error": "invalid_length"}, 400)
                        return
                    if length <= 0 or length > service.max_body_bytes:
                        self._json({"ok": False, "error": "body_size"}, 413)
                        return
                    try:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                        if not isinstance(payload, Mapping):
                            raise ValueError("payload must be an object")
                        observation = service.handle_snapshot(payload)
                    except (ValueError, TypeError, json.JSONDecodeError) as exc:
                        service._failure(exc, degraded=False)
                        self._json({"ok": False, "error": str(exc)[:160]}, 400)
                        return
                    except (OSError, RuntimeError, KeyError, AttributeError) as exc:
                        service._failure(exc, degraded=True)
                        self._json({"ok": False, "error": "bridge_failure"}, 500)
                        return
                    self._json({"ok": True, "observation": observation.to_event_payload()})

            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
            self.port = int(self._server.server_address[1])
            self._server.daemon_threads = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="eris-chess-bridge",
                daemon=True,
            )
            self._thread.start()
            self._success({"host": self.host, "port": self.port})
            if self.logger is not None:
                try:
                    self.logger.info("[chess-bridge] listening on http://%s:%s", self.host, self.port)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass
            return self

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.tracker.close()
        if self.health_registry is not None:
            self.health_registry.mark_stopped("chess.bridge")

    shutdown = stop
    close = stop

    def handle_snapshot(self, payload: Mapping[str, Any]) -> ChessObservation:
        snapshot = BoardSnapshot.from_mapping(payload)
        with self._lock:
            previous_game_id = str(self._last_snapshot_summary.get("game_id", "") or "")
            if previous_game_id and previous_game_id != snapshot.game_id:
                self._suggestion_candidate = {}
                self._delivered_suggestion = {}
                self._last_suggestion_outcome = {}
        observation = self.tracker.observe(snapshot)
        observation = self._annotate_suggestion_followthrough(observation)
        received_at = float(self.clock())
        now = snapshot.captured_at or received_at
        phase = getattr(getattr(observation, "phase", ""), "value", getattr(observation, "phase", ""))
        phase = str(phase or "unknown")
        with self._lock:
            self._last_snapshot_summary = {
                "game_id": snapshot.game_id,
                "mode": observation.mode.value,
                "phase": phase,
                "board_fen": snapshot.board_fen,
                "side_to_move": observation.side_to_move,
                "move_number": observation.move_number,
                "ply": snapshot.ply,
                "last_move_uci": observation.last_move_uci,
                "last_move_san": str(getattr(observation, "last_move_san", "") or ""),
                "previous_suggested_move_uci": str(getattr(observation, "previous_suggested_move_uci", "") or ""),
                "previous_suggested_move_san": str(getattr(observation, "previous_suggested_move_san", "") or ""),
                "user_followed_previous_suggestion": bool(getattr(observation, "user_followed_previous_suggestion", False)),
                "suggestion_outcome": str(getattr(observation, "suggestion_outcome", "") or ""),
                "suggested_move_uci": (
                    observation.suggestion.uci if observation.suggestion is not None else ""
                ),
                "suggested_move_san": (
                    observation.suggestion.san if observation.suggestion is not None else ""
                ),
                # Hai hệ quy chiếu được giữ song song: *_white cho engine/history,
                # *_user cho Tutor/Surface. Legacy fields vẫn là góc nhìn Trắng.
                "evaluation_cp": getattr(observation, "evaluation_cp", None),
                "evaluation_label": str(getattr(observation, "evaluation_label", "") or ""),
                "evaluation_cp_white": getattr(observation, "evaluation_cp_white", None),
                "evaluation_label_white": str(getattr(observation, "evaluation_label_white", "") or ""),
                "evaluation_cp_user": getattr(observation, "evaluation_cp_user", None),
                "evaluation_label_user": str(getattr(observation, "evaluation_label_user", "") or ""),
                "advantage_side": str(getattr(observation, "advantage_side", "") or ""),
                "user_position": str(getattr(observation, "user_position", "") or "unknown"),
                "evaluation_swing_cp": getattr(observation, "evaluation_swing_cp", None),
                "move_quality": str(getattr(observation, "move_quality", "") or ""),
                "hint_focus": str(getattr(observation, "hint_focus", "") or ""),
                "analysis_provider": str(getattr(observation, "analysis_provider", "") or ""),
                "analysis_depth": int(getattr(observation, "analysis_depth", 0) or 0),
                "analysis_nodes": int(getattr(observation, "analysis_nodes", 0) or 0),
                "analysis_elapsed_ms": int(getattr(observation, "analysis_elapsed_ms", 0) or 0),
                "analysis_degraded": bool(getattr(observation, "analysis_degraded", False)),
                "analysis_error": str(getattr(observation, "analysis_error", "") or "")[:500],
                "mate_in": getattr(observation, "mate_in", None),
                "principal_variation": list(getattr(observation, "principal_variation", ()) or ())[:24],
                "result": str(getattr(observation, "result", "") or ""),
                "orientation": str(getattr(snapshot, "orientation", "") or ""),
                "captured_at": snapshot.captured_at or received_at,
                # Freshness phải dựa trên lúc bridge NHẬN snapshot. Timestamp từ
                # extension có thể dùng clock khác hoặc bị serialize theo ms.
                "received_at": received_at,
                "engagement_id": "",
            }
        if self.logger is not None:
            try:
                self.logger.info(
                    "[chess-bridge] snapshot game=%s mode=%s phase=%s move=%s turn=%s last=%s suggestion=%s engine=%s",
                    snapshot.game_id,
                    observation.mode.value,
                    phase,
                    observation.move_number,
                    observation.side_to_move,
                    observation.last_move_uci or "-",
                    (
                        observation.suggestion.uci
                        if observation.suggestion is not None
                        else "-"
                    ),
                    str(getattr(observation, "analysis_provider", "") or "none"),
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        binding = EvidenceBinding(
            app="chess.com",
            surface_key=f"chess.com:{snapshot.game_id}",
            resource_id=f"chess:{snapshot.platform}:{snapshot.game_id}",
            tags=("chess", snapshot.platform, observation.mode.value),
        )
        engagement = self._resolve_engagement(binding=binding, now=now)
        with self._lock:
            if self._last_snapshot_summary.get("game_id") == snapshot.game_id:
                self._last_snapshot_summary["engagement_id"] = engagement.engagement_id
        companion_signal = build_companion_signal(
            observation,
            snapshot,
            engagement.domain_state,
        )
        event_payload = observation.to_event_payload()
        event_payload.update(companion_signal.to_dict())
        evidence_refs = (f"chesscom:{snapshot.game_id}:{snapshot.ply}",)
        emitted: list[str] = []
        with self._lock:
            if self._last_snapshot_summary.get("game_id") == snapshot.game_id:
                self._last_snapshot_summary.update(companion_signal.to_dict())

        # Nếu người dùng đã ra lệnh xem cờ trước khi mở bàn, phải nối snapshot
        # vào đúng engagement đó thay vì âm thầm tạo một task cờ thứ hai.
        board_seen_before = bool(engagement.domain_state.get("board_seen", False))
        if not board_seen_before:
            emitted.append("chess.board_detected")

        if observation.game_started:
            emitted.append("chess.game_started")
        if observation.last_move_uci:
            emitted.append("chess.move_played")
        elif not emitted and observation.phase is ChessObservationPhase.ACTIVE:
            emitted.append("chess.position_changed")
        elif not emitted and observation.phase is ChessObservationPhase.WAITING_FOR_START:
            emitted.append("chess.board_ready")
        if observation.game_over:
            emitted.append("chess.game_over")

        # Một snapshot có thể vừa đánh dấu bắt đầu vừa chứa nước đi đầu tiên.
        # Ghi đủ cả hai event để planner biết "ván bắt đầu" và "nước vừa đi".
        for kind in dict.fromkeys(emitted):
            updated = self.engagement_runtime.emit_domain_event(
                kind,
                payload=event_payload,
                engagement_id=engagement.engagement_id,
                binding=binding,
                evidence_refs=evidence_refs,
                now=now,
            )
            if updated is not None:
                engagement = updated

        # Nạp state cờ vào shared semantic context. Routine move chỉ cập nhật
        # state thô; move đáng nói/game start/end mới invalidates semantic packet.
        if self.semantic_context_callback is not None:
            try:
                semantic_events = tuple(dict.fromkeys(emitted))
                trigger_hint = companion_signal.speech_reason if companion_signal.should_wake else ""
                self.semantic_context_callback(
                    self.current_surface_state(),
                    events=semantic_events,
                    trigger_hint=trigger_hint,
                )
            except TypeError:
                try:
                    self.semantic_context_callback(self.current_surface_state(), events=tuple(dict.fromkeys(emitted)))
                except Exception as exc:
                    if self.logger is not None:
                        self.logger.debug("[chess-bridge] semantic callback failed: %s", exc)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.debug("[chess-bridge] semantic callback failed: %s", exc)

        wake_events = tuple(
            kind for kind in dict.fromkeys(emitted)
            if kind in {"chess.board_detected", "chess.game_started", "chess.move_played", "chess.game_over"}
        )
        # The bridge always records structured state, but it only wakes the public
        # initiative loop for a meaningful chess beat. Routine moves remain
        # available to chat without turning ERIS into a move-by-move alarm clock.
        if wake_events and companion_signal.should_wake:
            self._wake_autonomous(
                game_id=snapshot.game_id,
                move_number=observation.move_number,
                events=wake_events,
            )

        self._success({
            "game_id": snapshot.game_id,
            "mode": observation.mode.value,
            "phase": observation.phase.value,
            "events": emitted,
        })
        return observation

    def _wake_autonomous(self, *, game_id: str, move_number: int, events: tuple[str, ...]) -> None:
        callback = self.wake_callback
        if callback is None:
            return
        reason = f"chess_event:{events[-1]}:game={game_id}:move={move_number}"
        self._last_wakeup_reason = reason
        try:
            callback(reason)
        except Exception as exc:  # noqa: BLE001 - callback boundary must not break bridge HTTP
            if self.logger is not None:
                try:
                    self.logger.warning("[chess-bridge] autonomous wake failed: %s", exc)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass

    def _resolve_engagement(self, *, binding: EvidenceBinding, now: float):
        """Nối bridge vào task cờ người dùng đã tạo, nếu task đó còn chưa có bàn.

        Trước bản vá, câu "xem tôi đánh cờ" tạo một engagement không có
        ``resource_id``; snapshot sau đó lại tạo engagement thứ hai vì resource
        mới không khớp. Hậu quả là một task cứ hỏi "bàn đâu", task kia lại tự
        bình luận. Tách đôi như vậy rất sáng tạo, nhưng không hữu ích.
        """

        active = tuple(self.engagement_runtime.registry.active())
        for item in active:
            if item.kind == "chess" and item.evidence_binding.resource_id == binding.resource_id:
                return item

        foreground = self.engagement_runtime.registry.foreground()
        candidates = [
            item for item in active
            if item.kind == "chess" and not item.evidence_binding.meaningful
        ]
        target = foreground if foreground in candidates else (candidates[0] if candidates else None)
        if target is not None:
            return self.engagement_runtime.bind_engagement(
                target.engagement_id,
                binding,
                now=now,
                source="chess_bridge",
            ) or target

        return self.engagement_runtime.ensure_profile(
            "chess",
            objective="Đồng hành cùng người dùng trong ván cờ hiện tại",
            binding=binding,
            now=now,
            source="chess_bridge",
        )


    def wants_move_suggestion(self, user_text: str) -> bool:
        return chess_move_question(user_text)

    def _remember_suggestion_candidate(self, typed: ChessSurfaceState) -> None:
        if not typed.suggested_move_uci and not typed.suggested_move_san:
            return
        now = float(self.clock())
        candidate = {
            "game_id": typed.game_id,
            "uci": typed.suggested_move_uci.lower(),
            "san": typed.suggested_move_san,
            "natural": describe_chess_move_vi(
                san=typed.suggested_move_san,
                uci=typed.suggested_move_uci,
            ),
            "created_at": now,
            "move_number": typed.move_number,
        }
        with self._lock:
            self._suggestion_candidate = candidate

    def record_public_suggestion(self, public_text: str, *, authoritative: bool = False) -> bool:
        """Ghi suggestion chỉ sau khi nó thật sự xuất hiện ở public output."""

        text = str(public_text or "").strip()
        if not text:
            return False
        now = float(self.clock())
        with self._lock:
            candidate = dict(self._suggestion_candidate)
        if not candidate or now - float(candidate.get("created_at", 0.0) or 0.0) > 120.0:
            return False

        folded = " ".join(text.lower().split())
        uci = str(candidate.get("uci", "") or "").lower()
        san = str(candidate.get("san", "") or "").lower()
        natural = str(candidate.get("natural", "") or "").lower()
        destination = uci[2:4] if len(uci) >= 4 else ""
        piece_words = {"N": "mã", "B": "tượng", "R": "xe", "Q": "hậu", "K": "vua"}
        san_piece = piece_words.get(str(candidate.get("san", "") or "")[:1], "tốt")
        mentioned = bool(
            authoritative
            or (uci and uci in folded)
            or (san and re.search(rf"(?<![a-z0-9]){re.escape(san)}(?![a-z0-9])", folded, re.IGNORECASE))
            or (natural and natural in folded)
            or (destination and destination in folded and san_piece in folded)
        )
        if not mentioned:
            return False
        candidate["delivered_at"] = now
        with self._lock:
            self._delivered_suggestion = candidate
            self._suggestion_candidate = {}
        return True

    def _annotate_suggestion_followthrough(self, observation: ChessObservation) -> ChessObservation:
        """Giữ dấu vết user đã làm theo suggestion, không suy diễn từ engine.

        Kết quả được lưu qua các snapshot kế tiếp trong cùng ván. Nhờ vậy nếu
        bot đáp lại rồi user mới than "ngu rồi", Surface vẫn biết vị trí hiện
        tại bắt nguồn từ nước ERIS đã thật sự khuyên và user đã chọn làm theo.
        """

        last_uci = str(observation.last_move_uci or "").lower()
        with self._lock:
            delivered = dict(self._delivered_suggestion)
            remembered = dict(self._last_suggestion_outcome)

        if (
            delivered
            and str(delivered.get("game_id", "") or "") == observation.game_id
            and last_uci
            and observation.actor_is_user
        ):
            suggested_uci = str(delivered.get("uci", "") or "").lower()
            followed = bool(suggested_uci and last_uci == suggested_uci)
            remembered = {
                "game_id": observation.game_id,
                "uci": suggested_uci,
                "san": str(delivered.get("san", "") or ""),
                "followed": followed,
                "outcome": "followed" if followed else "declined",
                "resolved_at": float(self.clock()),
                "resolved_ply": int(getattr(observation, "ply", 0) or 0),
            }
            with self._lock:
                self._delivered_suggestion = {}
                self._last_suggestion_outcome = dict(remembered)

        if not remembered or str(remembered.get("game_id", "") or "") != observation.game_id:
            return observation
        return replace(
            observation,
            previous_suggested_move_uci=str(remembered.get("uci", "") or ""),
            previous_suggested_move_san=str(remembered.get("san", "") or ""),
            user_followed_previous_suggestion=bool(remembered.get("followed", False)),
            suggestion_outcome=str(remembered.get("outcome", "") or ""),
        )

    def active_engagement_id(self) -> str:
        """Trả engagement id đang active, độc lập với độ tươi của FEN."""

        with self._lock:
            engagement_id = str(self._last_snapshot_summary.get("engagement_id", "") or "").strip()
        if not engagement_id:
            return ""
        try:
            engagement = self.engagement_runtime.registry.get(engagement_id)
        except Exception:
            return ""
        return engagement_id if engagement is not None and bool(getattr(engagement, "active", False)) else ""

    def is_engagement_active(self) -> bool:
        """Phiên cờ còn active không đồng nghĩa snapshot FEN vẫn còn tươi."""

        return bool(self.active_engagement_id())

    def is_state_fresh(self, *, max_age_seconds: float = 30.0) -> bool:
        """Kiểm riêng freshness của board snapshot, không dùng nó làm lifecycle."""

        with self._lock:
            item = dict(self._last_snapshot_summary)
        received_at = float(item.get("received_at", 0.0) or 0.0)
        board_fen = str(item.get("board_fen", "") or "").strip()
        if not board_fen or not received_at:
            return False
        age = max(0.0, float(self.clock()) - received_at)
        return age <= max(0.5, float(max_age_seconds))

    def matches_turn(self, user_text: str) -> bool:
        """Nhận ngôn ngữ cờ tự nhiên; câu UI thuần vẫn để vision xử lý."""

        return chess_turn_relevant(
            user_text,
            engagement_active=self.is_engagement_active(),
        )

    def current_surface_state(self) -> dict[str, Any]:
        """Snapshot typed bất biến dùng chung cho chat và autonomous Surface."""

        with self._lock:
            item = dict(self._last_snapshot_summary)
        return ChessSurfaceState.from_mapping(item).to_dict()

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            last_snapshot = dict(self._last_snapshot_summary)
        return {
            "ok": True,
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "token_required": bool(self.token),
            "last_snapshot": last_snapshot,
            "last_wakeup_reason": self._last_wakeup_reason,
            "tracker": self.tracker.diagnostics(),
        }

    def current_grounding_evidence(
        self,
        *,
        max_age_seconds: float = 30.0,
        relevant_to_turn: bool = False,
        request_move_suggestion: bool = False,
    ) -> tuple[dict[str, object], ...]:
        """Xuất snapshot cờ tươi thành provenance chính thức cho chat grounding.

        Đây là state có cấu trúc từ extension, vì vậy nó đáng tin hơn việc bắt
        vision nhìn lại cùng một bàn cờ. ``relevant_to_turn`` chỉ quyết định vai
        trò primary/ambient; snapshot vẫn được đưa vào context khi engagement
        đang hoạt động để giữ continuity giữa các lượt.
        """

        with self._lock:
            item = dict(self._last_snapshot_summary)
        board_fen = str(item.get("board_fen", "") or "").strip()
        received_at = float(item.get("received_at", 0.0) or 0.0)
        age = max(0.0, float(self.clock()) - received_at) if received_at else float("inf")
        event_snapshot_still_current = bool(relevant_to_turn and self.is_engagement_active())
        if not board_fen or (
            age > max(0.5, float(max_age_seconds))
            and not event_snapshot_still_current
        ):
            return ()

        typed = ChessSurfaceState.from_mapping(item)
        game_id = typed.game_id or "unknown"
        move_number = typed.move_number
        phase = typed.phase or "unknown"
        side_to_move = typed.side_to_move or "unknown"
        last_move = typed.last_move_uci or "-"
        suggestion = typed.suggested_move_uci or "-"
        if relevant_to_turn and request_move_suggestion and suggestion == "-":
            # Tỷ lệ direct_move_rate chỉ điều khiển việc ERIS tự buột miệng gợi ý
            # trong autonomous. Khi user hỏi thẳng “nên đánh gì”, phải phân tích
            # ngay từ FEN hiện tại, không trả lời khai cuộc chung chung như sách
            # nhập môn bị thất lạc trang.
            try:
                turn = "w" if side_to_move in {"white", "w"} else "b"
                ply = max(0, (move_number - 1) * 2 + (1 if turn == "b" else 0))
                position = Position.from_board_fen(board_fen, turn=turn, ply=ply)
                analysis = self.tracker.analyze_position(position, top_n=1, on_demand=True)
                if analysis.candidates:
                    candidate = analysis.candidates[0]
                    suggestion = candidate.move.uci
                    item["suggested_move_uci"] = suggestion
                    item["suggested_move_san"] = move_san(position, candidate.move)
                    item["analysis_provider"] = analysis.provider
                    item["analysis_depth"] = analysis.depth
                    item["analysis_nodes"] = analysis.nodes
                    item["analysis_elapsed_ms"] = analysis.elapsed_ms
                    item["analysis_degraded"] = analysis.degraded
                    item["mate_in"] = analysis.mate_in
                    item["principal_variation"] = list(candidate.pv)[:24]
                    typed = ChessSurfaceState.from_mapping(item)
            except Exception as exc:  # noqa: BLE001 - evidence provider phải degrade yên
                if self.logger is not None:
                    try:
                        self.logger.debug("[chess-bridge] on-demand suggestion failed: %s", exc)
                    except Exception:
                        pass
        if relevant_to_turn and request_move_suggestion and typed.suggested_move:
            self._remember_suggestion_candidate(typed)

        perspective = (
            f"evaluation_cp_white={typed.evaluation_cp_white}; advantage_side={typed.advantage_side}; "
            f"user_side={typed.user_side or 'unknown'}; evaluation_cp_user={typed.evaluation_cp_user}; "
            f"user_position={typed.user_position or 'unknown'}"
        )
        claim = (
            "Chess board snapshot: "
            f"phase={phase}; side_to_move={side_to_move}; move_number={move_number}; "
            f"last_move={last_move}; suggested_move={suggestion}; board_fen={board_fen}; {perspective}"
        )
        return ({
            "id": f"chess:{game_id}:{move_number}",
            "source": self.structured_source_id,
            "evidence_kind": "structured_runtime",
            "engagement_kind": self.engagement_kind,
            "engagement_active": self.is_engagement_active(),
            "state_fresh": True,
            "snapshot_age_seconds": round(age, 3),
            "event_snapshot_still_current": event_snapshot_still_current,
            "claim": claim,
            # State typed là nguồn chính; claim text chỉ giữ để log/plugin cũ.
            "state": typed.to_dict(),
            "event_fingerprint": typed.event_fingerprint("chess.snapshot"),
            "confidence": 1.0,
            "grounded": True,
            "stale": False,
            "captured_at": float(item.get("captured_at", received_at) or received_at),
            "received_at": received_at,
            "relevant_to_turn": bool(relevant_to_turn),
            "role": "primary_candidate" if relevant_to_turn else "ambient",
            "engagement_id": str(item.get("engagement_id", "") or ""),
            "game_id": game_id,
            "phase": phase,
            "board_fen": board_fen,
            "side_to_move": side_to_move,
            "move_number": move_number,
            "last_move_uci": "" if last_move == "-" else last_move,
            "suggested_move_uci": "" if suggestion == "-" else suggestion,
            "tags": [
                "chess",
                "board",
                "extension",
                "structured_sensor",
                "direct_sensor",
            ],
        },)

    def _success(self, details: Mapping[str, Any]) -> None:
        if self.health_registry is not None:
            self.health_registry.record_success("chess.bridge", details=dict(details))

    def _failure(self, exc: BaseException, *, degraded: bool) -> None:
        if self.health_registry is not None:
            self.health_registry.record_failure(
                "chess.bridge",
                exc,
                kind=FailureKind.INVALID_EXTERNAL_DATA if not degraded else FailureKind.PROGRAMMER_ERROR,
                degraded=degraded,
            )
        if self.logger is not None:
            try:
                self.logger.warning("[chess-bridge] %s", exc)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass


__all__ = ["ChessCompanionService"]
