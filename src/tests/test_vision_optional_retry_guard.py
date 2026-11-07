from __future__ import annotations

from contextlib import contextmanager

from cortex.vision_semantic_compressor import RemoteCaptionClient
import runtime.inference_broker as inference_broker


class _VisionClient:
    model_name = "test-vision"

    def __init__(self) -> None:
        self.calls = 0

    def create_chat_completion(self, **_kwargs):
        self.calls += 1
        return {"choices": [{"message": {"content": "should not be reached"}}]}


def test_optional_broker_rejection_is_not_retried(monkeypatch):
    entries = 0

    @contextmanager
    def rejected_context(*_args, **_kwargs):
        nonlocal entries
        entries += 1
        raise inference_broker.InferenceRejected("shed_optional_user_active")
        yield  # pragma: no cover

    monkeypatch.setattr(inference_broker, "inference_context", rejected_context)
    client = _VisionClient()
    captioner = RemoteCaptionClient(client)

    assert captioner.caption_image("ZmFrZS1wbmc=") == ""
    assert entries == 1
    assert client.calls == 0
