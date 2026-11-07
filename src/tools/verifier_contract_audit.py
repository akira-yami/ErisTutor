"""Exhaustive offline contract audit. Experimental migration never edits verdicts."""
import copy
from runtime.conversation.claim_verifier import SCHEMA
from runtime.claim_domain_contract import KNOWN_CLAIM_DOMAINS, EVIDENCE_REQUIRED_DOMAINS

DOMAIN_MAP = {'derived_total':'user_statement', 'action_intent':'internal_state',
 'internal_state_counterfactual':'hypothetical', 'user_rule':'user_statement',
 'permission':'internal_state', 'instruction':'internal_state', 'self_action':'self_capability',
 'action_ref_creation':'self_capability', 'user_action':'user_profile',
 'assistant_context':'self_model', 'eris_action':'self_capability'}

def v2_schema():
    schema = copy.deepcopy(SCHEMA)
    item = schema['properties']['findings']['items']
    item['properties']['domain']['enum'] = sorted(KNOWN_CLAIM_DOMAINS | {'unclassified'})
    item['properties']['subtype'] = {'type':'string'}
    item['required'].append('subtype')
    return schema

def migrate_domains(raw):
    result = copy.deepcopy(raw)
    if not isinstance(result, dict): return result
    for f in result.get('findings', []) if isinstance(result.get('findings'), list) else []:
        if not isinstance(f, dict): continue
        domain = f.get('domain')
        f['subtype'] = '' if domain in KNOWN_CLAIM_DOMAINS else str(domain)
        if domain not in KNOWN_CLAIM_DOMAINS:
            f['domain'] = DOMAIN_MAP.get(domain, 'unclassified') if f.get('verdict') != 'supported' else 'unclassified'
    return result

def shape_errors(value, schema, path='$'):
    errors=[]
    types={'object':dict,'array':list,'string':str,'integer':int,'boolean':bool}
    if type(value) is not types[schema['type']]:
        return [{'code':'schema_type','path':path}]
    if 'enum' in schema and value not in schema['enum']: errors.append({'code':'schema_enum','path':path})
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value: errors.append({'code':'schema_missing','path':path+'.'+key})
        for key,v in value.items():
            if key in schema.get('properties', {}): errors.extend(shape_errors(v,schema['properties'][key],path+'.'+key))
            elif schema.get('additionalProperties') is False: errors.append({'code':'schema_extra','path':path+'.'+key})
    if isinstance(value,list):
        if len(value)>schema.get('maxItems',float('inf')): errors.append({'code':'schema_overflow','path':path})
        for i,v in enumerate(value): errors.extend(shape_errors(v,schema['items'],f'{path}[{i}]'))
    return errors

def audit_capture(sample, v2=False):
    raw=migrate_domains(sample.get('verifier_json')) if v2 else sample.get('verifier_json')
    if isinstance(raw,dict) and '_runtime_verifier_error' in raw:
        return {'schema_valid':False,'violations':[{'code':'transport_failure','reason':raw['_runtime_verifier_error']}],
                'effective_status':'unknown','json':raw}
    errors=shape_errors(raw,v2_schema() if v2 else SCHEMA)
    schema_valid=not errors
    def add(code,index=None,**detail): errors.append({'code':code,'finding_index':index,**detail})
    surfaces=sample.get('final_surfaces',{})
    evidence={e['ref']:e for e in sample.get('evidence_registry',[])}
    kinds={'shared_episode':{'memory'},'user_profile':{'memory'},'self_history':{'memory'},
           'user_statement':{'user_statement'},'grounded_environment':{'current_screen','live_vision'},
           'self_capability':{'capability_result'},'self_model':{'runtime_state'}}
    if isinstance(raw,dict):
        for i,f in enumerate(raw.get('findings',[]) if isinstance(raw.get('findings'),list) else []):
            if not isinstance(f,dict): continue
            domain=f.get('domain'); surface=f.get('surface'); seg=f.get('segment_id'); span=f.get('span')
            if domain not in KNOWN_CLAIM_DOMAINS:
                if v2 and domain=='unclassified':
                    add('supported_unclassified' if f.get('verdict')=='supported' else 'domain_unclassified',i)
                else: add('domain_unknown',i,domain=domain)
            source=surfaces.get(surface,'') if surface in ('display','memory') else ''
            if surface=='speech':
                speech=surfaces.get('speech',[])
                if type(seg) is int and 0<=seg<len(speech): source=speech[seg]
                else: add('segment_invalid',i)
            elif surface in ('display','memory') and (type(seg) is not int or seg not in (-1,0)): add('segment_invalid',i)
            if not isinstance(span,str) or not span.strip() or span not in source: add('span_mismatch',i)
            refs=f.get('support_refs')
            if isinstance(refs,list):
                if f.get('verdict')=='supported' and domain in EVIDENCE_REQUIRED_DOMAINS and not refs: add('evidence_required',i)
                for ref in refs:
                    if not isinstance(ref,str) or ref not in evidence: add('ref_missing',i,ref=ref); continue
                    e=evidence[ref]
                    if e.get('status')!='supported': add('ref_unavailable',i,ref=ref,status=e.get('status'))
                    if domain in kinds and e.get('kind') not in kinds[domain]: add('ref_kind_wrong',i,ref=ref)
                    if domain in ('shared_episode','user_profile','self_history') and e.get('scope') in ('summary','unresolved','fiction','utterance'): add('ref_scope_wrong',i,ref=ref)
            if raw.get('status')=='pass' and f.get('verdict')!='supported': add('pass_with_non_supported',i)
        if raw.get('status')=='pass':
            if raw.get('surface_equivalence') is False: add('pass_surface_mismatch')
            if raw.get('memory_equivalence') is False: add('pass_memory_mismatch')
    structural=any(e['code'].startswith('schema_') or e['code'] in ('domain_unknown','domain_unclassified','supported_unclassified','segment_invalid','span_mismatch') for e in errors)
    effective='unknown' if structural else 'fail' if errors else raw.get('status','unknown')
    return {'schema_valid':schema_valid,'violations':errors,'effective_status':effective,'json':raw}
