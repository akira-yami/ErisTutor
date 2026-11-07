from __future__ import annotations

"""
memory.predicate_catalog
========================

Nguồn chân lý canonical cho mọi predicate semantic memory.

Một predicate được chia thành bốn phần độc lập:
- OntologyPolicy: claim có nghĩa gì và quan hệ entailment/mâu thuẫn.
- QueryLexicon: người dùng có thể hỏi hoặc tự khai báo bằng cách nào.
- RetentionPolicy: lưu ở đâu, giữ bao lâu và độ quan trọng mặc định.
- SurfacePolicy: cách gọi thuộc tính và cách diễn đạt public.

Các module cũ như predicate_ontology.py và query_intent.py chỉ dựng compatibility
view từ catalog này. Vì vậy thêm một predicate builtin chỉ sửa đúng một nơi. Model
semantic vẫn có thể tạo predicate provisional lúc chạy mà không sửa source.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class FrameRoute:
    """Ánh xạ MeaningFrame sang predicate mà không cần bảng ở ClaimBuilder."""

    frame_type: str
    relation: str
    target_kind: str = ""
    priority: int = 0

    def matches(self, frame_type: str, relation: str, target_kind: str = "") -> bool:
        if self.frame_type and self.frame_type != str(frame_type or ""):
            return False
        if self.relation and self.relation != str(relation or ""):
            return False
        if self.target_kind and self.target_kind != str(target_kind or ""):
            return False
        return True


@dataclass(frozen=True, slots=True)
class OntologyPolicy:
    object_type: str = "unknown"
    modality: str = "fact"
    temporal: str = "unknown"
    scope: str = "self"
    cardinality: str = "multi"  # single | multi
    stability: str = "unknown"
    answer_target: str = ""
    contradicts: tuple[str, ...] = ()
    compatible_with: tuple[str, ...] = ()
    forbidden_for: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryLexicon:
    aliases: tuple[str, ...] = ()
    query_examples: tuple[str, ...] = ()
    anti_cues: tuple[str, ...] = ()
    disclosure_cues: tuple[str, ...] = ()
    base_confidence: float = 0.70
    strict_entailment: bool = True
    requires_freshness: bool = False


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    storage_layer: str = "semantic_memory"
    default_decay: str = "normal"
    retention_class: str = "medium_term"
    importance: float = 0.50
    confidence_floor: float = 0.0
    valid_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SurfacePolicy:
    display_name: str = "thông tin"
    found_template: str = ""
    missing_template: str = ""
    sensitivity: str = "normal"


@dataclass(frozen=True, slots=True)
class PredicateDefinition:
    """Định nghĩa đầy đủ nhưng được cấu thành từ các policy nhỏ."""

    predicate: str
    fact_key: str = ""
    frame_routes: tuple[FrameRoute, ...] = ()
    ontology: OntologyPolicy = field(default_factory=OntologyPolicy)
    query: QueryLexicon = field(default_factory=QueryLexicon)
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    surface: SurfacePolicy = field(default_factory=SurfacePolicy)
    provisional: bool = False
    source: str = "builtin"
    schema_version: int = 1

    # Các property dưới đây giữ tương thích với RegisteredPredicate cũ.
    @property
    def aliases(self) -> tuple[str, ...]:
        return self.query.aliases

    @property
    def query_examples(self) -> tuple[str, ...]:
        return self.query.query_examples

    @property
    def object_type(self) -> str:
        return self.ontology.object_type

    @property
    def modality(self) -> str:
        return self.ontology.modality

    @property
    def temporal(self) -> str:
        return self.ontology.temporal

    @property
    def cardinality(self) -> str:
        return self.ontology.cardinality

    @property
    def display_name(self) -> str:
        return self.surface.display_name

    @property
    def surface_template(self) -> str:
        return self.surface.found_template

    @property
    def missing_template(self) -> str:
        return self.surface.missing_template

    @property
    def sensitivity(self) -> str:
        return self.surface.sensitivity

    def query_phrases(self) -> tuple[str, ...]:
        values = (
            *self.query.aliases,
            *self.query.query_examples,
            self.surface.display_name,
            self.fact_key.replace(".", " "),
            self.predicate.replace("_", " "),
        )
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = str(value or "").strip()
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                out.append(clean)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PredicateDefinition":
        raw = dict(data)
        # Đọc được cả persistence v2 dạng composed và v1 dạng flattened.
        if "ontology" in raw or "query" in raw or "retention" in raw or "surface" in raw:
            return cls(
                predicate=str(raw.get("predicate") or ""),
                fact_key=str(raw.get("fact_key") or ""),
                frame_routes=tuple(FrameRoute(**dict(x)) for x in raw.get("frame_routes") or () if isinstance(x, Mapping)),
                ontology=OntologyPolicy(**dict(raw.get("ontology") or {})),
                query=QueryLexicon(**_tuple_fields(dict(raw.get("query") or {}), {"aliases", "query_examples", "anti_cues", "disclosure_cues"})),
                retention=RetentionPolicy(**dict(raw.get("retention") or {})),
                surface=SurfacePolicy(**dict(raw.get("surface") or {})),
                provisional=bool(raw.get("provisional", False)),
                source=str(raw.get("source") or "runtime"),
                schema_version=int(raw.get("schema_version") or 1),
            )
        return cls(
            predicate=str(raw.get("predicate") or ""),
            fact_key=str(raw.get("fact_key") or ""),
            ontology=OntologyPolicy(
                object_type=str(raw.get("object_type") or "unknown"),
                modality=str(raw.get("modality") or "fact"),
                temporal=str(raw.get("temporal") or "unknown"),
                cardinality="single" if str(raw.get("cardinality")) == "single" else "multi",
            ),
            query=QueryLexicon(
                aliases=tuple(raw.get("aliases") or ()),
                query_examples=tuple(raw.get("query_examples") or ()),
            ),
            surface=SurfacePolicy(
                display_name=str(raw.get("display_name") or "thông tin"),
                found_template=str(raw.get("surface_template") or ""),
                missing_template=str(raw.get("missing_template") or ""),
                sensitivity=str(raw.get("sensitivity") or "normal"),
            ),
            provisional=bool(raw.get("provisional", False)),
            source=str(raw.get("source") or "runtime_v1"),
        )


def _tuple_fields(data: dict[str, Any], names: set[str]) -> dict[str, Any]:
    for name in names:
        if name in data:
            data[name] = tuple(data.get(name) or ())
    return data


def _routes(frame_type: str, *relations: str, target_kind: str = "") -> tuple[FrameRoute, ...]:
    return tuple(FrameRoute(frame_type, relation, target_kind) for relation in relations)


def _definition(
    predicate: str,
    fact_key: str = "",
    *,
    routes: Iterable[FrameRoute] = (),
    object_type: str = "unknown",
    modality: str = "fact",
    temporal: str = "unknown",
    scope: str = "self",
    cardinality: str = "multi",
    stability: str = "unknown",
    answer_target: str = "",
    contradicts: Iterable[str] = (),
    compatible_with: Iterable[str] = (),
    forbidden_for: Iterable[str] = (),
    aliases: Iterable[str] = (),
    query_examples: Iterable[str] = (),
    anti_cues: Iterable[str] = (),
    disclosure_cues: Iterable[str] = (),
    base_confidence: float = 0.70,
    strict_entailment: bool = True,
    requires_freshness: bool = False,
    storage_layer: str = "semantic_memory",
    default_decay: str = "normal",
    retention_class: str = "medium_term",
    importance: float = 0.50,
    confidence_floor: float = 0.0,
    valid_seconds: float | None = None,
    display_name: str = "thông tin",
    found_template: str = "",
    missing_template: str = "",
    sensitivity: str = "normal",
) -> PredicateDefinition:
    return PredicateDefinition(
        predicate=predicate,
        fact_key=fact_key,
        frame_routes=tuple(routes),
        ontology=OntologyPolicy(
            object_type=object_type,
            modality=modality,
            temporal=temporal,
            scope=scope,
            cardinality="single" if cardinality == "single" else "multi",
            stability=stability,
            answer_target=answer_target or fact_key or predicate,
            contradicts=tuple(contradicts),
            compatible_with=tuple(compatible_with),
            forbidden_for=tuple(forbidden_for),
        ),
        query=QueryLexicon(
            aliases=tuple(aliases),
            query_examples=tuple(query_examples),
            anti_cues=tuple(anti_cues),
            disclosure_cues=tuple(disclosure_cues),
            base_confidence=base_confidence,
            strict_entailment=strict_entailment,
            requires_freshness=requires_freshness,
        ),
        retention=RetentionPolicy(
            storage_layer=storage_layer,
            default_decay=default_decay,
            retention_class=retention_class,
            importance=importance,
            confidence_floor=confidence_floor,
            valid_seconds=valid_seconds,
        ),
        surface=SurfacePolicy(
            display_name=display_name,
            found_template=found_template,
            missing_template=missing_template,
            sensitivity=sensitivity,
        ),
    )


# Catalog builtin duy nhất. Thêm loại memory mới tại đây, hoặc để model đăng ký
# provisional lúc chạy. Không cần chạm ClaimBuilder, query_intent hay ontology.
BUILTIN_PREDICATES: tuple[PredicateDefinition, ...] = (
    _definition(
        "name_is", "user.name",
        routes=_routes("identity", "name", "name_is"),
        object_type="person_name", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_name", contradicts=("name_is",),
        aliases=("tên", "ten", "name", "preferred name"),
        query_examples=("tôi tên gì", "nhớ tên tôi không", "bạn nhớ tôi tên gì không"),
        disclosure_cues=("tên", "ten", "gọi tôi", "goi toi"), base_confidence=0.82,
        default_decay="slow", retention_class="long_term", importance=0.96, confidence_floor=0.80,
        display_name="tên", found_template="Bạn tên {value} mà.",
        missing_template="Tôi chưa nhớ bạn từng nói tên mình.", sensitivity="personal",
    ),
    _definition(
        "nickname_is", "user.nickname",
        routes=_routes("identity", "nickname", "nickname_is"),
        object_type="person_name", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_nickname", contradicts=("nickname_is",),
        aliases=("biệt danh", "biet danh", "nickname", "biệt hiệu", "biet hieu", "tên gọi", "ten goi"),
        query_examples=("biệt danh của tôi là gì", "nhớ biệt danh tôi không"),
        disclosure_cues=("biệt danh", "biet danh", "tên gọi", "ten goi"), base_confidence=0.82,
        default_decay="slow", retention_class="long_term", importance=0.94, confidence_floor=0.78,
        display_name="biệt danh", found_template="Bạn từng bảo tôi gọi bạn là {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói biệt danh của mình.", sensitivity="personal",
    ),
    _definition(
        "gender_identity_is", "user.gender_identity",
        routes=_routes("identity", "gender", "gender_identity", "gender_identity_is"),
        object_type="gender_identity", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_gender_identity", contradicts=("gender_identity_is",),
        aliases=("giới tính", "gioi tinh", "gender", "gender identity", "nam hay nữ", "nam hay nu"),
        query_examples=("tôi giới tính gì", "nhớ tôi giới tính gì không", "tôi là nam hay nữ", "bạn nhớ giới tính của tôi không"),
        disclosure_cues=("giới tính", "gioi tinh", "nam", "nữ", "nu"), base_confidence=0.82,
        default_decay="slow", retention_class="long_term", importance=0.86, confidence_floor=0.76,
        display_name="giới tính", found_template="Bạn từng nói giới tính của mình là {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói rõ giới tính của mình.", sensitivity="sensitive",
    ),
    _definition(
        "birth_month_is", "user.birth_month",
        routes=_routes("identity", "birth_month", "birth_month_is"),
        object_type="month", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_birth_month", contradicts=("birth_month_is",),
        aliases=("tháng sinh", "thang sinh", "birth month", "birthday"),
        query_examples=("tôi sinh tháng mấy", "nhớ tháng sinh của tôi không"),
        disclosure_cues=("sinh tháng", "sinh thang", "tháng sinh", "thang sinh"), base_confidence=0.82,
        default_decay="slow", retention_class="long_term", importance=0.88, confidence_floor=0.78,
        display_name="tháng sinh", found_template="Bạn sinh tháng {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói tháng sinh của mình.", sensitivity="personal",
    ),
    _definition(
        "birth_year_is", "user.birth_year",
        routes=_routes("identity", "birth_year", "birth_year_is"),
        object_type="year", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_birth_year", contradicts=("birth_year_is",),
        aliases=("năm sinh", "nam sinh", "birth year", "2k"),
        query_examples=("tôi sinh năm nào", "nhớ năm sinh của tôi không"),
        disclosure_cues=("sinh năm", "sinh nam", "năm sinh", "nam sinh"), base_confidence=0.80,
        default_decay="slow", retention_class="long_term", importance=0.90, confidence_floor=0.78,
        display_name="năm sinh", found_template="Bạn sinh năm {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói năm sinh của mình.", sensitivity="personal",
    ),
    _definition(
        "age_observed_is", "user.age_observed",
        routes=_routes("identity", "age", "age_observed_is"),
        object_type="age", modality="observation", temporal="current", cardinality="single", stability="medium",
        answer_target="user_age_observed", contradicts=("age_observed_is",),
        aliases=("tuổi", "tuoi", "age"), query_examples=("tôi bao nhiêu tuổi", "nhớ tuổi tôi không"),
        disclosure_cues=("tuổi", "tuoi"), base_confidence=0.80,
        default_decay="fast", retention_class="short_term", importance=0.62, valid_seconds=86400 * 370,
        display_name="tuổi", found_template="Lần gần nhất bạn nói mình {value} tuổi.",
        missing_template="Tôi chưa nhớ bạn từng nói tuổi của mình.", sensitivity="personal",
    ),
    _definition(
        "current_location_is", "user.current_location",
        routes=_routes("location", "current_location", "current_location_is"),
        object_type="location", modality="observation", temporal="current", cardinality="single", stability="transient",
        answer_target="user_current_location", contradicts=("current_location_is",),
        forbidden_for=("user_living_location", "user_hometown", "user_location_preference"),
        aliases=("đang ở", "dang o", "current location", "hiện ở", "hien o"),
        query_examples=("hiện tôi đang ở đâu", "nhớ tôi đang ở đâu không"),
        anti_cues=("quê", "que", "thích", "thich"), disclosure_cues=("đang ở", "dang o", "hiện ở", "hien o"),
        base_confidence=0.82, requires_freshness=True,
        default_decay="fast", retention_class="short_term", importance=0.68, valid_seconds=3600,
        display_name="vị trí hiện tại", found_template="Lần gần nhất bạn nói mình đang ở {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói mình đang ở đâu.", sensitivity="sensitive",
    ),
    _definition(
        "living_location_is", "user.living_location",
        routes=_routes("location", "living_location", "living_location_is"),
        object_type="location", temporal="current", cardinality="single", stability="medium",
        answer_target="user_living_location", contradicts=("living_location_is",),
        forbidden_for=("user_current_location", "user_hometown", "user_location_preference"),
        aliases=("sống ở", "song o", "nơi ở", "noi o", "living location", "residence"),
        query_examples=("tôi đang sống ở đâu", "nhớ nơi tôi sống không"),
        anti_cues=("quê", "que", "thích", "thich"), disclosure_cues=("sống ở", "song o", "nơi ở", "noi o"),
        base_confidence=0.80,
        default_decay="normal", retention_class="medium_term", importance=0.78,
        display_name="nơi ở", found_template="Bạn từng nói mình đang sống ở {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói nơi mình đang sống.", sensitivity="sensitive",
    ),
    _definition(
        "hometown_is", "user.hometown",
        routes=_routes("location", "hometown", "hometown_is"),
        object_type="location", temporal="stable", cardinality="single", stability="stable",
        answer_target="user_hometown", contradicts=("hometown_is",),
        forbidden_for=("user_current_location", "user_living_location", "user_location_preference"),
        aliases=("quê", "que", "hometown", "quê quán", "que quan"),
        query_examples=("quê tôi ở đâu", "nhớ quê tôi không"),
        anti_cues=("đang", "dang", "hiện tại", "hien tai", "sống", "song", "thích", "thich"),
        disclosure_cues=("quê", "que", "quê quán", "que quan"), base_confidence=0.84,
        default_decay="slow", retention_class="long_term", importance=0.86,
        display_name="quê quán", found_template="Quê bạn là {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói quê mình ở đâu.", sensitivity="personal",
    ),
    _definition(
        "preference_location", "user.preference.location",
        routes=(
            *_routes("preference", "preference_location"),
            FrameRoute("preference", "likes", "location", 10),
            FrameRoute("preference", "dislikes", "location", 10),
        ),
        object_type="location", modality="preference", temporal="stable", cardinality="multi", stability="medium",
        answer_target="user_location_preference", contradicts=("preference_location",), compatible_with=("mentioned_location",),
        forbidden_for=("user_current_location", "user_living_location", "user_hometown"),
        aliases=("thích nơi", "thich noi", "thích ở", "thich o", "location preference", "địa điểm"),
        query_examples=("tôi thích nơi nào", "nhớ tôi thích ở đâu không"),
        anti_cues=("quê", "que", "đang", "dang", "sống", "song"), disclosure_cues=("thích ở", "thich o"),
        base_confidence=0.78,
        default_decay="slow", retention_class="long_term", importance=0.74,
        display_name="địa điểm yêu thích", found_template="Bạn từng nói mình thích {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói mình thích địa điểm nào.",
    ),
    _definition(
        "general_preference", "user.preference.general",
        routes=(
            *_routes("preference", "general_preference"),
            FrameRoute("preference", "likes", "", 1),
            FrameRoute("preference", "dislikes", "", 1),
        ),
        object_type="unknown", modality="preference", temporal="stable", cardinality="multi", stability="medium",
        answer_target="user_general_preference",
        aliases=("sở thích", "so thich", "preference", "thích", "thich", "không thích", "khong thich"),
        query_examples=("tôi thích gì", "nhớ sở thích của tôi không"),
        anti_cues=("địa điểm", "dia diem", "ở đâu", "o dau", "quê", "que"),
        disclosure_cues=("thích", "thich", "không thích", "khong thich"), base_confidence=0.66,
        default_decay="normal", retention_class="medium_term", importance=0.62,
        display_name="sở thích", found_template="Bạn từng nói mình thích {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói rõ sở thích đó.",
    ),
    _definition(
        "study_field_is", "user.study_field",
        routes=_routes("project", "study_field", "study_field_is"),
        object_type="study_field", temporal="current", cardinality="single", stability="medium",
        answer_target="user_study_field", contradicts=("study_field_is",),
        aliases=("ngành học", "nganh hoc", "học IT", "hoc IT", "study field", "major"),
        query_examples=("tôi học ngành gì", "nhớ tôi đang học gì không"),
        disclosure_cues=("học ngành", "hoc nganh", "đang học", "dang hoc"), base_confidence=0.76,
        default_decay="normal", retention_class="medium_term", importance=0.74, confidence_floor=0.74,
        display_name="ngành học", found_template="Bạn từng nói mình học {value}.",
        missing_template="Tôi chưa nhớ bạn từng nói mình học ngành gì.",
    ),
    _definition(
        "response_style_prefers", "user.response_style",
        routes=(
            *_routes("instruction", "response_style", "response_style_prefers"),
            *_routes("feedback", "tone_feedback", "response_style_prefers"),
        ),
        object_type="style_rule", modality="preference", temporal="stable", cardinality="single", stability="medium",
        answer_target="user_response_style", contradicts=("response_style_prefers",),
        aliases=("cách trả lời", "cach tra loi", "response style", "chú thích", "chu thich", "comment", "tone"),
        query_examples=("tôi thích bạn trả lời thế nào", "nhớ cách tôi muốn bạn trả lời không"),
        disclosure_cues=("trả lời", "tra loi", "chú thích", "chu thich"), base_confidence=0.78,
        default_decay="slow", retention_class="long_term", importance=0.82,
        display_name="cách trả lời mong muốn", found_template="Bạn muốn tôi trả lời theo kiểu: {value}.",
        missing_template="Tôi chưa nhớ bạn từng dặn cách muốn tôi trả lời.",
    ),
    _definition(
        "mentioned_location", "user.mentioned_location",
        routes=_routes("location", "mentioned_location"),
        object_type="location", modality="observation", temporal="session", stability="session",
        answer_target="mentioned_location", compatible_with=("preference_location", "current_location_is", "living_location_is", "hometown_is"),
        forbidden_for=("user_current_location", "user_living_location", "user_hometown", "user_location_preference"),
        aliases=("nhắc", "nhac", "mentioned location"), base_confidence=0.68, strict_entailment=False,
        storage_layer="memory_candidate", default_decay="session_only", retention_class="session", importance=0.30,
        display_name="địa điểm đã nhắc",
    ),
    _definition(
        "asked_about", "",
        routes=_routes("session_event", "asked_about"),
        object_type="topic", modality="observation", temporal="session", stability="session",
        answer_target="session_question", storage_layer="session_turn_ledger", default_decay="session_only",
        retention_class="session", importance=0.20, display_name="chủ đề đã hỏi",
    ),
    _definition(
        "greeted", "",
        routes=_routes("session_event", "greeting", "greeted"),
        object_type="utterance", modality="observation", temporal="session", stability="session",
        answer_target="session_greeting", storage_layer="session_turn_ledger", default_decay="session_only",
        retention_class="transient", importance=0.10, display_name="lời chào",
    ),
    _definition(
        "remember_unresolved", "memory_candidate.unresolved",
        routes=_routes("memory_request", "remember_unresolved"),
        object_type="text", modality="memory", temporal="stable", stability="medium",
        answer_target="memory_candidate", aliases=("nhớ nhưng chưa rõ", "unresolved memory", "candidate"),
        storage_layer="memory_candidate", default_decay="candidate", retention_class="candidate", importance=0.68,
        display_name="ký ức chưa phân loại",
    ),
)


BUILTIN_BY_PREDICATE: dict[str, PredicateDefinition] = {item.predicate: item for item in BUILTIN_PREDICATES}
BUILTIN_BY_FACT_KEY: dict[str, PredicateDefinition] = {item.fact_key: item for item in BUILTIN_PREDICATES if item.fact_key}


__all__ = [
    "FrameRoute",
    "OntologyPolicy",
    "QueryLexicon",
    "RetentionPolicy",
    "SurfacePolicy",
    "PredicateDefinition",
    "BUILTIN_PREDICATES",
    "BUILTIN_BY_PREDICATE",
    "BUILTIN_BY_FACT_KEY",
]
