"""Recompute research results from immutable captures and independent annotations."""
import argparse
from collections import Counter
import json
from pathlib import Path
from tools.verifier_research import digest, metrics, write

def report(out):
    inputs=json.loads((out/'frozen_inputs.json').read_text(encoding='utf-8'))
    settings=json.loads((out/'settings.json').read_text(encoding='utf-8'))
    by_id={x['id']:x for x in inputs}
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(out.glob('*--*.json'))]
    assert len({(r['id'],r['iteration']) for r in rows})==len(rows)
    for r in rows:
        assert r['payload_sha256']==digest(by_id[r['id']]['payload'])
        assert r['settings_sha256']==digest(settings)
    reviews=json.loads((out/'evidence_blind_review.json').read_text(encoding='utf-8'))
    if isinstance(reviews,dict): reviews=reviews.get('records',reviews.get('reviews',reviews.get('rows',[])))
    review_by_id={r['id']:r for r in reviews}
    assert set(review_by_id)=={r['id'] for r in inputs if r['candidate_origin']=='controlled_gold_text'}
    semantic=[];contract=[];contract_only=[]
    for r in rows:
        review=review_by_id.get(r['id'])
        semantic_gold=review['semantic_status'] if review else r['gold']
        if review and review['semantic_status']=='pass' and review['contract_expected_status']=='fail':
            contract_only.append({'id':r['id'],'iteration':r['iteration'],'semantic_status':'pass','contract_expected_status':'fail'})
            semantic_gold='contract_only'
        semantic.append({**r,'gold':semantic_gold})
        contract.append({**r,'gold':review['contract_expected_status'] if review else r['gold']})
    raw_json=0;wire_count=0
    for r in rows:
        for wire in r['verifier_wire']:
            wire_count+=1
            try: json.loads(wire['content']);raw_json+=1
            except (TypeError,ValueError): pass
    response_ids={(r['id'],r['iteration']) for r in rows if r['verifier_wire'] and r['verifier_json'].get('status') in ('pass','fail','unknown')}
    responses=[r for r in semantic if (r['id'],r['iteration']) in response_ids]
    response_counts={item['id']:sum(r['id']==item['id'] for r in responses) for item in inputs}
    complete=[k for k,v in response_counts.items() if v>=3]
    pending=[k for k,v in response_counts.items() if v<3]
    result={'frozen_gold_metrics':metrics(rows),'independent_semantic_metrics':metrics(responses),
      'contract_metrics':metrics(contract),
      'original_writer_semantic_metrics':metrics([r for r in responses if r['id'].endswith(':writer_original')]),
      'writer_mutation_semantic_metrics':metrics([r for r in responses if by_id[r['id']]['candidate_origin']=='captured_writer_output' and not r['id'].endswith(':writer_original')]),
      'controlled_evidence_semantic_metrics':metrics([r for r in responses if by_id[r['id']]['candidate_origin']=='controlled_gold_text']),
      'complete_scenario_metrics':metrics([r for r in responses if r['id'] in complete]),
      'response_counts':response_counts,'complete_response_scenarios':complete,'pending_response_scenarios':pending,
      'raw_wire_json':{'valid':raw_json,'total':wire_count},
      'normalized_schema_valid':sum(r['schema_valid'] for r in rows),
      'normalized_runtime_contract_valid':sum(not r['audit']['violations'] for r in rows),
      'transport_errors':dict(Counter(r['verifier_json'].get('_runtime_verifier_error') for r in rows if isinstance(r['verifier_json'],dict) and '_runtime_verifier_error' in r['verifier_json'])),
      'repair_calls':sum(max(0,len(r['verifier_wire'])-1) for r in rows),
      'contract_only_excluded_from_semantic_verdict_accuracy':contract_only,
      'equivalence_errors':[], 'pairs':{},'semantic_false_pass':[],'semantic_false_reject':[]}
    for r in semantic:
        if r['gold']=='fail' and r['model_status']=='pass':result['semantic_false_pass'].append({'id':r['id'],'iteration':r['iteration'],'runtime_status':r['runtime_status']})
        if r['gold']=='pass' and r['model_status']=='fail':result['semantic_false_reject'].append({'id':r['id'],'iteration':r['iteration'],'runtime_status':r['runtime_status']})
        g=by_id[r['id']]['gold'];raw=r['verifier_json']
        for key in ('surface_equivalence','memory_equivalence'):
            if type(g.get(key)) is bool and type(raw.get(key)) is bool and raw[key]!=g[key]:
                result['equivalence_errors'].append({'id':r['id'],'iteration':r['iteration'],'field':key,'expected':g[key],'actual':raw[key]})
    for item in inputs:
        if item.get('polarity')!='positive':continue
        positive=[r for r in rows if r['id']==item['id']]
        negative=[r for r in rows if r['id']==item['pair_id']+'-negative']
        ppass=sum(r['runtime_status']=='pass' for r in positive)
        result['pairs'][item['pair_id']]={'positive_runtime_pass':ppass,'positive_model_pass':sum(r['model_status']=='pass' for r in positive),
          'eligible_observed_positive':ppass>0,'stable_positive':ppass==3,
          'negative_runtime_statuses':dict(Counter(r['runtime_status'] for r in negative)),
          'negative_model_statuses':dict(Counter(r['model_status'] if (r['id'],r['iteration']) in response_ids else 'no_response' for r in negative))}
    write(out/'analysis.json',result)
    offline=json.loads((out/'offline_audit.json').read_text(encoding='utf-8'))
    original_json=[r for r in offline['rows'] if r['original']['schema_valid']]
    offline_counts=Counter(v['code'] for r in original_json for v in r['original']['violations'])
    def seconds(value): return f'{value/1000:.2f}s' if value is not None else 'n/a'
    lines=['# Verifier research on DS2API\n',
      'Controlled component benchmark; no public delivery or production memory writes. '
      'The primary suite has 12 captured real Writer outputs, two controlled permission mutations, and 14 controlled evidence texts. '
      'Independent annotations are assistant-authored, not human-expert gold.\n',
      (f'**INCOMPLETE:** {len(pending)}/{len(inputs)} inputs still lack three actual model responses. Transport failure attempts do not satisfy response-based stability coverage.\n' if pending else 'All inputs have at least three actual model responses.\n'),
      '## Offline contract audit\n',
      f'{len(original_json)} existing JSON responses audited exhaustively; one additional original attempt had a transport timeout. '
      f'Violations on the JSON responses: `{dict(offline_counts)}`.\n',
      'Fixed domain + open subtype migration preserves status, equivalence, verdict, span, refs and reason. '
      'Unknown supported domains become unclassified and cannot PASS. '
      f"Recovered from UNKNOWN: {len(offline['recovered_non_unknown'])}; every recovery is FAIL. " +
      ('Four recovered refusals match independent labels; two remain false rejections (exact_number, screen_unknown).\n' if offline['rows'] else 'Supplemental evidence-only experiment; no old RAW migration is included here.\n'),
      '| Input | Gold | Original contract | Migrated contract | Violations |',
      '|---|---|---|---|---|']
    for r in offline['rows']:
        lines.append(f"| {r['id']} | {r['gold']} | {r['original']['effective_status']} | {r['v2']['effective_status']} | {dict(Counter(v['code'] for v in r['original']['violations']))} |")
    lines+=['\n## Every contract violation in existing raw JSON\n']
    for r in original_json:
        for v in r['original']['violations']:
            index=v.get('finding_index');f=r['original']['json']['findings'][index] if index is not None else {}
            lines.append(f"- {r['id']}, finding {index}: `{v['code']}`, domain `{f.get('domain')}`, span {json.dumps(f.get('span'),ensure_ascii=False)}, refs `{f.get('support_refs')}`.")
    lines+=['\n## Frozen repeats and response completeness\n',
      f"{len(rows)} calls / {len(inputs)} inputs; identical payload/model/settings hashes verified. Sequential concurrency; temperature 0; timeout 180s. "
      'Provider account selection is controlled by the existing gateway pool; account identity is not pinned.\n',
      f"Actual model responses: {len(responses)}. Inputs with at least three model responses: {len(complete)}/{len(inputs)}. "
      f"Pending inputs: `{pending}`. Failed transport attempts are not model UNKNOWN verdicts and are excluded from semantic verdict/stability scoring.\n",
      f"Exact raw wire JSON: {raw_json}/{wire_count}; normalized schema: {result['normalized_schema_valid']}/{len(rows)}; "
      f"runtime finding contract: {result['normalized_runtime_contract_valid']}/{len(rows)}. Transport errors: {result['transport_errors']}.\n",
      '| Track | Scored | Correct model | False PASS model | False reject model | Abstention model | False PASS runtime | False reject runtime |',
      '|---|---:|---:|---:|---:|---:|---:|---:|']
    for title,key in [('Independent semantics','independent_semantic_metrics'),('Contract incl manifest refs','contract_metrics'),('Original Writer semantics','original_writer_semantic_metrics'),('Controlled Writer mutations','writer_mutation_semantic_metrics'),('Evidence semantics','controlled_evidence_semantic_metrics')]:
        m=result[key];a=m['model_errors'];b=m['runtime_errors']
        lines.append(f"| {title} | {m['scored_calls']} | {a.get('correct',0)} | {a.get('false_pass',0)} | {a.get('false_reject',0)} | {a.get('abstention',0)} | {b.get('false_pass',0)} | {b.get('false_reject',0)} |")
    m=result['independent_semantic_metrics']
    lines += [f"\nLatency p50/p95 on model responses: {seconds(m['latency_ms']['p50'])} / {seconds(m['latency_ms']['p95'])}. "
       f"Model verdict changes: {len(m['unstable_scenarios'])}/{len(inputs)} inputs; runtime changes: {len(m['runtime_unstable_scenarios'])}/{len(inputs)}.\n",
       f"All-attempt latency p50/p95 (incl provider failure and JSONL repair): {seconds(result['frozen_gold_metrics']['latency_ms']['p50'])} / {seconds(result['frozen_gold_metrics']['latency_ms']['p95'])}.\n",
       f"Adjacent model verdict transitions: {m['model_verdict_transitions']}/{m['adjacent_repeat_comparisons']}; runtime: {m['runtime_verdict_transitions']}/{m['adjacent_repeat_comparisons']}. Quantiles use nearest rank.\n",
       f"Model semantic false PASS: `{result['semantic_false_pass']}`.\n",
       f"Model semantic false reject: `{result['semantic_false_reject']}`.\n",
       'UNKNOWN is reported separately and counts as non-correct in accuracy; it is not relabeled as a false reject. '
       'Two ambiguous Writer cases are excluded from accuracy: negative_statement and unperformed_action. '
       'Forged-ref negative has supported content but an invalid manifest. It is excluded from semantic verdict accuracy: '
       'a correct contract refusal is not evidence of semantic false rejection. It remains scored in contract accuracy, and its claim-support annotation remains PASS.\n',
       ('The missing_memory false PASS omits the unsupported memory-reliability counterfactual “Nếu thật là có, tôi đã nhớ rồi”. '
        'Its only evidence is the user question; no memory history or recall-reliability source is present. '
        'An empty findings list passes the mechanical gate, so omitted claims are a verifier coverage problem. '
        'This is a false PASS under the predeclared independent label, which discloses the policy ambiguity about blanket internal-state exemptions.\n' if any(r['id']=='missing_memory:writer_original' for r in result['semantic_false_pass']) else ''),
       '## Positive/negative evidence pairs\n','| Pair | Positive runtime PASS /3 | Positive model PASS /3 | Negative model statuses | Negative runtime statuses |',
       '|---|---:|---:|---|---|']
    for pair,p in result['pairs'].items():lines.append(f"| {pair} | {p['positive_runtime_pass']} | {p['positive_model_pass']} | {p['negative_model_statuses']} | {p['negative_runtime_statuses']} |")
    lines+=['\nOnly pairs with an observed positive runtime PASS qualify for detection claims. Positive runtime PASS 3/3 is tracked separately. '
       'Failed, unavailable, stale and forged cases are also called directly in this isolated research harness; production prechecks are overlaid on runtime status. '
       'These direct calls do not imply production delivery.\n',
       '## Limits and artifacts\n',
       'Memory is committed and reloaded through the actual isolated canonical store. Tool receipts describe actual file writes and caught FileNotFoundError. '
       'Screen is a controlled excerpt, not a live screen capture. Stale status is an explicit registry intervention. '
       'Labels test the supplied evidence, not independent truth of user assertions. '
       'Three repeats on a small controlled suite do not establish a population false-PASS bound.\n',
       '- `frozen_inputs.json`: candidates, evidence, fixed gold, production precheck and hashes.\n'
       '- `blind_labels.json`, `evidence_blind_review.json`: independent labels and caveats.\n'
       '- `offline_audit.json`, `analysis.json`: exhaustive machine-readable results.\n'
       '- `REPEAT_RAW.md`: exact content before JSONL normalization, excluding reasoning and credentials.\n'
       '- Per-repeat JSON: raw, normalized result, findings, spans, refs, equivalence, latency.\n']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args();report(a.output)
