# -*- coding: utf-8 -*-
"""Task 3: manifest không được biến mất, cắt, hoặc tự cấp quyền evidence."""
from __future__ import annotations

import pytest

from runtime.claim_domain_contract import (
    inspect_claim_manifest, normalize_claim_manifest, validate_claim_manifest,
)
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import KernelTurn


def _item(domain="opinion", claim="Tôi thích sự tinh tế", refs=()):
    return {"domain": domain, "claim": claim, "evidence_refs": list(refs)}


def test_missing_manifest_survives_router_compatibility():
    router = AIRuntimeModelRouter(llm_service=None, logger=None)
    result = router._compat_public_envelope({"text": "Tôi từng đi du lịch", "speech_text":"旅行した"})
    assert result["claim_manifest"] is None
    assert result["_typed_claim_manifest"].structural_status == "missing"
    assert not validate_claim_manifest(result["_typed_claim_manifest"], allowed_domains=["self_history"]).allowed


def test_wrong_type_and_invalid_items_are_rejected():
    for bad in ("bad", True, None, [{"domain":"opinion", "claim":"x", "evidence_refs":"ref"}],
                [False], [_item() | {"claim": ""}],
                [{"domain":"opinion", "claim":"x"}],
                [_item() | {"evidence_refs": [31]}],
                [_item() | {"sneaky": "field"}]):
        e = inspect_claim_manifest({"claim_manifest": bad})
        assert e.present and e.structural_status == "invalid", bad
        verdict = validate_claim_manifest(e, allowed_domains=["opinion"])
        assert not verdict.allowed and verdict.reason == "claim_manifest_invalid"
    assert inspect_claim_manifest({"claim_manifest": None}).structural_status == "invalid"


def test_empty_manifest_is_distinct_from_missing():
    good = inspect_claim_manifest({"claim_manifest": []})
    missing = inspect_claim_manifest({})
    assert good.present and good.structural_status == "valid" and good.normalized == ()
    assert missing.structural_status == "missing" and not missing.present
    assert validate_claim_manifest(good, allowed_domains=[]).allowed
    assert not validate_claim_manifest(missing, allowed_domains=[]).allowed


def test_eleventh_claim_and_ninth_ref_are_reported():
    e = inspect_claim_manifest([_item() for _ in range(11)])
    assert e.structural_status == "oversize" and "claims_exceed_10" in e.violations
    assert len(e.normalized) == 11
    r = inspect_claim_manifest([_item(refs=[f"ref{i}" for i in range(9)])])
    assert r.structural_status == "oversize" and "claim[0]:refs_exceed_8" in r.violations
    assert len(r.normalized[0]["evidence_refs"]) == 9
    assert not validate_claim_manifest(r, allowed_domains=["opinion"]).allowed


def test_refs_checked_against_runtime_allowlist_even_for_opinion():
    claim = [_item("user_statement", "Bạn vừa nói rồi", ["event:e1"])]
    assert validate_claim_manifest(claim, allowed_domains=["user_statement"],
                                   allowed_evidence_refs=["event:e1"]).allowed
    denied = validate_claim_manifest(claim, allowed_domains=["user_statement"],
                                     allowed_evidence_refs=["event:other"])
    assert not denied.allowed and denied.unknown_evidence_refs == ("event:e1",)
    invented = validate_claim_manifest([_item(refs=["invented:1"])],
                                       allowed_domains=["opinion"])
    assert not invented.allowed
    assert validate_claim_manifest([_item()], allowed_domains=["opinion"]).allowed


def test_valid_fresh_preference_needs_no_embodied_history():
    assert validate_claim_manifest([_item("self_preference", "Thích vị đắng")],
                                   allowed_domains=["self_preference"]).allowed
    assert not validate_claim_manifest([_item("self_history", "Đã uống ở Kyoto")],
                                       allowed_domains=["self_history"]).allowed


def test_full_claim_strings_are_not_silently_cut():
    long_claim = "ý" * 480
    long_ref = "ref:" + "a" * 255
    good = inspect_claim_manifest([_item("user_statement", long_claim, [long_ref])])
    assert good.structural_status == "valid"
    assert good.normalized[0]["claim"] == long_claim
    assert good.normalized[0]["evidence_refs"] == (long_ref,)
    assert normalize_claim_manifest([_item("user_statement", long_claim, [long_ref])])[0]["claim"] == long_claim
    with pytest.raises(TypeError):
        good.normalized[0]["claim"] = "tampered"


