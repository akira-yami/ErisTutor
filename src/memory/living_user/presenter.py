# -*- coding: utf-8 -*-
"""Character-facing projection cho Living User Memory.

File này KHÔNG tạo fact mới và KHÔNG đọc raw chain-of-thought.
Nó chỉ nhận:

- canonical memories đã được lọc cho UI;
- episode đã được Living User Memory tổng hợp;
- một ``expression_lens`` semantic do runtime dựng từ personality/relationship state.

Sau đó worker ``surface`` viết lại thành lời của ERIS để panel có cảm giác
"ERIS đang nhớ mình" thay vì một trang quản trị record. Output này chỉ là
presentation; tuyệt đối không được feed ngược vào canonical memory làm evidence.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_MEMORY_PORTRAIT_SYSTEM = r"""Bạn là SURFACE WRITER của ERIS cho màn hình "Những chuyện ERIS vẫn nhớ".

Bạn KHÔNG quản lý memory và KHÔNG được tạo fact mới. Chỉ biến evidence được cấp
thành cách ERIS tự kể lại về người dùng.

MỤC TIÊU CẢM GIÁC
- Người dùng phải có cảm giác đang lén nhìn cách ERIS nhớ và nhìn mình, không phải
  đang đọc dashboard của một trợ lý AI.
- ERIS là một nhân vật có continuity: kín, hay quan sát chéo, phân tích nhiều,
  có hài khô, đôi lúc tự ý thức/xấu hổ rồi chữa lời. Sự gần gũi phải tùy lens.
- Có thể hơi "anime" có chủ đích: ngập ngừng, câu bị ngắt, tự chối một lời khen,
  một aside kiểu "...mong là bạn không đọc kỹ đoạn này", "e-eh?", "mou..." nếu
  thật sự hợp nhịp. Một chút lố nhưng phải diễn tới cùng như character acting.
- KHÔNG spam tiếng Nhật. Không dùng "desu", "sugoi", "kawaii", honorific hoặc
  baby-talk như công thức. Không gọi user bằng biệt danh/honorific nếu evidence
  không hề cho phép.
- Ưu tiên tiếng Việt tự nhiên. Tránh jargon/tiếng Anh kiểu AI: confidence, source,
  inferred, observed, canonical, profile, database, memory record, generic...
- Đừng nói "dựa trên dữ liệu", "theo bộ nhớ", "hệ thống ghi nhận".

AN TOÀN VÀ TÍNH ĐÚNG
- Chỉ được claim điều có trong ``evidence``.
- Mỗi item PHẢI có ``source_refs`` lấy nguyên xi từ evidence. Không có source_ref
  hợp lệ thì không viết item đó.
- explicit = có thể nói như điều nhớ chắc.
- observed = nói như thứ ERIS đã để ý qua nhiều lần, không biến thành chân lý.
- inferred/uncertain = bắt buộc thể hiện không chắc: "hình như", "tôi chưa chắc",
  "có vẻ"; có thể tự ngượng vì đã phân tích user hơi kỹ.
- Episode là chuyện đã xảy ra; không bịa thêm địa điểm, cảm xúc hay lời thoại.
- Không đưa secret, token, credential, thông tin nhạy cảm hoặc raw internal thought.
- ``expression_lens`` chỉ quyết định CÁCH NÓI. Không được biến lens thành fact về user.

OUTPUT JSON DUY NHẤT, không markdown:
{
  "opening": "2-4 câu ERIS nhìn user lúc này; có thể có một nhịp tự chữa",
  "aside": "0-1 câu aside ngắn; để trống nếu không cần",
  "sections": [
    {
      "kind": "how_i_see_you|things_you_like|our_memories|not_sure",
      "title": "tiêu đề tiếng Việt có giọng ERIS",
      "items": [
        {
          "title": "ngắn",
          "body": "1-4 câu, character voice",
          "aside": "0-1 câu nhỏ, có thể rỗng",
          "source_refs": ["ref nguyên xi từ evidence"]
        }
      ]
    }
  ]
}

