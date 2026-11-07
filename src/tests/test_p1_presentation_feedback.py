import json
from datetime import datetime, timezone
from runtime.workspace.artifact_protocol import adapter_math_workspace, write_bundle_sidecar
from runtime.workspace.presentation_feedback import recent_render_feedback


def bundle():
    return adapter_math_workspace({'parse_mode':'workspace_artifact', 'job_id':'p1-demo',
        'title':'Toán', 'artifacts':[{'type':'formula','latex':r'\\frac{1}{2}'}]})


def write_ack(tmp_path, bundle, **patch):
    artifact=bundle['artifacts'][0]
    fb={'type':'renderer.feedback.v1','renderer_id':'richchat-main',
        'intent_id':bundle['intent_id'],'artifact_id':artifact['artifact_id'],
        'revision':artifact['revision'],'status':'rendered',
        'code':'OK_RENDERED','retryable':False,
        'received_at':datetime.now(timezone.utc).isoformat()}
    fb.update(patch)
    root=tmp_path/'presentation_v1'/'feedback';root.mkdir(parents=True,exist_ok=True)
    (root/f"{artifact['artifact_id']}.r{artifact['revision']}.json").write_text(json.dumps(fb),'utf-8')


def test_reads_verified_browser_ack(tmp_path):
    b=bundle();write_bundle_sidecar(b,tmp_path);write_ack(tmp_path,b)
    result=recent_render_feedback(tmp_path)
    assert len(result)==1 and result[0]['status']=='rendered'
    assert result[0]['turn_binding']=='job_provisional'


def test_rejects_mismatched_ack(tmp_path):
    b=bundle();write_bundle_sidecar(b,tmp_path);write_ack(tmp_path,b,intent_id='pi_aaaaaaaaaaaaaaaaaaaaaaaa')
    assert recent_render_feedback(tmp_path)==[]


def test_rejects_unavailable_source_bundle(tmp_path):
    b=bundle();write_ack(tmp_path,b)
    assert recent_render_feedback(tmp_path)==[]


def test_rejects_tampered_status(tmp_path):
    b=bundle();write_bundle_sidecar(b,tmp_path);write_ack(tmp_path,b,status='visible')
    assert recent_render_feedback(tmp_path)==[]
