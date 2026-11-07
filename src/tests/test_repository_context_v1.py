from pathlib import Path

from runtime.agent_harness.repository_context import RepositoryContextManager
from runtime.agent_harness.context_policy import context_gate_reason, requires_verified_change


def _demo_repo(root: Path) -> None:
    (root / "src" / "cortex").mkdir(parents=True)
    (root / "src" / "context").mkdir(parents=True)
    (root / "src" / "cortex" / "self_model.py").write_text(
        "import json\nclass SelfModel:\n    def observe(self):\n        return json.dumps({})\n",
        encoding="utf-8",
    )
    (root / "src" / "cortex" / "motive_graph.py").write_text(
        "from .self_model import SelfModel\ndef build_graph():\n    return SelfModel()\n",
        encoding="utf-8",
    )
    (root / "src" / "context" / "bridge.py").write_text("def bridge():\n    return True\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo repo\n", encoding="utf-8")


def test_repo_context_indexes_full_tree_and_resolves_cotex_typo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _demo_repo(root)
    manager = RepositoryContextManager(tmp_path / "cache")

    ctx = manager.ensure(root, "tạo file txt tóm tắt tác dụng các file trong src/cotex")

    assert ctx["ready"] is True
    assert ctx["stats"]["files"] == 4
    assert ctx["stats"]["source_files"] == 3
    assert ctx["stats"]["symbols"] >= 4
    assert ctx["resolved_targets"][0]["path"] == "src/cortex"
    assert set(ctx["target_files"]) == {
        "src/cortex/self_model.py",
        "src/cortex/motive_graph.py",
    }
    assert ctx["confidence"] >= 0.9
    assert "Indexed full inventory: 4 files" in ctx["context_pack"]


def test_repo_context_reuses_unchanged_files_incrementally(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _demo_repo(root)
    manager = RepositoryContextManager(tmp_path / "cache")

    first = manager.ensure(root, "inspect cortex")
    second = manager.ensure(root, "inspect cortex")
    assert first["stats"]["changed"] == 4
    assert second["stats"]["changed"] == 0
    assert second["stats"]["reused"] == 4

    target = root / "src" / "cortex" / "self_model.py"
    target.write_text(target.read_text(encoding="utf-8") + "\ndef reconcile():\n    return True\n", encoding="utf-8")
    third = manager.ensure(root, "inspect cortex")
    assert third["stats"]["changed"] == 1
    assert third["stats"]["reused"] == 3
    assert "reconcile" in third["context_pack"]


def test_context_gate_requires_repo_and_exact_read_before_modifying_existing_file(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _demo_repo(root)
    patch = """--- a/src/cortex/self_model.py\n+++ b/src/cortex/self_model.py\n@@ -1,1 +1,1 @@\n-import json\n+import json as jsonlib\n"""
    state = {
        "workspace_path": str(root),
        "context_ready": True,
        "resolved_targets": [{"path": "src/cortex", "kind": "directory", "score": 3.0}],
    }

    no_read = context_gate_reason("project_apply_patch", {"patch": patch}, state=state, inspection_evidence=[])
    assert "Chưa có inspection evidence" in no_read

    wrong_read = context_gate_reason(
        "project_apply_patch",
        {"patch": patch},
        state=state,
        inspection_evidence=[{"tool": "project_read_file", "path": "src/context/bridge.py"}],
    )
    assert "chưa chạm vùng task" in wrong_read

    right_read = context_gate_reason(
        "project_apply_patch",
        {"patch": patch},
        state=state,
        inspection_evidence=[{"tool": "project_read_file", "path": "src/cortex/self_model.py"}],
    )
    assert right_read == ""


def test_completion_gate_detects_mutation_intent_without_matching_plain_review():
    assert requires_verified_change("tạo file txt tóm tắt cortex") is True
    assert requires_verified_change("sửa bug context bridge rồi chạy test") is True
    assert requires_verified_change("refactor the extension") is True
    assert requires_verified_change("đánh giá repo eris và giải thích kiến trúc") is False