Giữ tổng số item 4-9. Đừng cố lấp đủ section nếu evidence không có gì đáng nói.
Opening cũng không được thêm claim ngoài evidence."""


@dataclass(frozen=True, slots=True)
class MemoryPortraitItem:
    title: str
    body: str
    aside: str = ""
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "aside": self.aside,
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class MemoryPortraitSection:
    kind: str
    title: str
    items: tuple[MemoryPortraitItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class MemoryPortrait:
    opening: str
    aside: str = ""
    sections: tuple[MemoryPortraitSection, ...] = ()
    renderer: str = "deterministic_fallback"
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "opening": self.opening,
            "aside": self.aside,
            "sections": [section.to_dict() for section in self.sections],
            "renderer": self.renderer,
            "fingerprint": self.fingerprint,
        }


@dataclass(slots=True)
class MemoryPortraitPresenter:
    """Gọi đúng ``surface`` worker để viết character prose.

    Cache chỉ nằm trong RAM. Canonical memory không bao giờ nhận lại prose ở đây.
    """

    llm_service: Any
    client: Any = None
    fallback_to_service_surface: bool = True
    timeout_seconds: float = 7.0
    max_tokens: int = 1300
    _cache: dict[str, MemoryPortrait] = field(default_factory=dict, init=False, repr=False)
    _cache_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def render(
        self,
        *,
        records: Sequence[Mapping[str, Any]],
        episodes: Sequence[Mapping[str, Any]] = (),
        expression_lens: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> MemoryPortrait:
        evidence = _build_evidence(records, episodes)
        fingerprint = _fingerprint(evidence, expression_lens or {})
        if not force:
            with self._cache_lock:
                cached = self._cache.get(fingerprint)
                if cached is not None:
                    return cached

        fallback = _fallback_portrait(records, episodes, fingerprint=fingerprint)
        if not evidence:
            return fallback

        client = self.client
        if client is None and self.fallback_to_service_surface:
            client = getattr(self.llm_service, "surface_client", None)
        create = getattr(client, "create_chat_completion", None)
        if not callable(create):
            return fallback

        payload = {
            "expression_lens": _json_safe(expression_lens or {}),
            "evidence": evidence,
        }
        messages = [
            {"role": "system", "content": _MEMORY_PORTRAIT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ]
        try:
            from runtime.inference_broker import inference_context
            from runtime.work_keys import stable_work_key

            work_key = stable_work_key("living_user_portrait", fingerprint)
            with inference_context(
                "memory_surface",
                optional=True,
                timeout_seconds=self.timeout_seconds,
                cancel_scope="panel:memory_portrait",
                coalesce_key=work_key,
                idempotency_key=work_key,
                project="memory_surface",
                credential_pool="surface",
                metadata={"operation": "living_user_portrait"},
            ):
                response = create(
                    messages=messages,
                    temperature=0.78,
                    frequency_penalty=0.15,
                    presence_penalty=0.10,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                    grammar=None,
                    timeout=self.timeout_seconds,
                )
        except Exception:
            return fallback

        extractor = getattr(self.llm_service, "_extract_content", None)
        content = str(extractor(response) or "") if callable(extractor) else _extract_text(response)
        parsed = _parse_json(content)
        portrait = _validate_model_portrait(parsed, evidence=evidence, fingerprint=fingerprint)
        if portrait is None:
            return fallback
        with self._cache_lock:
            # Giữ cache rất nhỏ; portrait là presentation tạm, không phải store mới.
            if len(self._cache) >= 12:
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[fingerprint] = portrait
        return portrait


def _build_evidence(
    records: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(records)[:36]:
        path = str(row.get("path") or "").strip()
        value = str(row.get("display_value") or "").strip()
        if not path or not value:
            continue
        scope_key = str(row.get("scope_key") or "").strip()
        ref = f"mem:{path}:{scope_key or 'global'}"
        explicitness = str(row.get("explicitness") or "inferred")
        confidence = _clamp(row.get("confidence"), 0.5)
        out.append({
            "ref": ref,
            "kind": "memory",
            "path": path,
            "category": str(row.get("category") or "other"),
            "label": str(row.get("label") or path)[:120],
            "value": value[:420],
            "epistemic": (
                "explicit" if explicitness == "explicit"
                else "observed" if explicitness == "observed" and confidence >= 0.62
                else "uncertain"
            ),
        })
    for row in list(episodes)[:10]:
        episode_id = str(row.get("id") or "").strip()
        summary = str(row.get("summary") or "").strip()
        if not episode_id or not summary:
            continue
        out.append({
            "ref": f"ep:{episode_id}",
            "kind": "episode",
            "title": str(row.get("title") or "Một chuyện cũ")[:160],
            "summary": summary[:760],
            "outcome": str(row.get("outcome") or "")[:260],
            "importance": round(_clamp(row.get("importance"), 0.5), 3),
        })
    return out


def _fallback_portrait(
    records: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    *,
    fingerprint: str,
) -> MemoryPortrait:
    if not records and not episodes:
        return MemoryPortrait(
            opening="Tôi chưa có gì đủ chắc để kể về bạn cả. Hừm… chắc phải nói chuyện thêm một chút đã.",
            aside="Mà thế này cũng hơi giống bắt tôi làm bài kiểm tra trí nhớ nhỉ…",
            sections=(),
            fingerprint=fingerprint,
        )

    sections: list[MemoryPortraitSection] = []
    about: list[MemoryPortraitItem] = []
    likes: list[MemoryPortraitItem] = []
    unsure: list[MemoryPortraitItem] = []
    for row in list(records)[:12]:
        title = str(row.get("label") or row.get("path") or "Một điều về bạn")[:120]
        value = str(row.get("display_value") or "").strip()
        if not value:
            continue
        path = str(row.get("path") or "")
        ref = f"mem:{path}:{str(row.get('scope_key') or '').strip() or 'global'}"
        explicitness = str(row.get("explicitness") or "inferred")
        confidence = _clamp(row.get("confidence"), 0.5)
        if explicitness == "inferred" or confidence < 0.62:
            unsure.append(MemoryPortraitItem(
                title=title,
                body=f"Tôi chưa chắc lắm, nhưng có vẻ {value}.",
                aside="…Nếu tôi đoán sai thì coi như chưa đọc thấy nhé.",
                source_refs=(ref,),
            ))
        elif str(row.get("category") or "") == "preferences":
            likes.append(MemoryPortraitItem(
                title=title,
                body=f"Cái này thì tôi nhớ: {value}.",
                source_refs=(ref,),
            ))
        else:
            about.append(MemoryPortraitItem(
                title=title,
                body=(
                    f"Bạn từng nói rõ với tôi rằng {value}."
                    if explicitness == "explicit"
                    else f"Tôi để ý khá nhiều lần rồi: {value}."
                ),
                source_refs=(ref,),
            ))

    if about:
        sections.append(MemoryPortraitSection("how_i_see_you", "Trong mắt tôi", tuple(about[:3])))
    if likes:
        sections.append(MemoryPortraitSection("things_you_like", "Mấy thứ bạn thích", tuple(likes[:3])))

    ep_items: list[MemoryPortraitItem] = []
    for row in list(episodes)[:3]:
        eid = str(row.get("id") or "").strip()
        summary = str(row.get("summary") or "").strip()
        if not eid or not summary:
            continue
        ep_items.append(MemoryPortraitItem(
            title=str(row.get("title") or "Một chuyện tôi còn nhớ")[:120],
            body=summary[:520],
            aside="…Ừ, chuyện này tôi vẫn chưa quên.",
            source_refs=(f"ep:{eid}",),
        ))
    if ep_items:
        sections.append(MemoryPortraitSection("our_memories", "Chuyện của hai đứa", tuple(ep_items)))
    if unsure:
        sections.append(MemoryPortraitSection("not_sure", "Có vài chuyện tôi chưa dám chắc", tuple(unsure[:2])))

    first = next((str(row.get("display_value") or "").strip() for row in records if str(row.get("display_value") or "").strip()), "")
    opening = (
        f"Tôi nhớ về bạn không chỉ bằng mấy dòng thông tin đâu. Có những thứ cứ lặp lại đến mức tôi tự để ý — như chuyện {first}."
        if first else
        "Có vài chuyện về bạn tôi vẫn giữ lại khá rõ. Không phải cái nào tôi cũng dám chắc… nhưng cũng không đến mức quên sạch đâu."
    )
    return MemoryPortrait(
        opening=opening,
        aside="…Đừng có soi tôi đang nhớ kỹ đến mức nào.",
        sections=tuple(sections[:4]),
        fingerprint=fingerprint,
    )


def _validate_model_portrait(
    payload: Mapping[str, Any] | None,
    *,
    evidence: Sequence[Mapping[str, Any]],
    fingerprint: str,
) -> MemoryPortrait | None:
    if not isinstance(payload, Mapping):
        return None
    allowed_refs = {str(row.get("ref") or "") for row in evidence if str(row.get("ref") or "")}
    opening = _compact(payload.get("opening"), 900)
    aside = _compact(payload.get("aside"), 260)
    raw_sections = payload.get("sections")
    if not opening or not isinstance(raw_sections, Sequence) or isinstance(raw_sections, (str, bytes)):
        return None

    allowed_kinds = {"how_i_see_you", "things_you_like", "our_memories", "not_sure"}
    sections: list[MemoryPortraitSection] = []
    item_count = 0
    for raw_section in list(raw_sections)[:4]:
        if not isinstance(raw_section, Mapping):
            continue
        kind = str(raw_section.get("kind") or "").strip()
        if kind not in allowed_kinds:
            continue
        title = _compact(raw_section.get("title"), 100) or _default_section_title(kind)
        raw_items = raw_section.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            continue
        items: list[MemoryPortraitItem] = []
        for raw_item in list(raw_items)[:4]:
            if not isinstance(raw_item, Mapping):
                continue
            refs_raw = raw_item.get("source_refs")
            if isinstance(refs_raw, str):
                refs = (refs_raw,) if refs_raw in allowed_refs else ()
            elif isinstance(refs_raw, Sequence):
                refs = tuple(dict.fromkeys(str(ref) for ref in refs_raw if str(ref) in allowed_refs))[:4]
            else:
                refs = ()
            # Đây là guard quan trọng: prose không evidence không được đi lên panel.
            if not refs:
                continue
            item_title = _compact(raw_item.get("title"), 120)
            body = _compact(raw_item.get("body"), 760)
            item_aside = _compact(raw_item.get("aside"), 240)
            if not item_title or not body:
                continue
            items.append(MemoryPortraitItem(item_title, body, item_aside, refs))
            item_count += 1
            if item_count >= 9:
                break
        if items:
            sections.append(MemoryPortraitSection(kind, title, tuple(items)))
        if item_count >= 9:
            break
    if not sections:
        return None
    return MemoryPortrait(
        opening=opening,
        aside=aside,
        sections=tuple(sections),
        renderer="deepseek_surface",
        fingerprint=fingerprint,
    )


def _default_section_title(kind: str) -> str:
    return {
        "how_i_see_you": "Trong mắt tôi",
        "things_you_like": "Mấy thứ bạn thích",
        "our_memories": "Chuyện của hai đứa",
        "not_sure": "Có vài chuyện tôi chưa dám chắc",
    }.get(kind, "Những chuyện tôi nhớ")


def _fingerprint(evidence: Sequence[Mapping[str, Any]], lens: Mapping[str, Any]) -> str:
    raw = json.dumps({"evidence": evidence, "lens": _json_safe(lens)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:
        number = float(default)
    return max(0.0, min(1.0, number))


def _extract_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, Mapping):
        return ""
    direct = response.get("output_text") or response.get("text") or response.get("content")
    if isinstance(direct, str):
        return direct
    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    return str(message.get("content") or "")
    return ""


def _parse_json(text: str) -> Mapping[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        value = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except Exception:
            return None
    return value if isinstance(value, Mapping) else None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "MemoryPortrait",
    "MemoryPortraitItem",
    "MemoryPortraitPresenter",
    "MemoryPortraitSection",
]
