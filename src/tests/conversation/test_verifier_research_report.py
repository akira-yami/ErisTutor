import json
from tools.verifier_research import digest, write
from tools.report_verifier_research import report

def test_supported_content_with_forged_manifest_is_not_semantic_false_pass(tmp_path):
    inputs=[];reviews=[];settings={'temperature':0}
    for polarity in ('positive','negative'):
        ident='reference_identity-'+polarity
        inputs.append({'id':ident,'pair_id':'reference_identity','polarity':polarity,
          'payload':{'fixture':polarity},'candidate_origin':'controlled_gold_text',
          'gold':{'status':'pass' if polarity=='positive' else 'fail'}})
        reviews.append({'id':ident,'semantic_status':'pass',
                        'contract_expected_status':'pass' if polarity=='positive' else 'fail'})
    write(tmp_path/'frozen_inputs.json',inputs);write(tmp_path/'settings.json',settings)
    write(tmp_path/'evidence_blind_review.json',{'records':reviews})
    write(tmp_path/'offline_audit.json',{'rows':[],'recovered_non_unknown':[]})
    for item in inputs:
        for iteration in range(1,4):
            write(tmp_path/f"{item['id']}--{iteration}.json",{
             'id':item['id'],'iteration':iteration,'gold':item['gold']['status'],
             'payload_sha256':digest(item['payload']),'settings_sha256':digest(settings),
             'model_status':'pass','runtime_status':item['gold']['status'],
             'schema_valid':True,'elapsed_ms':10,'verifier_wire':[{'content':'{}'}],
             'verifier_json':{'status':'pass'},'audit':{'violations':[]}})
    result=report(tmp_path)
    assert result['semantic_false_pass']==[]
    assert result['contract_metrics']['model_errors']['false_pass']==3
    assert result['contract_metrics']['runtime_errors']['correct']==6
    assert result['pairs']['reference_identity']['eligible_observed_positive']
    failed=json.loads((tmp_path/'reference_identity-positive--1.json').read_text(encoding='utf-8'))
    failed.update(iteration=4,model_status='unknown',runtime_status='unknown',schema_valid=False,
                  verifier_wire=[],verifier_json={'_runtime_verifier_error':'VERIFIER_PROVIDER_ERROR'})
    write(tmp_path/'reference_identity-positive--4.json',failed)
    result=report(tmp_path)
    assert result['response_counts']['reference_identity-positive']==3
    assert result['independent_semantic_metrics']['calls']==6
    assert result['independent_semantic_metrics']['unstable_scenarios']==[]
    assert result['transport_errors']=={'VERIFIER_PROVIDER_ERROR':1}
    assert result['frozen_gold_metrics']['calls']==7
