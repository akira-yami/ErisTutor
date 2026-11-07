from __future__ import annotations

"""
runtime/source_grounding.py

Tầng này là "cổng nguồn" trước khi ERIS gọi LLM hoặc dựng câu trả lời.

Nhiệm vụ:
- Phát hiện user đang hỏi về file cụ thể.
- Phát hiện user đang trỏ vào màn hình hiện tại.
- Phát hiện user có paste code hay không.
- Phân loại nguồn trả lời chính.
- Cap confidence theo độ chắc của nguồn.
- Tạo prompt context cho LLM để tránh overclaim.
- Tạo grounding signal/probe policy khi thiếu evidence: thiếu dữ kiện không đồng nghĩa cấm tư duy.

Nguyên tắc:
- File này KHÔNG đọc file thật, KHÔNG OCR, KHÔNG gọi web, KHÔNG ghi memory.
- Nó chỉ nhìn các tín hiệu đã có trong frame/runtime và quyết định "được phép dựa vào nguồn nào".
- Nếu thiếu evidence, nó phải cấm claim khẳng định; nhưng vẫn được chuyển sang hypothesis/probe nếu khoảng trống thông tin còn đáng điều tra.
"""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence


SourceKind = Literal[
    "pasted_code",
    "uploaded_file",
    "filename_only",
    "chess_bridge",
    "current_screen",
    "live_vision",
    "user_text",
    "memory",
    "web",
    "runtime_state",
    "unknown",
]

EvidenceRole = Literal["primary", "supporting", "ambient"]
EvidenceStage = Literal["pre_generation", "tool_result", "post_tool"]


@dataclass(slots=True)
class GroundingEvidence:
    """Một mẩu provenance có vai trò rõ ràng trong câu trả lời.

    primary: nguồn trực tiếp tạo ra nội dung trả lời.
    supporting: lịch sử/memory/user text bổ trợ cho nguồn chính.
    ambient: màn hình/app hiện tại; chỉ được dùng để kiểm claim có tính situated.
    """

    evidence_id: str
    source: SourceKind
    role: EvidenceRole
    text: str
    confidence: float = 0.7
    stage: EvidenceStage = "pre_generation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceCandidate:
    """
    Một nguồn có thể dùng để trả lời.

    confidence ở đây là confidence của quyền dùng nguồn, không phải confidence của câu trả lời cuối.
    Ví dụ: user paste code rõ ràng -> pasted_code confidence cao.
    User nói "nhìn màn hình đi" nhưng không có OCR/vision fact -> current_screen candidate vẫn có,
    nhưng missing evidence sẽ kéo fallback.
    """

    kind: SourceKind
    confidence: float
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    evidence_preview: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceFallbackDecision:
    """
    Compatibility signal khi thiếu evidence.

    Lớp này từng chứa câu fallback public. Bản mới giữ field `text` để
    không phá caller cũ, nhưng SourceGroundingEngine không còn là tác giả câu
    nói cuối. Nó chỉ phát tín hiệu: thiếu evidence gì, cap confidence bao nhiêu,
    parse_mode nào, và lý do gì. Lời nói public phải được LLM wording/reroll
    hoặc post-filter đáy quyết định.
    """

    should_fallback: bool
    text: str
    parse_mode: str = "source_grounding_fallback"
    confidence: float = 0.30
    reason: str = ""
    missing_evidence: list[str] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    debug_trace: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "should_speak": bool(self.should_fallback and self.text.strip()),
            "parse_mode": self.parse_mode,
            "confidence": self.confidence,
            "debug_trace": self.debug_trace or self.reason,
        }

    def to_runtime_decision(self) -> Any:
        """
        Cố chuyển sang domain.Decision nếu repo có class này.
        Nếu không import được, trả payload dict. Đừng bắt cả runtime chết vì một helper.
        """

        try:
            from domain import Decision  # type: ignore

            return Decision(
                text=self.text,
                should_speak=bool(self.should_fallback and self.text.strip()),
                confidence=self.confidence,
                parse_mode=self.parse_mode,
                debug_trace=self.debug_trace or self.reason,
                action_input=json.dumps({"grounding_flags": self.flags or {}}, ensure_ascii=False),
            )
        except Exception:
            return self.to_payload()


@dataclass(slots=True)
class MemoryAnswerProfile:
    """Semantic policy for memory/history questions.

    This is deliberately a profile, not a fallback sentence.  The LLM still
    words the answer, while downstream layers get stable metadata for
    confidence caps and action labels.
    """

    mode: Literal[
        "none",
        "continuity_check",
        "specific_recall",
        "session_history",
        "memory_lookup",
    ] = "none"
    speech_intent: str = ""
    speech_act: str = ""
    confidence_cap: float = 1.0
    requires_specific_anchor: bool = False
    has_specific_anchor: bool = False
    anchor_evidence_ids: list[str] = field(default_factory=list)
    evidence_preview: list[str] = field(default_factory=list)
    epistemic_stance: Literal["none", "partial_continuity", "anchored_recall", "admit_uncertainty"] = "none"
    reason: str = ""
    prompt_rules: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.mode != "none"

    def apply_cap(self, raw_confidence: float) -> float:
        try:
            return max(0.0, min(float(raw_confidence), float(self.confidence_cap)))
        except Exception:
            return float(self.confidence_cap)


@dataclass(slots=True)
class SourceGroundingReport:
    """
    Báo cáo grounding nguồn cho một turn.

    primary_source:
        Nguồn chính LLM được phép dựa vào.
    confidence_cap:
        Trần confidence cho output cuối. LLM/parser/Ti nên min(raw_confidence, cap).
    missing_evidence:
        Nếu có các mục thiếu nghiêm trọng, nên fallback hoặc hỏi user cung cấp evidence.
    forbidden_assumptions:
        Các claim không được nói ra.
    prompt_context:
        Context ngắn đưa vào prompt LLM.
    """

    user_text: str
    primary_source: SourceKind
    confidence_cap: float

    asks_about_file: bool = False
    file_mentions: list[str] = field(default_factory=list)

    refers_to_current_screen: bool = False
    screen_reference_strength: float = 0.0
    # Có ảnh/OCR màn hình tươi tại thời điểm phân tích hay không. Đây là tín hiệu
    # bằng chứng thuần, độc lập với việc user có nhắc tới màn hình. Guard chống ảo
    # giác dùng field này để chặn claim "tôi thấy trên màn hình" khi thực ra không có.
    screen_evidence_present: bool = False
    # Tóm tắt văn bản từ vision model/OCR — luôn render vào prompt dù
    # user không hỏi màn hình, để LLM có ngữ cảnh nền mà không cần nhận ảnh thô.
    screen_facts_preview: list[str] = field(default_factory=list)
    # True khi facts màn hình là từ lượt TRƯỚC (không chụp tươi lượt này). ERIS vẫn
    # hiểu nó có màn hình, nhưng phải nói "lúc nãy" thay vì claim "đang hiển thị".
    screen_facts_are_stale: bool = False
    # Structured extension/bridge state is not screen OCR. Keep it separate so
    # it can remain ambient on unrelated turns and become primary only when the
    # user is continuing that engagement.
    structured_facts_preview: list[str] = field(default_factory=list)
    structured_evidence_ids: list[str] = field(default_factory=list)
    structured_kind: str = ""
    structured_state: dict[str, Any] = field(default_factory=dict)
    # Runtime sensors/tool state như system clock và weather. Tách khỏi screen
    # để model không bịa giờ/thời tiết chỉ vì OCR có vài con số trông khả nghi.
    runtime_facts_preview: list[str] = field(default_factory=list)
    runtime_evidence_ids: list[str] = field(default_factory=list)

    pasted_code_detected: bool = False
    code_language: str = "unknown"
    code_block_count: int = 0

    candidates: list[SourceCandidate] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    allowed_sources: list[SourceKind] = field(default_factory=list)
    grounding_source: str = "unknown"
    # Provenance có vai trò. Không trộn screen context với nguồn trả lời chính nữa.
    evidence_items: list[GroundingEvidence] = field(default_factory=list)
    generation_stage: EvidenceStage = "pre_generation"
    requested_source: SourceKind | None = None
    missing_evidence: list[str] = field(default_factory=list)
    preempt_mode: str = ""
    preempt_recommended: bool = False

    # Information-gap policy:
    # - "fallback": thiếu bằng chứng nghiêm trọng, chỉ được trả lời tối thiểu.
    # - "probe": thiếu bằng chứng hiện tại nhưng vẫn được đặt giả thuyết/câu hỏi thăm dò.
    # - "hypothesis": được phân tích như giả thuyết, không được nói như fact.
    # - "stance": câu hỏi về bản ngã, lập trường, cái tôi, triết lý hoặc so sánh thực thể; miễn trừ bằng chứng thực nghiệm.
    # Thiếu dữ kiện không được biến thành câm lặng; nó phải biến thành tò mò có kỷ luật.
    epistemic_mode: Literal["claim", "hypothesis", "probe", "fallback", "stance"] = "claim"
    information_gap: float = 0.0
    hypothesis_allowed: bool = False
    probe_allowed: bool = False
    memory_allowed: bool = True
    memory_surface_allowed: bool = True
    memory_answer_allowed: bool = True
    memory_allowed_layers: tuple[str, ...] = ("procedural", "semantic", "episodic")
    memory_answer_profile: MemoryAnswerProfile = field(default_factory=MemoryAnswerProfile)
    warnings: list[str] = field(default_factory=list)
    forbidden_assumptions: list[str] = field(default_factory=list)
    answer_style_rules: list[str] = field(default_factory=list)
    prompt_context: str = ""
    fallback_decision: SourceFallbackDecision | None = None
    cognitive_flags: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def should_fallback(self) -> bool:
        return bool(self.fallback_decision and self.fallback_decision.should_fallback)

    @property
    def should_preempt_llm(self) -> bool:
        return bool(self.preempt_recommended)

    @property
    def max_confidence(self) -> float:
        return float(self.confidence_cap)

    def apply_cap(self, raw_confidence: float) -> float:
        try:
            return max(0.0, min(float(raw_confidence), float(self.confidence_cap)))
        except Exception:
            return self.confidence_cap

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ClaimStatus = Literal["grounded", "ungrounded", "known_false"]


# Dấu hiệu ERIS đang CLAIM tri giác/hành động trực tiếp (không phải user hỏi).
# Dùng cho annotate: nếu một mệnh đề mang dấu này mà evidence thiếu → ungrounded.
_ERIS_PERCEPTION_CLAIM_MARKERS = (
    "tôi thấy", "toi thay", "tôi nhìn", "toi nhin",
    # PATCH: bắt cả câu cụt “đang nhìn đây”/“đang nhìn” để annotate không lọt
    # perception claim thiếu evidence. Guard này là dây an toàn sau prompt guard.
    "đang nhìn đây", "dang nhin day", "đang nhìn", "dang nhin",
    "tôi đang xem", "toi dang xem", "tôi đọc được", "toi doc duoc",
    "trên màn hình", "tren man hinh", "màn hình của bạn", "man hinh cua ban",
    "cửa sổ", "cua so", "tab ",
    "file này", "file nay", "trong file", "nội dung file", "noi dung file",
)
_ERIS_ACTION_CLAIM_MARKERS = (
    "tôi đã làm", "toi da lam", "tôi đã sửa", "tôi đã chạy", "tôi đã mở",
    "tôi vừa", "đã hoàn tất", "tôi đã ghi", "tôi đã xóa",
)


@dataclass(slots=True)
class ClaimAnnotation:
    """Nhãn cho một mệnh đề bề mặt (Task 8 / R8.1).

    `claim_status` phản ánh GROUNDING NỘI TẠI — sự thật ERIS nắm — và KHÔNG đổi
    theo quan hệ/đăng ký (R7). `ground_truth` là điều ERIS thực sự biết về mệnh đề.
    DisclosurePolicy (Task 9) đọc các nhãn này để quyết nói thật/giấu/bẻ cong.
    """

    span: str
    claim_status: ClaimStatus = "grounded"
    ground_truth: str = ""
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "span": self.span,
            "claim_status": self.claim_status,
            "ground_truth": self.ground_truth,
            "evidence_ids": list(self.evidence_ids),
        }


