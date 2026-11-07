# -*- coding: utf-8 -*-
"""Kiểm thử tích hợp: SymPy/NumPy -> SandboxWorld -> artifact đĩa + JSON."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from actions.capability_registry import capability_input_schema, validate_capability_arguments
from runtime.workspace import MathWorkspaceService


@pytest.fixture()
def service(tmp_path):
    return MathWorkspaceService(tmp_path / 'logs')


def execute(service, payload, **options):
    return asyncio.run(service.execute(payload, **options))


def test_matrix_determinant_artifact(service):
    result = execute(service, {'kind':'matrix','rows':[[1,2],[3,4]], 'title':'Ma trận A'})
    assert result['parse_mode'] == 'workspace_artifact'
    assert any('Định thức: -2' in a['text'] for a in result['artifacts'] if a['type']=='text')
    assert next(a for a in result['artifacts'] if a['type']=='image')['path'].endswith('matrix.png')
    verify_image(service, result)


def verify_image(service, result):
    for a in result['artifacts']:
        if a['type'] == 'image':
            target = service.workspace / a['path']
            assert target.is_file()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == a['sha256']
            assert target.read_bytes().startswith(b'\x89PNG')


def test_diff_real_worker(service):
    result = execute(service, {'kind':'differentiate','expression':'sin(x)*x^2'})
    assert any('sin(x)' in a['text'] and 'cos(x)' in a['text'] for a in result['artifacts'] if a['type']=='text')
    verify_image(service, result)


def test_plot_real_png(service):
    result = execute(service, {'kind':'plot', 'expression':'x^2-2', 'xmin':-3,'xmax':3})
    assert any(a['path'].endswith('plot.png') for a in result['artifacts'] if a['type']=='image')
    verify_image(service, result)


def test_solve_integrate(service):
    assert any('2' in a['text'] for a in execute(service, {'kind':'solve','expression':'x^2-4'})['artifacts'] if a['type']=='text')
    assert any('x**2' in a['text'] for a in execute(service, {'kind':'integrate','expression':'2*x'})['artifacts'] if a['type']=='text')


def test_reject_code_execution(service):
    with pytest.raises(RuntimeError):
        execute(service, {'kind':'simplify','expression':'__import__("os").system("id")'})


def test_reject_invalid_jobs_and_paths(service):
    with pytest.raises(ValueError):
        execute(service, {'kind':'simplify','expression':'x'}, job_id='../escape')
    with pytest.raises(ValueError):
        execute(service, {'kind':'python','code':'print(1)'})


def test_idempotent_job_and_collision(service):
    req={'kind':'plot','expression':'sin(x)'}
    first=execute(service, req, job_id='stable-1')
    second=execute(service, req, job_id='stable-1')
    assert first==second
    with pytest.raises(ValueError):
        execute(service, {'kind':'plot','expression':'cos(x)'}, job_id='stable-1')


def test_capability_schema():
    schema=capability_input_schema('math_workspace')
    assert 'kind' in schema['required']
    assert validate_capability_arguments('math_workspace', {'kind':'plot','expression':'x^2'})[0]
    assert not validate_capability_arguments('math_workspace', {'kind':'shell','expression':'hi'})[0]


def test_docker_runner_does_not_fallback(monkeypatch,tmp_path):
    from runtime.workspace.python_docker import DockerPythonWorkspace
    monkeypatch.setattr(DockerPythonWorkspace, 'available', staticmethod(lambda: False))
    runner=DockerPythonWorkspace(tmp_path)
    with pytest.raises(RuntimeError, match='Docker'):
        runner.execute('print(123)')


def test_docker_command_restricts_network_and_privileges(tmp_path):
    from runtime.workspace.python_docker import DockerPythonWorkspace
    runner=DockerPythonWorkspace(tmp_path)
    cmd=runner.command_for(tmp_path/'jobs'/'x')
    assert '--network' in cmd and cmd[cmd.index('--network')+1]=='none'
    assert '--read-only' in cmd and '--cap-drop' in cmd
    assert '--memory' in cmd and '--pids-limit' in cmd
    assert '--security-opt' in cmd
    assert '--mount' in cmd


def test_action_registry_contract_complete():
    from actions.capability_registry import validate_registry_contract_coverage,capability_definition
    ok,problems=validate_registry_contract_coverage()
    assert ok, problems
    assert capability_definition('math_workspace').callable
    assert capability_definition('python_workspace').callable
