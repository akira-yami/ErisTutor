import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .contracts import TelemetrySnapshot, UserSignals


@dataclass(frozen=True)
class _ContextCandidate:
    """Một ứng viên phân loại bối cảnh.

    Lưu ý: đây là dataclass private để giữ `TelemetrySnapshot` cũ không bị đổi contract.
    Nếu muốn nâng cấp downstream sau này, có thể đưa class này sang `contracts.py` hoặc
    `context_classifier.py` rồi cho snapshot mang nguyên object classification.
    """

    kind: str
    score: float
    confidence: float
    evidence: tuple[str, ...]
    negative_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ContextClassification:
    """Kết quả phân loại bối cảnh có confidence thay vì chỉ trả một nhãn.

    `primary_kind` vẫn là string cũ để downstream không vỡ.
    `confidence` và `ambiguity` giúp SeLens hạ claim_style khi tín hiệu yếu.
    `candidates` giữ các nhãn cạnh tranh để debug false positive dễ hơn.
    """

    primary_kind: str
    confidence: float
    ambiguity: float
    candidates: tuple[_ContextCandidate, ...]
    surface_kind: str
    content_kind: str
    activity_intent: str
    evidence: tuple[str, ...]
    conflicts: tuple[str, ...]
    confidence_cap: float


class SeLens:
    """Tầng Se: biến telemetry thô thành snapshot có thể đưa vào cognition.

    Phiên bản này thay phần router `keyword in haystack -> return kind` bằng một
    classifier mềm:

    - keyword/app/domain/file extension chỉ là feature, không phải kết luận cuối.
    - mọi context kind được chấm điểm cạnh tranh.
    - nhãn cuối có confidence và ambiguity.
    - nếu mơ hồ, claim_style bị hạ để downstream không phán bậy.

    Nói ngắn: Se không cố đọc ý định người dùng. Se chỉ nói "bề mặt đang giống gì"
    với mức chắc chắn bao nhiêu. Ý nghĩa sâu hơn để MeaningLayer/Ni/Attention xử lý.
    """

    _GENERIC_PROMPT_EVENTS = {
        "coding_window_active",
        "reference_window_active",
        "communication_window_active",
        "entertainment_window_active",
        "focus_aid_media_window_active",
        "general_window_active",
        "ambiguous_context_visible",
        "browser_url_visible",
        "many_browser_tabs_visible",
        "many_windows_open",
        "long_idle_period",
        "system_monitoring_visible",
    }

    # Giữ các tuple cũ để tương thích test/runtime đang gọi private field.
    # Chúng không còn là router tuyệt đối; chỉ là feature có trọng số.
    _CODING_KEYWORDS = (
        "visual studio code",
        "vscode",
        "visual studio",
        "rider",
        "pycharm",
        "intellij",
        "godot",
        "terminal",
        "powershell",
        "cmd.exe",
        "git bash",
    )
    _DOC_KEYWORDS = (
        "docs",
        "documentation",
        "readme",
        "stackoverflow",
        "stack overflow",
        "developer.mozilla",
        "learn.microsoft",
        "python.org",
    )
    _COMMUNICATION_KEYWORDS = (
        "discord",
        "telegram",
        "slack",
        "messenger",
        "outlook",
        "zalo",
        "zoom",
        "teams",
        "meet",
    )
    _ENTERTAINMENT_KEYWORDS = (
        "netflix",
        "steam",
        "game",
        "twitch",
    )
    _BROWSER_SURFACE_KEYWORDS = (
        "chrome",
        "firefox",
        "edge",
        "brave",
        "opera",
        "browser",
    )

    # Các domain này ít gây false positive hơn keyword trong title.
    # Domain là signal mạnh hơn vì nó có cấu trúc rõ: github.com khác "github issue" trong text.
    _DOC_DOMAINS = (
        "developer.mozilla.org",
        "learn.microsoft.com",
        "docs.python.org",
        "python.org",
        "stackoverflow.com",
        "stackexchange.com",
        "readthedocs.io",
        "docs.godotengine.org",
    )
    _REPO_DOMAINS = (
        "github.com",
        "gitlab.com",
        "bitbucket.org",
    )
    _COMMUNICATION_DOMAINS = (
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
    _MEDIA_DOMAINS = (
        "youtube.com",
        "youtu.be",
        "music.youtube.com",
        "spotify.com",
        "soundcloud.com",
        "netflix.com",
        "twitch.tv",
        "tiktok.com",
    )
    _SYSTEM_KEYWORDS = (
        "settings",
        "task manager",
        "file explorer",
        "explorer.exe",
        "control panel",
        "device manager",
    )
    _CODE_FILE_EXTENSIONS = (
        ".py",
        ".pyw",
        ".cs",
        ".gd",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".java",
        ".kt",
        ".rs",
        ".go",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".toml",
        ".ini",
        ".md",
        ".sql",
        ".sh",
        ".ps1",
    )
    _TECHNICAL_ANCHORS = (
        "python",
        "c#",
        "csharp",
        "godot",
        "debug",
        "traceback",
        "exception",
        "compile",
        "build",
        "runtime",
        "api",
        "llm",
        "json",
        "regex",
        "router",
        "architecture",
        "docs",
        "tutorial",
        "stack overflow",
        "stackoverflow",
        "github",
        "readme",
        "class",
        "function",
        "method",
        "import",
        "error",
    )
    _RESEARCH_ANCHORS = (
        "docs",
        "documentation",
        "tutorial",
        "guide",
        "reference",
        "stackoverflow",
        "stack overflow",
        "readme",
        "issue",
        "pull request",
        "manual",
        "wiki",
    )
    _WEAK_CONTEXT_HINT_RULES = (
        (("youtube", "youtu.be"), "youtube visible"),
        (("tiktok",), "tiktok visible"),
        (("reddit",), "reddit visible"),
        (("gmail", "mail.google"), "gmail visible"),
        (("github",), "github visible"),
        (
            ("stackoverflow", "stack overflow", "developer.mozilla", "learn.microsoft", "python.org"),
            "reference docs visible",
        ),
    )

    _CLAIM_STYLE_ALIASES = {
        "direct_fact": "DIRECT_FACT",
        "observed_pattern": "OBSERVED_PATTERN",
        "weak_impression": "WEAK_IMPRESSION",
        "forbidden_inference": "FORBIDDEN_INFERENCE",
        "soft_grounded": "OBSERVED_PATTERN",
        "grounded": "OBSERVED_PATTERN",
        "idle_self_reflection": "OBSERVED_PATTERN",
        "game_observation": "OBSERVED_PATTERN",
        "no_evidence_refusal": "WEAK_IMPRESSION",
        "idle_silence_boundary": "WEAK_IMPRESSION",
        "uncertainty": "WEAK_IMPRESSION",
        "soft_claim": "WEAK_IMPRESSION",
    }

    def __init__(self):
        self._context_overrides = self._load_context_overrides()

    # raw dict vào, snapshot có nghĩa đi ra.
    def build_snapshot(self, raw: dict) -> TelemetrySnapshot:
        active_window = str(raw.get("active_window", "") or raw.get("active_app", "") or "Desktop")
        active_app = str(raw.get("active_app", "") or active_window)
        browser_url = str(raw.get("browser_url", "") or "")
        browser_tabs = self._normalize_string_tuple(raw.get("browser_tabs", ()))
        open_windows = self._normalize_string_tuple(raw.get("open_windows", ()))
        content_metadata = self._normalize_content_metadata(raw.get("content_metadata", {}))

        # Phân loại bối cảnh bằng classifier mềm.
        # Metadata DOM/OCR/accessibility được đưa vào như text phụ, không ghi đè tabs gốc.
        # Nói cách khác: SeLens được cho ăn evidence tốt hơn, nhưng không được tự phong thánh.
        classification_tabs = self._augment_browser_tabs_with_content_metadata(browser_tabs, content_metadata)
        context_classification = self._classify_window_context(active_window, browser_url, classification_tabs)
        active_window_kind = context_classification.primary_kind

        idle_seconds = float(raw.get("idle_seconds", 0.0) or 0.0)
        cpu_percent = float(raw.get("cpu_percent", 0.0) or 0.0)
        ram_percent = float(raw.get("ram_percent", 0.0) or 0.0)
        recent_events = self._build_recent_events(
            raw=raw,
            active_app=active_app,
            active_window_kind=active_window_kind,
            browser_url=browser_url,
            browser_tabs=browser_tabs,
            open_windows=open_windows,
            idle_seconds=idle_seconds,
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            context_classification=context_classification,
            content_metadata=content_metadata,
        )
        user_signals = self._build_user_signals(
            raw=raw,
            active_window_kind=active_window_kind,
            idle_seconds=idle_seconds,
            browser_tabs=browser_tabs,
            open_windows=open_windows,
        )
        evidence = self._build_evidence(
            raw=raw,
            active_app=active_app,
            active_window_kind=active_window_kind,
            browser_url=browser_url,
            browser_tabs=browser_tabs,
            open_windows=open_windows,
            idle_seconds=idle_seconds,
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            recent_events=recent_events,
            context_classification=context_classification,
            content_metadata=content_metadata,
        )
        claim_style = self._build_claim_style(
            raw=raw,
            active_window_kind=active_window_kind,
            idle_seconds=idle_seconds,
            evidence=evidence,
            context_classification=context_classification,
            content_metadata=content_metadata,
        )
        ambient_seed = int(raw.get("ambient_tone_score", raw.get("mood_score", 50)) or 50)

        return TelemetrySnapshot(
            captured_at=str(raw.get("captured_at", "") or raw.get("time", "") or ""),
            active_window=active_window,
            active_window_kind=active_window_kind,
            focus_summary=self._focus_summary_for_kind(active_window_kind, active_app, browser_url),
            active_app=active_app,
            active_process=str(raw.get("active_process", "") or raw.get("process_name", "") or ""),
            active_window_title=str(raw.get("active_window_title", "") or active_window),
            foreground_hwnd=int(raw.get("foreground_hwnd", 0) or 0),
            foreground_pid=int(raw.get("foreground_pid", 0) or 0),
            window_class=str(raw.get("window_class", "") or ""),
            focus_changed=bool(raw.get("focus_changed", False)),
            interaction_state=str(raw.get("interaction_state", "unknown") or "unknown"),
            browser_url=browser_url,
            browser_tabs=browser_tabs,
            open_windows=open_windows,
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            ambient_tone=self._label_ambient_tone(ambient_seed),
            tease_bias=self._label_tease_bias(ambient_seed),
            inferred_pos_x=float(raw.get("inferred_pos_x", 0.5) or 0.5),
            position_label=self._label_position(float(raw.get("inferred_pos_x", 0.5) or 0.5)),
            idle_seconds=idle_seconds,
            idle_label=self._label_idle(idle_seconds),
            recent_events=recent_events,
            user_signals=user_signals,
            evidence=evidence,
            claim_style=claim_style,
            vision_facts=self._normalize_string_tuple(raw.get("vision_facts", ())),
            content_metadata=content_metadata,
        )

    def build_machine_context(self, raw: dict) -> dict[str, object]:
        return self.build_snapshot(raw).to_machine_context()

    def _load_context_overrides(self) -> dict[str, tuple[str, ...]]:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        config_path = os.path.join(project_root, "focus_context_config.json")
        defaults = {
            "force_communication": (),
            "focus_aid_media": (),
        }
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return defaults

        if not isinstance(data, dict):
            return defaults

        normalized: dict[str, tuple[str, ...]] = {}
        for key, fallback in defaults.items():
            raw_values = data.get(key, ())
            if not isinstance(raw_values, (list, tuple)):
                normalized[key] = fallback
                continue
            normalized[key] = tuple(
                str(value).strip().lower()
                for value in raw_values
                if str(value).strip()
            )
        return normalized

    def _contains_override(self, haystack: str, key: str) -> bool:
        # Override vẫn là keyword config, nhưng dùng matcher có boundary để giảm match bừa.
        keywords = self._context_overrides.get(key, ())
        return self._contains_any_phrase(haystack, keywords)

    def format_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        machine_context = snapshot.to_machine_context()
        lines = [
            "Machine context observed from runtime.",
            f"- time: {machine_context['time']}",
            f"- active_app: {machine_context['active_app']}",
            f"- active_window_kind: {machine_context['active_window_kind']}",
            f"- focus_summary: {machine_context['focus_summary']}",
            f"- idle_seconds: {machine_context['idle_seconds']}",
            f"- recent_events: {json.dumps(machine_context['recent_events'], ensure_ascii=False)}",
            f"- user_signals: {json.dumps(machine_context['user_signals'], ensure_ascii=False, sort_keys=True)}",
            f"- evidence: {json.dumps(machine_context['evidence'], ensure_ascii=False)}",
            f"- content_metadata: {json.dumps(machine_context.get('content_metadata', {}), ensure_ascii=False, sort_keys=True)}",
            f"- claim_style: {machine_context['claim_style']}",
            f"- browser_url: {machine_context['browser_url'] or ''}",
            f"- browser_tabs: {json.dumps(machine_context['browser_tabs'], ensure_ascii=False)}",
            f"- open_windows: {json.dumps(machine_context['open_windows'], ensure_ascii=False)}",
            f"- cpu_percent: {machine_context['cpu_percent']}",
            f"- ram_percent: {machine_context['ram_percent']}",
            f"- ambient_tone: {machine_context['ambient_tone']}",
            f"- tease_bias: {machine_context['tease_bias']}",
            f"- position: {machine_context['position_label']} ({machine_context['inferred_pos_x']})",
            f"- idle_label: {machine_context['idle_label']}",
        ]
        return "\n".join(lines)

    # tạo context nhẹ cho LLM:
    def format_directive_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        lines = [
            "Ambient cues from runtime. Keep them mostly invisible in the reply.",
            f"- room_feel: {self._describe_prompt_scene(snapshot)}; {self._describe_prompt_pace(snapshot)}",
            f"- human_rhythm: {self._describe_prompt_focus(snapshot)}",
            f"- claim_style: {snapshot.claim_style}",  # kiểu phát ngôn an toàn hiện tại từ snapshot
            f"- ambient_tone: {snapshot.ambient_tone}; tease_bias={snapshot.tease_bias}",
            f"- visible_context: {self._format_visible_context(snapshot)}",
        ]
        screen_facts = self._build_prompt_screen_facts(snapshot)
        if screen_facts:
            lines.append(f"- screen_facts: {json.dumps(screen_facts, ensure_ascii=False)}")
        # Gắn thêm cue hướng dẫn giọng và chiến lược trả lời.
        cues = self._build_prompt_cues(snapshot)
        if cues:
            lines.append(f"- cues: {json.dumps(cues, ensure_ascii=False)}")
        return "\n".join(lines)

    def _format_visible_context(self, snapshot: TelemetrySnapshot) -> str:
        app = (snapshot.active_app or snapshot.active_window or "Desktop").strip()
        app = app.replace("\n", " ")
        if len(app) > 96:
            app = app[:93].rstrip() + "..."
        content_meta = getattr(snapshot, "content_metadata", {}) or {}
        content_bits = self._format_content_metadata_inline(content_meta)
        context_meta = self._extract_context_meta_from_evidence(snapshot.evidence)
        if context_meta:
            confidence = context_meta.get("confidence")
            ambiguity = context_meta.get("ambiguity")
            base = f"{app} | kind={snapshot.active_window_kind} | confidence={confidence:.2f} | ambiguity={ambiguity:.2f} | idle={snapshot.idle_label}"
        else:
            base = f"{app} | kind={snapshot.active_window_kind} | idle={snapshot.idle_label}"
        return f"{base} | content={content_bits}" if content_bits else base

    def _is_generic_prompt_event(self, event: str) -> bool:
        normalized = str(event or "").strip().lower()
        return normalized in self._GENERIC_PROMPT_EVENTS or normalized.endswith("_window_active")

    def _is_generic_prompt_evidence(self, evidence: str) -> bool:
        lowered = str(evidence or "").strip().lower()
        if not lowered:
            return True
        generic_prefixes = (
            "active window is ",
            "focus kind is ",
            "there has been no input for ",
            "browser target is visible:",
            "context classification:",
        )
        if lowered.startswith(generic_prefixes):
            return True
        if re.fullmatch(r"\d+ browser tabs are visible", lowered):
            return True
        if re.fullmatch(r"\d+ windows are open", lowered):
            return True
        if lowered.startswith("recent events include "):
            listed = lowered.removeprefix("recent events include ")
            events = [item.strip() for item in listed.split(",") if item.strip()]
            return bool(events) and all(self._is_generic_prompt_event(item) for item in events)
        return False

    def _build_prompt_screen_facts(self, snapshot: TelemetrySnapshot) -> list[str]:
        facts: list[str] = []
        content_facts = self._content_metadata_prompt_facts(getattr(snapshot, "content_metadata", {}) or {})
        facts.extend(content_facts)
        for item in getattr(snapshot, "vision_facts", ()) or ():
            facts.append(str(item))
        for event in snapshot.recent_events:
            if not self._is_generic_prompt_event(event):
                facts.append(str(event))
        for item in snapshot.evidence:
            if not self._is_generic_prompt_evidence(item):
                facts.append(str(item))
        return facts[:4]

    def _normalize_string_tuple(self, values) -> tuple[str, ...]:
        normalized: list[str] = []
        if not isinstance(values, (list, tuple)):
            return ()
        for value in values:
            if isinstance(value, str):
                item = value.strip()
            else:
                item = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if item:
                normalized.append(item)
        return tuple(normalized)

    def _build_recent_events(
        self,
        *,
        raw: dict,
        active_app: str,
        active_window_kind: str,
        browser_url: str,
        browser_tabs: tuple[str, ...],
        open_windows: tuple[str, ...],
        idle_seconds: float,
        cpu_percent: float,
        ram_percent: float,
        context_classification: _ContextClassification | None = None,
        content_metadata: dict[str, object] | None = None,
    ) -> tuple[str, ...]:
        explicit = self._normalize_string_tuple(raw.get("recent_events", ()))
        if explicit:
            return explicit

        events: list[str] = []
        if context_classification and (
            context_classification.confidence < 0.42 or context_classification.ambiguity >= 0.70
        ):
            events.append("ambiguous_context_visible")
        elif active_window_kind == "coding":
            events.append("coding_window_active")
        elif active_window_kind == "documentation":
            events.append("reference_window_active")
        elif active_window_kind == "communication":
            events.append("communication_window_active")
        elif active_window_kind == "focus_aid_media":
            events.append("focus_aid_media_window_active")
        elif active_window_kind == "entertainment":
            events.append("entertainment_window_active")
        else:
            events.append("general_window_active")

        content_source = str((content_metadata or {}).get("source", "") or "")
        content_tier = str((content_metadata or {}).get("tier", "") or "")
        content_confidence = self._coerce_float((content_metadata or {}).get("confidence"), 0.0)
        if content_source and content_confidence >= 0.55:
            events.append("content_metadata_visible")
        if content_tier in {"L4", "L5"} and content_confidence >= 0.75:
            events.append("strong_content_evidence_visible")

        if browser_url:
            events.append("browser_url_visible")
        if len(browser_tabs) >= 4:
            events.append("many_browser_tabs_visible")
        if len(open_windows) >= 7:
            events.append("many_windows_open")
        if idle_seconds >= 300:
            events.append("long_idle_period")
        if self._contains_any_phrase(active_app, ("task manager",)):
            events.append("system_monitoring_visible")
        return tuple(events)

    def _build_user_signals(
        self,
        *,
        raw: dict,
        active_window_kind: str,
        idle_seconds: float,
        browser_tabs: tuple[str, ...],
        open_windows: tuple[str, ...],
    ) -> UserSignals:
        explicit = raw.get("user_signals", {})
        if not isinstance(explicit, dict):
            explicit = {}

        typing_speed = str(explicit.get("typing_speed", "") or self._infer_typing_speed(idle_seconds))
        window_switching = str(
            explicit.get("window_switching", "")
            or self._infer_window_switching(len(open_windows), len(browser_tabs))
        )
        focus_pattern = str(
            explicit.get("focus_pattern", "")
            or self._infer_focus_pattern(
                active_window_kind=active_window_kind,
                idle_seconds=idle_seconds,
                open_window_count=len(open_windows),
                browser_tab_count=len(browser_tabs),
            )
        )
        return UserSignals(
            typing_speed=typing_speed,
            window_switching=window_switching,
            focus_pattern=focus_pattern,
        )

    def _infer_typing_speed(self, idle_seconds: float) -> str:
        if idle_seconds < 5:
            return "high"
        if idle_seconds < 30:
            return "moderate"
        if idle_seconds < 90:
            return "low"
        return "zero"

    def _infer_window_switching(self, open_window_count: int, browser_tab_count: int) -> str:
        spread = max(open_window_count, browser_tab_count)
        if spread >= 10:
            return "high"
        if spread >= 6:
            return "moderate"
        return "low"

    def _infer_focus_pattern(
        self,
        *,
        active_window_kind: str,
        idle_seconds: float,
        open_window_count: int,
        browser_tab_count: int,
    ) -> str:
        if active_window_kind in {"coding", "documentation"}:
            if idle_seconds >= 180:
                return "stuck"
            if max(open_window_count, browser_tab_count) >= 8:
                return "fragmented"
            return "deep_work"
        if active_window_kind == "communication":
            return "conversation"
        if active_window_kind == "focus_aid_media":
            if idle_seconds >= 180:
                return "ambient_media"
            if max(open_window_count, browser_tab_count) >= 8:
                return "split_attention"
            return "background_media"
        if active_window_kind == "entertainment":
            if idle_seconds >= 180:
                return "passive_consumption"
            return "active_consumption"
        if idle_seconds >= 300:
            return "absent"
        if max(open_window_count, browser_tab_count) >= 8:
            return "fragmented"
        return "steady"

    # tạo bằng chứng dạng text:
    def _build_evidence(
        self,
        *,
        raw: dict,
        active_app: str,
        active_window_kind: str,
        browser_url: str,
        browser_tabs: tuple[str, ...],
        open_windows: tuple[str, ...],
        idle_seconds: float,
        cpu_percent: float,
        ram_percent: float,
        recent_events: tuple[str, ...],
        context_classification: _ContextClassification | None = None,
        content_metadata: dict[str, object] | None = None,
    ) -> tuple[str, ...]:
        explicit = list(self._normalize_string_tuple(raw.get("evidence", ())))

        evidence = explicit or [
            f"active window is {active_app}",
            f"focus kind is {active_window_kind}",
            f"there has been no input for {int(idle_seconds)} seconds",
        ]
        if context_classification and not explicit:
            evidence.append(self._format_context_classification_evidence(context_classification))
        evidence.extend(self._build_content_metadata_evidence(content_metadata or {}))
        if browser_url and not explicit:
            parsed = urlparse(browser_url)
            browser_target = parsed.netloc or browser_url
            evidence.append(f"browser target is visible: {browser_target}")
        if not explicit:
            evidence.extend(
                self._build_weak_context_hints(
                    active_window=active_app,
                    browser_url=browser_url,
                    browser_tabs=browser_tabs,
                )
            )
            if browser_tabs:
                evidence.append(f"{len(browser_tabs)} browser tabs are visible")
            if open_windows:
                evidence.append(f"{len(open_windows)} windows are open")
            if recent_events:
                evidence.append(f"recent events include {', '.join(recent_events[:2])}")
        return tuple(dict.fromkeys(evidence))[:12]

    def _build_claim_style(
        self,
        *,
        raw: dict,
        active_window_kind: str,
        idle_seconds: float,
        evidence: tuple[str, ...],
        context_classification: _ContextClassification | None = None,
        content_metadata: dict[str, object] | None = None,
    ) -> str:
        explicit = str(raw.get("claim_style", "") or "").strip()
        if explicit:
            return self._normalize_claim_style(explicit)

        content_confidence = self._content_metadata_confidence(content_metadata or {})
        content_tier = str((content_metadata or {}).get("tier", "") or "")
        has_concrete_content = self._content_metadata_has_concrete_text(content_metadata or {})
        if has_concrete_content and content_tier == "L5" and content_confidence >= 0.86:
            return "OBSERVED_PATTERN"
        if has_concrete_content and content_tier == "L4" and content_confidence >= 0.78:
            return "OBSERVED_PATTERN"

        concrete_evidence = self._concrete_evidence(evidence)
        quality_score = self._evidence_quality_score(concrete_evidence)

        # Router mơ hồ thì không được cho phép giọng chắc, trừ khi DOM/OCR đã có fact cụ thể.
        # classification yếu không được biến thành "bạn đang ..." như sự thật.
        if context_classification and content_confidence < 0.72:
            if context_classification.confidence < 0.48:
                return "WEAK_IMPRESSION"
            if context_classification.ambiguity >= 0.66:
                return "WEAK_IMPRESSION"
            if context_classification.confidence_cap < 0.55:
                return "WEAK_IMPRESSION"

        if has_concrete_content and content_confidence >= 0.62:
            return "DIRECT_FACT"
        if active_window_kind in {"communication", "documentation", "system", "focus_aid_media"} and idle_seconds >= 600:
            return "WEAK_IMPRESSION"
        if not concrete_evidence or quality_score < 1.0:
            return "WEAK_IMPRESSION"
        if len(concrete_evidence) >= 2 and quality_score < 1.6:
            return "WEAK_IMPRESSION"
        if len(concrete_evidence) >= 2 and quality_score >= 2.0:
            return "OBSERVED_PATTERN"
        return "DIRECT_FACT"

    def _normalize_claim_style(self, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        canonical = normalized.upper()
        if canonical in {"DIRECT_FACT", "OBSERVED_PATTERN", "WEAK_IMPRESSION", "FORBIDDEN_INFERENCE"}:
            return canonical
        return self._CLAIM_STYLE_ALIASES.get(normalized.lower(), "WEAK_IMPRESSION")

    # ------------------------------------------------------------------
    # Context classifier mềm
    # ------------------------------------------------------------------
    def _classify_window_kind(self, active_window: str, browser_url: str, browser_tabs: tuple[str, ...]) -> str:
        """API cũ: trả về string kind.

        Runtime/test cũ có thể đang gọi trực tiếp private method này. Vì vậy không xóa.
        Bên trong nó gọi classifier mới rồi lấy `primary_kind`.
        """

        return self._classify_window_context(active_window, browser_url, browser_tabs).primary_kind

    def _classify_window_context(
        self,
        active_window: str,
        browser_url: str,
        browser_tabs: tuple[str, ...],
    ) -> _ContextClassification:
        """Phân loại bối cảnh bằng điểm số cạnh tranh.

        Khác bản cũ:
        - Không return sớm theo keyword.
        - Một tab/media không tự động thành entertainment.
        - Chrome/Edge chỉ là surface browser, không triệt tiêu docs/code/domain phía sau.
        - Có conflict và ambiguity để downstream giữ giọng mềm.
        """

        active_text = self._normalize_text(active_window)
        browser_text = self._normalize_text(" | ".join((browser_url, *browser_tabs)))
        combined_text = self._normalize_text(" | ".join(part for part in (active_window, browser_url, *browser_tabs) if part))
        browser_domain = self._domain_from_url(browser_url)

        surface_kind = self._infer_surface_kind(active_text, browser_domain, browser_url)
        content_kind = self._infer_content_kind(active_text, browser_text, browser_domain)

        scores: dict[str, float] = {
            "coding": 0.0,
            "documentation": 0.0,
            "communication": 0.0,
            "focus_aid_media": 0.0,
            "entertainment": 0.0,
            "system": 0.0,
            "general": 0.15,
        }
        evidence: dict[str, list[str]] = {kind: [] for kind in scores}
        negative: dict[str, list[str]] = {kind: [] for kind in scores}
        conflicts: list[str] = []

        def add(kind: str, amount: float, reason: str) -> None:
            scores[kind] = scores.get(kind, 0.0) + amount
            evidence.setdefault(kind, []).append(reason)

        def subtract(kind: str, amount: float, reason: str) -> None:
            scores[kind] = max(0.0, scores.get(kind, 0.0) - amount)
            negative.setdefault(kind, []).append(reason)

        # Surface app/process/title. Đây là signal mạnh nhất khi app là IDE/terminal/chat/system.
        if self._contains_any_phrase(active_text, self._CODING_KEYWORDS):
            add("coding", 2.35, "active app/title matches coding surface")
        if self._contains_any_file_extension(active_text):
            add("coding", 1.35, "active title contains code-like file extension")
        if self._contains_any_phrase(active_text, ("traceback", "exception", "compile failed", "build failed", "terminal")):
            add("coding", 1.10, "active title contains debug/build signal")

        if self._contains_any_phrase(active_text, self._DOC_KEYWORDS):
            add("documentation", 1.25, "active title matches documentation keyword")
        if self._domain_matches(browser_domain, self._DOC_DOMAINS):
            add("documentation", 2.25, "browser domain is documentation/reference")
        if self._domain_matches(browser_domain, self._REPO_DOMAINS):
            add("documentation", 1.35, "browser domain is repository/reference")
            if self._contains_any_file_extension(browser_text):
                add("coding", 0.75, "repository view contains code-like file")
        if surface_kind == "browser" and self._contains_any_phrase(browser_text, self._RESEARCH_ANCHORS):
            add("documentation", 1.15, "browser tabs contain research/reference anchors")
        if len(browser_tabs) >= 4 and self._contains_any_phrase(browser_text, ("github", "docs", "readme", "stack overflow", "stackoverflow")):
            add("documentation", 0.55, "tab spread suggests reference-driven browsing")

        if self._contains_any_phrase(active_text, self._COMMUNICATION_KEYWORDS):
            add("communication", 2.10, "active app/title matches communication surface")
        if self._domain_matches(browser_domain, self._COMMUNICATION_DOMAINS):
            add("communication", 2.20, "browser domain is communication/mail/meeting")

        if self._contains_any_phrase(combined_text, self._SYSTEM_KEYWORDS):
            add("system", 2.15, "window/url contains system surface")

        if surface_kind == "browser":
            add("general", 0.70, "active app looks like browser surface")

        # Override config là bias mạnh, nhưng không còn là kết luận tuyệt đối.
        if self._contains_override(combined_text, "force_communication"):
            add("communication", 2.60, "config override: force_communication")
        if self._contains_override(combined_text, "focus_aid_media"):
            add("focus_aid_media", 1.70, "config override: focus_aid_media")

        # Media domain cần xét anchor kỹ thuật trước khi gọi là giải trí.
        media_domain = self._domain_matches(browser_domain, self._MEDIA_DOMAINS)
        technical_anchor = self._has_technical_anchor(active_text, browser_text)
        research_anchor = self._contains_any_phrase(combined_text, self._RESEARCH_ANCHORS)

        if media_domain:
            dom_content_hint = self._contains_any_phrase(browser_text, ("browser_dom", "dom_ocr_agreement", "youtube_video", "tiktok_video"))
            add("focus_aid_media", 0.80 if dom_content_hint else 0.65, "media domain visible")
            add("entertainment", 0.70 if dom_content_hint else 0.55, "media domain may be leisure")
            if dom_content_hint:
                subtract("general", 0.25, "concrete media metadata weakens generic browser reading")
            if technical_anchor or research_anchor:
                add("documentation", 0.95, "media title/tabs contain technical or research anchor")
                add("focus_aid_media", 0.85, "media may support current work/research")
                subtract("entertainment", 0.45, "technical/research anchor weakens leisure reading")
                conflicts.append("media_surface_with_technical_anchor")

        # Keyword giải trí trong title rất yếu, vì "game engine", "game theory", "Steam docs"
        # đều làm router keyword cũ chết rất xấu.
        if self._contains_any_phrase(active_text, ("netflix", "twitch")):
            add("entertainment", 1.55, "active title matches strong entertainment surface")
        if self._contains_any_phrase(active_text, ("steam",)):
            add("entertainment", 1.05, "active title matches game store surface")
        if self._contains_any_phrase(active_text, ("game",)):
            if technical_anchor or research_anchor:
                add("documentation", 0.45, "game keyword appears with technical/research anchor")
                subtract("entertainment", 0.30, "game keyword is ambiguous in technical context")
                conflicts.append("ambiguous_game_keyword")
            else:
                add("entertainment", 0.45, "active title contains weak game keyword")

        # Nếu code/docs đủ rõ, media/tab giải trí chỉ còn là conflict hoặc background, không được thắng quá dễ.
        if scores["coding"] >= 1.80 and scores["entertainment"] > 0.0:
            subtract("entertainment", 0.25, "coding surface nearby reduces entertainment certainty")
            add("focus_aid_media", 0.25, "possible background media while coding")
            conflicts.append("coding_surface_with_media_hint")
        if scores["documentation"] >= 1.80 and scores["entertainment"] > 0.0:
            subtract("entertainment", 0.20, "reference context reduces entertainment certainty")
            conflicts.append("reference_context_with_media_hint")

        # Nếu mọi nhãn đều yếu, general thắng nhưng confidence bị cap thấp.
        sorted_items = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        primary_kind, primary_score = sorted_items[0]
        second_kind, second_score = sorted_items[1]

        if primary_score < 0.65:
            primary_kind = "general"
            primary_score = max(scores.get("general", 0.15), 0.35)
            second_score = sorted_items[0][1]
            evidence.setdefault("general", []).append("no strong context feature")

        gap = max(0.0, primary_score - second_score)
        ambiguity = self._clamp(1.0 - (gap / 1.45), 0.0, 1.0)
        if primary_score < 1.10:
            ambiguity = self._clamp(ambiguity + 0.18, 0.0, 1.0)

        confidence_cap = self._context_confidence_cap(primary_kind, surface_kind, content_kind, primary_score)
        confidence = self._score_to_confidence(primary_score, gap, ambiguity)
        confidence = min(confidence, confidence_cap)

        # Khi chênh lệch quá nhỏ, đừng quá chắc dù score cao. Đời đã đủ nhiều người chắc sai.
        if gap < 0.25 and primary_kind != "general":
            confidence = min(confidence, 0.54)
            conflicts.append(f"close_context_scores:{primary_kind}_vs_{second_kind}")

        candidates = tuple(
            _ContextCandidate(
                kind=kind,
                score=round(score, 3),
                confidence=round(self._score_to_confidence(score, max(0.0, score - second_score), ambiguity), 3),
                evidence=tuple(evidence.get(kind, ())[:4]),
                negative_evidence=tuple(negative.get(kind, ())[:3]),
            )
            for kind, score in sorted_items
            if score > 0.0
        )
        activity_intent = self._infer_activity_intent(primary_kind, surface_kind, content_kind, confidence, ambiguity)
        summary_evidence = tuple(evidence.get(primary_kind, ())[:4]) or ("no strong context feature",)

        return _ContextClassification(
            primary_kind=primary_kind,
            confidence=round(confidence, 3),
            ambiguity=round(ambiguity, 3),
            candidates=candidates,
            surface_kind=surface_kind,
            content_kind=content_kind,
            activity_intent=activity_intent,
            evidence=summary_evidence,
            conflicts=tuple(dict.fromkeys(conflicts))[:4],
            confidence_cap=round(confidence_cap, 3),
        )

    def _infer_surface_kind(self, active_text: str, browser_domain: str, browser_url: str) -> str:
        if self._contains_any_phrase(active_text, self._CODING_KEYWORDS):
            return "ide_or_terminal"
        if self._contains_any_phrase(active_text, self._COMMUNICATION_KEYWORDS):
            return "communication_app"
        if self._contains_any_phrase(active_text, self._SYSTEM_KEYWORDS):
            return "system_surface"
        if browser_url or self._contains_any_phrase(active_text, self._BROWSER_SURFACE_KEYWORDS):
            return "browser"
        if self._contains_any_phrase(active_text, self._ENTERTAINMENT_KEYWORDS):
            return "media_or_game_surface"
        return "desktop_or_unknown"

    def _infer_content_kind(self, active_text: str, browser_text: str, browser_domain: str) -> str:
        combined = f"{active_text} | {browser_text}"
        if self._contains_any_file_extension(combined):
            return "code_or_project_file"
        if self._domain_matches(browser_domain, self._DOC_DOMAINS) or self._contains_any_phrase(combined, self._RESEARCH_ANCHORS):
            return "reference"
        if self._domain_matches(browser_domain, self._REPO_DOMAINS):
            return "repository"
        if self._domain_matches(browser_domain, self._COMMUNICATION_DOMAINS):
            return "communication"
        if self._domain_matches(browser_domain, self._MEDIA_DOMAINS):
            return "media"
        if self._contains_any_phrase(combined, self._SYSTEM_KEYWORDS):
            return "system"
        return "unknown"

    def _infer_activity_intent(
        self,
        primary_kind: str,
        surface_kind: str,
        content_kind: str,
        confidence: float,
        ambiguity: float,
    ) -> str:
        # Activity intent là diễn giải nhẹ, không phải ToM. Nếu mơ hồ thì trả unknown.
        if confidence < 0.45 or ambiguity >= 0.72:
            return "unknown"
        if primary_kind == "coding":
            return "technical_focus"
        if primary_kind == "documentation":
            return "research"
        if primary_kind == "communication":
            return "communication"
        if primary_kind == "focus_aid_media":
            return "supportive_background"
        if primary_kind == "entertainment":
            return "recovery_or_leisure"
        if primary_kind == "system":
            return "system_maintenance"
        if surface_kind == "browser" and content_kind == "unknown":
            return "general_browsing"
        return "unknown"

    def _context_confidence_cap(self, primary_kind: str, surface_kind: str, content_kind: str, primary_score: float) -> float:
        # Không phải kind nào cũng được chắc như nhau. General/browser mơ hồ phải bị cap thấp.
        if primary_kind == "general":
            return 0.56 if surface_kind == "browser" else 0.48
        if primary_kind == "focus_aid_media":
            return 0.72
        if primary_kind == "entertainment" and primary_score < 1.40:
            return 0.62
        if content_kind == "unknown" and primary_score < 1.20:
            return 0.58
        return 0.92

    def _score_to_confidence(self, score: float, gap: float, ambiguity: float) -> float:
        # Score cao chưa đủ. Cần gap với ứng viên thứ hai để tránh "hai nhãn cùng đúng".
        score_part = self._clamp(score / 3.0, 0.0, 1.0) * 0.66
        gap_part = self._clamp(gap / 1.6, 0.0, 1.0) * 0.26
        ambiguity_penalty = ambiguity * 0.18
        return self._clamp(0.18 + score_part + gap_part - ambiguity_penalty, 0.18, 0.92)

    def _format_context_classification_evidence(self, classification: _ContextClassification) -> str:
        reason = "; ".join(classification.evidence[:2])
        if classification.conflicts:
            reason = f"{reason}; conflicts={','.join(classification.conflicts[:2])}"
        return (
            "context classification: "
            f"kind={classification.primary_kind} "
            f"confidence={classification.confidence:.2f} "
            f"ambiguity={classification.ambiguity:.2f} "
            f"surface={classification.surface_kind} "
            f"content={classification.content_kind} "
            f"intent={classification.activity_intent} "
            f"reason={reason}"
        )

    def _extract_context_meta_from_evidence(self, evidence: tuple[str, ...]) -> dict[str, float] | None:
        for item in evidence or ():
            lowered = str(item or "").lower()
            if not lowered.startswith("context classification:"):
                continue
            confidence_match = re.search(r"confidence=([0-9.]+)", lowered)
            ambiguity_match = re.search(r"ambiguity=([0-9.]+)", lowered)
            if confidence_match and ambiguity_match:
                try:
                    return {
                        "confidence": float(confidence_match.group(1)),
                        "ambiguity": float(ambiguity_match.group(1)),
                    }
                except ValueError:
                    return None
        return None

    def _build_focus_summary(self, active_window_kind: str, active_app: str, browser_url: str) -> str:
        if active_window_kind == "coding":
            return f"bạn đang ở bối cảnh code hoặc project: {active_app}"
        if active_window_kind == "documentation":
            return f"bạn đang đọc tài liệu hoặc reference: {browser_url or active_app}"
        if active_window_kind == "communication":
            return f"bạn đang ở bối cảnh giao tiếp: {active_app}"
        if active_window_kind == "focus_aid_media":
            return f"Có media nền hoặc nguồn hỗ trợ tập trung gần bối cảnh hiện tại: {active_app}"
        if active_window_kind == "entertainment":
            return f"bạn đang nghiêng sang giải trí hoặc nghỉ nhịp: {active_app}"
        if active_window_kind == "system":
            return f"bạn đang thao tác hệ thống: {active_app}"
        return f"bạn đang ở bối cảnh tổng quát: {active_app}"

    def _label_position(self, inferred_pos_x: float) -> str:
        if inferred_pos_x <= 0.15:
            return "mép trái"
        if inferred_pos_x >= 0.85:
            return "mép phải"
        if 0.4 <= inferred_pos_x <= 0.6:
            return "giữa màn hình"
        return "lệch tâm"

    def _label_idle(self, idle_seconds: float) -> str:
        if idle_seconds >= 600:
            return "rất lâu không tương tác"
        if idle_seconds >= 180:
            return "đang nhàn rỗi"
        if idle_seconds >= 45:
            return "đang chậm lại"
        return "đang hoạt động"

    def _describe_prompt_scene(self, snapshot: TelemetrySnapshot) -> str:
        kind = snapshot.active_window_kind
        if kind == "coding":
            return "workbench glow, project nearby"
        if kind == "documentation":
            return "reference material nearby"
        if kind == "communication":
            return "conversation nearby"
        if kind == "focus_aid_media":
            return "ambient media nearby"
        if kind == "entertainment":
            return "leisure nearby"
        if kind == "system":
            return "system surface nearby"
        return "ordinary desktop quiet"

    def _describe_prompt_pace(self, snapshot: TelemetrySnapshot) -> str:
        if snapshot.idle_seconds >= 600:
            return "very quiet for a while"
        if snapshot.idle_seconds >= 180:
            return "a noticeable pause is present"
        if snapshot.idle_seconds >= 45:
            return "slowing down"
        return "active"

    def _describe_prompt_focus(self, snapshot: TelemetrySnapshot) -> str:
        signals = snapshot.user_signals
        focus_pattern = signals.focus_pattern or "unknown"
        if focus_pattern in {"deep_work", "conversation", "steady"}:
            return "settled"
        if focus_pattern in {"stuck", "absent"}:
            return "quiet pause"
        if focus_pattern in {
            "fragmented",
            "passive_consumption",
            "active_consumption",
            "background_media",
            "ambient_media",
            "split_attention",
        }:
            return "attention lightly spread"
        return "unclear, keep claims soft"

    def _build_prompt_cues(self, snapshot: TelemetrySnapshot) -> list[str]:
        cues: list[str] = [
            "Telemetry is weak ambient evidence, not proof of mood, intent, productivity, or laziness.",
            "Use it for pacing and light presence only.",
        ]
        kind = snapshot.active_window_kind
        focus_pattern = snapshot.user_signals.focus_pattern or ""
        switching = snapshot.user_signals.window_switching or ""
        context_meta = self._extract_context_meta_from_evidence(snapshot.evidence)

        if context_meta and (context_meta["confidence"] < 0.50 or context_meta["ambiguity"] >= 0.66):
            cues.append("context classification is ambiguous; do not infer intent from window kind alone")

        if kind in {"coding", "documentation"}:
            cues.append("keep close to the work context instead of narrating the desktop")
        elif kind == "communication":
            cues.append("treat this as light conversation, not a productivity lecture")
        elif kind == "focus_aid_media":
            cues.append("treat visible media as possible background aid, not automatic leisure or avoidance")
        elif kind == "entertainment":
            cues.append("do not overcorrect a leisure moment unless drift is obvious")

        if focus_pattern == "stuck":
            cues.append("there may be a quiet stall, so a small concrete observation is enough")
        elif focus_pattern in {"fragmented", "split_attention"} or switching == "high":
            cues.append("attention may be spread out, but avoid quoting counts or inventories")
        elif focus_pattern == "deep_work":
            cues.append("respect concentration and keep the line brief")

        if snapshot.claim_style == "OBSERVED_PATTERN":
            cues.append("observed patterns are allowed only when grounded by concrete, non-generic evidence")
        elif snapshot.claim_style == "WEAK_IMPRESSION":
            cues.append("use explicit hedge words such as có lẽ, tôi nghĩ, or tôi có cảm giác")

        return cues[:7]

    # ------------------------------------------------------------------
    # Content metadata L0-L5
    # ------------------------------------------------------------------
    def _normalize_content_metadata(self, value) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        allowed_scalars = {
            "source",
            "tier",
            "privacy_level",
            "page_kind",
            "url",
            "title",
            "creator",
            "caption_sample",
            "visible_text_sample",
            "media_state",
        }
        normalized: dict[str, object] = {}
        for key in allowed_scalars:
            raw_value = value.get(key)
            if raw_value is None:
                continue
            text = str(raw_value).replace("\n", " ").replace("\t", " ").strip()
            text = re.sub(r"\s+", " ", text)
            if key in {"title", "caption_sample", "visible_text_sample"} and len(text) > 360:
                text = text[:357].rstrip() + "..."
            elif len(text) > 500:
                text = text[:497].rstrip() + "..."
            if text:
                normalized[key] = text
        hashtags = value.get("hashtags") or ()
        if isinstance(hashtags, str):
            raw_tags = re.findall(r"#[\w\-À-ỹ]+", hashtags, flags=re.UNICODE)
            if not raw_tags:
                raw_tags = [part.strip() for part in re.split(r"[,\s]+", hashtags) if part.strip()]
        elif isinstance(hashtags, (list, tuple, set)):
            raw_tags = [str(item).strip() for item in hashtags if str(item).strip()]
        else:
            raw_tags = []
        normalized_tags: list[str] = []
        for tag in raw_tags:
            tag = tag if tag.startswith("#") else f"#{tag}"
            tag = tag[:48]
            if tag and tag != "#":
                normalized_tags.append(tag)
        if normalized_tags:
            normalized["hashtags"] = tuple(dict.fromkeys(normalized_tags))[:12]
        for key in ("confidence", "reliability", "collected_at", "stale_after_seconds"):
            if key in value:
                normalized[key] = self._coerce_float(value.get(key), 0.0)
        notes = self._normalize_string_tuple(value.get("evidence_notes", ()))[:6]
        conflicts = self._normalize_string_tuple(value.get("conflicts", ()))[:4]
        if notes:
            normalized["evidence_notes"] = notes
        if conflicts:
            normalized["conflicts"] = conflicts
        return normalized

    def _augment_browser_tabs_with_content_metadata(
        self,
        browser_tabs: tuple[str, ...],
        content_metadata: dict[str, object],
    ) -> tuple[str, ...]:
        extras: list[str] = []
        for key in ("title", "creator", "caption_sample", "visible_text_sample", "page_kind", "source", "tier"):
            value = str(content_metadata.get(key, "") or "").strip()
            if value:
                extras.append(value)
        hashtags = content_metadata.get("hashtags", ())
        if isinstance(hashtags, (list, tuple)) and hashtags:
            extras.append(" ".join(str(tag) for tag in hashtags[:8]))
        return tuple(dict.fromkeys((*browser_tabs, *extras)))[:14]

    def _build_content_metadata_evidence(self, content_metadata: dict[str, object]) -> tuple[str, ...]:
        if not content_metadata:
            return ()
        source = str(content_metadata.get("source", "") or "").strip()
        tier = str(content_metadata.get("tier", "") or "").strip()
        confidence = self._content_metadata_confidence(content_metadata)
        privacy_level = str(content_metadata.get("privacy_level", "normal") or "normal")
        page_kind = str(content_metadata.get("page_kind", "unknown") or "unknown")
        if not source:
            return ()
        evidence = [
            "content metadata: "
            f"source={source} tier={tier or 'unknown'} confidence={confidence:.2f} "
            f"privacy={privacy_level} page_kind={page_kind}"
        ]
        title = str(content_metadata.get("title", "") or "").strip()
        creator = str(content_metadata.get("creator", "") or "").strip()
        caption = str(content_metadata.get("caption_sample", "") or "").strip()
        visible_text = str(content_metadata.get("visible_text_sample", "") or "").strip()
        media_state = str(content_metadata.get("media_state", "") or "").strip()
        hashtags = content_metadata.get("hashtags", ())
        if title:
            evidence.append(f"content title visible: {self._trim_text(title, 160)}")
        if creator:
            evidence.append(f"content creator visible: {self._trim_text(creator, 96)}")
        if caption:
            evidence.append(f"visible video caption: {self._trim_text(caption, 220)}")
        if isinstance(hashtags, (list, tuple)) and hashtags:
            evidence.append("visible hashtags: " + " ".join(str(tag) for tag in hashtags[:8]))
        if visible_text and visible_text != caption:
            label = "accessibility text visible" if source == "accessibility" else "visible text sample"
            evidence.append(f"{label}: {self._trim_text(visible_text, 240)}")
        if media_state and media_state != "unknown":
            evidence.append(f"media state visible: {media_state}")
        conflicts = self._normalize_string_tuple(content_metadata.get("conflicts", ()))[:2]
        if conflicts:
            evidence.append("content metadata conflicts: " + ", ".join(conflicts))
        return tuple(dict.fromkeys(evidence))[:7]

    def _content_metadata_prompt_facts(self, content_metadata: dict[str, object]) -> list[str]:
        if not content_metadata:
            return []
        confidence = self._content_metadata_confidence(content_metadata)
        if confidence < 0.55:
            return []
        source = str(content_metadata.get("source", "") or "")
        tier = str(content_metadata.get("tier", "") or "")
        facts: list[str] = []
        title = str(content_metadata.get("title", "") or "").strip()
        caption = str(content_metadata.get("caption_sample", "") or "").strip()
        visible_text = str(content_metadata.get("visible_text_sample", "") or "").strip()
        hashtags = content_metadata.get("hashtags", ())
        if title:
            facts.append(f"content title: {self._trim_text(title, 150)}")
        if caption:
            facts.append(f"visible caption: {self._trim_text(caption, 180)}")
        if isinstance(hashtags, (list, tuple)) and hashtags:
            facts.append("visible hashtags: " + " ".join(str(tag) for tag in hashtags[:6]))
        if visible_text and not caption:
            facts.append(f"visible text: {self._trim_text(visible_text, 180)}")
        if facts:
            facts.append(f"content evidence tier={tier or 'unknown'} source={source} confidence={confidence:.2f}")
        return facts[:4]

    def _format_content_metadata_inline(self, content_metadata: dict[str, object]) -> str:
        if not content_metadata:
            return ""
        confidence = self._content_metadata_confidence(content_metadata)
        if confidence < 0.45:
            return ""
        title = str(content_metadata.get("title", "") or "").strip()
        caption = str(content_metadata.get("caption_sample", "") or "").strip()
        source = str(content_metadata.get("source", "") or "").strip()
        tier = str(content_metadata.get("tier", "") or "").strip()
        text = caption or title
        if not text:
            return f"{source}/{tier} conf={confidence:.2f}" if source else ""
        return f"{self._trim_text(text, 72)} ({source}/{tier} conf={confidence:.2f})"

    def _content_metadata_confidence(self, content_metadata: dict[str, object]) -> float:
        return self._clamp(self._coerce_float(content_metadata.get("confidence"), 0.0), 0.0, 1.0)

    def _content_metadata_has_concrete_text(self, content_metadata: dict[str, object]) -> bool:
        if not content_metadata:
            return False
        if str(content_metadata.get("privacy_level", "normal") or "normal") in {"private", "sensitive"}:
            return False
        if str(content_metadata.get("title", "") or "").strip():
            return True
        if str(content_metadata.get("caption_sample", "") or "").strip():
            return True
        if str(content_metadata.get("visible_text_sample", "") or "").strip():
            return True
        hashtags = content_metadata.get("hashtags", ())
        return isinstance(hashtags, (list, tuple)) and bool(hashtags)

    def _trim_text(self, value: str, limit: int) -> str:
        text = str(value or "").replace("\n", " ").replace("\t", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > limit:
            return text[: max(0, limit - 3)].rstrip() + "..."
        return text

    def _coerce_float(self, value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _looks_like_browser_surface(self, active_window: str, browser_url: str, browser_tabs: tuple[str, ...]) -> bool:
        title = self._normalize_text(active_window)
        return bool(browser_url) or self._contains_any_phrase(title, self._BROWSER_SURFACE_KEYWORDS)

    def _build_weak_context_hints(
        self,
        *,
        active_window: str,
        browser_url: str,
        browser_tabs: tuple[str, ...],
    ) -> tuple[str, ...]:
        haystack = self._normalize_text(" | ".join((active_window, browser_url, *browser_tabs)))
        hints: list[str] = []
        for needles, label in self._WEAK_CONTEXT_HINT_RULES:
            if self._contains_any_phrase(haystack, needles):
                hints.append(f"weak context hint: {label}")
        return tuple(dict.fromkeys(hints))[:3]

    def _is_weak_context_hint_evidence(self, evidence: str) -> bool:
        lowered = str(evidence or "").strip().lower()
        return lowered.startswith("weak context hint:")

    def _concrete_evidence(self, evidence: tuple[str, ...]) -> tuple[str, ...]:
        items = [
            item
            for item in evidence
            if item
            and not self._is_generic_prompt_evidence(item)
            and not self._is_weak_context_hint_evidence(item)
        ]
        return tuple(items)

    def _evidence_quality_score(self, evidence: tuple[str, ...]) -> float:
        return sum(self._evidence_item_quality(item) for item in evidence)

    def _evidence_item_quality(self, evidence: str) -> float:
        lowered = str(evidence or "").strip().lower()
        if not lowered:
            return 0.0
        if lowered.startswith("content metadata:"):
            confidence_match = re.search(r"confidence=([0-9.]+)", lowered)
            try:
                confidence = float(confidence_match.group(1)) if confidence_match else 0.0
            except ValueError:
                confidence = 0.0
            if "tier=l5" in lowered:
                return 2.4
            if "tier=l4" in lowered:
                return 2.0
            if "tier=l3" in lowered:
                return 1.4
            return 0.6 + confidence
        if lowered.startswith(("content title visible:", "visible video caption:", "visible hashtags:", "accessibility text visible:", "visible text sample:")):
            return 1.6
        if any(
            marker in lowered
            for marker in (
                "browser target is visible",
                "browser tabs are visible",
                "windows are open",
                "browser_url_visible",
                "context classification:",
            )
        ):
            return 0.2
        if any(
            marker in lowered
            for marker in (
                "error",
                "traceback",
                "exception",
                "failed build",
                "compile failed",
                "task manager",
                "file explorer",
                "settings",
                "zoom",
                "teams",
                "meet",
                "meeting",
                "edited",
                "saved",
                "selection",
            )
        ):
            return 1.3
        if any(
            marker in lowered
            for marker in (
                "debug",
                "build",
                "terminal",
                "documentation",
                "reference",
                "readme",
                "github",
                "youtube",
                "tiktok",
                "reddit",
                "gmail",
            )
        ):
            return 0.6
        return 0.9

    def _label_ambient_tone(self, ambient_seed: int) -> str:
        if ambient_seed >= 80:
            return "bright"
        if ambient_seed <= 30:
            return "dry"
        return "neutral"

    def _label_tease_bias(self, ambient_seed: int) -> str:
        if ambient_seed >= 80:
            return "playful"
        if ambient_seed <= 30:
            return "wry"
        return "neutral"

    def _focus_summary_for_kind(self, active_window_kind: str, active_app: str, browser_url: str) -> str:
        if active_window_kind == "focus_aid_media":
            return f"Background media may be present alongside the main task: {active_app}"
        return self._build_focus_summary(active_window_kind, active_app, browser_url)

    # ------------------------------------------------------------------
    # Helper xử lý text/domain/keyword. Để hết ở cuối cho class dễ đọc.
    # ------------------------------------------------------------------
    def _normalize_text(self, value: str) -> str:
        text = str(value or "").replace("\n", " ").replace("\t", " ").lower()
        return re.sub(r"\s+", " ", text).strip()

    def _domain_from_url(self, url: str) -> str:
        parsed = urlparse(str(url or ""))
        domain = (parsed.netloc or "").lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def _domain_matches(self, domain: str, expected_domains: tuple[str, ...]) -> bool:
        domain = self._domain_from_url(domain) if "://" in str(domain or "") else str(domain or "").lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        for expected in expected_domains:
            expected = str(expected or "").lower().strip()
            if not expected:
                continue
            if domain == expected or domain.endswith("." + expected):
                return True
        return False

    def _phrase_in_text(self, text: str, phrase: str) -> bool:
        text = self._normalize_text(text)
        phrase = self._normalize_text(phrase)
        if not text or not phrase:
            return False

        # Với phrase chứa ký tự đặc biệt như cmd.exe, c#, .py thì substring có kiểm soát ổn hơn.
        if re.search(r"[^a-z0-9\s]", phrase):
            return phrase in text

        # Với phrase chữ thường, dùng boundary để "game" không match lung tung trong từ dài.
        pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
        return re.search(pattern, text) is not None

    def _contains_any_phrase(self, text: str, phrases: tuple[str, ...]) -> bool:
        return any(self._phrase_in_text(text, phrase) for phrase in phrases)

    def _contains_any_file_extension(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        for extension in self._CODE_FILE_EXTENSIONS:
            # Extension cần boundary cuối để `.py` trong `copy` không match bậy.
            pattern = rf"{re.escape(extension.lower())}(?![a-z0-9])"
            if re.search(pattern, normalized):
                return True
        return False

    def _has_technical_anchor(self, active_text: str, browser_text: str) -> bool:
        combined = f"{active_text} | {browser_text}"
        return self._contains_any_phrase(combined, self._TECHNICAL_ANCHORS) or self._contains_any_file_extension(combined)

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))
