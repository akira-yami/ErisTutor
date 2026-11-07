"""Foundation-layer tests.

Scope: the modules owned by the feat/foundation branch — domain, transport,
failure_meta, secure_redaction.
"""


def test_decision_to_payload_minimal():
    from domain import Decision

    d = Decision(text="hello", animation="stand")
    payload = d.to_payload()
    assert payload["text"] == "hello"
    assert payload["animation"] == "stand"
    assert payload["should_speak"] is True


def test_decision_filler_text_is_silenced():
    from domain import Decision

    d = Decision(text="...", animation="stand", should_speak=True)
    payload = d.to_payload()
    # Filler-only utterances are blanked and marked not-speakable.
    assert payload["text"] == ""
    assert payload["should_speak"] is False


def test_failure_meta_roundtrip_and_normalize():
    from failure_meta import build_failure_meta, normalize_failure_meta

    meta = build_failure_meta(kind="rate_limited", stage="llm", origin="provider")
    assert meta["kind"] == "rate_limited"
    # Unknown kinds fall back to "unknown".
    assert build_failure_meta(kind="bogus")["kind"] == "unknown"
    assert normalize_failure_meta({"kind": "safety_block"})["kind"] == "safety_block"
    assert normalize_failure_meta("not-a-dict") is None


def test_secure_redactor_hides_api_key():
    from runtime.secure_redaction import GLOBAL_SECURE_REDACTOR

    result = GLOBAL_SECURE_REDACTOR.text("my key is sk-ABCDEFGH1234567890 ok")
    assert "sk-ABCDEFGH1234567890" not in result.value
    assert result.blocked_kinds  # at least one redaction fired


def test_udp_payload_sanitize_strips_surrogates():
    from transport.udp import _sanitize_udp_payload

    cleaned = _sanitize_udp_payload({"text": "ok\ud800bad"})
    # Lone surrogate must be stripped so json.dumps + sendto never fail.
    assert "\ud800" not in cleaned["text"]
