from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class FakeProvider:
    replies: list[str] = field(default_factory=list)
    failures: list[BaseException] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate(self, prompt: str, *, context: Mapping[str, Any] | None = None) -> str:
        self.calls.append({"prompt": prompt, "context": dict(context or {})})
        if self.failures:
            raise self.failures.pop(0)
        if self.replies:
            return self.replies.pop(0)
        return "Đã ghi nhận."


@dataclass(slots=True)
class FakeTransport:
    messages: list[dict[str, Any]] = field(default_factory=list)

    def send_json(self, payload: Mapping[str, Any]) -> None:
        self.messages.append(dict(payload))


@dataclass(slots=True)
class FakePersistence:
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    fail_next: BaseException | None = None

    def save(self, payload: Mapping[str, Any]) -> None:
        if self.fail_next is not None:
            error = self.fail_next
            self.fail_next = None
            raise error
        self.snapshots.append(dict(payload))

    @property
    def latest(self) -> dict[str, Any]:
        return dict(self.snapshots[-1]) if self.snapshots else {}
