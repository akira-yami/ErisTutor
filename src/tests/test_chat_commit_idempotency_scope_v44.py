from types import SimpleNamespace

from runtime.ai_orchestrated.orchestrator import _chat_commit_idempotency_key


def test_chat_commit_key_is_stable_inside_one_logical_turn() -> None:
    host = SimpleNamespace(
        runtime_session_id="runtime-100",
        _active_public_lifecycle_turn_id="chat:1700000000000",
    )

    first = _chat_commit_idempotency_key(host, "session_turn:4")
    second = _chat_commit_idempotency_key(host, "session_turn:4")

    assert first == second
    assert "runtime-100" in first
    assert "chat:1700000000000" in first


def test_chat_commit_key_changes_across_runtime_restarts() -> None:
    first_host = SimpleNamespace(
        runtime_session_id="runtime-100",
        _active_public_lifecycle_turn_id="chat:1700000000000",
    )
    second_host = SimpleNamespace(
        runtime_session_id="runtime-200",
        _active_public_lifecycle_turn_id="chat:1700000000000",
    )

    assert _chat_commit_idempotency_key(first_host, "session_turn:4") != (
        _chat_commit_idempotency_key(second_host, "session_turn:4")
    )


def test_chat_commit_key_changes_for_new_user_message() -> None:
    host = SimpleNamespace(runtime_session_id="runtime-100")
    host._active_public_lifecycle_turn_id = "chat:1700000000000"
    first = _chat_commit_idempotency_key(host, "session_turn:4")

    host._active_public_lifecycle_turn_id = "chat:1700000005000"
    second = _chat_commit_idempotency_key(host, "session_turn:4")

    assert first != second
