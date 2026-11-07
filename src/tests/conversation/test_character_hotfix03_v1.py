# -*- coding: utf-8 -*-
"""HOTFIX 03 — manifest và EvidenceRegistry phải giao CÙNG một allowlist.

Mọi test đều dùng fake model/provider. Không gọi DS2API, ElevenLabs hay
memory thật của người dùng. Không bỏ gate hoặc tự sửa claim_manifest của model.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.claim_domain_contract import inspect_claim_manifest, validate_claim_manifest
from runtime.conversation.claim_evidence import (
    EvidenceEntry, build_evidence_registry, build_writer_evidence_policy,
)
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.runtime_writer import RuntimeConversationWriter, _inspect_writer_manifest
from test_character_claim_integrity_v1 import Parser, Router


def _registry(kind="user_turn"):
    e = ConversationEvent(event_id="user:hello" if kind == "user_turn" else "autonomous:hello",
                          session_id="hotfix03", kind=kind,
                          text="hé lo" if kind == "user_turn" else "", generation=0)
    reg = build_evidence_registry(event=e, state=None, affect=None, report=None,
                                  ledger_turns=(), memory_records=(), sampled_at=1.)
    return e, reg


def _entry(ref, kind, scope, status="supported"):
    return EvidenceEntry(ref=ref, source_kind=kind, owner="test", subject="eris",
                         scope=scope, recorded_at=1., temporal_scope="current_turn",
                         status=status, content={"text": "sample"})


def _host(router):
    return SimpleNamespace(ai_model_router=router, parser=Parser(), ai_stage_planner=None,
                           current_stage_user_text="", chat_history=[], sent=[], last_text="")


def _run(kind="user_turn", payloads=None):
    class Agent:
        def decide(self, **_kwargs):
            return NextConversationMove(intent="chào", say="đáp lại lời chào")
    class Delivery:
        def __init__(self): self.calls = []
        def deliver(self, **kwargs): self.calls.append(kwargs)
    router = Router()
    if payloads is not None:
        router.payloads = payloads
    d = Delivery()
    e, reg = _registry(kind)
    kernel = ConversationKernel(Agent(), writer=RuntimeConversationWriter(_host(router)),
                                delivery=d)
    result = kernel.handle_event(e, runtime_context={"__evidence_registry": reg})
    return result, d, router


@pytest.mark.parametrize("kind", ["user_turn", "autonomous_signal"])
def test_simple_greeting_with_empty_manifest_still_goes_to_delivery(kind):
    """Lời chào không có factual claim không cần bịa lịch sử để có evidence."""
    result, delivery, router = _run(kind)
    assert result.status == "delivering"
    assert len(delivery.calls) == 1
    assert len(router.writer_calls) == 1 and len(router.worker_calls) == 0
    context = router.writer_calls[0]["grounding_context"]
    assert "CHARACTER_V1_CITABLE_REFS" in context
    assert "Không cần khai manifest" in context


def test_invalid_model_ref_is_not_a_semantic_public_gate():
    invalid=[{'domain':'user_statement','claim':'Bạn đã chào','evidence_refs':['evidence:phantom']}]
    result,delivery,router=_run(payloads=[
        {'text':'Chào cậu.','speech_text':'こんにちは。','claim_manifest':invalid}])
    assert result.status=='delivering' and len(delivery.calls)==1
    assert len(router.writer_calls)==1 and router.worker_calls==[]


def test_unknown_only_registry_entry_has_no_citable_id():
    _, reg = _registry()
    reg = replace(reg, entries=reg.entries + (_entry("memory:legacy-1", "memory", "unresolved", "unknown"),))
    policy = build_writer_evidence_policy(reg)
    assert "memory:legacy-1" not in policy.rendered
    assert "status=unknown" in policy.rendered  # chỉ thống kê, không phải ref
    assert "memory:legacy-1" not in policy.allowed_refs
    with pytest.raises(RuntimeError, match="claim_evidence_not_allowed:UNKNOWN_REF"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "user_statement", "claim": "Người dùng đã nói",
             "evidence_refs": ["memory:legacy-1"]}]}, reg, policy)


def test_started_tool_cannot_be_cited_as_success_or_action():
    _, reg = _registry()
    reg = replace(reg, entries=reg.entries + (
        _entry("capability:started", "capability_result", "capability_execution", "unknown"),))
    policy = build_writer_evidence_policy(reg)
    assert "capability:started" not in policy.rendered
    with pytest.raises(RuntimeError, match="UNKNOWN_REF"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "user_statement", "claim": "Đã hoàn thành",
             "evidence_refs": ["capability:started"]}]}, reg, policy)


def test_wrong_scope_ref_is_rejected_without_leaking_ref_to_log():
    _, reg = _registry()
    reg = replace(reg, entries=reg.entries + (
        _entry("web:result-1", "web", "source_excerpt"),))
    policy = build_writer_evidence_policy(reg)
    assert "web:result-1" in policy.rendered
    with pytest.raises(RuntimeError, match="claim_evidence_not_allowed:WRONG_SCOPE"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "user_statement", "claim": "Người dùng nói X",
             "evidence_refs": ["web:result-1"]}]}, reg, policy)
    decision = validate_claim_manifest(
        inspect_claim_manifest({"claim_manifest": [{
            "domain": "user_statement", "claim": "Người dùng nói X",
            "evidence_refs": ["web:result-1"]}]}),
        allowed_domains=policy.allowed_domains,
        allowed_evidence_refs=policy.allowed_refs,
        evidence_domains_by_ref=policy.refs_by_domain)
    assert decision.evidence_failure_codes == ("WRONG_SCOPE",)


def test_required_domain_without_ref_is_missing_ref_not_unknown_ref():
    e, reg = _registry()
    policy = build_writer_evidence_policy(reg)
    with pytest.raises(RuntimeError, match="claim_evidence_not_allowed:MISSING_REF"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "conversation_history", "claim": "Bạn vừa chào",
             "evidence_refs": []}]}, reg, policy)


def test_real_user_utterance_ref_is_permitted_but_not_world_proof():
    e, reg = _registry()
    policy = build_writer_evidence_policy(reg)
    _inspect_writer_manifest({"claim_manifest": [
        {"domain": "user_statement", "claim": "Bạn vừa viết hé lo",
         "evidence_refs": [f"event:{e.event_id}"]}]}, reg, policy)
    assert policy.refs_by_domain["user_statement"] == (f"event:{e.event_id}",)
    assert "user_profile" not in policy.allowed_domains


def test_large_registry_only_accepts_sources_actually_rendered():
    e, reg = _registry()
    reg = replace(reg, entries=reg.entries + tuple(
        _entry(f"web:long:{i}", "web", "source_excerpt") for i in range(25)))
    policy = build_writer_evidence_policy(reg, max_chars=260)
    assert policy.allowed_refs
    assert "web:long:24" not in policy.allowed_refs
    assert "web:long:24" not in policy.rendered
    with pytest.raises(RuntimeError, match="UNKNOWN_REF"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "discovery_artifact", "claim": "Thông tin trên web",
             "evidence_refs": ["web:long:24"]}]}, reg, policy)


def test_new_opinion_may_have_empty_refs_but_not_invented_refs():
    _, reg = _registry()
    policy = build_writer_evidence_policy(reg)
    _inspect_writer_manifest({"claim_manifest": [
        {"domain": "opinion", "claim": "Tớ thích màu xanh", "evidence_refs": []}]}, reg, policy)
    with pytest.raises(RuntimeError, match="UNKNOWN_REF"):
        _inspect_writer_manifest({"claim_manifest": [
            {"domain": "opinion", "claim": "Tớ thích màu xanh",
             "evidence_refs": ["fake:portrait"]}]}, reg, policy)


def test_invalid_claims_are_not_silently_removed_from_manifest():
    _, reg = _registry()
    policy = build_writer_evidence_policy(reg)
    with pytest.raises(RuntimeError, match="public_writer_manifest_invalid"):
        _inspect_writer_manifest({"claim_manifest": "[]"}, reg, policy)