def test_legacy_runtime_writer_does_not_block_invalid_model_manifest():
    class Router:
        def __init__(self,raw):
            self.model=AIRuntimeModelRouter(llm_service=None,logger=None)
            self.raw=raw
        def generate_public_reply(self,**kw):
            return self.model._compat_public_envelope(self.raw)
    event=ConversationEvent(event_id='e1',session_id='s',kind='user_turn',text='Hi')
    turn=KernelTurn(session_id='s',generation=1,run_id='r1',
                    move=NextConversationMove(intent='answer',say='reply'),note='')
    for manifest in ('wrong type',[_item() for _ in range(11)],None):
        raw={'text':'Xin chào','speech_text':'こんにちは'}
        if manifest is not None:raw['claim_manifest']=manifest
        rendered=RuntimeConversationWriter(type('Host',(),{'ai_model_router':Router(raw)})()).realize(
            turn=turn,event=event,recent_history=(),runtime_context={})
        assert rendered.surface.display_text=='Xin chào'


def test_router_compat_does_not_hide_manifest_value_inside_private_metadata():
    from runtime.json_boundary import public_payload
    router = AIRuntimeModelRouter(llm_service=None, logger=None)
    raw = {"text":"test", "speech_text":"テスト", "claim_manifest": [_item()]}
    result = router._compat_public_envelope(raw)
    assert public_payload(result)["claim_manifest"] == raw["claim_manifest"]
    assert "_typed_claim_manifest" not in public_payload(result)


def test_writer_model_ref_is_not_authority_for_runtime_state():
    from types import SimpleNamespace
    from runtime.conversation.claim_evidence import build_evidence_registry
    event=ConversationEvent(event_id='e2',session_id='s',kind='user_turn',text='Tôi thích trà',generation=1)
    reg=build_evidence_registry(event=event,state=SimpleNamespace(version=10,last_applied_turn_seq=1),
                                affect=None,report=None,ledger_turns=(),memory_records=(),sampled_at=100)
    turn=KernelTurn(session_id='s',generation=1,run_id='r1',
                    move=NextConversationMove(intent='answer',say='reply'),note='')
    class Router:
        def __init__(self,manifest):
            self.m=manifest; self.model=AIRuntimeModelRouter(llm_service=None,logger=None)
        def generate_public_reply(self,**kwargs):
            return self.model._compat_public_envelope({'text':'Trà thơm thật','speech_text':'お茶',
                                                        'claim_manifest':self.m})
    for manifest in ([_item('user_statement','X',['event:fake'])],
                     [_item('shared_episode','Kyoto',['event:e2'])],
                     [_item('self_preference','Tôi thích trà')]):
        host=type('Host',(),{'ai_model_router':Router(manifest)})()
        rendered=RuntimeConversationWriter(host).realize(turn=turn,event=event,recent_history=(),
             runtime_context={'__evidence_registry':reg})
        assert rendered.surface.display_text=='Trà thơm thật'


def test_no_semantic_grounding_rewrite_or_second_writer_call(monkeypatch):
    from types import SimpleNamespace
    class Router:
        def __init__(self):self.i=0;self.model=AIRuntimeModelRouter(llm_service=None,logger=None)
        def generate_public_reply(self,**kwargs):
            self.i+=1
            return self.model._compat_public_envelope({'text':'Xin chào','speech_text':'こんにちは',
                                                       'claim_manifest':'broken'})
    class Verdict:
        allowed=False
        def retry_instruction(self):return 'viết lại'
    import runtime.grounding_surface_gate as gate
    monkeypatch.setattr(gate,'evaluate_grounded_surface',lambda *a,**k:Verdict())
    router=Router()
    event=ConversationEvent(event_id='e1',session_id='s',kind='user_turn',text='Hi')
    turn=KernelTurn(session_id='s',generation=1,run_id='r1',
                    move=NextConversationMove(intent='answer',say='reply'),note='')
    rendered=RuntimeConversationWriter(SimpleNamespace(ai_model_router=router)).realize(
        turn=turn,event=event,recent_history=(),
        runtime_context={'__source_grounding_engine':object(),'__source_grounding_report':object()})
    assert router.i==1 and rendered.surface.display_text=='Xin chào'
