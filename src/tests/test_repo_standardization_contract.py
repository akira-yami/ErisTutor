from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_test_runner_targets_src_tests() -> None:
    runner = ROOT / "run_tests.py"
    source = runner.read_text(encoding="utf-8")

    assert '"src/tests/test_runtime_readiness_v18.py"' in source
    assert 'pytest", "src/tests/"' in source
    assert '"tests/' not in source


def test_pytest_collects_only_project_tests() -> None:
    config = (ROOT / "pytest.ini").read_text(encoding="utf-8")

    assert re.search(r"(?m)^testpaths\s*=\s*src/tests\s*$", config)
    assert re.search(r"(?m)^pythonpath\s*=\s*src\s+\.\s*$", config)
    assert "portable_python" in config
    assert "tools/reference-repos" in config


def test_root_pyproject_is_packaging_source_of_truth() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_finder = payload["tool"]["setuptools"]["packages"]["find"]

    assert package_finder["where"] == ["src"]
    assert "agent" in payload["project"]["optional-dependencies"]
    assert "semantic" in payload["project"]["optional-dependencies"]
    assert not (ROOT / "src" / "pyproject.toml").exists()
    assert not (ROOT / "src" / "pytest.ini").exists()


def test_no_local_runtime_credentials_are_tracked() -> None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = set(result.stdout.splitlines())

    forbidden = {
        ".v57_runs/v57-20260812-172540-69a257/subject_core/capability_keyring/keyring.json",
        ".v57_runs/v57-20260812-172540-69a257/subject_core/capability_keyring/keys/local-hmac-v1.key",
        "data/subject_core/capability_keyring/keyring.json",
        "data/subject_core/capability_keyring/keys/local-hmac-v1.key",
        "tools/whale2api/accounts.password.translation",
    }
    assert tracked.isdisjoint(forbidden)