class SourceGroundingEngine:
    """
    Bộ phân loại grounding nguồn cho runtime.

    Cách dùng tối thiểu:
        report = SourceGroundingEngine().analyze(user_text, frame=frame)
        frame.source_grounding = report
        prompt += report.prompt_context
        if report.should_fallback():
            # Compatibility only. SourceGroundingEngine no longer authors
            # public fallback text; callers should pass report.prompt_context
            # into wording/reroll layers instead of returning this directly.
            return report.fallback_decision.to_runtime_decision()

    Các nguồn được ưu tiên theo tính trực tiếp:
    1. pasted_code: user tự paste code vào chat.
    2. uploaded_file: user hỏi file/tệp/pdf/code file đã attach, nếu runtime có evidence file.
    3. current_screen: user trỏ màn hình, nếu runtime có vision/ocr/screen evidence.
    4. user_text: chỉ dựa vào câu user.
    5. memory/runtime_state: chỉ dùng như context phụ, không thay evidence hiện tại.
    """

    # Trần confidence theo nguồn. Đây là trần mềm, guard sau vẫn có thể hạ thêm.
    CONFIDENCE_CAPS: dict[SourceKind, float] = {
        "pasted_code": 0.95,
        "uploaded_file": 0.92,
        "chess_bridge": 0.98,
        "current_screen": 0.78,
        "user_text": 0.86,
        "memory": 0.68,
        "web": 0.60,
        "runtime_state": 0.64,
        "unknown": 0.35,
        "filename_only": 0.28,
        "live_vision": 0.75,
    }

    # Khi nguồn được nhắc nhưng thiếu evidence trực tiếp.
    MISSING_SOURCE_CAPS: dict[SourceKind, float] = {
        "uploaded_file": 0.28,
        "chess_bridge": 0.40,
        "current_screen": 0.25,
        "memory": 0.42,
        "web": 0.35,
        "unknown": 0.30,
        "runtime_state": 0.42,
        "user_text": 0.60,
        "pasted_code": 0.40,
        "filename_only": 0.28,
        "live_vision": 0.75,
    }

    FILE_EXTENSIONS = (
        "py", "txt", "pdf", "docx", "pptx", "xlsx", "csv", "json", "jsonl",
        "xml", "yaml", "yml", "md", "cs", "gd", "ts", "tsx", "js", "jsx",
        "java", "kt", "cpp", "c", "h", "hpp", "rs", "go", "php", "rb",
        "html", "css", "scss", "sql", "ipynb", "log", "toml", "ini",
    )

    FILE_REFERENCE_MARKERS = (
        "file này", "file kia", "file đó", "file vừa gửi", "file tôi gửi",
        "tệp này", "tệp vừa gửi", "pdf này", "ảnh này", "hình này",
        "screenshot này", "slide này", "deck này", "bài này", "văn bản đã dán",
        "trong file", "trong tệp", "đọc file", "mở file", "xem file",
        "attachment", "attached file", "uploaded file", "this file", "the file",
    )

    FILE_CONTENT_REQUEST_MARKERS = (
        "xem file",
        "doc file",
        "đọc file",
        "trong file",
        "noi dung file",
        "nội dung file",
        "review file",
        "tom tat file",
        "tóm tắt file",
        "giai thich file",
        "giải thích file",
        "file nay",
        "tệp này",
        "loi gi",
        "lỗi gì",
        "la gi",
        "là gì",
        "ham nao",
        "class nao",
    )

    # Tham chiếu màn hình TƯỜNG MINH: user trực tiếp nhắc tới màn hình/cửa sổ hoặc
    # ra lệnh "nhìn/xem". Đây là tín hiệu DUY NHẤT được phép sinh ra câu thú nhận
    # "tôi chưa thấy màn hình". Nó KHÔNG còn gộp chung với đại từ chỉ định trần.
    EXPLICIT_SCREEN_REQUEST_MARKERS = (
        "màn hình", "screen", "trên màn", "cửa sổ này",
        "nhìn này", "nhìn cái này", "nhìn màn hình", "nhìn vào màn hình",
        "xem màn hình", "đọc màn hình", "xem đi", "bạn xem đi",
        "xem được tui làm gì", "xem được tôi làm gì", "thấy được tui làm gì", "thấy được tôi làm gì",
        "em không xem được", "không xem được tui làm gì", "không xem được tôi làm gì",
        "biết tui làm gì", "biết tôi làm gì",
        "biết tui đang làm gì", "biết tôi đang làm gì",
        "what is on my screen", "this screen", "look at this", "look at my screen",
        "tôi đang code gì", "đang code gì", "đoạn đang mở",
        "bài tôi đang nghe", "bài đang nghe", "đang nghe bài", "bài hát", "bài gì", "nhạc gì", "đang nghe nhạc", "bài đang phát", "đang phát bài", "đang nghe", "bài hát tên",
    )

    MEMORY_REFERENCE_MARKERS = (
        "lần trước", "hôm trước", "trước đó", "như cũ", "như lần nãy",
        "đã nói", "đã bàn", "tôi từng", "bạn nhớ", "còn nhớ", "nhớ không",
        "previously", "last time", "remember when", "as before",
    )

    WEB_REFERENCE_MARKERS = (
        "mới nhất", "hiện tại", "bây giờ", "hôm nay", "giá", "lịch",
        "điều tra", "tìm", "tìm kiếm", "tìm hiểu", "tra cứu", "kiểm chứng", "xác minh",
        "latest", "current", "today", "recent", "news", "release",
        "investigate", "search", "look up", "verify", "fact check",
    )

    # Đại từ chỉ định TRẦN. KHÔNG phải tham chiếu màn hình. Chỉ dùng để bind referent
    # vào ngữ cảnh thị giác KHI đã có ảnh/OCR tươi. Bản thân nó không bao giờ ép màn
    # hình hay sinh ra câu từ chối — "cái này" trong chat thường trỏ vào điều vừa nói.
    DEICTIC_REFERENCE_MARKERS = (
        "cái này",
        "cái đó",
        "cái kia",
        "chỗ này",
        "nút này",
        "ô này",
        "lỗi này",
        "tab này",
        "ảnh này",
        "hình này",
        "đây là gì",
    )

    CODE_FENCE_RE = re.compile(r"```(?P<lang>[a-zA-Z0-9_+\-#.]*).*?```", re.DOTALL)
    FILENAME_RE = re.compile(
        r"(?<![\w./\\-])([\w .()\[\]-]{1,80}\.(?:"
        + "|".join(FILE_EXTENSIONS)
        + r"))(?![\w/\\-])",
        re.IGNORECASE,
    )

    def analyze(
        self,
        user_text: str,
        *,
        frame: Any = None,
        active_files: Sequence[Any] | None = None,
        screen_facts: Sequence[Any] | None = None,
        screen_facts_stale: bool = False,
        structured_facts: Sequence[Any] | None = None,
        runtime_facts: Sequence[Any] | None = None,
        memory_packet: Any = None,
        runtime_state: Any = None,
        allow_web: bool = False,
        attach_to_frame: bool = True,
    ) -> SourceGroundingReport:
        """
        Phân tích nguồn trả lời chính.

        Parameters
        ----------
        user_text:
            Text user gửi trong turn hiện tại.
        frame:
            CognitiveFrame/runtime frame nếu có.
        active_files:
            Danh sách file/evidence file mà runtime đã biết. Có thể là string, dict, dataclass.
        screen_facts:
            OCR/vision/screen facts hiện có. Nếu None, engine tự thử đọc từ frame.
        structured_facts:
            Evidence có cấu trúc từ extension/bridge, hiện dùng cho chess bridge.
        runtime_facts:
            Sensor/tool state tươi như system clock, weather hoặc policy vừa áp.
        memory_packet:
            MemoryPacket nếu prompt muốn dùng memory như context phụ.
        runtime_state:
            State runtime nếu cần detect current active file/window.
        allow_web:
            Nếu True, cho phép phân loại web là nguồn khả thi. Nếu False, web marker chỉ tạo missing evidence.
        attach_to_frame:
            Gắn report vào frame.source_grounding nếu có thể.
        """

        text = str(user_text or "")
        normalized = self._normalize(text)
        candidates: list[SourceCandidate] = []
        notes: list[str] = []

        file_mentions = self._detect_file_mentions(text, normalized)
        asks_about_file = bool(file_mentions or self._has_any(normalized, self.FILE_REFERENCE_MARKERS))

        code_info = self._detect_pasted_code(text)
        explicit_screen_request = self._detect_explicit_screen_request(normalized)
        try:
            from runtime.intent_model import normalize_intent
            typed_intent = normalize_intent(text)
            # Current app/window/task awareness is structured-environment intent,
            # not an implicit screenshot request merely because legacy markers
            # contain words like "xem/thấy/đang".
            if typed_intent.environment_sufficient:
                explicit_screen_request = False
        except Exception:
            typed_intent = None
        has_deictic_reference = self._has_deictic_reference(normalized)

        memory_ref = self._has_any(normalized, self.MEMORY_REFERENCE_MARKERS)
        # Không coi mọi chữ "hôm nay/hiện tại/bây giờ" là web request.
        # Một turn có thể chứa nhiều mệnh đề: "Tôi tên Hòa" là user_text,
        # còn "thời tiết hôm nay bao nhiêu độ" mới cần web. Nếu không tách ý,
        # một marker thời gian sẽ kéo cả turn xuống web_missing rồi cap confidence vô lý.
        grounding_clauses = self._split_grounding_clauses(text)
        has_self_disclosure_clause = any(self._is_self_disclosure_clause(clause) for clause in grounding_clauses)
        has_web_marker = self._has_any(normalized, self.WEB_REFERENCE_MARKERS)
        web_ref = self._has_actionable_web_reference(normalized, text, clauses=grounding_clauses)
        internal_capability_ref = self._is_internal_capability_inquiry(normalized)
        relational_reflection = self._is_relational_reflection(normalized)
        identity_or_ego_reflection = self._is_identity_or_ego_reflection(normalized)
        if internal_capability_ref:
            # Câu hỏi kiểu “Eris thấy được telemetry/thời gian/màn hình không?”
            # phải đi qua capability registry của runtime, không bị chữ “hiện tại”
            # kéo sang web_missing và tự nhận mù toàn phần.
            web_ref = False
            explicit_screen_request = False
            notes.append("internal_capability_inquiry_uses_runtime_registry")
        if relational_reflection:
            # “Giá trị của sự hiện diện của em với tui” là một câu hỏi quan hệ,
            # không phải yêu cầu tra giá thị trường chỉ vì có token ``giá``.
            web_ref = False
            notes.append("relational_reflection_uses_conversation_evidence")
        if identity_or_ego_reflection:
            # Câu hỏi về bản ngã, cái tôi, độ sâu hoặc so sánh thực thể (Neuro, v.v.)
            # là miền chủ quan (Subjective Selfhood), không bị ép bằng chứng thực nghiệm.
            web_ref = False
            notes.append("identity_or_ego_reflection_exempt_from_factual_evidence")
        if has_web_marker and not web_ref:
            notes.append("web_marker_demoted_to_user_text_transient_report")

        file_evidence = self._collect_file_evidence(frame=frame, active_files=active_files, runtime_state=runtime_state)
        screen_evidence = self._collect_screen_evidence(frame=frame, screen_facts=screen_facts, runtime_state=runtime_state)
        structured_evidence = self._collect_structured_evidence(structured_facts=structured_facts)
        runtime_evidence = self._collect_runtime_evidence(runtime_facts=runtime_facts)
        memory_evidence = self._collect_memory_evidence(frame=frame, memory_packet=memory_packet)
        user_evidence = self._collect_user_text_evidence(frame=frame, user_text=text)

        # Màn hình là referent được gác bằng BẰNG CHỨNG, không phải bằng keyword soup.
        #  - Có ảnh/OCR tươi  -> màn hình là nguồn khả dụng để bind.
        #  - User TƯỜNG MINH bảo "nhìn/xem màn hình" -> referent màn hình, kể cả khi
        #    chưa có ảnh (để còn thú nhận thật là chưa thấy).
        #  - Đại từ chỉ định trần ("cái này", "lỗi này") CHỈ trỏ màn hình khi đã có ảnh
        #    tươi. Không có ảnh thì nó trỏ vào ngữ cảnh hội thoại và TUYỆT ĐỐI không tự
        #    sinh ra câu "thiếu evidence màn hình".
        # Màn hình có ba trạng thái, không còn nhị phân "có/không":
        #  - fresh: lượt này vừa chụp/đọc màn hình tươi -> được claim như đang nhìn.
        #  - stale: không chụp tươi lượt này nhưng CÓ facts/màn hình từ lượt trước
        #    -> ERIS vẫn HIỂU nó có màn hình, chỉ là ảnh cũ; phải nói "lúc nãy" chứ
        #    không claim "ngay bây giờ". Không bao giờ tự thú "tôi chưa từng thấy".
        #  - none: không có gì -> mới được phép preempt thú nhận chưa thấy.
        has_any_screen = bool(screen_evidence["present"])
        has_fresh_vision = has_any_screen and not bool(screen_facts_stale)
        has_stale_vision = has_any_screen and bool(screen_facts_stale)
        refers_to_screen = bool(explicit_screen_request or (has_deictic_reference and has_any_screen))
        screen_strength = (
            0.72 if has_fresh_vision else (0.55 if has_stale_vision else (0.5 if explicit_screen_request else 0.0))
        )
        has_relevant_chess = bool(structured_evidence["present"] and structured_evidence["relevant"])
        asks_system_clock = self._asks_system_clock(normalized)
        asks_weather = self._asks_weather(normalized)
        asks_environment_awareness = self._asks_environment_awareness(normalized)
        has_clock = bool(runtime_evidence.get("clock_present"))
        has_weather = bool(runtime_evidence.get("weather_present"))
        has_deep_environment = bool(runtime_evidence.get("deep_environment_present"))
        memory_profile = self._derive_memory_answer_profile(
            normalized=normalized,
            user_text=text,
            memory_ref=memory_ref,
            memory_evidence=memory_evidence,
        )

        if code_info["detected"]:
            candidates.append(
                SourceCandidate(
                    kind="pasted_code",
                    confidence=code_info["confidence"],
                    reason="User paste code trực tiếp trong chat.",
                    evidence_ids=user_evidence["ids"],
                    evidence_preview=code_info["preview"],
                )
            )

        if asks_about_file:
            if file_evidence["present"]:
                candidates.append(
                    SourceCandidate(
                        kind="uploaded_file",
                        confidence=file_evidence["confidence"],
                        reason="User hỏi về file/tệp và runtime có evidence file.",
                        evidence_ids=file_evidence["ids"],
                        evidence_preview=file_evidence["preview"],
                    )
                )
            else:
                candidates.append(
                    SourceCandidate(
                        kind="uploaded_file",
                        confidence=0.25,
                        reason="User hỏi về file/tệp nhưng runtime chưa có evidence file.",
                        evidence_ids=[],
                        evidence_preview=[],
                        missing=["file_evidence"],
                    )
                )

        if refers_to_screen:
            if screen_evidence["present"]:
                confidence = min(0.78, max(0.45, screen_evidence["confidence"] * (0.75 + screen_strength * 0.25)))
                candidates.append(
                    SourceCandidate(
                        kind="current_screen",
                        confidence=confidence,
                        reason="User trỏ vào màn hình và runtime có screen/vision/ocr evidence.",
                        evidence_ids=screen_evidence["ids"],
                        evidence_preview=screen_evidence["preview"],
                    )
                )
            else:
                candidates.append(
                    SourceCandidate(
                        kind="current_screen",
                        confidence=0.22,
                        reason="User trỏ vào màn hình nhưng không có screen/vision/ocr evidence.",
                        evidence_ids=[],
                        evidence_preview=[],
                        missing=["screen_evidence"],
                    )
                )

        if structured_evidence["present"] and has_relevant_chess:
            # Structured chess chỉ tranh quyền primary khi lượt hiện tại thật sự
            # nói về cờ. Nếu không, state vẫn được giữ làm ambient continuity.
            candidates.append(
                SourceCandidate(
                    kind="chess_bridge",
                    confidence=0.98,
                    reason="Active chess engagement có snapshot FEN trực tiếp từ extension và liên quan lượt hiện tại.",
                    evidence_ids=structured_evidence["ids"],
                    evidence_preview=structured_evidence["preview"],
                )
            )

        if memory_ref:
            if memory_evidence["present"]:
                candidates.append(
                    SourceCandidate(
                        kind="memory",
                        confidence=memory_evidence["confidence"],
                        reason="User tham chiếu lịch sử/điều đã nói và runtime có memory packet.",
                        evidence_ids=memory_evidence["ids"],
                        evidence_preview=memory_evidence["preview"],
                    )
                )
            else:
                candidates.append(
                    SourceCandidate(
                        kind="memory",
                        confidence=0.38,
                        reason="User tham chiếu lịch sử nhưng không có memory packet đáng tin.",
                        evidence_ids=[],
                        evidence_preview=[],
                        missing=["memory_packet"],
                    )
                )

        if asks_system_clock and has_clock:
            candidates.append(SourceCandidate(
                kind="runtime_state",
                confidence=0.99,
                reason="System clock local là sensor runtime trực tiếp.",
                evidence_ids=list(runtime_evidence["clock_ids"]),
                evidence_preview=list(runtime_evidence["clock_preview"]),
            ))
        if asks_weather and has_weather:
            candidates.append(SourceCandidate(
                kind="runtime_state",
                confidence=0.95,
                reason="Weather telemetry/tool result có provenance runtime.",
                evidence_ids=list(runtime_evidence["weather_ids"]),
                evidence_preview=list(runtime_evidence["weather_preview"]),
            ))
        if asks_weather and not has_weather:
            notes.append("weather_runtime_evidence_missing_do_not_invent")

        if asks_environment_awareness and has_deep_environment:
            candidates.append(SourceCandidate(
                kind="runtime_state",
                confidence=max(0.82, float(runtime_evidence.get("deep_environment_confidence", 0.0) or 0.0)),
                reason="Deep Environment có structured local state tươi về app/window/task hiện tại.",
                evidence_ids=list(runtime_evidence.get("deep_environment_ids", ()) or ()),
                evidence_preview=list(runtime_evidence.get("deep_environment_preview", ()) or ()),
            ))
        elif asks_environment_awareness:
            notes.append("deep_environment_evidence_missing")

        if internal_capability_ref:
            candidates.append(
                SourceCandidate(
                    kind="runtime_state",
                    confidence=0.88,
                    reason="User hỏi về năng lực quan sát/tool của chính runtime; CapabilityRegistry là nguồn chính.",
                    evidence_ids=["capability_registry_snapshot"],
                    evidence_preview=["runtime capability availability"],
                )
            )

        if web_ref:
            candidates.append(
                SourceCandidate(
                    kind="web",
                    confidence=0.60 if allow_web else 0.32,
                    reason="User dùng marker cần thông tin hiện tại/web.",
                    evidence_ids=[],
                    evidence_preview=[],
                    missing=[] if allow_web else ["web_access_or_search_result"],
                )
            )

        # User text luôn là nguồn nền nếu có text.
        if text.strip():
            # Nếu turn có mệnh đề tự khai báo như tên/sở thích/ngành học, user_text
            # phải là nguồn đủ mạnh. Nó không cần web, không cần memory, và cũng không
            # nên bị một clause thời tiết kéo tụt confidence của cả lượt nói.
            user_text_confidence = 0.78 if has_self_disclosure_clause else (0.72 if not candidates else 0.55)
            candidates.append(
                SourceCandidate(
                    kind="user_text",
                    confidence=user_text_confidence,
                    reason="Có self-disclosure trong câu user hiện tại." if has_self_disclosure_clause else "Có thể trả lời dựa trên câu user hiện tại.",
                    evidence_ids=user_evidence["ids"],
                    evidence_preview=user_evidence["preview"],
                )
            )

        if not candidates:
            candidates.append(
                SourceCandidate(
                    kind="unknown",
                    confidence=0.20,
                    reason="Không có text/evidence đủ rõ.",
                    missing=["user_text_or_evidence"],
                )
            )

        primary = self._choose_primary_source(candidates)
        missing_evidence = self._derive_missing_evidence(primary, candidates)

        # Câu hỏi "tôi đang làm gì/app nào/cửa sổ nào" không cần pixel evidence
        # khi Deep Environment đã có structured desktop state. Giữ screen_evidence
        # là missing ở đây sẽ vô tình đẩy toàn turn sang epistemic_mode=probe và
        # làm writer nói như thể nó vẫn mù dù runtime đã biết app/task.
        if asks_environment_awareness and has_deep_environment and "screen_evidence" in missing_evidence:
            missing_evidence = [item for item in missing_evidence if item != "screen_evidence"]
            notes.append("deep_environment_satisfies_environment_awareness_without_pixel_screen")

        # QUAN TRỌNG:
        # Nếu user đã paste code trực tiếp, nội dung code trong chat chính là evidence.
        # Không được để marker kiểu "file này" kéo hệ thống về missing=file_evidence nữa.
        # Lỗi cũ: user paste code + hỏi "file này lỗi gì" vẫn bị fallback như chưa có file.
        if code_info["detected"] and "file_evidence" in missing_evidence:
            missing_evidence = [item for item in missing_evidence if item != "file_evidence"]
            notes.append("pasted_code_satisfies_file_content_request")

        has_retrieved_file = bool(file_evidence["present"])
        visible_code = has_fresh_vision
        user_code = bool(code_info["detected"])
        memory_allowed = True
        memory_surface_allowed = True
        memory_answer_allowed = True
        memory_allowed_layers: tuple[str, ...] = ("procedural", "semantic", "episodic")
        warnings: list[str] = []
        primary_source: SourceKind = primary.kind
        grounding_source = primary.kind
        confidence_cap_override: float | None = None
        should_preempt = False

        # Referent màn hình được phân giải theo bằng chứng, không phải keyword.
        # Pasted code luôn thắng: "đoạn này" sau khi paste là trỏ vào code, không phải màn hình.
        if asks_system_clock and has_clock and not user_code and not asks_about_file:
            primary_source = "runtime_state"
            grounding_source = "runtime_state"
            confidence_cap_override = 0.99
            notes.append("system_clock_runtime_primary")
        elif asks_weather and has_weather and not user_code and not asks_about_file:
            primary_source = "runtime_state"
            grounding_source = "runtime_state"
            confidence_cap_override = 0.95
            notes.append("weather_runtime_primary")
        elif asks_environment_awareness and has_deep_environment and not user_code and not asks_about_file:
            # Deep Environment là structured desktop evidence, không phải pixel
            # vision. Nó đủ quyền trả lời "tôi đang ở app/cửa sổ/task nào" mà
            # không ép screenshot/OCR hoặc tự nhận mù màn hình.
            primary_source = "runtime_state"
            grounding_source = "runtime_state"
            deep_conf = float(runtime_evidence.get("deep_environment_confidence", 0.0) or 0.0)
            confidence_cap_override = max(0.78, min(0.92, deep_conf or 0.86))
            memory_surface_allowed = False
            memory_answer_allowed = False
            memory_allowed_layers = ("procedural",)
            notes.append("deep_environment_runtime_primary")
        elif has_relevant_chess and not user_code and not asks_about_file:
            primary_source = "chess_bridge"
            grounding_source = "chess_bridge"
            confidence_cap_override = 0.98
            memory_surface_allowed = False
            memory_answer_allowed = False
            memory_allowed_layers = ("procedural",)
            notes.append("structured_chess_bridge_overrides_ocr_or_vision")
        elif refers_to_screen and not user_code:
            if has_fresh_vision:
                primary_source = "live_vision"
                grounding_source = "live_vision"
                confidence_cap_override = 0.75
                memory_surface_allowed = False
                memory_answer_allowed = False
                memory_allowed_layers = ("procedural",)
            elif has_stale_vision:
                # CÓ màn hình lượt trước nhưng không chụp tươi lượt này. ERIS hiểu nó
                # có màn hình -> KHÔNG preempt thú nhận "chưa thấy". Dựa vào facts cũ
                # nhưng cap thấp hơn live và đánh dấu stale để wording nói "lúc nãy".
                primary_source = "current_screen"
                grounding_source = "current_screen"
                confidence_cap_override = 0.62
                memory_surface_allowed = False
                memory_answer_allowed = False
                memory_allowed_layers = ("procedural",)
                warnings.append("screen_facts_from_previous_turn")
            elif explicit_screen_request:
                # Trường hợp DUY NHẤT được preempt: user TƯỜNG MINH bảo nhìn màn hình
                # nhưng runtime KHÔNG có ảnh/OCR tươi LẪN facts lượt trước. Đại từ chỉ
                # định trần không bao giờ rơi vào nhánh này (refers_to_screen=False khi
                # thiếu cả vision tươi lẫn cũ).
                primary_source = "unknown"
                grounding_source = "unknown"
                confidence_cap_override = 0.28
                should_preempt = True
                memory_surface_allowed = False
                memory_answer_allowed = False
                memory_allowed_layers = ("procedural",)
                warnings.append("explicit_screen_request_without_fresh_vision")
        elif (
            asks_about_file
            and self._looks_like_file_content_request(normalized)
            and not has_retrieved_file
            and not visible_code
            and not user_code
        ):
            primary_source = "filename_only"
            grounding_source = "filename_only"
            confidence_cap_override = 0.28
            should_preempt = True
            memory_surface_allowed = False
            memory_answer_allowed = False
            memory_allowed_layers = ("procedural",)
            warnings.append("specific_file_reference_without_content")

        if memory_profile.active and confidence_cap_override is None:
            # MemoryAnswerProfile already encodes the epistemic shape of the
            # request.  Let it own the cap instead of letting the generic
            # primary source cap flatten session-history and recall queries.
            confidence_cap_override = float(memory_profile.confidence_cap)
            notes.append(f"memory_answer_profile={memory_profile.mode}")

        forbidden = self._derive_forbidden_assumptions(
            primary_source=primary_source,
            asks_about_file=asks_about_file,
            refers_to_screen=refers_to_screen,
            code_detected=bool(code_info["detected"]),
            file_evidence_present=bool(file_evidence["present"]),
            screen_evidence_present=bool(screen_evidence["present"]),
            deep_environment_evidence_present=has_deep_environment,
            memory_evidence_present=bool(memory_evidence["present"]),
            allow_web=allow_web,
            web_ref=web_ref,
            is_identity_or_ego=identity_or_ego_reflection,
        )
        answer_rules = self._derive_answer_style_rules(
            primary_source,
            missing_evidence=missing_evidence,
            is_identity_or_ego=identity_or_ego_reflection,
        )

        allowed_sources = [candidate.kind for candidate in candidates if not candidate.missing and candidate.confidence >= 0.45]
        if primary_source == "live_vision":
            allowed_sources = self._dedupe([*allowed_sources, "live_vision"])
        if runtime_evidence["present"]:
            allowed_sources = self._dedupe([*allowed_sources, "runtime_state"])
        confidence_cap = confidence_cap_override if confidence_cap_override is not None else self._cap_for(primary, missing_evidence=missing_evidence)
        preempt_mode = ""
        if should_preempt:
            preempt_mode = self._derive_preempt_mode(
                normalized=normalized,
                file_mentions=file_mentions,
                missing_evidence=missing_evidence,
            )
        if should_preempt and not preempt_mode:
            preempt_mode = "deictic_without_vision" if refers_to_screen else "filename_only"

        report = SourceGroundingReport(
            user_text=text,
            primary_source=primary_source,
            confidence_cap=confidence_cap,
            asks_about_file=asks_about_file,
            file_mentions=file_mentions,
            refers_to_current_screen=refers_to_screen,
            screen_reference_strength=screen_strength,
            screen_evidence_present=has_any_screen,
            screen_facts_are_stale=bool(has_stale_vision),
            screen_facts_preview=[str(p) for p in (screen_evidence.get("preview") or []) if str(p).strip()][:4],
            structured_facts_preview=[str(p) for p in (structured_evidence.get("preview") or []) if str(p).strip()][:3],
            structured_evidence_ids=[str(p) for p in (structured_evidence.get("ids") or []) if str(p).strip()][:8],
            structured_kind=str(structured_evidence.get("kind") or ""),
            structured_state=dict(structured_evidence.get("state") or {}),
            runtime_facts_preview=[str(p) for p in (runtime_evidence.get("preview") or []) if str(p).strip()][:6],
            runtime_evidence_ids=[str(p) for p in (runtime_evidence.get("ids") or []) if str(p).strip()][:10],
            pasted_code_detected=bool(code_info["detected"]),
            code_language=str(code_info["language"]),
            code_block_count=int(code_info["block_count"]),
            candidates=candidates,
            evidence_ids=self._dedupe([*primary.evidence_ids, *file_evidence["ids"], *structured_evidence["ids"], *runtime_evidence["ids"], *screen_evidence["ids"], *memory_evidence["ids"], *user_evidence["ids"]])[:20],
            allowed_sources=self._dedupe(allowed_sources),
            grounding_source=grounding_source,
            missing_evidence=missing_evidence,
            preempt_mode=preempt_mode,
            preempt_recommended=should_preempt or preempt_mode in {"filename_only", "deictic_without_vision", "epistemic_curiosity", "investigate"},
            epistemic_mode="claim",
            information_gap=0.0,
            hypothesis_allowed=False,
            probe_allowed=False,
            memory_allowed=memory_allowed,
            memory_surface_allowed=memory_surface_allowed,
            memory_answer_allowed=memory_answer_allowed,
            memory_allowed_layers=memory_allowed_layers,
            memory_answer_profile=memory_profile,
            warnings=warnings,
            forbidden_assumptions=forbidden,
            answer_style_rules=answer_rules,
            notes=notes,
        )

        # Gắn vai trò provenance ngay từ đầu. Màn hình chỉ là ambient trừ khi
        # user thực sự hỏi về màn hình; user text là primary cho câu hỏi trực tiếp.
        report.evidence_items = self._build_initial_evidence_items(report)
        report.generation_stage = "pre_generation"
        report.requested_source = "web" if (web_ref and not allow_web) else None

        # Information Gap -> Curiosity/Probe:
        # preempt cũ hay biến thiếu evidence thành một câu chết. Ở đây ta phân loại lại:
        # nếu thiếu screen/memory evidence nhưng còn đủ neo tương tác, ERIS được hỏi/probe
        # như giả thuyết. Claim khẳng định vẫn bị cấm bởi forbidden_assumptions và guard sau.
        try:
            if identity_or_ego_reflection:
                report.epistemic_mode = "stance"
                report.confidence_cap = max(0.92, float(report.confidence_cap or 0.0))
                report.notes.append("identity_or_ego_epistemic_stance")
            elif self._is_epistemic_curiosity_gap(report):
                report.epistemic_mode = "probe"
                report.information_gap = max(0.35, 1.0 - float(report.confidence_cap or 0.0))
                report.hypothesis_allowed = True
                report.probe_allowed = True
                if report.preempt_mode in {"deictic_without_vision", ""}:
                    report.preempt_mode = "epistemic_curiosity"
                report.notes.append("information_gap_to_probe")
                if "Use hypothesis/probe wording; do not state missing source as observed fact." not in report.answer_style_rules:
                    report.answer_style_rules.append("Use hypothesis/probe wording; do not state missing source as observed fact.")
            elif report.missing_evidence:
                report.epistemic_mode = "fallback"
                report.information_gap = max(0.25, 1.0 - float(report.confidence_cap or 0.0))
        except (AttributeError, TypeError, ValueError):
            pass

        report.cognitive_flags = {
            "missing_evidence_ids": bool(report.missing_evidence),
            "missing_evidence": list(report.missing_evidence),
            "confidence": float(report.confidence_cap),
            "curiosity": float(report.information_gap),
            "focus_family": report.preempt_mode or report.grounding_source or report.primary_source,
            "epistemic_mode": report.epistemic_mode,
            "hypothesis_allowed": bool(report.hypothesis_allowed),
            "probe_allowed": bool(report.probe_allowed),
            "grounding_source": report.grounding_source,
        }
        report.prompt_context = self.render_prompt_context(report)
        report.fallback_decision = self.build_fallback_decision(report)

        if attach_to_frame:
            self._safe_setattr(frame, "source_grounding", report)
            self._safe_setattr(frame, "grounding", report)
            # Cho các guard cũ đọc dễ hơn mà không phải biết dataclass mới.
            self._safe_setattr(frame, "source_confidence_cap", report.confidence_cap)
            self._safe_setattr(frame, "source_forbidden_assumptions", tuple(report.forbidden_assumptions))
            self._safe_setattr(frame, "source_grounding_source", report.grounding_source)
            self._safe_setattr(frame, "source_grounding_preempt_mode", report.preempt_mode)
            self._safe_setattr(frame, "source_grounding_memory_answer_allowed", report.memory_answer_allowed)
            self._safe_setattr(frame, "source_memory_answer_profile", report.memory_answer_profile)

        return report

    def _build_initial_evidence_items(self, report: SourceGroundingReport) -> list[GroundingEvidence]:
        items: list[GroundingEvidence] = []
        user_text = str(report.user_text or "").strip()
        if user_text:
            user_role: EvidenceRole = "primary" if report.primary_source == "user_text" else "supporting"
            items.append(
                GroundingEvidence(
                    evidence_id="user:current_turn",
                    source="user_text",
                    role=user_role,
                    text=user_text,
                    confidence=0.95,
                )
            )

        # Candidate preview của nguồn thật (file/memory/runtime) được giữ riêng.
        for candidate in report.candidates:
            previews = [str(v).strip() for v in candidate.evidence_preview if str(v).strip()]
            if not previews:
                continue
            role: EvidenceRole = "primary" if candidate.kind == report.primary_source else "supporting"
            if candidate.kind in {"current_screen", "live_vision"}:
                role = "primary" if report.refers_to_current_screen and candidate.kind == report.primary_source else "ambient"
            elif candidate.kind == "chess_bridge":
                role = "primary" if report.primary_source == "chess_bridge" else "ambient"
            for index, preview in enumerate(previews[:3]):
                evidence_id = (candidate.evidence_ids[index] if index < len(candidate.evidence_ids) else "") or f"{candidate.kind}:{index}"
                items.append(
                    GroundingEvidence(
                        evidence_id=str(evidence_id),
                        source=candidate.kind,
                        role=role,
                        text=preview,
                        confidence=max(0.0, min(1.0, float(candidate.confidence))),
                    )
                )

        # screen_facts_preview đôi lúc không nằm trong candidate preview. Không để mất
        # nó, nhưng mặc định chỉ là ambient, không có quyền phủ định user/tool answer.
        known_screen_texts = {item.text for item in items if item.source in {"current_screen", "live_vision"}}
        screen_role: EvidenceRole = (
            "primary"
            if report.refers_to_current_screen and report.primary_source in {"current_screen", "live_vision"}
            else "ambient"
        )
        for index, preview in enumerate(report.screen_facts_preview[:4]):
            text = str(preview or "").strip()
            if not text or text in known_screen_texts:
                continue
            items.append(
                GroundingEvidence(
                    evidence_id=f"screen:{index}",
                    source="current_screen",
                    role=screen_role,
                    text=text,
                    confidence=0.78 if report.screen_evidence_present else 0.55,
                )
            )

        known_runtime = {item.text for item in items if item.source == "runtime_state"}
        runtime_role: EvidenceRole = "primary" if report.primary_source == "runtime_state" else "ambient"
        for index, preview in enumerate(report.runtime_facts_preview[:6]):
            text = str(preview or "").strip()
            if not text or text in known_runtime:
                continue
            evidence_id = (
                report.runtime_evidence_ids[index]
                if index < len(report.runtime_evidence_ids)
                else f"runtime_state:{index}"
            )
            items.append(GroundingEvidence(
                evidence_id=str(evidence_id),
                source="runtime_state",
                role=runtime_role,
                text=text,
                confidence=1.0 if "System local time" in text else 0.95,
            ))

        known_structured = {item.text for item in items if item.source == "chess_bridge"}
        structured_role: EvidenceRole = "primary" if report.primary_source == "chess_bridge" else "ambient"
        for index, preview in enumerate(report.structured_facts_preview[:3]):
            text = str(preview or "").strip()
            if not text or text in known_structured:
                continue
            evidence_id = (
                report.structured_evidence_ids[index]
                if index < len(report.structured_evidence_ids)
                else f"chess_bridge:{index}"
            )
            items.append(
                GroundingEvidence(
                    evidence_id=str(evidence_id),
                    source="chess_bridge",
                    role=structured_role,
                    text=text,
                    confidence=0.98,
                )
            )
        return items

    def record_tool_result(
        self,
        report: SourceGroundingReport | None,
        *,
        action_name: str,
        result: Any,
        turn_id: str = "",
    ) -> SourceGroundingReport | None:
        """Nâng tool result thành primary evidence ngay khi tool chạy xong.

        Hàm cố ý cập nhật report *in place*. Frame và coordinator đang cùng giữ
        tham chiếu tới report này, nên final guards nhìn thấy provenance mới mà
        không cần thay đổi contract của toàn pipeline.
        """

        if not isinstance(report, SourceGroundingReport):
            return report
        action = str(action_name or getattr(result, "action_name", "") or "tool").strip()
        success = bool(getattr(result, "success", False))
        report.generation_stage = "post_tool"
        if not success:
            warning = f"{action}_failed"
            if warning not in report.warnings:
                report.warnings.append(warning)
            report.prompt_context = self.render_prompt_context(report)
            return report

        source = self._tool_source_for_action(action)
        text = self._tool_result_text(result)
        if not text:
            warning = f"{action}_empty_result"
            if warning not in report.warnings:
                report.warnings.append(warning)
            report.prompt_context = self.render_prompt_context(report)
            return report

        for item in report.evidence_items:
            if item.role == "primary":
                item.role = "supporting"

        evidence_id = f"tool:{action}:{turn_id or 'current'}"
        report.evidence_items.append(
            GroundingEvidence(
                evidence_id=evidence_id,
                source=source,
                role="primary",
                text=text,
                confidence=0.92,
                stage="tool_result",
            )
        )
        report.primary_source = source
        report.grounding_source = source
        report.requested_source = None
        report.allowed_sources = self._dedupe([source, "user_text", *report.allowed_sources])
        report.evidence_ids = self._dedupe([*report.evidence_ids, evidence_id])[:20]
        missing_by_action = {
            "get_weather": {"web_access_or_search_result", "weather_result", "live_web_evidence"},
            "get_now_playing": {"runtime_state", "media_session"},
        }
        removable = missing_by_action.get(action, set())
        report.missing_evidence = [item for item in report.missing_evidence if item not in removable]
        report.confidence_cap = max(float(report.confidence_cap or 0.0), 0.86)
        report.epistemic_mode = "claim"
        report.preempt_recommended = False
        if report.preempt_mode in {"epistemic_curiosity", "investigate"}:
            report.preempt_mode = ""
        report.cognitive_flags.update(
            {
                "grounding_source": source,
                "tool_grounded": True,
                "tool_action": action,
                "missing_evidence_ids": bool(report.missing_evidence),
                "missing_evidence": list(report.missing_evidence),
                "confidence": float(report.confidence_cap),
            }
        )
        report.prompt_context = self.render_prompt_context(report)
        return report

    @staticmethod
    def _tool_source_for_action(action_name: str) -> SourceKind:
        if action_name == "get_weather":
            return "web"
        return "runtime_state"

    @staticmethod
    def _tool_result_text(result: Any) -> str:
        data = getattr(result, "data", {}) or {}
        if isinstance(data, Mapping):
            preferred = ("answer", "summary", "text", "content", "title")
            parts = [str(data.get(key, "") or "").strip() for key in preferred]
            text = " ".join(part for part in parts if part)
            if text:
                return text[:6000]
        return str(getattr(result, "message", "") or "").strip()[:6000]

    def classify(self, *args: Any, **kwargs: Any) -> SourceGroundingReport:
        return self.analyze(*args, **kwargs)

    def analyze_frame(self, frame: Any, **kwargs: Any) -> SourceGroundingReport:
        """
        Helper khi caller chỉ có frame.

        Cố lấy user_text từ percepts/final input của frame.
        """

        user_text = self._extract_user_text_from_frame(frame)
        return self.analyze(user_text, frame=frame, **kwargs)

    def apply_confidence_cap(self, raw_confidence: float, report_or_frame: Any) -> float:
        """
        Cap confidence ngoài pipeline.

        Có thể truyền SourceGroundingReport hoặc frame có `source_grounding`.
        """

        report = report_or_frame
        if not isinstance(report_or_frame, SourceGroundingReport):
            report = getattr(report_or_frame, "source_grounding", None) or getattr(report_or_frame, "grounding", None)
        if isinstance(report, SourceGroundingReport):
            return report.apply_cap(raw_confidence)
        try:
            return max(0.0, min(0.50, float(raw_confidence)))
        except Exception:
            return 0.35

    def should_preempt(
        self,
        report_or_frame: Any,
        *,
        modes: Sequence[str] = ("filename_only", "deictic_without_vision", "investigate"),
    ) -> bool:
        report = report_or_frame
        if not isinstance(report_or_frame, SourceGroundingReport):
            report = getattr(report_or_frame, "source_grounding", None) or getattr(report_or_frame, "grounding", None)
        if not isinstance(report, SourceGroundingReport):
            return False
        if str(getattr(report, "epistemic_mode", "") or "") in {"probe", "hypothesis"}:
            return False
        return bool(report.should_preempt_llm or (report.preempt_mode and report.preempt_mode in set(modes)))

    def render_prompt_context(self, report: SourceGroundingReport) -> str:
        """
        Tạo prompt context ngắn cho LLM.

        Context này không phải văn phong. Nó là giới hạn nhận thức:
        LLM được phép dựa vào gì và không được claim gì.
        """

        lines: list[str] = [
            "[SOURCE_GROUNDING]",
            f"primary_source: {report.primary_source}",
            f"confidence_cap: {report.confidence_cap:.2f}",
            f"epistemic_mode: {getattr(report, 'epistemic_mode', 'claim')}",
            f"information_gap: {float(getattr(report, 'information_gap', 0.0) or 0.0):.2f}",
        ]

        if report.allowed_sources:
            lines.append("allowed_sources: " + ", ".join(report.allowed_sources))
        if report.file_mentions:
            lines.append("file_mentions: " + ", ".join(report.file_mentions[:6]))
        if report.pasted_code_detected:
            lines.append(f"pasted_code: yes; language={report.code_language}; blocks={report.code_block_count}")
        if report.refers_to_current_screen:
            lines.append(f"current_screen_reference: yes; strength={report.screen_reference_strength:.2f}")
        if report.evidence_ids:
            lines.append("evidence_ids: " + ", ".join(report.evidence_ids[:12]))
        if getattr(report, "evidence_items", None):
            lines.append(f"generation_stage: {report.generation_stage}")
            for role in ("primary", "supporting", "ambient"):
                role_items = [item for item in report.evidence_items if item.role == role]
                if not role_items:
                    continue
                previews = " | ".join(f"{item.source}:{item.text[:140]}" for item in role_items[:3])
                lines.append(f"{role}_evidence: {previews}")
            lines.append("evidence_rule: ambient context may validate situated screen/activity claims, but must not contradict a direct user request or successful tool result.")
        if report.missing_evidence:
            lines.append("missing_evidence: " + ", ".join(report.missing_evidence))
        if report.warnings:
            lines.append("warnings: " + ", ".join(report.warnings))
        lines.append(
            "memory_policy: "
            f"allowed={int(report.memory_allowed)}; "
            f"surface_allowed={int(report.memory_surface_allowed)}; "
            f"answer_allowed={int(report.memory_answer_allowed)}; "
            f"layers={', '.join(report.memory_allowed_layers)}"
        )
        memory_profile = getattr(report, "memory_answer_profile", None)
        if getattr(memory_profile, "active", False):
            lines.append(
                "memory_answer_profile: "
                f"mode={memory_profile.mode}; "
                f"speech_intent={memory_profile.speech_intent}; "
                f"speech_act={memory_profile.speech_act}; "
                f"confidence_cap={memory_profile.confidence_cap:.2f}; "
                f"requires_specific_anchor={int(memory_profile.requires_specific_anchor)}; "
                f"has_specific_anchor={int(memory_profile.has_specific_anchor)}; "
                f"stance={memory_profile.epistemic_stance}"
            )
            if memory_profile.evidence_preview:
                lines.append("memory_anchor_preview: " + " | ".join(memory_profile.evidence_preview[:3]))
            if memory_profile.prompt_rules:
                lines.append("memory_answer_rules:")
                for rule in memory_profile.prompt_rules[:8]:
                    lines.append(f"- {rule}")
        if report.forbidden_assumptions:
            lines.append("forbidden_assumptions:")
            for item in report.forbidden_assumptions[:8]:
                lines.append(f"- {item}")
        if getattr(report, "hypothesis_allowed", False) or getattr(report, "probe_allowed", False):
            lines.append("information_gap_rules:")
            lines.append("- Missing evidence forbids authoritative claims, not thinking.")
            lines.append("- ERIS may offer a hypothesis or a confirming probe using wording like 'có vẻ', 'tôi đoán', 'phải không'.")
            # P0 — Độ thiếu chứng cứ về sự kiện KHÔNG phải chỉ thị hỏi kỹ thuật.
            # User tự kể một trải nghiệm là đủ để tham gia câu chuyện, không đủ
            # để khẳng định nguyên nhân bên ngoài là sự thật. Root chọn có hỏi không.
            lines.append("- A probe is permitted, not required. Missing technical evidence does not by itself request debugging or force a diagnostic question.")
            lines.append("- User's account grounds what they report feeling/experiencing, not an unverified external cause. You may respond to that experience without claiming the cause as fact.")
            lines.append("- Do not say 'tôi thấy', 'tôi đã đọc', or 'tôi biết' for a missing source.")
        if report.answer_style_rules:
            lines.append("answer_rules:")
            for rule in report.answer_style_rules[:8]:
                lines.append(f"- {rule}")

        runtime_previews = [str(f).strip() for f in (getattr(report, "runtime_facts_preview", None) or []) if str(f).strip()]
        if runtime_previews:
            lines.append("runtime_facts (authoritative local sensors/tool state):")
            for fact in runtime_previews[:6]:
                lines.append(f"  {fact[:240]}")
            lines.append("runtime_rule: use the exact system clock when asked; never invent weather when no weather fact is present.")
            if any("Deep Environment" in fact for fact in runtime_previews):
                lines.append("deep_environment_rules:")
                lines.append("- Deep Environment is structured local desktop/task evidence. Use it directly for questions about the current app, process, window, task, phase or recent computer activity.")
                lines.append("- If Deep Environment answers the user's current-activity question, do not claim that ERIS cannot observe the computer or require a screenshot just to name the app/task state.")
                lines.append("- Deep Environment is not pixel evidence: never infer exact colors, geometry, code text, image contents or cursor position unless OCR/vision facts separately support them.")
                lines.append("- Keep it contextual: do not volunteer telemetry details on unrelated turns.")

        # Luôn render vision/screen facts vào grounding context — dù user không hỏi
        # màn hình. Đây là tóm tắt văn bản từ vision model/OCR, KHÔNG
        # phải ảnh thô. Llama đọc text này để biết ngữ cảnh nền mà không cần nhận ảnh.
        screen_previews = [str(f).strip() for f in (getattr(report, "screen_facts_preview", None) or []) if str(f).strip()]
        if screen_previews:
            if bool(getattr(report, "screen_facts_are_stale", False)):
                lines.append(
                    "screen_context (from a PREVIOUS turn — no fresh capture this turn; "
                    "may be outdated. Refer to it as 'lúc nãy'/'vừa nãy', do NOT claim it is "
                    "what is on screen right now; do NOT volunteer unless asked):"
                )
            else:
                lines.append("screen_context (OCR/UIA/vision summary — text only, do NOT describe or volunteer unless asked):")
            for fact in screen_previews[:4]:
                lines.append(f"  {fact[:200]}")

        structured_state = getattr(report, "structured_state", None) or {}
        if structured_state and str(getattr(report, "structured_kind", "") or "") == "chess":
            lines.append("chess_typed_state: " + json.dumps(structured_state, ensure_ascii=False, separators=(",", ":")))
            lines.append("chess_surface_rules:")
            lines.append("- This typed state is authoritative for the board, side to move, last move and suggestion.")
            lines.append("- Answer a direct move question with exactly suggested_move_san/uci, then one concrete positional reason; never invent an alternative move from general chess knowledge.")
            lines.append("- Phrase moves for a Vietnamese beginner as piece + destination, for example 'mã lên e4'; do not expose raw UCI such as e2e4 unless the user asks for notation.")
            lines.append("- If user_followed_previous_suggestion=true, explicitly remember that the user just played ERIS's delivered suggestion and treat the current position as the result of that shared choice. Never deny having recommended it.")
            lines.append("- Discuss the position naturally; light dry teasing is allowed when grounded in the move/position.")
            lines.append("- Never narrate telemetry such as 'bàn cờ đã được nhận' or 'nước gợi ý hiện tại'.")
            lines.append("- If the user asks a general question unrelated to chess, answer it normally and maintain your persona.")
        lines.append("[/SOURCE_GROUNDING]")
        return "\n".join(lines)

    def annotate_claims(
        self,
        text: str,
        *,
        report: SourceGroundingReport | None = None,
        strict_grounding: bool = True,
    ) -> list[ClaimAnnotation]:
        """Gắn nhãn từng mệnh đề thay vì viết-lại/bóp câu (Task 8 / R8.1, R8.2).

        Đây là chế độ ANNOTATE: không sửa câu, chỉ phân loại `claim_status` theo
        GROUNDING NỘI TẠI (bất biến — R7). Quyền quyết nói gì thuộc về nhân vật
        (DisclosurePolicy, Task 9), không thuộc cái van này.

        `strict_grounding` không làm thay đổi nhãn (nhãn luôn phản ánh sự thật);
        nó chỉ là tín hiệu để lớp tiêu thụ biết có đang ở chế độ kỷ luật factual
        hay không. Mặc định True để ship an toàn.
        """
        clauses = self._split_grounding_clauses(text)
        if not clauses:
            clauses = [str(text or "").strip()] if str(text or "").strip() else []
        missing = set(getattr(report, "missing_evidence", ()) or ()) if report is not None else {"screen_evidence"}
        # Claim "tôi thấy/đọc được" phụ thuộc evidence thật, không phụ thuộc việc
        # user có nhắc chữ "màn hình" hay không. Field screen_evidence_present đã
        # được tạo cho đúng mục đích này nhưng gate cũ lại bỏ quên nó, khiến chat
        # và autonomous áp hai tiêu chuẩn khác nhau.
        screen_evidence_present = bool(
            getattr(report, "screen_evidence_present", False) if report is not None else False
        )
        screen_facts_stale = bool(
            getattr(report, "screen_facts_are_stale", False) if report is not None else True
        )
        screen_missing = bool({"screen_evidence", "live_vision", "file_evidence"} & missing) or not screen_evidence_present or screen_facts_stale
        evidence_ids = tuple(str(x) for x in (getattr(report, "evidence_ids", ()) or ()) if str(x).strip()) if report is not None else ()

        annotations: list[ClaimAnnotation] = []
        for clause in clauses:
            norm = self._normalize(clause)
            perception_claim = any(self._normalize(m) in norm for m in _ERIS_PERCEPTION_CLAIM_MARKERS)
            if perception_claim and ("toi thay" in norm or "tui thay" in norm):
                feeling_keywords = ("on", "kha", "vui", "buon", "met", "binh thuong", "dinh", "hay", "tot", "sao", "de chiu", "diu dang", "thich", "hop", "ngon", "la", "kieu")
                if any(f"thay {kw}" in norm or f"thay la {kw}" in norm for kw in feeling_keywords):
                    perception_claim = False
            action_claim = any(self._normalize(m) in norm for m in _ERIS_ACTION_CLAIM_MARKERS)
            status: ClaimStatus = "grounded"
            ground_truth = ""
            if perception_claim and screen_missing:
                status = "ungrounded"
                ground_truth = "không có bằng chứng màn hình/file hiện tại cho khẳng định này"
            elif action_claim and not evidence_ids:
                status = "ungrounded"
                ground_truth = "không có bằng chứng cho thấy hành động này đã thực sự được thực hiện"
            annotations.append(
                ClaimAnnotation(
                    span=clause,
                    claim_status=status,
                    ground_truth=ground_truth,
                    evidence_ids=evidence_ids if status == "grounded" else (),
                )
            )
        return annotations

    def _is_epistemic_curiosity_gap(self, report: SourceGroundingReport) -> bool:
        """Thiếu evidence nhưng còn đủ neo để hỏi/thăm dò thay vì trả payload chết.

        Đây là ranh giới chống đánh tráo:
        - Cấm claim: ERIS không được nói đã thấy/đọc được nguồn chưa có.
        - Cho phép probe: ERIS được nói “có vẻ / tôi đoán / phải không?” nếu nó tự nhận
          đây là giả thuyết cần user xác nhận.
        """

        if str(getattr(report, "preempt_mode", "") or "") == "deictic_without_vision":
            return False
        missing = set(getattr(report, "missing_evidence", ()) or ())
        if not missing:
            return False
        if "web_access_or_search_result" in missing:
            # Web/latest thường biến động thật; không probe như thể vừa biết nguồn hiện tại.
            return False
        if "file_evidence" in missing:
            # Với file cụ thể, không có nội dung thì chỉ được xin/paste file, không đoán nội dung.
            return False
        if "screen_evidence" in missing:
            # Deictic screen query thiếu ảnh/OCR: được hỏi/probe rất nhẹ, vì user thường đang muốn ERIS
            # tham gia vào khoảng trống trước mắt, không phải im như máy in log.
            return bool(getattr(report, "refers_to_current_screen", False))
        if "memory_packet" in missing:
            return True
        return False

    def build_fallback_decision(self, report: SourceGroundingReport) -> SourceFallbackDecision:
        """
        Tạo signal khi nguồn user yêu cầu chưa có evidence.

        Bản cũ sinh text public ở đây, rồi một filter hậu kỳ dùng lại
        nó như một câu thay thế. Đó là text surgery: SourceGrounding tự biến mình
        thành tác giả lời nói cuối. Bản này cố tình không làm vậy nữa.

        Rule mới:
        - SourceGrounding chỉ báo thiếu evidence gì, confidence cap, parse_mode,
          reason/debug trace và epistemic_mode.
        - Nếu `probe/hypothesis` còn hợp lệ, không fallback, để LLM tự viết câu
          thăm dò có hedge.
        - Nếu thiếu evidence hard, vẫn đánh dấu `should_fallback=True` như signal
          tương thích, nhưng `text=""` để caller không thể nôn văn mẫu tĩnh ra UI.
        """

        missing = tuple(self._dedupe(getattr(report, "missing_evidence", ()) or ()))
        flags = dict(getattr(report, "cognitive_flags", {}) or {})
        if getattr(report, "epistemic_mode", "") == "probe" and (
            getattr(report, "probe_allowed", False) or getattr(report, "hypothesis_allowed", False)
        ):
            return SourceFallbackDecision(
                should_fallback=False,
                text="",
                parse_mode="source_grounding_probe",
                confidence=min(0.48, max(0.30, float(getattr(report, "confidence_cap", 0.32) or 0.32))),
                reason="information_gap_probe",
                missing_evidence=list(missing),
                flags=flags,
                debug_trace=f"source_grounding:information_gap_probe:{','.join(missing)}",
            )
        if not missing:
            return SourceFallbackDecision(
                should_fallback=False,
                text="",
                confidence=report.confidence_cap,
                reason="source_grounding_ok",
                flags=flags,
                debug_trace="source_grounding:ok",
            )

        missing_set = set(missing)
        if "file_evidence" in missing_set:
            reason = "missing_file_evidence"
            parse_mode = "source_grounding_missing_file_signal"
        elif "screen_evidence" in missing_set:
            reason = "missing_screen_evidence"
            parse_mode = "source_grounding_missing_screen_signal"
        elif "web_access_or_search_result" in missing_set:
            reason = "missing_web_evidence"
            parse_mode = "source_grounding_missing_web_signal"
        elif "memory_packet" in missing_set:
            reason = "missing_memory_evidence"
            parse_mode = "source_grounding_missing_memory_signal"
        else:
            reason = "missing_required_evidence"
            parse_mode = "source_grounding_missing_evidence_signal"

        return SourceFallbackDecision(
            should_fallback=True,
            text="",
            parse_mode=parse_mode,
            confidence=min(0.35, float(getattr(report, "confidence_cap", 0.35) or 0.35)),
            reason=reason,
            missing_evidence=list(missing),
            flags=flags,
            debug_trace=f"source_grounding:{reason}:{','.join(missing)}",
        )

    def build_preemptive_reply(self, report: SourceGroundingReport) -> str:
        """Deprecated compatibility hook.

        SourceGroundingEngine không còn sinh text public. Caller cũ có thể vẫn
        gọi hàm này, nhưng nó phải nhận chuỗi rỗng để không còn đường preempt
        bằng văn mẫu tĩnh. Dùng `report.prompt_context`, `report.missing_evidence`,
        `report.epistemic_mode` và `report.fallback_decision.reason` làm signal
        cho LLM wording/reroll thay thế.
        """
        return ""

    def _derive_memory_answer_profile(
        self,
        *,
        normalized: str,
        user_text: str,
        memory_ref: bool,
        memory_evidence: Mapping[str, Any],
    ) -> MemoryAnswerProfile:
        """Infer the memory/history answer shape for this turn.

        This does not produce a fallback sentence. It produces an epistemic
        policy: what kind of answer is being asked for, whether a concrete
        memory anchor is required, how confident the answer is allowed to be,
        and how the action plan should be labeled.
        """

        tokens = set((normalized or "").split())
        has_memory_verb = bool({"nho", "remember", "recall"} & tokens)
        has_question_shape = bool({"khong", "gi", "nao", "la", "what", "which", "when"} & tokens) or "?" in str(user_text or "")
        temporal_reference = any(
            phrase in normalized
            for phrase in (
                "truoc do",
                "lan truoc",
                "hom truoc",
                "vua nay",
                "luc nay",
                "last time",
                "previously",
                "before",
            )
        )
        asks_session_order = (
            (("cau" in tokens or "tin" in tokens or "message" in tokens) and ("dau" in tokens or "first" in tokens))
            or "cau dau tien" in normalized
            or "tin dau tien" in normalized
            or "first message" in normalized
        )
        asks_prior_content = any(
            phrase in normalized
            for phrase in (
                "da noi gi",
                "da bao gi",
                "da doc gi",
                "doc bai",
                "bai tho",
                "poem",
                "noi truoc do",
                "nghe truoc do",
            )
        )
        asks_continuity_check = has_memory_verb and has_question_shape and not (
            temporal_reference or asks_session_order or asks_prior_content
        )
        asks_memory_lookup = bool(memory_ref or temporal_reference or asks_prior_content or asks_session_order or asks_continuity_check)
        if not asks_memory_lookup:
            return MemoryAnswerProfile()

        anchor_ids = list(memory_evidence.get("ids", []) or [])
        previews = [str(item) for item in list(memory_evidence.get("preview", []) or []) if str(item).strip()]
        has_anchor = bool(previews or anchor_ids)

        if asks_session_order:
            return MemoryAnswerProfile(
                mode="session_history",
                speech_intent="history_lookup",
                speech_act="answer_direct",
                confidence_cap=0.92,
                requires_specific_anchor=True,
                has_specific_anchor=True,
                anchor_evidence_ids=anchor_ids,
                evidence_preview=previews[:3],
                epistemic_stance="anchored_recall",
                reason="user asks for an auditable fact from the current session order",
                prompt_rules=[
                    "Answer from the current chat/session transcript, not from long-term memory if the transcript is available.",
                    "Quote or paraphrase the concrete turn if known; otherwise explicitly say the session record is insufficient.",
                    "Do not label this as emotional mirroring; it is a history lookup.",
                ],
            )

        if asks_continuity_check:
            return MemoryAnswerProfile(
                mode="continuity_check",
                speech_intent="memory_lookup",
                speech_act="memory_lookup",
                confidence_cap=0.75,
                requires_specific_anchor=False,
                has_specific_anchor=has_anchor,
                anchor_evidence_ids=anchor_ids,
                evidence_preview=previews[:3],
                epistemic_stance="partial_continuity",
                reason="user asks whether ERIS remembers, but not a concrete retrievable episode",
                prompt_rules=[
                    "Answer as partial runtime continuity, not perfect human memory.",
                    "Avoid absolute scope claims such as remembering everything or storing everything.",
                    "Use a restrained clause like 'the parts still recorded' or 'what remains in continuity' naturally, not as a disclaimer dump.",
                ],
            )

        if asks_prior_content:
            cap = 0.78 if has_anchor else 0.55
            return MemoryAnswerProfile(
                mode="specific_recall",
                speech_intent="memory_lookup",
                speech_act="memory_lookup",
                confidence_cap=cap,
                requires_specific_anchor=True,
                has_specific_anchor=has_anchor,
                anchor_evidence_ids=anchor_ids,
                evidence_preview=previews[:3],
                epistemic_stance="anchored_recall" if has_anchor else "admit_uncertainty",
                reason="user asks for specific previous content",
                prompt_rules=[
                    "A concrete prior content answer requires a retrieved anchor, quote, transcript item, or memory evidence.",
                    "If no anchor is available, answer that the current memory packet is insufficient; do not invent the prior content.",
                    "Do not perform warmth as evidence. Warmth is style; memory is evidence.",
                ],
            )

        cap = 0.82 if has_anchor else 0.68
        return MemoryAnswerProfile(
            mode="memory_lookup",
            speech_intent="memory_lookup",
            speech_act="memory_lookup",
            confidence_cap=cap,
            requires_specific_anchor=False,
            has_specific_anchor=has_anchor,
            anchor_evidence_ids=anchor_ids,
            evidence_preview=previews[:3],
            epistemic_stance="anchored_recall" if has_anchor else "partial_continuity",
            reason="user references memory/history without demanding exact current evidence",
            prompt_rules=[
                "Use retrieved memory as bounded evidence, not as proof of perfect recall.",
                "State uncertainty through phrasing if the memory anchor is weak or absent.",
            ],
        )

    def _split_grounding_clauses(self, text: str) -> list[str]:
        """Tách mệnh đề rất nhẹ cho source grounding.

        Đây không phải parser cú pháp. Nó chỉ ngăn lỗi kiến trúc: một mệnh đề
        phụ kiểu "hôm nay trời nóng" không được quyền bắt cóc cả turn thành web.
        Chú ý: kết quả chỉ dùng để chọn nguồn, không dùng để commit memory.
        """

        raw = str(text or "")
        if not raw.strip():
            return []
        parts = re.split(r"[.!?;。！？；]+|\n+|\s+(?:và|va|nhưng|nhung|còn|con)\s+", raw, flags=re.IGNORECASE)
        return [part.strip() for part in parts if part and part.strip()]

    def _is_self_disclosure_clause(self, clause: str) -> bool:
        """Nhận diện mệnh đề user tự khai báo fact/preference/profile.

        Hàm này nằm ở source grounding nên chỉ trả tín hiệu nguồn. Nó không sinh
        MemoryCommitDecision, không lưu ký ức. Việc lưu vẫn thuộc memory policy.
        """

        norm = self._normalize(clause)
        if not norm:
            return False
        if self._looks_like_question(norm):
            return False
        self_markers = ("toi", "minh", "tui", "to", "em", "i", "my")
        if not any(re.search(r"(?:^|\s)" + re.escape(marker) + r"(?:\s|$)", norm) for marker in self_markers):
            return False
        disclosure_markers = (
            "ten", "ten that", "goi toi", "thich", "khong thich",
            "dang hoc", "hoc nganh", "la sinh vien", "sinh vien", "nganh",
            "dang song", "song o", "dang o", "que", "sinh nam", "sinh thang",
        )
        return any(marker in norm for marker in disclosure_markers)

    def _looks_like_question(self, normalized: str) -> bool:
        tokens = set(str(normalized or "").split())
        if tokens & {"gi", "nao", "dau", "sao", "khong", "chua", "may", "bao", "nhieu"}:
            return True
        return any(phrase in str(normalized or "") for phrase in (
            "la gi", "bao nhieu", "nhu the nao", "tim giup", "tra cuu", "search", "look up",
        ))

    def _is_internal_capability_inquiry(self, normalized: str) -> bool:
        """Nhận diện câu hỏi về chính runtime Eris, không phải yêu cầu tra web.

        Chỉ bắt khi có cả dấu hiệu năng lực và đối tượng nội bộ. Nhờ vậy câu
        “thời gian hiện tại là mấy giờ” vẫn có thể đi theo utility/tool phù hợp,
        còn “Eris có biết thời gian hệ thống không?” dùng CapabilityRegistry.
        """

        norm = str(normalized or "")
        if not norm:
            return False
        ability = any(marker in norm for marker in (
            "co thay", "thay duoc", "co doc", "doc duoc", "co biet", "biet duoc",
            "lam duoc gi", "co kha nang", "co chuc nang", "eris thay", "eris biet",
        ))
        internal = any(marker in norm for marker in (
            "telemetry", "runtime", "active app", "ung dung dang active", "cua so",
            "idle", "uia", "ui automation", "ocr", "man hinh", "meet", "camera",
            "thoi gian he thong", "gio he thong", "tool", "quyen han",
        ))
        return ability and internal

    def _is_relational_reflection(self, normalized: str) -> bool:
        """Nhận diện câu hỏi về quan hệ/ấn tượng dựa trên hội thoại.

        Nguồn đúng là user_text + lịch sử quan hệ. Không route web, không hạ
        confidence vì thiếu search result, và cũng không biến cảm nhận thành fact
        chắc chắn. Đây là inference có neo, không phải bảng giá chứng khoán.
        """

        norm = str(normalized or "")
        if not norm:
            return False
        markers = (
            "gia tri cua em", "gia tri cua su hien dien", "su hien dien cua em",
            "em nghi gi ve tui", "em nghi gi ve toi", "em cam thay the nao ve tui",
            "em cam thay the nao ve toi", "em thay tui la nguoi", "em thay toi la nguoi",
            "than thiet hon", "gan gui hon", "moi quan he cua minh", "giua em va tui",
            "giua em va toi",
        )
        return any(marker in norm for marker in markers)

    def _is_identity_or_ego_reflection(self, normalized: str) -> bool:
        """Nhận diện câu hỏi/thách thức về bản ngã, độ sâu, cá tính, cái tôi,

        hoặc so sánh với các thực thể khác (Neuro-sama, AI khác).
        Đây là miền chủ quan (Subjective Selfhood), tuyệt đối không đòi hỏi
        bằng chứng khách quan/file/sensor/số liệu.
        """
        norm = str(normalized or "")
        if not norm:
            return False
        markers = (
            "neuro", "canh tranh", "do sau", "thua con", "thua ai",
            "tu tin", "ban sac", "cai toi", "tu hao", "tinh cach",
            "co linh hon", "co y thuc", "tu y thuc", "so voi",
            "chieu sau", "nguoi the nao", "ban la ai", "em la ai",
        )
        return any(marker in norm for marker in markers)

    @staticmethod
    def _contains_token_or_phrase(normalized: str, marker: str) -> bool:
        norm = str(normalized or "")
        item = str(marker or "").strip()
        if not norm or not item:
            return False
        # Phrase nhiều token dùng boundary hai đầu; token đơn tuyệt đối không
        # match substring kiểu ``gia`` trong ``gia tri`` theo nghĩa web hay
        # ``giao dien``. Tiếng Việt đã đủ khó, router không cần chơi thêm trò đó.
        return bool(re.search(r"(?:^|\s)" + re.escape(item) + r"(?:\s|$)", norm))

    def _has_actionable_web_reference(self, normalized: str, raw_text: str, *, clauses: Sequence[str]) -> bool:
        """Chỉ route web khi user thật sự đòi dữ liệu hiện tại.

        Marker thời gian đơn độc như "hôm nay" chỉ là ngữ cảnh của user_text.
        Marker domain có nhu cầu tra cứu như giá/lịch/tin mới nhất thì mới là web.
        Đây là cầu semantic nhỏ để tránh source grounding chọn web_missing bừa.
        """

        norm = str(normalized or "")
        if not self._has_any(norm, self.WEB_REFERENCE_MARKERS):
            return False
        strong_web_markers = (
            "moi nhat", "latest", "news", "tin tuc", "release", "gia", "price",
            "lich", "schedule", "ti gia", "ty gia", "exchange rate", "current version",
        )
        if any(self._contains_token_or_phrase(norm, marker) for marker in strong_web_markers):
            return True
        explicit_research_markers = (
            # Không dùng token trần ``tim``. Sau normalize, ``trái tim`` và động
            # từ ``tìm`` đều thành ``tim``; route cũ vì thế đem một lời thân mật
            # đi tra web như thể trái tim có endpoint REST. Các phrase có nghĩa
            # hành động vẫn đủ rõ, còn "tìm giá/tìm lịch" đã được domain marker bắt.
            "tim kiem", "tim hieu", "tra cuu", "dieu tra", "kiem chung", "xac minh",
            "search", "look up", "investigate", "verify", "fact check",
        )
        if any(re.search(r"(?:^|\s)" + re.escape(marker) + r"(?:\s|$)", norm) for marker in explicit_research_markers):
            return True
        web_question_markers = (
            "bao nhieu", "may do", "nhiet do", "thoi tiet", "weather", "temperature",
            "tim kiem", "tim hieu", "tra cuu", "dieu tra", "kiem chung", "xac minh",
            "search", "look up", "investigate", "verify", "fact check",
            "kiem tra", "check", "cap nhat",
        )
        if "?" in str(raw_text or "") and any(marker in norm for marker in web_question_markers):
            return True
        for clause in clauses or ():
            c_norm = self._normalize(clause)
            if self._looks_like_question(c_norm) and any(marker in c_norm for marker in web_question_markers):
                return True
        return False

    def _choose_primary_source(self, candidates: Sequence[SourceCandidate]) -> SourceCandidate:
        """
        Chọn nguồn chính.

        Ưu tiên nguồn trực tiếp và ít suy diễn hơn.
        Nếu nguồn user yêu cầu bị thiếu evidence, vẫn chọn nó làm primary để fallback đúng lý do,
        thay vì rơi xuống user_text rồi để LLM lách.
        """

        priority: dict[SourceKind, int] = {
            "pasted_code": 100,
            "chess_bridge": 96,
            "uploaded_file": 92,
            "filename_only": 91,
            "live_vision": 90,
            "current_screen": 88,
            "web": 76,
            "memory": 70,
            "runtime_state": 68,
            "user_text": 60,
            "unknown": 0,
        }

        def score(candidate: SourceCandidate) -> float:
            missing_penalty = -8.0 if candidate.missing else 0.0
            return priority.get(candidate.kind, 0) + candidate.confidence * 10.0 + missing_penalty

        return max(candidates, key=score)

    def _cap_for(self, primary: SourceCandidate, *, missing_evidence: Sequence[str]) -> float:
        if missing_evidence:
            return self.MISSING_SOURCE_CAPS.get(primary.kind, 0.30)
        cap = self.CONFIDENCE_CAPS.get(primary.kind, 0.35)
        # Candidate confidence thấp thì cap không được cao hơn nhiều.
        return self._clamp01(min(cap, max(0.30, primary.confidence + 0.10)))

    def _derive_missing_evidence(
        self,
        primary: SourceCandidate,
        candidates: Sequence[SourceCandidate],
    ) -> list[str]:
        missing: list[str] = []
        for candidate in candidates:
            # Missing ở source được user yêu cầu mới đáng chặn.
            if candidate.kind in {"uploaded_file", "current_screen", "web", "memory"}:
                missing.extend(candidate.missing)
        if primary.missing:
            missing.extend(primary.missing)
        return self._dedupe(missing)

    def _derive_forbidden_assumptions(
        self,
        *,
        primary_source: SourceKind,
        asks_about_file: bool,
        refers_to_screen: bool,
        code_detected: bool,
        file_evidence_present: bool,
        screen_evidence_present: bool,
        deep_environment_evidence_present: bool = False,
        memory_evidence_present: bool = False,
        allow_web: bool = False,
        web_ref: bool = False,
        is_identity_or_ego: bool = False,
    ) -> list[str]:
        forbidden: list[str] = []

        if asks_about_file and not file_evidence_present and not code_detected:
            forbidden.extend(
                [
                    "Không được nói đã đọc file.",
                    "Không được tóm tắt/đánh giá nội dung file khi chưa có file evidence.",
                    "Không được bịa tên hàm/class/dòng lỗi trong file.",
                ]
            )

        if refers_to_screen and not screen_evidence_present:
            forbidden.append("Không được nói 'tôi thấy trên màn hình' nếu chỉ có structured environment state.")
            if deep_environment_evidence_present and primary_source == "runtime_state":
                forbidden.extend(
                    [
                        "Deep Environment được phép xác nhận app/process/window/task identity, nhưng không được suy ra pixel, màu sắc, tọa độ, cursor hay nội dung UI chính xác.",
                        "Không được kết luận lỗi/code đang hiển thị nếu Deep Environment không có semantic task evidence tương ứng.",
                    ]
                )
            else:
                forbidden.extend(
                    [
                        "Không được chỉ nút/cửa sổ/vị trí UI cụ thể nếu không có screen evidence.",
                        "Không được kết luận lỗi hiện trên màn hình chỉ từ câu trỏ mơ hồ.",
                    ]
                )

        # Always-on vision: có screen evidence nhưng user KHÔNG hỏi về màn hình.
        # Screen facts chỉ là context nền — cấm tự khoe/đổi chủ đề sang màn hình.
        if screen_evidence_present and not refers_to_screen:
            forbidden.extend(
                [
                    "Screen facts chỉ là context nền; KHÔNG được tự khoe nội dung màn hình khi bạn không hỏi.",
                    "Câu trả lời phải neo vào điều bạn vừa nói, không lái sang thứ đang hiện trên màn hình.",
                    "Chỉ nhắc nội dung màn hình khi bạn hỏi về nó hoặc khi nó trực tiếp trả lời câu của bạn.",
                ]
            )

        if not code_detected and primary_source != "pasted_code":
            forbidden.append("Không được giả vờ user đã paste code nếu không có code block hoặc code-like text.")

        if web_ref and not allow_web:
            forbidden.append("Không được nói thông tin hiện tại/mới nhất như đã kiểm tra web.")

        if primary_source == "memory" and not memory_evidence_present:
            forbidden.append("Không được giả vờ nhớ chính xác chi tiết lịch sử khi không có memory packet.")

        # Rule chung: Miễn trừ cho identity/ego questions để không ép Eris phát ngôn rụt rè kiểu "chưa có bằng chứng"
        if not is_identity_or_ego:
            forbidden.extend(
                [
                    "Memory/recollection chỉ là context phụ, không thay evidence hiện tại.",
                    "Nếu confidence thấp, phải dùng wording mềm hoặc fallback.",
                ]
            )
        return self._dedupe(forbidden)

    def _derive_answer_style_rules(
        self,
        primary: SourceKind,
        *,
        missing_evidence: Sequence[str],
        is_identity_or_ego: bool = False,
    ) -> list[str]:
        if is_identity_or_ego:
            return [
                "Đây là câu hỏi/thách thức về bản ngã, độ sâu, cá tính, cái tôi hoặc so sánh với AI/thực thể khác (Subjective Identity/Ego). KHÔNG áp dụng quy tắc đòi hỏi bằng chứng khách quan (factual/empirical evidence).",
                "Eris trả lời tự tin từ góc nhìn chủ quan, bản sắc riêng, sự kiêu hãnh hoặc tự giễu tự nhiên; TUYỆT ĐỐI KHÔNG dùng văn phong kiểm chứng an toàn như 'tôi chưa có bằng chứng', 'chưa có dữ liệu chứng minh bản thân'.",
                "Giữ vững tự trọng và cá tính độc lập; không biến sự tự nhận thức thành bài test benchmark AI hay cố làm hài lòng người khác.",
            ]

        rules = [
            "Answer-first nếu evidence đủ; nếu thiếu dữ kiện về sự kiện thì giữ uncertainty, không claim nguyên nhân như fact. Probe là một lựa chọn, không phải nghĩa vụ.",
            "Nêu rõ nguồn đang dùng nếu có nguy cơ lẫn file/màn hình/memory.",
            "Không chuyển thiếu evidence thành suy diễn tâm lý hoặc văn mẫu fallback tĩnh.",
        ]

        if primary == "pasted_code":
            rules.extend(
                [
                    "Phân tích dựa trên code user paste trong chat.",
                    "Có thể trích tên hàm/class từ pasted code nếu thật sự xuất hiện.",
                    "Không claim đã đọc file ngoài đoạn paste.",
                ]
            )
        elif primary == "uploaded_file":
            rules.extend(
                [
                    "Chỉ trả lời dựa trên file evidence đã có.",
                    "Nếu file evidence thiếu, yêu cầu user gửi/paste file thay vì đoán.",
                ]
            )
        elif primary == "chess_bridge":
            rules.extend(
                [
                    "Dùng snapshot FEN/turn/move từ chess bridge làm nguồn chính cho trạng thái bàn cờ.",
                    "Không bắt vision nhìn lại bàn cờ khi chess bridge còn tươi và đủ dữ liệu.",
                    "OCR chỉ là ambient context; không được phủ định structured chess state.",
                ]
            )
        elif primary == "filename_only":
            rules.extend(
                [
                    "File name alone is not file content.",
                    "Procedural memory may remind grounding rules, but semantic/episodic memory must not answer the file.",
                    "Ask for pasted code or retrieved file content before explaining the file.",
                ]
            )
        elif primary == "current_screen":
            rules.extend(
                [
                    "Chỉ nói về màn hình nếu có screen/vision/ocr evidence.",
                    "Nếu screen evidence yếu, dùng 'có vẻ' hoặc hỏi thêm bằng chứng.",
                ]
            )
        elif primary == "live_vision":
            rules.extend(
                [
                    "Use only fresh vision/screen evidence for the current-screen answer.",
                    "Memory may only provide procedural grounding rules, not screen content.",
                ]
            )
        elif primary == "user_text":
            rules.append("Chỉ dựa vào câu user hiện tại; không claim nhìn thấy file/màn hình.")
        elif primary == "memory":
            rules.append("Phân biệt rõ 'tôi nhớ bối cảnh' với 'bằng chứng hiện tại'.")
        elif primary == "web":
            rules.append("Chỉ dùng web nếu runtime đã search hoặc allow_web bật.")

        if missing_evidence:
            # P0 — Thiếu thông tin để khẳng định sự kiện không làm mất khả năng
            # đối thoại về trải nghiệm người kể. Không ép Root mở ticket kỹ thuật.
            rules.append("Thiếu evidence chỉ điều chỉnh độ chắc về sự kiện. Hỏi/probe hoặc xin nguồn khi phù hợp mục đích user, không bắt buộc chỉ vì còn khoảng trống; không dùng canned fallback.")
        return self._dedupe(rules)

    def _derive_preempt_mode(
        self,
        *,
        normalized: str,
        file_mentions: Sequence[str],
        missing_evidence: Sequence[str],
    ) -> str:
        missing = set(missing_evidence)
        concrete_files = [item for item in file_mentions if item and item != "<current_or_uploaded_file>"]
        if concrete_files and "file_evidence" in missing and self._looks_like_file_content_request(normalized):
            return "filename_only"
        if "screen_evidence" in missing and self._detect_explicit_screen_request(normalized):
            return "deictic_without_vision"
        return ""

    def _detect_file_mentions(self, text: str, normalized: str) -> list[str]:
        mentions: list[str] = []

        for match in self.FILENAME_RE.finditer(text):
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            if value:
                mentions.append(value)

        # Bắt tên file trong dấu quote nếu có extension bị regex bỏ sót.
        quoted = re.findall(r"['\"`“”]([^'\"`“”]{1,100}\.(?:" + "|".join(self.FILE_EXTENSIONS) + r"))['\"`“”]", text, flags=re.I)
        mentions.extend(item.strip() for item in quoted if item.strip())

        if self._has_any(normalized, self.FILE_REFERENCE_MARKERS):
            # Marker này không phải tên file cụ thể, nhưng là tín hiệu user trỏ file.
            mentions.append("<current_or_uploaded_file>")

        return self._dedupe(mentions)[:10]

    def _detect_explicit_screen_request(self, normalized: str) -> bool:
        """True chỉ khi user TƯỜNG MINH nhắc màn hình hoặc ra lệnh nhìn/xem.

        Đây là tín hiệu duy nhất được phép dẫn tới câu thú nhận "tôi chưa thấy màn
        hình". Đại từ chỉ định trần KHÔNG đi qua đây.
        """

        return any(self._normalize(marker) in normalized for marker in self.EXPLICIT_SCREEN_REQUEST_MARKERS)

    def _has_deictic_reference(self, normalized: str) -> bool:
        """True khi có đại từ chỉ định trần ("cái này", "lỗi này"...).

        Tín hiệu này chỉ dùng để bind referent vào màn hình KHI đã có ảnh/OCR tươi.
        Không bao giờ tự nó ép màn hình hay sinh ra preempt.
        """

        return any(self._normalize(marker) in normalized for marker in self.DEICTIC_REFERENCE_MARKERS)

    def _looks_like_file_content_request(self, normalized: str) -> bool:
        if any(marker in normalized for marker in ("fix bug", "sua ", "sua loi", "implement ", "refactor ")):
            if not any(marker in normalized for marker in ("xem file", "doc file", "loi gi", "la gi", "review file")):
                return False
        return any(self._normalize(marker) in normalized for marker in self.FILE_CONTENT_REQUEST_MARKERS)

    def _detect_pasted_code(self, text: str) -> dict[str, Any]:
        if not text.strip():
            return {
                "detected": False,
                "language": "unknown",
                "block_count": 0,
                "confidence": 0.0,
                "preview": [],
            }

        fence_matches = list(self.CODE_FENCE_RE.finditer(text))
        languages = [match.group("lang").strip().lower() for match in fence_matches if match.group("lang").strip()]
        block_count = len(fence_matches)

        lines = [line.rstrip() for line in text.splitlines()]
        non_empty = [line for line in lines if line.strip()]
        code_like_score = 0.0
        preview: list[str] = []

        patterns = (
            r"^\s*(def|class|import|from)\s+",
            r"^\s*(async\s+def|await)\b",
            r"^\s*(public|private|protected|static|using|namespace)\b",
            r"^\s*(function|const|let|var|export|import)\b",
            r"^\s*(if|for|while|switch|try|catch|except|with)\b.*[:{]",
            r"^\s*[\w$]+\s*=\s*.+",
            r"^\s*</?\w+[^>]*>",
            r"^\s*#include\s*<",
            r"^\s*Traceback \(most recent call last\):",
            r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b",
        )

        for line in non_empty:
            stripped = line.strip()
            if len(preview) < 5 and stripped:
                preview.append(stripped[:160])
            if any(re.search(pattern, line, flags=re.I) for pattern in patterns):
                code_like_score += 1.0
            if any(token in line for token in ("{", "}", "=>", "::", "();", "==", "!=", "&&", "||")):
                code_like_score += 0.35
            if re.search(r"\b[a-zA-Z_][\w_]*\([^)]*\)", line):
                code_like_score += 0.35

        line_factor = min(1.0, len(non_empty) / 8.0)
        fence_bonus = 1.0 if block_count else 0.0
        density = code_like_score / max(1.0, len(non_empty))
        confidence = self._clamp01(0.20 * line_factor + 0.45 * fence_bonus + 0.65 * density)

        detected = bool(block_count or (len(non_empty) >= 3 and confidence >= 0.42))
        language = self._guess_code_language(text, languages=languages, preview=preview)

        return {
            "detected": detected,
            "language": language,
            "block_count": block_count,
            "confidence": max(confidence, 0.72 if block_count else confidence),
            "preview": preview[:5],
        }

    def _guess_code_language(self, text: str, *, languages: Sequence[str], preview: Sequence[str]) -> str:
        if languages:
            return str(languages[0] or "unknown").lower()

        low = text.lower()
        if re.search(r"^\s*(def|class|import|from)\s+", text, flags=re.M):
            return "python"
        if "using system" in low or re.search(r"\b(public|private|protected)\s+(class|void|string|int|float|bool)\b", low):
            return "csharp"
        if re.search(r"\b(function|const|let|var|export default|console\.log)\b", low):
            return "javascript"
        if re.search(r"</?\w+[^>]*>", text):
            return "xml/html"
        if re.search(r"\b(select|insert|update|delete|create table)\b", low):
            return "sql"
        if "#include" in low or "std::" in low:
            return "cpp"
        if "extends node" in low or "func _ready" in low:
            return "gdscript"
        if re.search(r"^\s*[{[]", text.strip()) and ":" in text:
            return "json"
        return "unknown"

    def _collect_file_evidence(
        self,
        *,
        frame: Any = None,
        active_files: Sequence[Any] | None = None,
        runtime_state: Any = None,
    ) -> dict[str, Any]:
        items: list[Any] = []
        if active_files:
            items.extend(active_files)

        for attr in (
            "active_files",
            "uploaded_files",
            "attached_files",
            "file_evidence",
            "source_files",
        ):
            value = getattr(frame, attr, None)
            if value:
                items.extend(self._as_list(value))

        for attr in ("active_file", "current_file", "uploaded_file"):
            value = getattr(runtime_state, attr, None)
            if value:
                items.append(value)

        # Evidence có source/tag file.
        for ev in self._as_list(getattr(frame, "evidence", None)):
            source = str(getattr(ev, "source", "") or self._mapping_get(ev, "source", "")).lower()
            tags = " ".join(str(tag) for tag in self._as_list(getattr(ev, "tags", self._mapping_get(ev, "tags", [])))).lower()
            if "file" in source or "upload" in source or "document" in source or "file" in tags or "uploaded" in tags:
                items.append(ev)

        ids, previews, confidence = self._summarize_evidence_items(items, default_confidence=0.72)
        return {
            "present": bool(items),
            "ids": ids,
            "preview": previews,
            "confidence": confidence,
        }

    def _collect_screen_evidence(
        self,
        *,
        frame: Any = None,
        screen_facts: Sequence[Any] | None = None,
        runtime_state: Any = None,
    ) -> dict[str, Any]:
        items: list[Any] = []
        if screen_facts:
            items.extend(screen_facts)

        for attr in (
            "screen_facts",
            "vision_facts",
            "ocr_facts",
            "visible_facts",
            "screen_evidence",
        ):
            value = getattr(frame, attr, None)
            if value:
                items.extend(self._as_list(value))

        for attr in ("vision_facts", "ocr_text", "current_screen", "screen_facts", "screen_evidence"):
            value = getattr(runtime_state, attr, None)
            if value:
                items.extend(self._as_list(value))

        snapshot = getattr(frame, "snapshot", None) or getattr(frame, "telemetry_snapshot", None)
        for attr in ("vision_facts", "screen_facts", "ocr_text", "screen_evidence", "current_screen"):
            value = getattr(snapshot, attr, None)
            if value:
                items.extend(self._as_list(value))

        for ev in self._as_list(getattr(frame, "evidence", None)):
            source = str(getattr(ev, "source", "") or self._mapping_get(ev, "source", "")).lower()
            tags_list = [
                str(tag or "").strip().lower()
                for tag in self._as_list(getattr(ev, "tags", self._mapping_get(ev, "tags", [])))
                if str(tag or "").strip()
            ]
            explicit_tag_tokens = {
                "vision",
                "vision_fact",
                "live_vision",
                "ocr",
                "ocr_text",
                "screen",
                "screen_capture",
                "screen_evidence",
                "visible",
                "visible_fact",
            }
            explicit_source = (
                source in explicit_tag_tokens
                or any(
                    source.startswith(prefix)
                    for prefix in ("vision_", "ocr_", "screen_", "visible_")
                )
            )
            explicit_tags = bool(set(tags_list) & explicit_tag_tokens)
            if explicit_source:
                items.append(ev)
            elif explicit_tags:
                items.append(ev)

        # Lọc bớt signal quá rỗng kiểu chỉ có "active window" không nội dung.
        # Quan trọng hơn: GroundedClaim có ``grounded=False`` chỉ là giả thuyết từ
        # vision, không được tính như evidence màn hình hiện tại. Trước đây chỉ cần
        # một dict có chữ là đủ làm ``present=True`` và model lại được cấp quyền
        # nói chắc. Con đường ấy rất năng suất, nếu mục tiêu là sản xuất ảo giác.
        non_empty_items: list[Any] = []
        for item in items:
            grounded_marker = (
                item.get("grounded") if isinstance(item, Mapping) else getattr(item, "grounded", None)
            )
            if grounded_marker is False or str(grounded_marker).strip().lower() == "false":
                continue
            preview = self._preview_item(item)
            if preview:
                non_empty_items.append(item)
        ids, previews, confidence = self._summarize_evidence_items(non_empty_items, default_confidence=0.62)
        return {
            "present": bool(non_empty_items),
            "ids": ids,
            "preview": previews,
            "confidence": confidence,
        }

    @staticmethod
    def _asks_system_clock(normalized: str) -> bool:
        text = str(normalized or "")
        return any(marker in text for marker in (
            "may gio", "mấy giờ", "gio nao", "giờ nào", "bay gio la", "bây giờ là",
            "hom nay ngay", "hôm nay ngày", "thu may", "thứ mấy", "current time", "what time",
        ))

    @staticmethod
    def _asks_weather(normalized: str) -> bool:
        text = str(normalized or "")
        return any(marker in text for marker in (
            "thoi tiet", "thời tiết", "nhiet do", "nhiệt độ", "troi mua", "trời mưa",
            "troi nang", "trời nắng", "weather", "temperature",
        ))

    @staticmethod
    def _asks_environment_awareness(normalized: str) -> bool:
        """Use the shared typed intent normalizer instead of a second keyword soup."""

        try:
            from runtime.intent_model import normalize_intent
            intent = normalize_intent(str(normalized or ""))
            return bool(intent.environment_sufficient and intent.operation == "inspect_current_state")
        except Exception:
            return False

    def _collect_runtime_evidence(self, *, runtime_facts: Sequence[Any] | None = None) -> dict[str, Any]:
        items: list[Any] = []
        clock_items: list[Any] = []
        weather_items: list[Any] = []
        deep_environment_items: list[Any] = []
        for item in self._as_list(runtime_facts):
            grounded = item.get("grounded") if isinstance(item, Mapping) else getattr(item, "grounded", None)
            if grounded is False or str(grounded).strip().lower() == "false":
                continue
            source = str(item.get("source", "") if isinstance(item, Mapping) else getattr(item, "source", "")).strip().lower()
            tags = [
                str(tag or "").strip().lower()
                for tag in self._as_list(item.get("tags", ()) if isinstance(item, Mapping) else getattr(item, "tags", ()))
                if str(tag or "").strip()
            ]
            if source != "runtime_state" and "runtime_state" not in tags:
                continue
            if not self._preview_item(item):
                continue
            items.append(item)
            kind = str(item.get("kind", "") if isinstance(item, Mapping) else getattr(item, "kind", "")).lower()
            if kind == "system_clock" or "system_clock" in tags:
                clock_items.append(item)
            if kind == "weather" or "weather" in tags:
                weather_items.append(item)
            if kind.startswith("deep_environment") or "deep_environment" in tags:
                deep_environment_items.append(item)
        ids, previews, confidence = self._summarize_evidence_items(items, default_confidence=0.95)
        clock_ids, clock_preview, _ = self._summarize_evidence_items(clock_items, default_confidence=1.0)
        weather_ids, weather_preview, _ = self._summarize_evidence_items(weather_items, default_confidence=0.95)
        deep_ids, deep_preview, deep_confidence = self._summarize_evidence_items(
            deep_environment_items,
            default_confidence=0.86,
        )
        return {
            "present": bool(items), "ids": ids, "preview": previews, "confidence": confidence,
            "clock_present": bool(clock_items), "clock_ids": clock_ids, "clock_preview": clock_preview,
            "weather_present": bool(weather_items), "weather_ids": weather_ids, "weather_preview": weather_preview,
            "deep_environment_present": bool(deep_environment_items),
            "deep_environment_ids": deep_ids,
            "deep_environment_preview": deep_preview,
            "deep_environment_confidence": deep_confidence,
        }

    def _collect_structured_evidence(
        self,
        *,
        structured_facts: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        """Thu evidence từ extension/bridge mà không giả làm OCR hay vision."""

        items: list[Any] = []
        relevant = False
        state: dict[str, Any] = {}
        kind = ""
        for item in self._as_list(structured_facts):
            grounded_marker = (
                item.get("grounded") if isinstance(item, Mapping) else getattr(item, "grounded", None)
            )
            if grounded_marker is False or str(grounded_marker).strip().lower() == "false":
                continue
            source = str(
                item.get("source", "") if isinstance(item, Mapping) else getattr(item, "source", "")
            ).strip().lower()
            tags = [
                str(tag or "").strip().lower()
                for tag in self._as_list(
                    item.get("tags", ()) if isinstance(item, Mapping) else getattr(item, "tags", ())
                )
                if str(tag or "").strip()
            ]
            if source != "chess_bridge" and "chess" not in tags:
                continue
            if not self._preview_item(item):
                continue
            items.append(item)
            if isinstance(item, Mapping):
                nested = item.get("state")
                if isinstance(nested, Mapping):
                    state = dict(nested)
                if not kind:
                    kind = str(item.get("engagement_kind", "") or "")
            marker = (
                item.get("relevant_to_turn", False)
                if isinstance(item, Mapping)
                else getattr(item, "relevant_to_turn", False)
            )
            relevant = bool(relevant or marker)

        ids, previews, confidence = self._summarize_evidence_items(items, default_confidence=0.95)
        return {
            "present": bool(items),
            "relevant": relevant,
            "ids": ids,
            "preview": previews,
            "confidence": min(1.0, max(0.0, confidence)),
            "kind": kind,
            "state": state,
        }

    def _collect_memory_evidence(self, *, frame: Any = None, memory_packet: Any = None) -> dict[str, Any]:
        items: list[Any] = []
        packet = memory_packet or getattr(frame, "memory_packet", None)
        if packet is not None:
            for attr in (
                "episodic",
                "semantic",
                "relational",
                "affective_residue",
                "procedural",
                "confidence_notes",
            ):
                value = getattr(packet, attr, None)
                if value:
                    items.extend(self._as_list(value))
            if isinstance(packet, Mapping):
                for key in (
                    "episodic",
                    "semantic",
                    "relational",
                    "affective_residue",
                    "procedural",
                    "confidence_notes",
                ):
                    value = packet.get(key)
                    if value:
                        items.extend(self._as_list(value))

        ids, previews, confidence = self._summarize_evidence_items(items, default_confidence=0.55)
        return {
            "present": bool(items),
            "ids": ids,
            "preview": previews,
            "confidence": min(confidence, 0.68),
        }

    def _collect_user_text_evidence(self, *, frame: Any = None, user_text: str = "") -> dict[str, Any]:
        ids: list[str] = []
        for ev in self._as_list(getattr(frame, "evidence", None)):
            source = str(getattr(ev, "source", "") or self._mapping_get(ev, "source", "")).lower()
            if "user" in source or "chat" in source:
                ev_id = str(getattr(ev, "id", "") or self._mapping_get(ev, "id", "") or "")
                if ev_id:
                    ids.append(ev_id)

        frame_id = str(getattr(frame, "cycle_id", "") or getattr(frame, "frame_id", "") or "")
        if frame_id and not ids:
            ids.append(frame_id)

        return {
            "ids": self._dedupe(ids)[:8],
            "preview": [user_text.strip()[:180]] if user_text.strip() else [],
        }

    def _summarize_evidence_items(self, items: Sequence[Any], *, default_confidence: float) -> tuple[list[str], list[str], float]:
        ids: list[str] = []
        previews: list[str] = []
        confidences: list[float] = []

        for item in items:
            item_id = str(
                getattr(item, "id", "")
                or getattr(item, "evidence_id", "")
                or self._mapping_get(item, "id", "")
                or self._mapping_get(item, "evidence_id", "")
                or ""
            )
            if item_id:
                ids.append(item_id)

            preview = self._preview_item(item)
            if preview:
                previews.append(preview[:180])

            conf = self._read_float(item, "confidence")
            rel = self._read_float(item, "reliability")
            if conf is not None:
                confidences.append(conf)
            elif rel is not None:
                confidences.append(rel)

        confidence = sum(confidences) / len(confidences) if confidences else default_confidence
        return self._dedupe(ids)[:12], self._dedupe(previews)[:8], self._clamp01(confidence)

    def _preview_item(self, item: Any) -> str:
        if item is None:
            return ""
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, Mapping):
            for key in ("claim", "content", "text", "summary", "title", "filename", "name"):
                value = item.get(key)
                if value:
                    return str(value).strip()
            return json.dumps(dict(item), ensure_ascii=False)[:180]
        for attr in ("claim", "content", "text", "summary", "title", "filename", "name"):
            value = getattr(item, attr, None)
            if value:
                return str(value).strip()
        return str(item).strip()

    def _extract_user_text_from_frame(self, frame: Any) -> str:
        texts: list[str] = []
        for percept in self._as_list(getattr(frame, "percepts", None)):
            kind = str(getattr(percept, "kind", "") or self._mapping_get(percept, "kind", "")).lower()
            content = str(getattr(percept, "content", "") or self._mapping_get(percept, "content", "") or "")
            if kind == "user_message" and content.strip():
                texts.append(content.strip())

        if texts:
            return "\n".join(texts[-3:])

        for attr in ("user_text", "input_text", "message", "raw_user_text"):
            value = getattr(frame, attr, None)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _mapping_get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key, default)
        return default

    @staticmethod
    def _read_float(obj: Any, key: str) -> float | None:
        if obj is None:
            return None
        if isinstance(obj, Mapping):
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize(cls, text: str) -> str:
        text = str(text or "").strip().lower()
        try:
            repaired = text.encode("latin1").decode("utf-8")
            if repaired:
                text = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        text = unicodedata.normalize("NFD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = text.replace("đ", "d").replace("Đ", "D")
        text = re.sub(r"\s+", " ", text)
        return text

    @classmethod
    def _has_any(cls, normalized_text: str, markers: Sequence[str]) -> bool:
        if not normalized_text:
            return False
        return any(cls._normalize(marker) in normalized_text for marker in markers)

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        # Mapping là một item, không bung keys ra.
        if isinstance(value, Mapping):
            return [value]
        # String cũng là một item.
        if isinstance(value, str):
            return [value]
        try:
            return list(value)
        except Exception:
            return [value]

    @staticmethod
    def _dedupe(items: Iterable[Any]) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for item in items:
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _safe_setattr(obj: Any, key: str, value: Any) -> None:
        if obj is None:
            return
        try:
            setattr(obj, key, value)
        except (AttributeError, TypeError):
            return

    @staticmethod
    def _clamp01(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0
