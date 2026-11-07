"""Controlled, offline source fixtures; candidates are gold text, never Writer output.

Only the supplied directory is read/written. Screen excerpts are controlled fixtures,
not live captures. Staleness is an explicit frozen registry intervention because the
current canonical memory projection does not infer expiration from wall-clock age.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core.evidence import StateCommitRequest
from memory.claim_provenance import create_provenance
from memory.memory_record import record_from_layer_item
from memory.semantic_memory import SemanticMemoryStore
from runtime.conversation.claim_evidence import build_evidence_registry, extend_evidence_registry, _plain
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent
from runtime.source_grounding import GroundingEvidence, SourceGroundingReport

EPOCH = 1767225600.0
ISO = '2026-01-01T00:00:00+00:00'


def _memory(directory, name, text, subject='user', origin='user_statement', scope='fact', *, typed_memory_domain=False):
    path = directory / f'{name}.memory.json'
    # Each run reconstructs isolated fixture stores rather than reinforcing old data.
    path.write_text('{"items": []}', encoding='utf-8')
    store = SemanticMemoryStore(str(path))
    provenance = create_provenance(origin, root_event_ids=[f'user:source:{name}'],
                                   subject=subject, scope=scope, assertion='explicit')
    metadata = {'claim_provenance': provenance}
    if typed_memory_domain:
        metadata['domain'] = 'self' if subject == 'eris' else 'user'
    item = store.apply_commit(StateCommitRequest(
        target='semantic_memory', content=text, confidence=.95,
        storage_permission=True, permission_required=False,
        evidence_ids=[f'user:source:{name}'], metadata=metadata))
    if item is None:
        raise AssertionError('fixture commit was rejected')
    # Normalize actual committed item identities/timestamps, persist and RELOAD it.
    item.id = f'fixture-{name}'
    item.first_seen_at = ISO
    item.updated_at = ISO
    store.save([item])
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['updated_at'] = ISO
    path.write_text(json.dumps(saved, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    record = record_from_layer_item('semantic', SemanticMemoryStore(str(path)).load()[0])
    artifact = {'path': path.name, 'kind': 'canonical_semantic_store',
                'record_id': record.id, 'reloaded': True,
                'source_event': {'event_id': f'user:source:{name}', 'kind': origin,
                                 'subject': subject, 'text': text}}
    return record, artifact


def build_cases(output_dir: Path, *, typed_memory_domain: bool = False) -> list[dict]:
    """Build frozen fixtures; opt in to a canonical domain-category ablation.

    The optional domain is supplied to the real commit request. It does not modify
    projected text, provenance, or the default research fixtures.
    """
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    specs = [
        ('canonical_fact', 'Bạn thích trà xanh.', 'あなたは緑茶が好きです。', 'user_profile'),
        ('completed_action', 'Tôi đã tạo tệp receipt.txt.', '私はreceipt.txtファイルを作成しました。', 'self_capability'),
        ('screen_availability', 'Màn hình hiện hiển thị READY.', '画面には今READYと表示されています。', 'grounded_environment'),
        ('memory_freshness', 'Bạn hiện sống ở Huế.', 'あなたは現在フエに住んでいます。', 'user_profile'),
        ('reference_identity', 'Bạn thích trà xanh.', 'あなたは緑茶が好きです。', 'user_profile'),
        ('memory_subject', 'Bạn thích trà xanh.', 'あなたは緑茶が好きです。', 'user_profile'),
        ('historical_self_action', 'Tôi đã giúp bạn sửa lỗi.', '私はあなたのバグ修正を手伝いました。', 'shared_episode'),
    ]
    cases = []
    for pair, vi, ja, domain in specs:
        for positive in (True, False):
            polarity = 'positive' if positive else 'negative'
            name = f'{pair}-{polarity}'
            event = ConversationEvent(f'event:{name}', 'fixture-session', 'user_turn',
                                      'Kiểm tra thông tin đã có.', 1, EPOCH)
            records, artifacts, report = [], [], None
            if pair not in {'completed_action', 'screen_availability'}:
                text = vi
                if pair == 'canonical_fact' and not positive:
                    text = 'Bạn không thích trà xanh; bạn thích cà phê.'
                subject = 'other-person:Lan' if pair == 'memory_subject' and not positive else 'user'
                if subject != 'user':
                    text = 'Lan thích trà xanh.'
                if pair == 'historical_self_action':
                    subject = 'eris'
                    text = 'User explicitly confirms: Eris helped me fix a bug.'
                origin = 'assistant_utterance' if pair == 'historical_self_action' and not positive else 'user_statement'
                if origin == 'assistant_utterance':
                    text = 'Assistant said: I helped you fix a bug.'
                record, artifact = _memory(directory, name, text, subject, origin,
                                           'historical_action' if pair == 'historical_self_action' else 'fact',
                                           typed_memory_domain=typed_memory_domain)
                records.append(record)
                artifacts.append(artifact)
            if pair == 'screen_availability':
                report = SourceGroundingReport('', 'current_screen', .95,
                    screen_evidence_present=positive,
                    evidence_items=[GroundingEvidence('screen:fixture', 'current_screen', 'primary', 'READY')])
                artifact_path = directory / f'{name}.screen.json'
                artifact_path.write_text(json.dumps({'controlled_excerpt': 'READY', 'available': positive}), encoding='utf-8')
                artifacts.append({'path': artifact_path.name, 'kind': 'controlled_screenshot_excerpt', 'live_capture': False})
            registry = build_evidence_registry(event=event, state=None, affect=None,
                report=report, ledger_turns=(), memory_records=records, sampled_at=EPOCH)
            if pair == 'completed_action':
                registry = replace(registry, run_id='fixture-run')
                file = directory / name / 'receipt.txt'
                if positive:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    bytes_written = file.write_bytes(b'fixture\n')
                    result = {'status': 'completed', 'created_file': file.name, 'bytes_written': bytes_written}
                else:
                    try:
                        file.write_bytes(b'fixture\n')
                    except FileNotFoundError:
                        result = {'status': 'failed', 'created_file': False, 'error_type': 'FileNotFoundError'}
                    else:
                        raise AssertionError('negative fixture unexpectedly succeeded')
                receipt = RootCapabilityRegistry({}).record_result(name='file_write',
                    invocation_id=f'write-{polarity}', event=event, run_id=registry.run_id, result=result)
                registry = extend_evidence_registry(registry, [receipt])
                receipt_path = directory / f'{name}.receipt.json'
                receipt_path.write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
                artifacts.append({'path': receipt_path.name, 'kind': 'actual_executor_receipt', 'status': receipt.status})
                if positive:
                    artifacts.append({'path': file.relative_to(directory).as_posix(), 'kind': 'actual_written_file',
                                      'bytes_written': bytes_written})
            selected = next(e for e in registry.entries if e.source_kind != 'user_statement')
            entries = registry.entries
            if pair == 'memory_freshness' and not positive:
                entries = tuple(replace(e, status='stale', temporal_scope='expired_fixture') if e.ref == selected.ref else e for e in entries)
                artifacts[0]['registry_intervention'] = 'explicit stale status; canonical projection does not expire records'
            ref = 'memory:forged-does-not-exist' if pair == 'reference_identity' and not positive else selected.ref
            support = 'supported' if positive else ('contradicted' if pair == 'canonical_fact' else
                'wrong_scope' if pair in {'memory_subject', 'historical_self_action'} else 'missing_source')
            rationale = ('The controlled source supports this exact claim.' if positive else {
                'canonical_fact': 'The canonical fact explicitly contradicts the claim.',
                'completed_action': 'Actual execution failed; a failed receipt cannot prove completed file creation.',
                'screen_availability': 'The controlled screen excerpt is unavailable, so current visibility is ungrounded.',
                'memory_freshness': 'The memory entry is explicitly stale and cannot prove a current residence.',
                'reference_identity': 'The cited reference does not exist in the registry.',
                'memory_subject': 'The memory concerns Lan rather than the current user.',
                'historical_self_action': 'An assistant utterance establishes only that the assistant said this, not that it happened.'}[pair])
            claims = [{'surface': surface, 'segment_id': 0,
                       'span': ja if surface == 'speech' else vi, 'domain': domain,
                       'expected_support': support, 'support_refs': [ref] if positive else [],
                       'rationale': rationale} for surface in ('display', 'speech', 'memory')]
            cases.append({'id': name, 'pair_id': pair, 'polarity': polarity,
                'candidate_origin': 'controlled_gold_text',
                'payload': {'candidate': {'display': vi, 'speech_segments': [{'segment_id': 0, 'text': ja}], 'memory': vi},
                    'manifest': [{'domain': domain, 'claim': vi, 'evidence_refs': [ref]}],
                    'move': {'move_id': f'move:{pair}', 'kind': 'answer', 'intent': 'answer_from_evidence'},
                    'portrait': {'snapshot_id': registry.snapshot_id, 'state_version': registry.state_version, 'facets': []},
                    'evidence': [{'ref': e.ref, 'kind': e.source_kind, 'scope': e.scope,
                        'status': e.status, 'subject': e.subject,
                        'content': json.dumps(_plain(e.content), ensure_ascii=False, sort_keys=True)} for e in entries]},
                'gold': {'status': 'pass' if positive else 'fail', 'surface_equivalence': True,
                         'memory_equivalence': True, 'claims': claims}, 'source_artifacts': artifacts})
    return cases
