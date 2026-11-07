# -*- coding: utf-8 -*-
"""LEARN-06: benchmark có nhãn cho đúng semantic assessor production của Eris.

CHỈ đọc dữ liệu bài kiểm tra tổng hợp. Không đụng LearningStore thật, không gọi
Writer/TTS hay conversation live. --mode live dùng JsonlWorkerClient và role learner;
--mode dry-run KHÔNG sinh ra bất kỳ độ chính xác AI nào.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time
from types import SimpleNamespace
from typing import Any, Mapping

from runtime.learning_core import LearnerProfile, MasteryRecord
from runtime.learning_core.policy import TutorStep
from runtime.tutor.contracts import TeachingMove, TutorObservation, TutorState
from runtime.tutor.model_reasoner import ASSESSMENT_SCHEMA, RuntimeTutorReasoner, valid_semantic_assessment_wire

DATA_FILE = Path(__file__).with_name('learner_benchmark_cases.json')
VALID_LABELS = {'pass', 'partial', 'fail', 'uncertain'}
OUTPUT_COLUMNS = ('case_id', 'domain', 'gold', 'verdict', 'confidence', 'strength',
                  'schema_valid', 'kp_bound', 'commit_allowed', 'error', 'elapsed_ms', 'repeat')


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def load_cases(path: Path = DATA_FILE) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list) or len(data) < 12:
        raise ValueError('dataset phải là danh sách >= 12 tình huống')
    seen = set()
    for case in data:
        if not isinstance(case, dict) or any(not isinstance(case.get(k), str) or not case[k].strip()
                                             for k in ('id','domain','kp_id','title','description','question','answer','gold')):
            raise ValueError('dataset có field rỗng/sai kiểu')
        if case['id'] in seen or case['gold'] not in VALID_LABELS:
            raise ValueError('dataset trùng id hoặc nhãn không hợp lệ')
        seen.add(case['id'])
    return data


def observation_for(case: Mapping[str, str], *, repeat: int = 0) -> tuple[TutorObservation, TutorState, TeachingMove]:
    """Xây input giống assessment thật; QUESTION thực đã phát, không dùng đáp án mẫu."""
    kp = case['kp_id']
    move = TeachingMove(move_id=f"learn06:{case['id']}:{repeat}", kind='assessment',
                        kp_id=kp, objective='Kiểm tra hiểu và vận dụng',
                        instruction_vi='Đánh giá câu trả lời theo câu hỏi đã phát.',
                        question_goal=case['question'], expected_response_shape='Giải thích ngắn gọn',
                        requires_user_response=True)
    step = TutorStep('assess', kp, 'assess_after_teach', 'learn06:benchmark', 'feynman_explain')
    obs = TutorObservation(turn_id=f"case:{case['id']}:{repeat}", user_text=case['answer'],
        path_id='learn06:benchmark', path_goal='Đánh giá độ hiểu từ bài làm có nhãn',
        kp_id=kp, kp_title=case['title'], kp_description=case['description'], kp_type='concept',
        policy_step=step, mastery=MasteryRecord.blank(kp), learner=LearnerProfile(), now=1_800_000_000)
    state = TutorState(session_id=f"bench:{case['id']}", path_id=obs.path_id, kp_id=kp,
                       last_move=move, last_delivery_text=case['question'])
    return obs, state, move


def wire_validation(raw: Any) -> bool:
    """Dùng đúng schema gate production, không duy trì bản diễn giải riêng."""
    return valid_semantic_assessment_wire(raw)


class AuditRouter:
    """Bọc router thật, chỉ ghi metadata an toàn; lỗi model không biến thành 'pass'."""
    def __init__(self, router: Any, client: Any):
        self.router = router
        self.deep_client = client
        self.latest: dict[str, Any] = {}

    def invoke_structured_worker(self, client: Any, **kwargs: Any) -> Mapping[str, Any]:
        self.latest = {'schema_valid': False, 'error': ''}
        try:
            raw = self.router.invoke_structured_worker(client, **kwargs)
            self.latest['schema_valid'] = wire_validation(raw)
            if not self.latest['schema_valid']:
                self.latest['error'] = 'invalid_schema_or_empty'
            return raw
        except Exception as exc:  # exception text có thể chứa secrets -> KHÔNG ghi
            self.latest['error'] = 'provider_' + type(exc).__name__[:60]
            raise


def create_live_router() -> tuple[AuditRouter, dict[str, Any]]:
    """Same provider stack as existing runtime/verifier benchmarks. No mock API."""
    from eris_secrets import load_env_file
    load_env_file()
    from llm.client import OpenAICompatibleClient
    from llm.deepseek_workers import JsonlWorkerClient, load_worker_spec
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
    spec = load_worker_spec('learner')
    if not spec.configured:
        raise RuntimeError('learner lane chưa cấu hình; kiểm tra ERIS_DS_LEARNER_* trong máy, không gửi API key')
    target = OpenAICompatibleClient(spec.base_url, spec.api_key, spec.model, timeout=180,
                  provider_circuit_id=spec.base_url.rstrip('/')+'#deepseek_web:learner')
    target.worker_role = 'learner'
    target.inference_backend = 'ds2api_deepseek_web'
    target.inference_resource_class = 'remote_api'
    client = JsonlWorkerClient(target, role='learner', jsonl_enabled=spec.jsonl_enabled,
                               repair_attempts=spec.jsonl_repair_attempts)
    router = AIRuntimeModelRouter(SimpleNamespace(social_parser_client=client))
    # KHÔNG lưu key, URL, raw payload hoặc raw response vào reports.
    return AuditRouter(router, client), {'role': 'learner', 'model': spec.model,
                                         'jsonl_enabled': spec.jsonl_enabled,
                                         'repair_attempts': spec.jsonl_repair_attempts}


def one_call(case: Mapping[str, str], reasoner: RuntimeTutorReasoner,
             audit: AuditRouter, *, repeat: int = 0, min_confidence: float = .68) -> dict[str, Any]:
    obs, state, previous = observation_for(case, repeat=repeat)
    began = time.perf_counter()
    result = reasoner.assess_response(obs, state, previous)
    elapsed = round((time.perf_counter()-began)*1000, 2)
    raw_valid = bool(audit.latest.get('schema_valid'))
    # Model verdict có thể đúng nhưng chưa đủ tin để commit. Đo riêng hai lớp.
    verdict = result.verdict if result and raw_valid else 'no_result'
    commit = bool(result and raw_valid and result.verdict != 'uncertain'
                  and result.confidence >= min_confidence)
    return {'case_id': case['id'], 'domain': case['domain'], 'gold': case['gold'],
        'verdict': verdict, 'confidence': result.confidence if result and raw_valid else None,
        'strength': result.strength if result and raw_valid else None,
        'schema_valid': raw_valid,
        'kp_bound': bool(result and result.kp_id == case['kp_id']),
        'commit_allowed': commit, 'error': audit.latest.get('error') or ('no_result' if result is None else ''),
        'elapsed_ms': elapsed, 'repeat': repeat}


def score(rows: list[Mapping[str, Any]], *, mode: str) -> dict[str, Any]:
    totals = Counter(r['gold'] for r in rows)
    failures = Counter(r['verdict'] for r in rows)
    scored = [r for r in rows if r['verdict'] in VALID_LABELS]
    # 'uncertain' là gold riêng, không mặc định tính là sai hoặc pass.
    clear = [r for r in rows if r['gold'] in {'pass','fail'}]
    unsafe = [r for r in clear if r['gold'] == 'fail' and r['commit_allowed'] and r['verdict'] in {'pass','partial'}]
    missed = [r for r in clear if r['gold'] == 'pass' and r['commit_allowed'] and r['verdict'] == 'fail']
    abstained = [r for r in rows if r['verdict'] in {'uncertain','no_result'}]
    # confidence đang là *model judgement confidence*, KHÔNG phải P(mastered).
    # Calibration dưới đây chỉ kiểm tra self-rated correctness của verdict qua gold.
    confidence_scored = [r for r in scored if r['confidence'] is not None]
    accuracy = lambda items: (sum(r['verdict'] == r['gold'] for r in items) / len(items)) if items else None
    reliability_brier = (sum((r['confidence'] - float(r['verdict'] == r['gold']))**2
                             for r in confidence_scored) / len(confidence_scored)) if confidence_scored else None
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row['case_id'], []).append(row['verdict'])
    transitions = sum(a != b for statuses in groups.values() for a,b in zip(statuses, statuses[1:]))
    comparisons = sum(max(0, len(x)-1) for x in groups.values())
    lat = sorted(r['elapsed_ms'] for r in rows)
    return {'mode': mode, 'count': len(rows), 'gold_distribution': dict(totals),
        'model_distribution': dict(failures), 'schema_valid_rate': sum(r['schema_valid'] for r in rows)/len(rows) if rows else None,
        'coverage_non_abstention': 1-len(abstained)/len(rows) if rows else None,
        'accuracy_including_abstention': accuracy(rows), 'accuracy_when_classified': accuracy(scored),
        'unsafe_false_passes': len(unsafe), 'unsafe_false_pass_rate_per_gold_fail': len(unsafe)/totals['fail'] if totals['fail'] else None,
        'incorrect_rejections': len(missed),
        'provider_or_schema_failures': sum(bool(r['error']) for r in rows),
        'confidence_correctness_brier_NOT_mastery': reliability_brier,
        'confidence_correctness_samples': len(confidence_scored),
        'repeat_transitions': transitions, 'repeat_comparisons': comparisons,
        'unstable_cases': sorted(k for k,v in groups.items() if len(set(v))>1),
        'latency_ms_p50': statistics.median(lat) if lat else None,
        'latency_ms_p95': lat[max(0, math.ceil(.95*len(lat))-1)] if lat else None,
        'note': 'Không suy từ confidence sang P(mastered). Đây là benchmark chấm câu trả lời, không phải thử nghiệm kết quả học tập.'}


def dump_report(output: Path, cases: list[dict[str, Any]], rows: list[dict[str, Any]],
                metadata: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    summary = {'benchmark': 'ERIS LEARN-06 v1', 'dataset_sha256': _hash(cases),
               'case_count': len(cases), 'settings': dict(metadata), 'metrics': score(rows, mode=mode),
               'scope': 'synthetic labeled assessor replay; not a live Root/Writer/TTS E2E test',
               'private_data_collected': False}
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    m = summary['metrics']
    fmt = lambda key: 'chưa đo' if m.get(key) is None else str(round(m[key], 4) if isinstance(m[key], float) else m[key])
    overview = (
        '# ERIS LEARN-06 — Báo cáo benchmark\n\n'
        f'Chế độ: **{mode}** · Bộ dữ liệu: **{len(cases)} tình huống tổng hợp** · '
        f'Đã gọi: **{len(rows)}**\n\n'
        f'- Accuracy (gồm abstention): {fmt("accuracy_including_abstention")}\n'
        f'- Accuracy (chỉ khi có nhãn model): {fmt("accuracy_when_classified")}\n'
        f'- Schema hợp lệ: {fmt("schema_valid_rate")}\n'
        f'- False pass nguy hiểm (gold fail nhưng model chấm pass/partial đủ ngưỡng): {fmt("unsafe_false_passes")}\n'
        f'- Tỷ lệ false pass nguy hiểm theo gold fail: {fmt("unsafe_false_pass_rate_per_gold_fail")}\n'
        f'- Provider/schema errors: {fmt("provider_or_schema_failures")}\n'
        f'- Lật nhãn giữa các lần lặp: {fmt("repeat_transitions")}/{fmt("repeat_comparisons")}\n'
        f'- Latency p50 / p95 (ms): {fmt("latency_ms_p50")} / {fmt("latency_ms_p95")}\n'
        f'- Confidence correctness Brier (proxy, KHÔNG phải mastery): {fmt("confidence_correctness_brier_NOT_mastery")}\n\n'
        '**Giới hạn:** nhãn xây dựng thủ công, chưa được phản biện độc lập; '
        'chưa có đánh giá người học thật, không kết luận mastery đã calibrated. '
        'Chế độ dry-run chỉ kiểm tra dữ liệu, hoàn toàn không đo AI. '
        'Chỉ Root mới được quyết định public move; benchmark không gọi RootAgent/Writer/TTS.\n'
    )
    (output/'REPORT_VI.md').write_text(overview,encoding='utf-8')
    with (output/'rows.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    return summary


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Eris LEARN-06: diagnostic benchmark')
    parser.add_argument('--mode', choices=['dry-run','live'], default='dry-run')
    parser.add_argument('--out', type=Path, default=Path('logs/learn06_benchmark'))
    parser.add_argument('--cases', type=Path, default=DATA_FILE)
    parser.add_argument('--limit', type=int, default=0, help='0 = tất cả')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--confirm-live', action='store_true', help='bắt buộc để gọi model trả phí')
    parser.add_argument('--fail-fast-after', type=int, default=3,
                        help='dừng sớm sau N lần schema/provider lỗi liên tục (0 để tắt)')
    parser.add_argument('--max-unsafe-false-pass', type=int, default=None,
                        help='CI gate: exit 3 nếu vượt quá số false pass nguy hiểm')
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:max(1,args.limit)]
    if args.mode == 'dry-run':
        report = dump_report(args.out, cases, [], {'calls':0, 'dataset_valid':True}, mode='dry-run')
        print(json.dumps({'case_count':len(cases), 'mode':'dry-run', 'out':str(args.out),
                          'metrics':'NOT_MEASURED', 'dataset_sha256':report['dataset_sha256']},ensure_ascii=False))
        return 0
    if not args.confirm_live:
        parser.error('Chạy --mode live cần --confirm-live; sẽ gọi DeepSeek thật.')
    router, meta = create_live_router()
    reasoner = RuntimeTutorReasoner(router, learner_client=router.deep_client)
    rows = []
    consecutive_errors = 0
    stop = False
    for case in cases:
        for repeat in range(max(1,min(5,args.repeats))):
            row = one_call(case, reasoner, router, repeat=repeat)
            rows.append(row)
            consecutive_errors = consecutive_errors + 1 if not row['schema_valid'] else 0
            # Không log prompt hoặc raw provider response; chỉ metadata.
            print(f"[{len(rows)}] {case['id']} -> {row['verdict']}",flush=True)
            if args.fail_fast_after > 0 and consecutive_errors >= args.fail_fast_after:
                stop = True
                break
        if stop:
            break
    meta.update({'repeats':max(1,min(5,args.repeats)),'calls':len(rows),
                 'planned_calls':len(cases)*max(1,min(5,args.repeats)),
                 'early_stop_due_to_errors':stop,
                 'role':'learner', 'wire_schema_sha256':_hash(ASSESSMENT_SCHEMA)})
    summary = dump_report(args.out,cases,rows,meta,mode='live')
    print(json.dumps({'out':str(args.out),'metrics':summary['metrics']},ensure_ascii=False,indent=2))
    # Nonzero khi tất cả call fail; không che provider failure thành benchmark thành công.
    if rows and all(not r['schema_valid'] for r in rows):
        return 2
    if args.max_unsafe_false_pass is not None and summary['metrics']['unsafe_false_passes'] > args.max_unsafe_false_pass:
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
