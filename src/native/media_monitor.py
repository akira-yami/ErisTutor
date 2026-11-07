# -*- coding: utf-8 -*-
from __future__ import annotations

"""GSMTC media monitor event-driven cho Windows.

Không poll giao diện player bằng OCR. Khi winsdk có mặt, module mở
GlobalSystemMediaTransportControlsSessionManager trên một asyncio thread riêng,
subscribe session/playback/media-property events và chỉ phát metadata semantic.
"""

import asyncio
import os
import threading
import time
from typing import Any, Callable, Mapping

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent


class MediaMonitor:
    def __init__(
        self,
        callback: Callable[[NormalizedEnvironmentEvent], None],
        *,
        sanity_refresh_seconds: float = 12.0,
        include_content_metadata: bool | None = None,
    ) -> None:
        self.callback = callback
        self.sanity_refresh_seconds = max(5.0, float(sanity_refresh_seconds))
        if include_content_metadata is None:
            include_content_metadata = os.getenv("ERIS_MEDIA_CONTENT_METADATA", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.include_content_metadata = bool(include_content_metadata)
        try:  # pragma: no cover - Windows dependency
            import winsdk.windows.media.control as _control  # type: ignore
        except Exception:
            _control = None
        self._control = _control
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._manager: Any = None
        self._subscriptions: list[tuple[Any, str, Any]] = []
        self._last_signature: dict[str, tuple[Any, ...]] = {}

    @property
    def available(self) -> bool:
        return os.name == "nt" and self._control is not None

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> "MediaMonitor":
        if not self.available or self.alive:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_thread, name="eris-gsmtc-monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @staticmethod
    def _playback_name(value: Any) -> str:
        if value is None:
            return "unknown"
        text = str(value).split(".")[-1].strip().lower()
        mapping = {"0": "closed", "1": "opened", "2": "changing", "3": "stopped", "4": "playing", "5": "paused"}
        return mapping.get(text, text or "unknown")

    def emit_session(self, session: Mapping[str, Any], *, now: float | None = None) -> NormalizedEnvironmentEvent:
        title = str(session.get("title", "") or "") if self.include_content_metadata else ""
        metadata = {
            "playback": session.get("playback", ""),
            "source_app": session.get("source_app", ""),
        }
        if self.include_content_metadata:
            metadata.update({
                "artist": session.get("artist", ""),
                "timeline_position_ms": session.get("timeline_position_ms", 0),
            })
        event = NormalizedEnvironmentEvent(
            timestamp=time.time() if now is None else float(now),
            source="gsmtc",
            event_type="media_changed",
            action=str(session.get("playback", "changed") or "changed"),
            process_name=str(session.get("source_app", "") or ""),
            role="media_session",
            title=title,
            semantic=True,
            source_confidence=0.94,
            confidence=0.94,
            novelty=0.5,
            importance=0.14,
            user_relevance=0.35,
            attention_level=1,
            metadata=metadata,
            evidence_refs=("media:gsmtc",),
        )
        self.callback(event)
        return event

    @staticmethod
    def _source_app(session: Any) -> str:
        return str(
            getattr(session, "source_app_user_model_id", "")
            or getattr(session, "source_app_user_model_id", "")
            or ""
        )

    async def _session_payload(self, session: Any) -> dict[str, Any]:  # pragma: no cover - Windows E2E
        source_app = self._source_app(session)
        playback = "unknown"
        title = ""
        artist = ""
        timeline_ms = 0
        try:
            info = session.get_playback_info()
            playback = self._playback_name(getattr(info, "playback_status", None))
        except Exception:
            pass
        try:
            props = await session.try_get_media_properties_async()
            title = str(getattr(props, "title", "") or "")
            artist = str(getattr(props, "artist", "") or "")
        except Exception:
            pass
        try:
            timeline = session.get_timeline_properties()
            position = getattr(timeline, "position", None)
            # WinRT TimeSpan thường expose duration theo 100ns ticks hoặc timedelta.
            if hasattr(position, "total_seconds"):
                timeline_ms = int(max(0.0, float(position.total_seconds())) * 1000.0)
            elif hasattr(position, "duration"):
                timeline_ms = int(max(0, int(position.duration)) / 10_000)
        except Exception:
            pass
        return {
            "source_app": source_app,
            "playback": playback,
            "title": title[:240],
            "artist": artist[:160],
            "timeline_position_ms": timeline_ms,
        }

    async def _emit_if_changed(self, session: Any, *, force: bool = False) -> None:  # pragma: no cover - Windows E2E
        payload = await self._session_payload(session)
        key = payload.get("source_app", "") or f"session:{id(session)}"
        signature = (
            payload.get("playback"), payload.get("title"), payload.get("artist"),
            int(payload.get("timeline_position_ms", 0) or 0) // 5000,
        )
        if not force and self._last_signature.get(key) == signature:
            return
        self._last_signature[key] = signature
        self.emit_session(payload)

    def _subscribe(self, obj: Any, event_name: str, handler: Callable[..., None]) -> None:  # pragma: no cover
        """winsdk projection dùng add_<event>; helper giữ token để remove khi stop."""
        add = getattr(obj, f"add_{event_name}", None)
        if callable(add):
            try:
                token = add(handler)
                self._subscriptions.append((obj, event_name, token))
            except Exception:
                pass

    def _schedule_session_refresh(self, session: Any) -> None:  # pragma: no cover
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._emit_if_changed(session), loop)

    def _subscribe_session(self, session: Any) -> None:  # pragma: no cover
        def changed(sender: Any, _args: Any = None) -> None:
            self._schedule_session_refresh(sender or session)

        for name in ("playback_info_changed", "media_properties_changed", "timeline_properties_changed"):
            self._subscribe(session, name, changed)

    async def _rebind_sessions(self) -> list[Any]:  # pragma: no cover
        if self._manager is None:
            return []
        try:
            sessions = list(self._manager.get_sessions())
        except Exception:
            sessions = []
        for session in sessions:
            self._subscribe_session(session)
            await self._emit_if_changed(session, force=True)
        return sessions

    async def _main(self) -> None:  # pragma: no cover - Windows E2E
        manager_cls = getattr(self._control, "GlobalSystemMediaTransportControlsSessionManager", None)
        if manager_cls is None:
            return
        self._manager = await manager_cls.request_async()

        def sessions_changed(_sender: Any, _args: Any = None) -> None:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._rebind_sessions(), self._loop)

        self._subscribe(self._manager, "sessions_changed", sessions_changed)
        await self._rebind_sessions()
        while not self._stop.is_set():
            await asyncio.sleep(self.sanity_refresh_seconds)
            # Sanity refresh thấp tần số để recover event bị mất, không thay thế
            # event-driven path bằng polling UI.
            try:
                sessions = list(self._manager.get_sessions())
            except Exception:
                sessions = []
            for session in sessions:
                await self._emit_if_changed(session)

    def _remove_subscriptions(self) -> None:  # pragma: no cover
        for obj, name, token in reversed(self._subscriptions):
            remove = getattr(obj, f"remove_{name}", None)
            if callable(remove):
                try:
                    remove(token)
                except Exception:
                    pass
        self._subscriptions.clear()

    def _run_thread(self) -> None:  # pragma: no cover - Windows E2E
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:
            pass
        finally:
            self._remove_subscriptions()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None


__all__ = ["MediaMonitor"]
