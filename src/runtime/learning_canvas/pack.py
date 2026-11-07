"""Bounded, hand-authored knowledge packs, validated before any registration."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.learning_core.catalog import validate_path
from runtime.learning_core.contracts import KnowledgePoint, LearningPath
from runtime.learning_core.records import DomainSpec, identifier


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid_pack_text")
    return value


def _items(value: Any, limit: int) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= limit:
        raise ValueError("invalid_pack_list")
    return value


@dataclass(frozen=True)
class KnowledgePack:
    group_id: str
    domain: DomainSpec
    path: LearningPath
    _nodes: tuple[dict, ...]

    @property
    def nodes(self) -> tuple[dict, ...]:
        return copy.deepcopy(self._nodes)

    def node(self, node_id: str) -> dict:
        for node in self._nodes:
            if node["id"] == node_id:
                return copy.deepcopy(node)
        raise ValueError("unknown_canvas_node")

    def detail(self, node_id: str) -> dict:
        node = self.node(node_id)
        # Answers, hidden harnesses and test expectations stay on the backend.
        node["exercises"] = [
            {key: exercise[key] for key in ("id", "type", "prompt")}
            for exercise in node["exercises"]
        ]
        return node


def validate_pack(raw: Any) -> KnowledgePack:
    if not isinstance(raw, dict) or raw.get("schema") != "eris.learning.pack.v1":
        raise ValueError("invalid_pack_schema")
    group_id = identifier(raw.get("group_id"))
    if raw.get("version") != "1":
        raise ValueError("unsupported_pack_version")
    namespace = identifier(raw.get("skill_namespace"))
    title = _text(raw.get("title"))
    domain = DomainSpec(group_id, title, namespace, version="1", capabilities=("choice", "java", "python"))
    path_id = identifier(raw.get("path_id"))
    if not path_id.startswith(namespace + ":"):
        raise ValueError("pack_namespace_mismatch")
    nodes = copy.deepcopy(_items(raw.get("nodes"), 16))
    ids: set[str] = set()
    points = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("invalid_pack_node")
        node_id = identifier(node.get("id"))
        if not node_id.startswith(namespace + ":"):
            raise ValueError("pack_namespace_mismatch")
        if node_id in ids:
            raise ValueError("duplicate_pack_id")
        ids.add(node_id)
        _text(node.get("title"))
        _text(node.get("summary"), 500)
        prerequisites = node.get("prerequisites")
        if not isinstance(prerequisites, list) or len(prerequisites) > 16:
            raise ValueError("invalid_pack_prerequisites")
        for prerequisite in prerequisites:
            identifier(prerequisite)
        if len(set(prerequisites)) != len(prerequisites):
            raise ValueError("duplicate_prerequisite")
        points.append(KnowledgePoint(node_id, node["title"], description=node["summary"],
                                     prerequisites=tuple(prerequisites)))
        for block in _items(node.get("blocks"), 8):
            if not isinstance(block, dict):
                raise ValueError("invalid_pack_block")
            block_id = identifier(block.get("id"))
            if block_id in ids:
                raise ValueError("duplicate_pack_id")
            ids.add(block_id)
            if block.get("kind") not in {"explanation", "java", "python"}:
                raise ValueError("invalid_pack_block_kind")
            _text(block.get("title"))
            _text(block.get("text"), 3000)
        kinds = {block["kind"] for block in node["blocks"]}
        if kinds not in ({"explanation", "java"}, {"explanation", "python"}, {"explanation", "java", "python"}):
            if "python" in kinds:
                raise ValueError("pack_requires_explanation_and_python")
            raise ValueError("pack_requires_explanation_and_java")
        for exercise in _items(node.get("exercises"), 4):
            if not isinstance(exercise, dict):
                raise ValueError("invalid_pack_exercise")
            exercise_id = identifier(exercise.get("id"))
            if exercise_id in ids:
                raise ValueError("duplicate_pack_id")
            ids.add(exercise_id)
            _text(exercise.get("prompt"), 1000)
            _text(exercise.get("hint"), 1000)
            if exercise.get("type") == "multiple_choice":
                options = _items(exercise.get("options"), 6)
                if len(options) < 2:
                    raise ValueError("invalid_choice_options")
                keys = []
                for option in options:
                    if not isinstance(option, dict):
                        raise ValueError("invalid_choice_option")
                    keys.append(identifier(option.get("id")))
                    _text(option.get("text"), 500)
                if len(set(keys)) != len(keys) or exercise.get("answer") not in keys:
                    raise ValueError("invalid_choice_answer")
                _text(exercise.get("explanation"), 1000)
            elif exercise.get("type") in {"java", "python"}:
                is_py = exercise.get("type") == "python"
                if not is_py:
                    _text(exercise.get("class_name"), 80)
                _text(exercise.get("solution"), 6000)
                _text(exercise.get("starter_code"), 3000)
                _text(exercise.get("harness"), 6000)
                tests = _items(exercise.get("tests"), 8)
                keys = []
                for test in tests:
                    if not isinstance(test, dict):
                        raise ValueError("invalid_python_test" if is_py else "invalid_java_test")
                    keys.append(identifier(test.get("id")))
                    _text(test.get("description"), 500)
                if len(set(keys)) != len(keys):
                    raise ValueError("duplicate_python_test" if is_py else "duplicate_java_test")
            else:
                raise ValueError("invalid_pack_exercise_type")
    path = LearningPath(path_id, title, _text(raw.get("goal"), 500), tuple(points),
                        source="hand_authored", domain_id=group_id)
    validate_path(path)
    pack = KnowledgePack(group_id, domain, path, tuple(nodes))
    # A whole node must fit in one UDP response; reject rather than truncate it.
    for node in nodes:
        if len(json.dumps(pack.detail(node["id"]), ensure_ascii=False).encode("utf-8")) > 24000:
            raise ValueError("pack_node_payload_too_large")
    return pack


def load_pack(path: Path | None = None) -> KnowledgePack:
    source = path or Path(__file__).with_name("packs") / "java-di.json"
    if source.stat().st_size > 256000:
        raise ValueError("pack_payload_too_large")
    return validate_pack(json.loads(source.read_text(encoding="utf-8")))


def register_pack(learning, pack: KnowledgePack) -> None:
    """Idempotent registration; never activate a path or manufacture evidence."""
    learning.register_domain(pack.domain)
    learning.register_path(pack.path)
