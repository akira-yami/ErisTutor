"""Thu thập metadata nội dung đang nhìn thấy cho SeLens.

Mục tiêu của file này không phải "đọc màn hình như tiên tri". Nó chỉ gom
các fact có nguồn rõ ràng theo thang L0-L5 rồi trả về dict nhỏ để SeLens tự
quyết confidence. Nếu nguồn không có, trả rỗng. Không được bịa.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - chỉ có trên Windows runtime thật
    import uiautomation as auto  # type: ignore
except Exception:  # pragma: no cover - CI/Linux không có UIAutomation
    auto = None  # type: ignore


# Thang evidence L0-L5. Đây là cap, không phải confidence tuyệt đối.
# Confidence cuối còn bị trừ bởi stale/conflict/privacy.
SOURCE_TIERS: dict[str, tuple[str, float]] = {
    "window_title": ("L0", 0.40),
    "browser_url": ("L1", 0.55),
    "media_session": ("L2", 0.65),
    "accessibility": ("L3", 0.72),
    "browser_dom": ("L4", 0.85),
    "dom_ocr_agreement": ("L5", 0.92),
}

MEDIA_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
    "tiktok.com",
    "twitch.tv",
    "netflix.com",
    "spotify.com",
    "soundcloud.com",
)

COMMUNICATION_DOMAINS = (
    "mail.google.com",
    "gmail.com",
    "outlook.live.com",
    "discord.com",
    "web.telegram.org",
    "zalo.me",
    "slack.com",
    "meet.google.com",
    "teams.microsoft.com",
    "zoom.us",
)

SENSITIVE_APP_HINTS = (
    "discord",
    "telegram",
    "zalo",
    "messenger",
    "gmail",
    "mail",
    "outlook",
    "slack",
    "teams",
    "zoom",
    "meet",
)


@dataclass(slots=True)
class ContentMetadata:
    """Fact thô về nội dung foreground.

    Ghi chú tiếng Việt cho tương lai:
    - `source` là nguồn mạnh nhất đang có, không phải mọi nguồn đã thử.
    - `tier` là L0-L5 theo ladder evidence.
    - `confidence` đã bị cap theo source. SeLens có thể hạ tiếp nếu cần.
    - `visible_text_sample` là text UI/DOM/OCR đã cắt ngắn, không phải toàn bộ trang.
    """

    source: str = ""
    tier: str = ""
    confidence: float = 0.0
    reliability: float = 0.0
    privacy_level: str = "normal"
    page_kind: str = "unknown"
    url: str = ""
    title: str = ""
    creator: str = ""
    hashtags: tuple[str, ...] = ()
    caption_sample: str = ""
    visible_text_sample: str = ""
    media_state: str = "unknown"
    collected_at: float = 0.0
    stale_after_seconds: float = 20.0
    evidence_notes: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "source": self.source,
            "tier": self.tier,
            "confidence": round(float(self.confidence), 3),
            "reliability": round(float(self.reliability), 3),
            "privacy_level": self.privacy_level,
            "page_kind": self.page_kind,
            "url": self.url,
            "title": self.title,
            "creator": self.creator,
            "hashtags": list(self.hashtags),
            "caption_sample": self.caption_sample,
            "visible_text_sample": self.visible_text_sample,
            "media_state": self.media_state,
            "collected_at": round(float(self.collected_at), 3) if self.collected_at else 0.0,
            "stale_after_seconds": round(float(self.stale_after_seconds), 1),
            "evidence_notes": list(self.evidence_notes),
            "conflicts": list(self.conflicts),
        }
        if self.debug:
            payload["debug"] = self.debug
        return {key: value for key, value in payload.items() if value not in ("", [], (), {}, 0.0)}


class ContentMetadataCollector:
    """Best-effort collector cho metadata nội dung foreground.

    Luồng ưu tiên:
    1. DOM bridge cache do extension/local bridge ghi ra. Mạnh nhất.
    2. Accessibility tree foreground window. Vừa đủ đọc caption/UI text, nhưng nhiễu.
    3. Media/browser title fallback. Yếu, chỉ hơn active window một chút.

    OCR ROI không chạy mặc định ở đây để tránh đốt máy mỗi tick. Nếu có module OCR
    riêng ghi cache vào `ERIS_LIGHT_OCR_CACHE_PATH`, collector sẽ dùng nó để nâng
    DOM -> L5 khi text khớp. Loài người đã phát minh cooldown vì có lý do.
    """

    def __init__(self) -> None:
        self.enabled = _env_bool("ERIS_TELEMETRY_CONTENT_METADATA", True)
        self.accessibility_enabled = _env_bool("ERIS_TELEMETRY_ACCESSIBILITY_ENABLED", True)
        self.accessibility_max_nodes = _env_int("ERIS_ACCESSIBILITY_MAX_NODES", 120)
        self.accessibility_max_depth = _env_int("ERIS_ACCESSIBILITY_MAX_DEPTH", 7)
        self.dom_stale_after = _env_float("ERIS_DOM_BRIDGE_STALE_AFTER_SEC", 12.0)
        self.ocr_stale_after = _env_float("ERIS_OCR_CACHE_STALE_AFTER_SEC", 8.0)
        self.dom_paths = tuple(_candidate_cache_paths("ERIS_BROWSER_DOM_BRIDGE_PATH", "browser_dom_state.json"))
        self.ocr_paths = tuple(_candidate_cache_paths("ERIS_LIGHT_OCR_CACHE_PATH", "ocr_state.json"))
        self.media_session_paths = tuple(_candidate_cache_paths("ERIS_MEDIA_SESSION_CACHE_PATH", "media_session_state.json"))

    def collect(
        self,
        *,
        active_window: str,
        browser_url: str = "",
        browser_tabs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not self.enabled:
            return {}

        now = time.time()
        url = str(browser_url or "")
        active_window = str(active_window or "")
        browser_tabs = tuple(str(tab) for tab in (browser_tabs or ()) if str(tab).strip())
        privacy_level = self._privacy_level(active_window=active_window, url=url)

        # Không đọc text sâu ở chat/mail/meeting. Nếu muốn override thì thêm env allow sau.
        if privacy_level in {"private", "sensitive"}:
            fallback = self._from_browser_or_window_title(
                active_window=active_window,
                browser_url=url,
                browser_tabs=browser_tabs,
                now=now,
                privacy_level=privacy_level,
            )
            fallback.evidence_notes = tuple(dict.fromkeys((*fallback.evidence_notes, "privacy guard blocked deep content extraction")))
            fallback.confidence = min(fallback.confidence, 0.35)
            fallback.reliability = min(fallback.reliability, 0.35)
            return fallback.to_dict()

        dom = self._read_dom_bridge(now=now, current_url=url)
        ocr = self._read_ocr_cache(now=now, current_url=url)
        media_session = self._read_media_session_cache(now=now, current_url=url)
        accessibility = self._read_accessibility_text(active_window=active_window, url=url) if self.accessibility_enabled else None
        fallback = self._from_browser_or_window_title(
            active_window=active_window,
            browser_url=url,
            browser_tabs=browser_tabs,
            now=now,
            privacy_level=privacy_level,
        )

        best = self._merge_sources(dom=dom, ocr=ocr, accessibility=accessibility, media_session=media_session, fallback=fallback, now=now)
        return best.to_dict() if best.confidence > 0.0 else {}

    def _merge_sources(
        self,
        *,
        dom: ContentMetadata | None,
        ocr: ContentMetadata | None,
        accessibility: ContentMetadata | None,
        media_session: ContentMetadata | None,
        fallback: ContentMetadata,
        now: float,
    ) -> ContentMetadata:
        # DOM là nguồn chính nếu còn tươi. OCR/accessibility chỉ bổ sung hoặc xác nhận.
        if dom:
            notes = list(dom.evidence_notes)
            conflicts = list(dom.conflicts)
            if accessibility and accessibility.visible_text_sample:
                dom.visible_text_sample = _choose_longer(dom.visible_text_sample, accessibility.visible_text_sample, limit=360)
                notes.append("accessibility text attached")
            if ocr and _text_overlap(dom.caption_sample or dom.visible_text_sample or dom.title, ocr.visible_text_sample):
                tier, cap = SOURCE_TIERS["dom_ocr_agreement"]
                dom.source = "dom_ocr_agreement"
                dom.tier = tier
                dom.confidence = min(cap, max(dom.confidence, 0.88))
                dom.reliability = min(cap, max(dom.reliability, 0.86))
                notes.append("dom and ocr text agree")
            elif ocr and ocr.visible_text_sample:
                conflicts.append("ocr text did not clearly match dom text")
            dom.evidence_notes = tuple(dict.fromkeys(notes))[:6]
            dom.conflicts = tuple(dict.fromkeys(conflicts))[:4]
            return dom

        if accessibility and accessibility.confidence >= fallback.confidence:
            if ocr and _text_overlap(accessibility.visible_text_sample, ocr.visible_text_sample):
                accessibility.confidence = min(0.78, accessibility.confidence + 0.06)
                accessibility.reliability = min(0.78, accessibility.reliability + 0.06)
                accessibility.evidence_notes = tuple(dict.fromkeys((*accessibility.evidence_notes, "accessibility and ocr text agree")))
            return accessibility

        if media_session and media_session.confidence >= fallback.confidence:
            return media_session
        if ocr and ocr.confidence >= fallback.confidence:
            return ocr
        return fallback

    def _read_dom_bridge(self, *, now: float, current_url: str) -> ContentMetadata | None:
        # Thử đọc fresh trước (stale_after = dom_stale_after, mặc định 12s)
        data = self._read_fresh_json(self.dom_paths, now=now, stale_after=self.dom_stale_after)
        stale_fallback = False
        if not data:
            # Không có data tươi → thử đọc data stale cũ hơn nhưng vẫn trong 300s
            # (5 phút). Trường hợp xảy ra khi Eris vừa khởi động lại, extension
            # chưa kịp gửi POST mới. Data cũ vẫn có URL/title hữu ích; chỉ cần
            # hạ confidence để downstream không overfit vào nó.
            data = self._read_fresh_json(self.dom_paths, now=now, stale_after=300.0)
            stale_fallback = bool(data)
        if not data:
            return None

        # Hỗ trợ cả shape {tabs:[...]} lẫn object trực tiếp. Extension viết kiểu nào cũng sống được.
        if isinstance(data.get("tabs"), list):
            candidates = [item for item in data.get("tabs", []) if isinstance(item, dict)]
            data = _pick_matching_url(candidates, current_url) or (candidates[0] if candidates else {})
        elif isinstance(data.get("active"), dict):
            data = data.get("active", {}) or {}

        if not isinstance(data, dict):
            return None

        source = str(data.get("source") or "browser_dom")
        if source not in SOURCE_TIERS:
            source = "browser_dom"
        tier, cap = SOURCE_TIERS[source]
        # Data stale có cap thấp hơn — tối đa ngang L1 (browser_url) để tránh
        # override OCR/accessibility tươi bằng URL cũ 1 tiếng.
        if stale_fallback:
            cap = min(cap, SOURCE_TIERS["browser_url"][1])
        url = _clean_text(data.get("url") or current_url, 500)
        page_kind = _infer_page_kind(url=url, title=str(data.get("title") or ""))
        title = _clean_text(data.get("title") or data.get("video_title") or data.get("og_title"), 180)
        creator = _clean_text(data.get("creator") or data.get("channel") or data.get("author"), 120)
        caption = _clean_text(data.get("caption") or data.get("caption_sample") or data.get("subtitle"), 260)
        visible_text = _clean_text(data.get("visible_text") or data.get("visible_text_sample") or data.get("text"), 360)
        hashtags = _normalize_hashtags(data.get("hashtags") or data.get("tags") or ())
        media_state = _normalize_media_state(data.get("media_state") or data.get("state") or "unknown")
        raw_conf = _coerce_float(data.get("confidence"), cap)
        confidence = min(cap, max(0.0, raw_conf))
        if caption or hashtags:
            confidence = max(confidence, min(cap, 0.80))
        elif title:
            confidence = max(confidence, min(cap, 0.72))
        if not any((title, caption, visible_text, hashtags)):
            return None

        evidence_notes: tuple[str, ...] = (
            ("browser DOM bridge cache (stale fallback)",)
            if stale_fallback
            else ("browser DOM bridge cache",)
        )
        return ContentMetadata(
            source=source,
            tier=tier,
            confidence=round(confidence, 3),
            reliability=round(min(cap, max(confidence - 0.03, 0.0)), 3),
            privacy_level="normal",
            page_kind=page_kind,
            url=url,
            title=title,
            creator=creator,
            hashtags=hashtags,
            caption_sample=caption,
            visible_text_sample=visible_text,
            media_state=media_state,
            collected_at=now,
            stale_after_seconds=self.dom_stale_after,
            evidence_notes=evidence_notes,
        )

    def _read_media_session_cache(self, *, now: float, current_url: str) -> ContentMetadata | None:
        """Đọc cache now-playing nếu module khác ghi được Media Session/SMTC.

        Python thuần đọc GlobalSystemMediaTransportControls trên Windows khá phiền.
        Vì vậy collector nhận cache chuẩn trước: title/artist/state/url/confidence.
        Có cache thì lên L2; không có thì không giả vờ biết nhạc/video đang phát.
        """
        data = self._read_fresh_json(self.media_session_paths, now=now, stale_after=10.0)
        if not isinstance(data, dict):
            return None
        title = _clean_text(data.get("title") or data.get("track") or data.get("video_title"), 180)
        creator = _clean_text(data.get("artist") or data.get("creator") or data.get("channel"), 120)
        if not title and not creator:
            return None
        tier, cap = SOURCE_TIERS["media_session"]
        confidence = min(cap, _coerce_float(data.get("confidence"), 0.62))
        return ContentMetadata(
            source="media_session",
            tier=tier,
            confidence=round(confidence, 3),
            reliability=round(min(confidence, 0.62), 3),
            privacy_level="normal",
            page_kind=_infer_page_kind(url=current_url, title=title),
            url=_clean_text(data.get("url") or current_url, 500),
            title=title,
            creator=creator,
            media_state=_normalize_media_state(data.get("media_state") or data.get("state") or "unknown"),
            collected_at=now,
            stale_after_seconds=10.0,
            evidence_notes=("media session cache",),
        )

    def _read_ocr_cache(self, *, now: float, current_url: str) -> ContentMetadata | None:
        data = self._read_fresh_json(self.ocr_paths, now=now, stale_after=self.ocr_stale_after)
        if not isinstance(data, dict):
            return None
        text = _clean_text(data.get("visible_text") or data.get("text") or data.get("caption"), 360)
        if not text:
            return None
        tier, cap = SOURCE_TIERS["accessibility"]  # OCR riêng lẻ nhiễu tương đương L3, không tự lên L5.
        confidence = min(cap, _coerce_float(data.get("confidence"), 0.66))
        return ContentMetadata(
            source="ocr_cache",
            tier=tier,
            confidence=round(confidence, 3),
            reliability=round(min(confidence, 0.68), 3),
            privacy_level="normal",
            page_kind=_infer_page_kind(url=current_url, title=text),
            url=_clean_text(data.get("url") or current_url, 500),
            visible_text_sample=text,
            collected_at=now,
            stale_after_seconds=self.ocr_stale_after,
            evidence_notes=("light OCR cache",),
        )

    def _read_accessibility_text(self, *, active_window: str, url: str) -> ContentMetadata | None:
        if auto is None:
            return None
        try:  # pragma: no cover - phụ thuộc Windows foreground UI thật
            window = auto.GetForegroundControl()
            if not window:
                return None
            texts: list[str] = []
            for node, depth in auto.WalkTree(window, maxDepth=self.accessibility_max_depth):
                if len(texts) >= self.accessibility_max_nodes:
                    break
                name = _clean_text(getattr(node, "Name", ""), 180)
                if not name:
                    continue
                lowered = name.lower()
                # Lọc vài label UI nhiễu phổ biến; giữ caption/text có nghĩa.
                if lowered in {"minimize", "maximize", "close", "back", "forward", "refresh", "new tab"}:
                    continue
                texts.append(name)
            sample = _dedupe_join(texts, limit=360)
        except Exception:
            return None

        if not sample:
            return None
        tier, cap = SOURCE_TIERS["accessibility"]
        return ContentMetadata(
            source="accessibility",
            tier=tier,
            confidence=0.68 if _domain_matches(_domain_from_url(url), MEDIA_DOMAINS) else 0.58,
            reliability=0.62,
            privacy_level="normal",
            page_kind=_infer_page_kind(url=url, title=active_window),
            url=url,
            visible_text_sample=sample,
            collected_at=time.time(),
            stale_after_seconds=10.0,
            evidence_notes=("foreground accessibility tree text",),
        )

    def _from_browser_or_window_title(
        self,
        *,
        active_window: str,
        browser_url: str,
        browser_tabs: tuple[str, ...],
        now: float,
        privacy_level: str,
    ) -> ContentMetadata:
        title = _clean_text(browser_tabs[0] if browser_tabs else active_window, 180)
        source = "browser_url" if browser_url else "window_title"
        tier, cap = SOURCE_TIERS[source]
        confidence = cap if browser_url else min(cap, 0.36)
        return ContentMetadata(
            source=source,
            tier=tier,
            confidence=confidence,
            reliability=max(0.30, confidence - 0.05),
            privacy_level=privacy_level,
            page_kind=_infer_page_kind(url=browser_url, title=title),
            url=_clean_text(browser_url, 500),
            title=title,
            collected_at=now,
            stale_after_seconds=60.0,
            evidence_notes=("fallback from browser url/tab title" if browser_url else "fallback from active window title",),
        )

    def _read_fresh_json(self, paths: tuple[Path, ...], *, now: float, stale_after: float) -> dict[str, Any] | None:
        for path in paths:
            try:
                if not path.exists() or not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                ts = _coerce_float(data.get("timestamp") or data.get("collected_at") or data.get("updated_at"), 0.0)
                if ts > 0 and now - ts > stale_after:
                    continue
                # Nếu bridge không ghi timestamp thì dùng mtime làm TTL. Ít sang, nhưng còn hơn đoán.
                if ts <= 0 and now - path.stat().st_mtime > stale_after:
                    continue
                return data
            except Exception:
                continue
        return None

    def _privacy_level(self, *, active_window: str, url: str) -> str:
        domain = _domain_from_url(url)
        haystack = f"{active_window} {url} {domain}".lower()
        if _domain_matches(domain, COMMUNICATION_DOMAINS):
            return "private"
        if any(hint in haystack for hint in SENSITIVE_APP_HINTS):
            return "private"
        return "normal"


def _candidate_cache_paths(env_name: str, filename: str) -> list[Path]:
    paths: list[Path] = []
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        paths.append(Path(local_app_data) / "ERIS" / filename)
    paths.append(Path.cwd() / filename)
    paths.append(Path.cwd() / "memory_core" / filename)
    return paths


def _pick_matching_url(candidates: list[dict[str, Any]], current_url: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    current_domain = _domain_from_url(current_url)
    for item in candidates:
        if str(item.get("active") or "").lower() in {"1", "true", "yes"}:
            return item
    for item in candidates:
        if current_url and str(item.get("url") or "").strip() == current_url:
            return item
    for item in candidates:
        if current_domain and _domain_from_url(str(item.get("url") or "")) == current_domain:
            return item
    return None


def _infer_page_kind(*, url: str, title: str) -> str:
    domain = _domain_from_url(url)
    lowered_title = str(title or "").lower()
    path = urlparse(str(url or "")).path.lower()
    if _domain_matches(domain, ("youtube.com", "youtu.be", "music.youtube.com")):
        return "youtube_video" if "watch" in path or "youtu.be" in domain else "youtube"
    if _domain_matches(domain, ("tiktok.com",)):
        return "tiktok_video"
    if _domain_matches(domain, ("twitch.tv",)):
        return "stream"
    if _domain_matches(domain, ("spotify.com", "soundcloud.com")):
        return "music"
    if _domain_matches(domain, ("github.com", "gitlab.com")):
        return "repository"
    if _domain_matches(domain, COMMUNICATION_DOMAINS):
        return "communication"
    if any(token in lowered_title for token in ("docs", "documentation", "readme", "stackoverflow")):
        return "reference"
    return "unknown"


def _normalize_hashtags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = re.findall(r"#[\w\-À-ỹ]+", value, flags=re.UNICODE)
        if not raw:
            raw = [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = []
    tags: list[str] = []
    for item in raw:
        if not item:
            continue
        tag = item if item.startswith("#") else f"#{item}"
        tag = _clean_text(tag, 48)
        if tag and tag != "#":
            tags.append(tag)
    return tuple(dict.fromkeys(tags))[:12]


def _normalize_media_state(value: Any) -> str:
    state = str(value or "unknown").strip().lower()
    if state in {"playing", "play"}:
        return "playing"
    if state in {"paused", "pause"}:
        return "paused"
    if state in {"stopped", "ended", "idle"}:
        return "stopped"
    return "unknown"


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _dedupe_join(values: list[str], *, limit: int) -> str:
    seen: list[str] = []
    for value in values:
        clean = _clean_text(value, 180)
        if not clean or clean in seen:
            continue
        if any(clean in older for older in seen):
            continue
        seen.append(clean)
    return _clean_text(" | ".join(seen), limit)


def _choose_longer(left: str, right: str, *, limit: int) -> str:
    if len(str(right or "")) > len(str(left or "")):
        return _clean_text(right, limit)
    return _clean_text(left, limit)


def _text_overlap(left: str, right: str) -> bool:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    return overlap >= 3 or overlap / max(1, min(len(left_tokens), len(right_tokens))) >= 0.42


def _tokenize(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-ZÀ-ỹ0-9]{2,}", str(value or "").lower()) if token]


def _domain_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    domain = (parsed.netloc or "").lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_matches(domain: str, expected_domains: tuple[str, ...]) -> bool:
    domain = _domain_from_url(domain) if "://" in str(domain or "") else str(domain or "").lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return any(domain == expected or domain.endswith("." + expected) for expected in expected_domains if expected)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
