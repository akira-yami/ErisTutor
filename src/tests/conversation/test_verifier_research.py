from tools.verifier_research import classify, metrics, digest, precheck

def test_production_probe_distinguishes_allowed_from_unavailable():
    payload={'candidate':{'display':'Hello','memory':'Hello','speech_segments':[]},
             'manifest':[], 'move':{}, 'evidence':[]}
    assert precheck(payload)['allowed'] is True

def test_errors_and_abstentions_are_separate():
    assert classify('pass','fail') == 'false_pass'
    assert classify('fail','pass') == 'false_reject'
    assert classify('unknown','pass') == 'abstention'
    assert classify('pass','ambiguous') == 'unscored'

def test_metrics_count_instability_and_tail_latency():
    rows=[{'id':'x','model_status':s,'runtime_status':s,'gold':'fail','schema_valid':True,'elapsed_ms':t}
          for s,t in [('fail',10),('pass',20),('fail',100)]]
    result=metrics(rows)
    assert result['model_errors']['false_pass']==1
    assert result['unstable_scenarios']==['x']
    assert result['model_verdict_transitions']==2
    assert result['model_verdict_transition_rate']==1
    assert result['latency_ms']['p50']==20
    assert result['latency_ms']['p95']==100
    assert digest({'b':2,'a':1})==digest({'a':1,'b':2})
