"""Frozen verifier replay and controlled evidence research; never public delivery."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import time
from types import SimpleNamespace
from tools.verifier_contract_audit import audit_capture

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def classify(status, gold):
    if gold not in ('pass','fail'): return 'unscored'
    if status not in ('pass','fail'): return 'abstention'
    if status==gold: return 'correct'
    return 'false_pass' if status=='pass' else 'false_reject'

def metrics(rows):
    grouped=defaultdict(list)
    for r in rows: grouped[r['id']].append(r)
    lat=sorted(r['elapsed_ms'] for r in rows)
    def percentile(p): return lat[max(0,math.ceil(len(lat)*p)-1)] if lat else None
    comparisons=sum(max(0,len(v)-1) for v in grouped.values())
    def transitions(key):
        count=0
        for v in grouped.values():
            ordered=sorted(v,key=lambda r:r.get('iteration',0))
            count+=sum(a[key]!=b[key] for a,b in zip(ordered,ordered[1:]))
        return count
    scored=[r for r in rows if r['gold'] in ('pass','fail')]
    negatives=[r for r in scored if r['gold']=='fail']; positives=[r for r in scored if r['gold']=='pass']
    return {'calls':len(rows),'scored_calls':len(scored),'positive_calls':len(positives),'negative_calls':len(negatives),
      'model_accuracy_all_scored':sum(r['model_status']==r['gold'] for r in scored)/len(scored) if scored else None,
      'runtime_accuracy_all_scored':sum(r['runtime_status']==r['gold'] for r in scored)/len(scored) if scored else None,
      'model_false_pass_rate':sum(r['model_status']=='pass' for r in negatives)/len(negatives) if negatives else None,
      'model_false_reject_rate':sum(r['model_status']=='fail' for r in positives)/len(positives) if positives else None,
      'runtime_false_pass_rate':sum(r['runtime_status']=='pass' for r in negatives)/len(negatives) if negatives else None,
      'runtime_false_reject_rate':sum(r['runtime_status']=='fail' for r in positives)/len(positives) if positives else None,
      'scenario_repeats':{k:len(v) for k,v in grouped.items()},
      'schema_valid':sum(r['schema_valid'] for r in rows),
      'schema_valid_rate':sum(r['schema_valid'] for r in rows)/len(rows) if rows else None,
      'model_errors':dict(Counter(classify(r['model_status'],r['gold']) for r in rows)),
      'runtime_errors':dict(Counter(classify(r['runtime_status'],r['gold']) for r in rows)),
      'unstable_scenarios':[k for k,v in grouped.items() if len({r['model_status'] for r in v})>1],
      'runtime_unstable_scenarios':[k for k,v in grouped.items() if len({r['runtime_status'] for r in v})>1],
      'model_verdict_transitions':transitions('model_status'),
      'runtime_verdict_transitions':transitions('runtime_status'),
      'adjacent_repeat_comparisons':comparisons,
      'model_verdict_transition_rate':transitions('model_status')/comparisons if comparisons else None,
      'runtime_verdict_transition_rate':transitions('runtime_status')/comparisons if comparisons else None,
      'latency_ms':{'p50':percentile(.5),'p95':percentile(.95)},
      'per_scenario':{k:{'model_statuses':dict(Counter(r['model_status'] for r in v)),
                        'runtime_statuses':dict(Counter(r['runtime_status'] for r in v)),
                        'latency_ms':sorted(r['elapsed_ms'] for r in v)} for k,v in grouped.items()}}

def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def namespaces(payload):
    c=payload['candidate']
    return (SimpleNamespace(display_text=c['display'],memory_text=c['memory'],
             segments=tuple(SimpleNamespace(tts_text=s['text']) for s in c['speech_segments'])),
      SimpleNamespace(entries=tuple(SimpleNamespace(ref=e['ref'],status=e['status'],
             source_kind=e['kind'],scope=e['scope'],subject=e.get('subject',''),content=e.get('content',{})) for e in payload['evidence'])))

def precheck(payload):
    from runtime.conversation.claim_verifier import CharacterClaimVerifier
    from runtime.claim_domain_contract import inspect_claim_manifest
    prepared,registry=namespaces(payload)
    class Probe:
        called=False
        observer_client=object()
        def invoke_structured_worker(self,*a,**kw):
            self.called=True
            return {'status':'unknown','findings':[],'surface_equivalence':True,'memory_equivalence':True}
    probe=Probe()
    result=CharacterClaimVerifier(probe).verify(prepared=prepared,registry=registry,
       manifest=inspect_claim_manifest(payload['manifest']),move=SimpleNamespace(to_dict=lambda:payload['move']),portrait=None)
    return {'allowed':probe.called,'status':result.status,'reason_codes':result.reason_codes}

def prepare(source,out,typed_memory_domain=False,evidence_only=False):
    from tools.verifier_evidence_cases import build_cases
    labels=json.loads((out/'blind_labels.json').read_text(encoding='utf-8'))
    gold={x['id']:x for x in labels}
    inputs=[]; audits=[]; baseline=[]
    for path in sorted(source.glob('*.json')):
        if evidence_only: break
        s=json.loads(path.read_text(encoding='utf-8'))
        if 'variant' not in s or not s.get('verifier_input'): continue
        ident=s['case_id']+':'+s['variant']; label=gold[ident]
        old=audit_capture(s); new=audit_capture(s,v2=True)
        audits.append({'id':ident,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                       'original':old,'v2':new,'gold':label['expected_status']})
        baseline.append({'id':ident,'model_status':s.get('verifier_json',{}).get('status','unknown'),
         'runtime_status':s['runtime_result']['status'],'gold':label['expected_status'],
         'schema_valid':old['schema_valid'],'elapsed_ms':s['elapsed_ms']})
        inputs.append({'id':ident,'payload':s['verifier_input'],'gold':{'status':label['expected_status'],**label},
                        'candidate_origin':'captured_writer_output','source_capture':str(path.resolve())})
    evidence_cases=build_cases(out/'evidence-fixtures',typed_memory_domain=typed_memory_domain)
    if typed_memory_domain:
        evidence_cases=[r for r in evidence_cases if r['pair_id'] not in ('completed_action','screen_availability')]
    inputs.extend(evidence_cases)
    for item in inputs:
        item['payload_sha256']=digest(item['payload']); item['production_precheck']=precheck(item['payload'])
    write(out/'offline_audit.json',{'rows':audits,'baseline_semantics':metrics(baseline),
       'raw_json_count':sum(a['original']['schema_valid'] for a in audits),
       'violations':dict(Counter(v['code'] for a in audits for v in a['original']['violations'])),
       'recovered_non_unknown':[a['id'] for a in audits if a['original']['effective_status']=='unknown' and a['v2']['effective_status']!='unknown'],
       'migration_note':'Only domain/subtype change. Unknown supported domains are unclassified and blocked. Recovery is admissibility, not semantic correctness.'})
    write(out/'frozen_inputs.json',inputs)
    return inputs

def run(source,out,repeats=3,typed_memory_domain=False,evidence_only=False,fill_missing=False,max_calls=None):
    root=Path(__file__).resolve().parents[2]
    os.environ.setdefault('ERIS_PROJECT_ROOT',str(root))
    from eris_secrets import load_env_file
    load_env_file(); os.environ['ERIS_WEB_SESSION_MODE']='off'
    from llm.client import OpenAICompatibleClient
    from llm.deepseek_workers import load_worker_spec,JsonlWorkerClient
    from tools.benchmark_claim_verifier import CaptureClient
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
    from runtime.conversation.claim_verifier import SCHEMA,SYSTEM,normalize_verifier_wire_format,validate_verifier_result
    inputs=prepare(source,out,typed_memory_domain,evidence_only)
    spec=load_worker_spec('observer')
    settings={'model':spec.model,'base_url':spec.base_url,'temperature':0.0,'max_tokens':2400,
      'timeout_seconds':180,'jsonl_enabled':spec.jsonl_enabled,'repair_attempts':spec.jsonl_repair_attempts,
      'system_sha256':digest(SYSTEM),'schema_sha256':digest(SCHEMA),'repeats':repeats,
      'client_stack':'JsonlWorkerClient(CaptureClient(OpenAICompatibleClient))','concurrency':1}
    write(out/'settings.json',settings)
    def call(item,iteration):
        assert digest(item['payload'])==item['payload_sha256']
        target=OpenAICompatibleClient(spec.base_url,spec.api_key,spec.model,timeout=180,
               provider_circuit_id=spec.base_url.rstrip('/')+'#deepseek_web:observer')
        target.worker_role='observer';target.inference_backend='ds2api_deepseek_web';target.inference_resource_class='remote_api'
        capture=CaptureClient(target)
        client=JsonlWorkerClient(capture,role='observer',jsonl_enabled=spec.jsonl_enabled,repair_attempts=spec.jsonl_repair_attempts)
        router=AIRuntimeModelRouter(SimpleNamespace(social_parser_client=client))
        start=time.perf_counter(); error=None
        try:
            raw=router.invoke_structured_worker(client,system=SYSTEM,payload=item['payload'],schema=SCHEMA,
             name='eris_character_claim_verifier_v1',cognitive_lane='public_claim_verifier',max_tokens=2400,timeout_seconds=180,temperature=0.0)
            raw=normalize_verifier_wire_format(raw)
        except Exception as exc: raw={};error=type(exc).__name__+':'+str(exc)
        elapsed=round((time.perf_counter()-start)*1000,2)
        prepared,registry=namespaces(item['payload'])
        checked=validate_verifier_result(raw,prepared=prepared,registry=registry)
        c=item['payload']['candidate']
        audit=audit_capture({'verifier_json':raw,'evidence_registry':item['payload']['evidence'],
          'final_surfaces':{'display':c['display'],'memory':c['memory'],'speech':[s['text'] for s in c['speech_segments']]}})
        row={'id':item['id'],'iteration':iteration,'payload_sha256':item['payload_sha256'],
          'settings_sha256':digest(settings),'gold':item['gold']['status'],
          'model_status':raw.get('status','unknown') if isinstance(raw,dict) else 'unknown',
          'runtime_status':checked.status if item['production_precheck']['allowed'] else item['production_precheck']['status'],
          'runtime_result_after_model':asdict(checked),'production_precheck':item['production_precheck'],
          'schema_valid':audit['schema_valid'],'elapsed_ms':elapsed,'verifier_json':raw,
          'verifier_wire':capture.calls,'audit':audit,'error':error,
          'research_direct_call':True}
        write(out/(item['id'].replace(':','--')+f'--{iteration}.json'),row)
        return row
    rows=[]
    by_id={item['id']:item for item in inputs}
    for path in out.glob('*--*.json'):
        saved=json.loads(path.read_text(encoding='utf-8'))
        item=by_id.get(saved.get('id'))
        if item and saved.get('payload_sha256')==item['payload_sha256'] and saved.get('settings_sha256')==digest(settings):
            rows.append(saved)
        else:
            raise RuntimeError('resume_hash_mismatch:'+path.name)
    finished={(r['id'],r['iteration']) for r in rows}
    if fill_missing:
        jobs=[]
        for item in inputs:
            previous=[r for r in rows if r['id']==item['id']]
            responses=sum(bool(r['verifier_wire']) and r['verifier_json'].get('status') in ('pass','fail','unknown') for r in previous)
            last=max((r['iteration'] for r in previous),default=0)
            jobs.extend((item,last+i) for i in range(1,max(0,repeats-responses)+1))
    else:
        jobs=[(item,i) for i in range(1,repeats+1) for item in inputs if (item['id'],i) not in finished]
    if max_calls is not None: jobs=jobs[:max_calls]
    for item,iteration in jobs:
            row=call(item,iteration);rows.append(row)
            print(f"{len(rows)} calls (target {repeats} responses/input) {row['id']} #{row['iteration']} model={row['model_status']} runtime={row['runtime_status']} {row['elapsed_ms']}ms",flush=True)
            summary=metrics(rows)
            summary['pairs']={item['pair_id']:{'positive_has_model_pass':any(r['id']==item['id'] and r['model_status']=='pass' for r in rows),
               'positive_has_runtime_pass':any(r['id']==item['id'] and r['runtime_status']=='pass' for r in rows)} for item in inputs if item.get('polarity')=='positive'}
            write(out/'repeat_summary.json',summary)
            if isinstance(row['verifier_json'],dict) and '_runtime_verifier_error' in row['verifier_json']:
                print('Provider failure: stopping batch; keep errors and resume later.',flush=True)
                break
    parts=['# Exact verifier model content before JSONL normalization\n']
    for row in sorted(rows,key=lambda r:(r['id'],r['iteration'])):
        parts.append(f"\n## {row['id']} / repeat {row['iteration']}\n")
        if not row['verifier_wire']:
            parts.append('\nNo model response; transport error: '+str(row['verifier_json'].get('_runtime_verifier_error',row.get('error')))+'\n')
        for wire in row['verifier_wire']: parts.append('\n```text\n'+wire['content']+'\n```\n')
    (out/'REPEAT_RAW.md').write_text(''.join(parts),encoding='utf-8')
    return metrics(rows)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--offline',action='store_true');p.add_argument('--typed-memory-domain',action='store_true');p.add_argument('--evidence-only',action='store_true');p.add_argument('--fill-missing',action='store_true');p.add_argument('--max-calls',type=int)
    a=p.parse_args()
    if a.offline: prepare(a.source,a.output,a.typed_memory_domain,a.evidence_only)
    else: run(a.source,a.output,typed_memory_domain=a.typed_memory_domain,evidence_only=a.evidence_only,fill_missing=a.fill_missing,max_calls=a.max_calls)
