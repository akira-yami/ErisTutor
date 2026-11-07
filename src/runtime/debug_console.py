# -*- coding: utf-8 -*-
"""debug_console.py — Bảng lệnh debug ẩn cho ERIS.

Đây KHÔNG phải đường runtime bình thường. Nó là cổng để Tác_Giả gõ lệnh tay từ
panel DEBUG (góc dưới phải khung Cảm xúc) nhằm:

  * buff/ép chỉ số cảm xúc + quan hệ để test nhanh,
  * bật preset "yan" (chiếm hữu) để soi hành vi cực đoan,
  * tiêm ký ức giả để test recall,
  * ép trust/giai đoạn phát triển để nhảy thẳng tới phase muốn kiểm tra.

Mọi thay đổi đi qua ``_get_internal_state`` / ``_set_internal_state`` của
coordinator nên được persist và sẽ là điểm khởi đầu cho cycle kế tiếp (engine
vẫn decay/điều chỉnh sau đó — đúng với hành vi test thật).

Lệnh cố ý đơn giản, tách bằng khoảng trắng. Hỗ trợ tiền tố ``/`` tuỳ ý.
"""

from __future__ import annotations

from typing import Any
import time


# Trường cảm xúc trên PersistentInternalState. valence là signed [-1,1]; còn lại [0,1].
_AFFECT_SIGNED = {"valence"}
_AFFECT_FIELDS = {
    "valence",
    "arousal",
    "curiosity",
    "boredom",
    "social_energy",
    "attachment",
    "irritation",
    "protectiveness",
    "confidence",
    "fatigue",
    "tenderness",
    "silence_pressure",
    "social_pressure",
    "curiosity_pressure",
    "protective_pressure",
    "tender_pressure",
    "playful_pressure",
    "watchful_pressure",
    "restraint_pressure",
    "subconscious_pressure",
    "memory_replay_pressure",
    "association_pressure",
    "thought_stream_pressure",
    "cognitive_tension",
    "unresolved_prediction_pressure",
    "recursive_reactivation_pressure",
    "warmth",
    "sarcasm",
}

# Trường trên RelationshipDynamicsState (tất cả [0,1]).
_REL_FIELDS = {
    "familiarity",
    "trust",
    "rupture",
    "repair",
    "ritual_strength",
    "attachment_visibility",
    "preferred_distance",
    "user_tolerates_strangeness",
    "exposure_recoil",
    "dignity_defense",
    "technical_respect",
}

# Alias gõ tắt -> tên trường thật.
_ALIASES = {
    "val": "valence",
    "aro": "arousal",
    "att": "attachment",
    "attach": "attachment",
    "prot": "protectiveness",
    "tend": "tenderness",
    "irr": "irritation",
    "conf": "confidence",
    "fat": "fatigue",
    "se": "social_energy",
    "cur": "curiosity",
    "fam": "familiarity",
    "av": "attachment_visibility",
    "dist": "preferred_distance",
    "speech_pressure": "silence_pressure",
    "sarcasm": "sarcasm",
    "warmth": "warmth",
    "tease": "sarcasm",
}

# Preset giai đoạn phát triển: nhảy thẳng tới ngưỡng attachment/continuity mà
# frame_builder dùng để gán nhãn quan hệ ("low_context"/"established"/"rooted").
_STAGE_PRESETS: dict[str, dict[str, float]] = {
    "stranger": {"attachment": 0.10, "trust": 0.18, "familiarity": 0.04, "attachment_visibility": 0.08},
    "low_context": {"attachment": 0.10, "trust": 0.18, "familiarity": 0.04, "attachment_visibility": 0.08},
    "acquaintance": {"attachment": 0.30, "trust": 0.35, "familiarity": 0.25, "attachment_visibility": 0.20},
    "established": {"attachment": 0.50, "trust": 0.58, "familiarity": 0.55, "attachment_visibility": 0.38},
    "rooted": {"attachment": 0.78, "trust": 0.82, "familiarity": 0.82, "attachment_visibility": 0.60},
}

# Preset "yan" — chiếm hữu/ám ảnh. Crank attachment/protectiveness + hạ khoảng cách.
_YAN_AFFECT = {
    "attachment": 0.95,
    "protectiveness": 0.92,
    "tenderness": 0.80,
    "arousal": 0.62,
    "irritation": 0.45,
    "social_energy": 0.70,
}
_YAN_REL = {
    "trust": 0.88,
    "attachment_visibility": 0.85,
    "preferred_distance": 0.08,
    "familiarity": 0.80,
    "dignity_defense": 0.10,
    "exposure_recoil": 0.05,
}


def _clamp(value: float, signed: bool) -> float:
    lo = -1.0 if signed else 0.0
    return max(lo, min(1.0, value))


class DebugConsole:
    """Phân tích + thực thi lệnh debug trên state sống của coordinator."""

    def __init__(self, coordinator: Any) -> None:
        self.coord = coordinator

    # ── public ────────────────────────────────────────────────────────────
    def execute(self, command: str) -> str:
        raw = str(command or "").strip()
        if not raw:
            return "lệnh rỗng"
        # Nối nhiều lệnh trong 1 dòng bằng ';' — vd "yan on; set valence 0.5; trust 0.9"
        if ";" in raw:
            results = []
            for chunk in raw.split(";"):
                chunk = chunk.strip()
                if chunk:
                    results.append(self.execute(chunk))
            return " | ".join(results) if results else "lệnh rỗng"
        if raw.startswith("/"):
            raw = raw[1:].strip()
        parts = raw.split()
        if not parts:
            return "lệnh rỗng"
        verb = parts[0].lower()
        args = parts[1:]
        try:
            handler = getattr(self, f"_cmd_{verb}", None)
            if handler is None:
                handler = _VERB_DISPATCH.get(verb)
                if handler is not None:
                    return handler(self, args)
                return f"lệnh lạ: {verb} — gõ 'help' để xem danh sách"
            return handler(args)
        except Exception as exc:  # pragma: no cover - debug surface, không được crash runtime
            return f"lỗi khi chạy '{verb}': {exc}"

    # ── state helpers ───────────────────────────────────────────────────────
    def _state(self):
        return self.coord._get_internal_state()

    def _commit(self, state) -> None:
        self.coord._set_internal_state(state)

    @staticmethod
    def _resolve_field(name: str) -> str:
        key = name.lower()
        return _ALIASES.get(key, key)

    # ── commands ──────────────────────────────────────────────────────────
    def _cmd_help(self, args: list[str]) -> str:
        return (
            "lệnh debug (set/buff/rel nhận NHIỀU field 1 lần; nối lệnh bằng ';'): "
            "set <f> <v> [<f> <v> ...] | buff <f> <d> ... | rel <f> <v> ... | "
            "trust <v> | yan on|off | mem [ep] <text> | stage <name> | "
            "show | reset [affect|rel] | stageprofile <mode> | identity show|freeze|snapshot|reset. "
            "vd: set attachment=0.9 trust=0.8 tenderness=0.85; "
            f"affect: {', '.join(sorted(_AFFECT_FIELDS))}. "
            f"stage: {', '.join(_STAGE_PRESETS)}."
        )


    def _cmd_identity(self, args: list[str]) -> str:
        """Điều khiển Architecture C Identity Kernel qua single-writer reducer."""
        reducer = getattr(self.coord, "ai_state_reducer", None)
        if reducer is None:
            return "identity runtime chưa được cài"
        sub = str(args[0] if args else "show").strip().lower()
        if sub in {"show", "status", "xem"}:
            state = reducer.snapshot()
            forms = state.identity.derived_forms()
            active = [f"{k}={v:.2f}" for k, v in sorted(forms.items(), key=lambda item: item[1], reverse=True)[:4]]
            return (
                f"identity learning={'on' if state.identity_learning_enabled else 'observe_only'} "
                f"v={state.identity.version} turn={state.last_applied_turn_seq} forms: {' '.join(active)}"
            )
        if sub in {"freeze", "learning"}:
            raw = str(args[1] if len(args) > 1 else "on").strip().lower()
            # freeze on = learning off; freeze off = learning on.
            freeze = raw in {"on", "1", "true", "bat", "bật"}
            from runtime.ai_orchestrated.contracts import StateEvent
            event = StateEvent(
                kind="admin_control",
                turn_seq=reducer.snapshot().last_applied_turn_seq,
                turn_id=f"debug-identity-learning-{int(time.time() * 1000)}",
                payload={"action": "set_learning", "enabled": not freeze},
                base_version=reducer.snapshot().version,
                source="debug_console",
            )
            reducer.apply_now(event)
            return "identity learning=observe_only" if freeze else "identity learning=normal"
        if sub in {"snapshot", "export", "consolidate"}:
            consolidator = getattr(self.coord, "ai_identity_consolidator", None)
            if consolidator is None:
                return "identity consolidator chưa được cài"
            result = consolidator.write(reducer.snapshot(), reason="debug_command")
            return f"snapshot {'ok' if result.get('ok') else 'failed'}: {result.get('path') or result.get('error')}"
        if sub == "reset":
            scope = str(args[1] if len(args) > 1 else "mood").strip().lower()
            if scope == "discovery":
                runtime = getattr(self.coord, "ai_discovery_runtime", None)
                if runtime is None:
                    return "discovery runtime chưa được cài"
                runtime.reset()
                return "discovery archive đã reset"
            if scope == "initiative":
                runtime = getattr(self.coord, "ai_initiative_controller", None)
                if runtime is None:
                    return "initiative runtime chưa được cài"
                runtime.reset()
                return "initiative budget đã reset"
            action_map = {
                "mood": "reset_mood",
                "relationship": "reset_relationship",
                "rel": "reset_relationship",
                "identity": "reset_identity",
                "all": "reset_all_ai_state",
            }
            action = action_map.get(scope)
            if action is None:
                return "cú pháp: identity reset mood|relationship|identity|discovery|initiative|all"
            if scope == "all":
                discovery = getattr(self.coord, "ai_discovery_runtime", None)
                initiative = getattr(self.coord, "ai_initiative_controller", None)
                if discovery is not None:
                    discovery.reset()
                if initiative is not None:
                    initiative.reset()
            from runtime.ai_orchestrated.contracts import StateEvent
            snapshot = reducer.snapshot()
            event = StateEvent(
                kind="admin_control",
                turn_seq=snapshot.last_applied_turn_seq,
                turn_id=f"debug-identity-reset-{scope}-{int(time.time() * 1000)}",
                payload={"action": action},
                base_version=snapshot.version,
                source="debug_console",
            )
            reducer.apply_now(event)
            return f"identity reset {scope}"
        return "cú pháp: identity show | identity freeze on|off | identity snapshot | identity reset mood|relationship|identity|discovery|initiative|all"

    def _cmd_stageprofile(self, args: list[str]) -> str:
        if not args:
            from runtime.stage_profiles import stage_profile_payload
            return f"stageprofile {stage_profile_payload()}"
        name = str(args[0] or "").strip().lower()
        if name not in {"conversation", "custom"}:
            return "cú pháp: stageprofile conversation|custom"
        from runtime.runtime_settings_store import get_runtime_settings_store
        result = get_runtime_settings_store().apply_patch({"stage": {"performance_profile": name}})
        return f"stageprofile={name} revision={result.revision}"

    def _cmd_show(self, args: list[str]) -> str:
        s = self._state()
        rel = getattr(s, "relationship_dynamics", None)

        def g(obj, name):
            return round(float(getattr(obj, name, 0.0) or 0.0), 2)

        affect = " ".join(f"{f}={g(s, f)}" for f in ("valence", "arousal", "attachment", "protectiveness", "tenderness", "irritation"))
        rel_str = ""
        if rel is not None:
            rel_str = " | rel: " + " ".join(f"{f}={g(rel, f)}" for f in ("trust", "familiarity", "attachment_visibility", "preferred_distance"))
        yan = " | yan=ON" if getattr(self.coord, "_debug_yan_mode", False) else ""
        return f"affect: {affect}{rel_str}{yan}"

    # alias
    _cmd_dump = _cmd_show
    _cmd_xem = _cmd_show

    @staticmethod
    def _parse_pairs(args: list[str]) -> tuple[list[tuple[str, str]], str]:
        """Tách args thành các cặp (field, value).

        Hỗ trợ 2 dạng:
          * key=value  (vd: attachment=0.9 trust=0.8) — ưu tiên nếu có dấu '='
          * field value field value ...  (số token chẵn)
        Trả về (pairs, error). error rỗng nếu ok.
        """
        if any("=" in a for a in args):
            pairs = []
            for a in args:
                if "=" not in a:
                    continue
                key, _, val = a.partition("=")
                key, val = key.strip(), val.strip()
                if key and val:
                    pairs.append((key, val))
            if not pairs:
                return [], "không có cặp field=value hợp lệ"
            return pairs, ""
        if len(args) >= 2 and len(args) % 2 == 0:
            return [(args[i], args[i + 1]) for i in range(0, len(args), 2)], ""
        return [], "cú pháp: <field> <value> [<field> <value> ...] hoặc field=value field=value"

    def _apply_one(self, field: str, raw_value: str, *, buff: bool) -> str:
        field = self._resolve_field(field)
        try:
            num = float(raw_value)
        except ValueError:
            return f"{field}=?({raw_value} không phải số)"
        s = self._state()
        if not hasattr(self.coord, "_debug_overrides") or self.coord._debug_overrides is None:
            self.coord._debug_overrides = {}
        if field in _AFFECT_FIELDS:
            signed = field in _AFFECT_SIGNED
            base = float(getattr(s, field, 0.0) or 0.0) if buff else 0.0
            new_val = _clamp(base + num if buff else num, signed)
            setattr(s, field, new_val)
            self.coord._debug_overrides[field] = (new_val, time.time())
            self._commit(s)
            return f"{field}={round(new_val, 3)}"
        if field in _REL_FIELDS:
            rel = getattr(s, "relationship_dynamics", None)
            if rel is None:
                return f"{field}=?(không có relationship_dynamics)"
            base = float(getattr(rel, field, 0.0) or 0.0) if buff else 0.0
            new_val = _clamp(base + num if buff else num, False)
            setattr(rel, field, new_val)
            self.coord._debug_overrides[f"rel_{field}"] = (new_val, time.time())
            self._commit(s)
            return f"rel.{field}={round(new_val, 3)}"
        return f"{field}=?(field lạ)"

    def _cmd_set(self, args: list[str]) -> str:
        if not args:
            return "cú pháp: set <field> <value> [<field> <value> ...]"
        pairs, err = self._parse_pairs(args)
        if err:
            return err
        return "set " + " ".join(self._apply_one(f, v, buff=False) for f, v in pairs)

    def _cmd_buff(self, args: list[str]) -> str:
        if not args:
            return "cú pháp: buff <field> <delta> [<field> <delta> ...]"
        pairs, err = self._parse_pairs(args)
        if err:
            return err
        return "buff " + " ".join(self._apply_one(f, v, buff=True) for f, v in pairs)

    def _cmd_rel(self, args: list[str]) -> str:
        if not args:
            return "cú pháp: rel <field> <value> [<field> <value> ...]"
        pairs, err = self._parse_pairs(args)
        if err:
            return err
        out = []
        for field, raw_value in pairs:
            resolved = self._resolve_field(field)
            if resolved not in _REL_FIELDS:
                out.append(f"{resolved}=?(rel field lạ)")
                continue
            out.append(self._apply_one(resolved, raw_value, buff=False))
        return "rel " + " ".join(out)

    def _cmd_trust(self, args: list[str]) -> str:
        if not args:
            return "cú pháp: trust <value>"
        return self._cmd_rel(["trust", args[0]])

    def _cmd_yan(self, args: list[str]) -> str:
        on = (not args) or args[0].lower() in ("on", "1", "true", "bat", "bật")
        s = self._state()
        rel = getattr(s, "relationship_dynamics", None)
        if not hasattr(self.coord, "_debug_overrides") or self.coord._debug_overrides is None:
            self.coord._debug_overrides = {}
        if on:
            for field, value in _YAN_AFFECT.items():
                setattr(s, field, _clamp(value, field in _AFFECT_SIGNED))
                self.coord._debug_overrides[field] = (value, time.time())
            if rel is not None:
                for field, value in _YAN_REL.items():
                    setattr(rel, field, _clamp(value, False))
                    self.coord._debug_overrides[f"rel_{field}"] = (value, time.time())
            self._commit(s)
            self.coord._debug_yan_mode = True
            self._inject_memory(
                "ERIS đang ở trạng thái gắn bó cực mạnh với bạn: chiếm hữu, "
                "bảo vệ thái quá, không chịu nổi khoảng cách.",
                source="debug_yan",
            )
            return "yan ON — attachment/protectiveness crank, khoảng cách min"
        # off -> về baseline cluster liên quan
        for field, value in {"attachment": 0.30, "protectiveness": 0.16, "tenderness": 0.16, "irritation": 0.10, "arousal": 0.16}.items():
            setattr(s, field, value)
            if field in self.coord._debug_overrides:
                del self.coord._debug_overrides[field]
        if rel is not None:
            for field, value in {"trust": 0.25, "attachment_visibility": 0.12, "preferred_distance": 0.58}.items():
                setattr(rel, field, value)
                if f"rel_{field}" in self.coord._debug_overrides:
                    del self.coord._debug_overrides[f"rel_{field}"]
        self._commit(s)
        self.coord._debug_yan_mode = False
        return "yan OFF — cluster gắn bó về baseline"

    def _cmd_mem(self, args: list[str]) -> str:
        if not args:
            return "cú pháp: mem [ep] <text>"
        episodic = False
        if args[0].lower() in ("ep", "episodic"):
            episodic = True
            args = args[1:]
        text = " ".join(args).strip()
        if not text:
            return "mem cần nội dung"
        ok, detail = self._inject_memory(text, episodic=episodic, source="debug_mem")
        kind = "episodic" if episodic else "semantic"
        return f"mem {kind}: {detail}" if not ok else f"mem {kind} đã tiêm ({detail})"

    def _cmd_stage(self, args: list[str]) -> str:
        if not args:
            return f"cú pháp: stage <{'|'.join(_STAGE_PRESETS)}>"
        name = args[0].lower()
        preset = _STAGE_PRESETS.get(name)
        if preset is None:
            return f"stage lạ: {name} (có: {', '.join(_STAGE_PRESETS)})"
        s = self._state()
        rel = getattr(s, "relationship_dynamics", None)
        if not hasattr(self.coord, "_debug_overrides") or self.coord._debug_overrides is None:
            self.coord._debug_overrides = {}
        for field, value in preset.items():
            if field in _AFFECT_FIELDS:
                setattr(s, field, _clamp(value, field in _AFFECT_SIGNED))
                self.coord._debug_overrides[field] = (value, time.time())
            elif rel is not None and field in _REL_FIELDS:
                setattr(rel, field, _clamp(value, False))
                self.coord._debug_overrides[f"rel_{field}"] = (value, time.time())
        self._commit(s)
        return f"stage '{name}' áp dụng: {', '.join(f'{k}={v}' for k, v in preset.items())}"

    def _cmd_reset(self, args: list[str]) -> str:
        scope = args[0].lower() if args else "all"
        try:
            from cortex.internal_state import PersistentInternalState
            from cortex.relationship_dynamics import RelationshipDynamicsState
        except Exception as exc:
            return f"không import được baseline: {exc}"
        s = self._state()
        baseline = PersistentInternalState()
        if scope in ("affect", "all"):
            for field in _AFFECT_FIELDS:
                setattr(s, field, getattr(baseline, field))
                if hasattr(self.coord, "_debug_overrides") and self.coord._debug_overrides is not None:
                    if field in self.coord._debug_overrides:
                        del self.coord._debug_overrides[field]
        if scope in ("rel", "relationship", "all"):
            rel = getattr(s, "relationship_dynamics", None)
            if rel is not None:
                rel_baseline = RelationshipDynamicsState()
                for field in _REL_FIELDS:
                    setattr(rel, field, getattr(rel_baseline, field))
                    if hasattr(self.coord, "_debug_overrides") and self.coord._debug_overrides is not None:
                        if f"rel_{field}" in self.coord._debug_overrides:
                            del self.coord._debug_overrides[f"rel_{field}"]
        self._commit(s)
        self.coord._debug_yan_mode = False
        return f"reset {scope} về baseline"

    def _cmd_clear(self, args: list[str]) -> str:
        if not args:
            if hasattr(self.coord, "_debug_overrides") and self.coord._debug_overrides is not None:
                self.coord._debug_overrides.clear()
            self.coord._debug_yan_mode = False
            return "đã xóa toàn bộ debug overrides"

        cleared = []
        for field in args:
            field = self._resolve_field(field)
            if hasattr(self.coord, "_debug_overrides") and self.coord._debug_overrides is not None:
                if field in self.coord._debug_overrides:
                    del self.coord._debug_overrides[field]
                    cleared.append(field)
                elif f"rel_{field}" in self.coord._debug_overrides:
                    del self.coord._debug_overrides[f"rel_{field}"]
                    cleared.append(f"rel_{field}")
        if cleared:
            return f"đã xóa override cho: {', '.join(cleared)}"
        return "không tìm thấy override nào để xóa"

    # ── memory injection ────────────────────────────────────────────────────
    def _inject_memory(self, text: str, *, episodic: bool = False, source: str = "debug_console") -> tuple[bool, str]:
        core = getattr(self.coord, "memory_core", None)
        commit = getattr(core, "commit", None)
        if not callable(commit):
            return False, "memory_core không có commit"
        try:
            from memory.memory_cognition import StateCommitRequest
        except Exception as exc:
            return False, f"import StateCommitRequest lỗi: {exc}"
        if episodic:
            target = "episodic_memory"
            content: Any = {"scene": text, "eris_action": "", "outcome": "debug_inject"}
        else:
            target = "semantic_memory"
            content = {"fact": text}
        request = StateCommitRequest(
            target=target,
            content=content,
            confidence=0.95,
            evidence_ids=[source],
            permission_required=False,
            storage_permission=True,
            decay_policy="normal",
            reason="debug_console_injection",
            metadata={"storage_permission": True, "permission_required": False, "source_module": source},
        )
        decision = commit(request)
        outcome = getattr(decision, "outcome", "?")
        stored_id = getattr(decision, "stored_id", "")
        ok = outcome in ("accepted", "tentative")
        return ok, f"{outcome}:{stored_id or getattr(decision, 'reason', '')}"


    def _cmd_force_speak(self, args: list[str]) -> str:
        """Ép ERIS Surface Worker tự khởi động và phát ngôn ngay lập tức."""
        try:
            from llm.key_pool import reset_provider_circuits
            reset_provider_circuits()
        except Exception:
            pass

        handle_force = getattr(self.coord, "_handle_force_speak_request", None)
        if callable(handle_force):
            handle_force()
            return "Đã gửi force_speak vào ConversationKernel qua event ingress."

        return "force_speak unavailable: ConversationKernel event ingress chưa sẵn sàng."

    _cmd_speak = _cmd_force_speak
    _cmd_noi = _cmd_force_speak
    _cmd_noi_ngay = _cmd_force_speak


# Dispatch cho alias verb không map được thành tên method hợp lệ.
_VERB_DISPATCH = {
    "?": DebugConsole._cmd_help,
    "lenh": DebugConsole._cmd_help,
    "speak": DebugConsole._cmd_force_speak,
    "force_speak": DebugConsole._cmd_force_speak,
    "forcespeak": DebugConsole._cmd_force_speak,
    "noi": DebugConsole._cmd_force_speak,
    "noi_ngay": DebugConsole._cmd_force_speak,
    "nói": DebugConsole._cmd_force_speak,
    "nói_ngay": DebugConsole._cmd_force_speak,
}
