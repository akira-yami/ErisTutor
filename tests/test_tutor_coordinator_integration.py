# -*- coding: utf-8 -*-
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath("src"))

from domain import Decision
from runtime.coordinator import RuntimeCoordinator


class MockTransport:
    def __init__(self):
        self.sent_messages = []

    def send_json(self, payload):
        self.sent_messages.append(payload)
        return True


class MockClient:
    def create_chat_completion(self, messages, **kwargs):
        # Return a valid structured response or dialogue
        response_format = kwargs.get("response_format")
        if response_format and response_format.get("type") == "json_schema":
            name = response_format.get("json_schema", {}).get("name", "")
            if "step" in name or "root" in name:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"action": "say", "say": "Chào bạn! Mình có thể giúp gì cho bạn hôm nay?", "teaching_move": null}'
                            }
                        }
                    ]
                }
        return {
            "choices": [
                {
                    "message": {
                        "content": "Chào bạn! Eris đây, mình cùng bắt đầu nhé."
                    }
                }
            ]
        }


def test_coordinator_tutor_and_kernel_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_HOME", str(tmp_path / ".eris_home"))

    transport = MockTransport()
    client = MockClient()
    llm_service = SimpleNamespace(
        client=client,
        companion_client=client,
        surface_client=client,
        learner_client=client,
        executive_client=client,
    )
    logger = MagicMock()

    parser = MagicMock()
    parser.parse.side_effect = lambda raw, **kwargs: Decision(
        text=json.loads(raw).get("text", "") if (isinstance(raw, str) and raw.startswith("{")) else str(raw),
        animation="none",
    )

    coord = RuntimeCoordinator(
        logger=logger,
        transport=transport,
        llm_service=llm_service,
        parser=parser,
        telemetry_service=MagicMock(),
        memory_core=SimpleNamespace(living_user=None),
        body_running_fn=lambda: True,
    )

    # 1. Verify Tutor Executive and Capabilities are bound
    assert coord.learning_service is not None
    assert coord.tutor_executive is not None
    assert coord.tutor_capability is not None

    # 2. Verify Conversation Kernel & Orchestrators are bound
    assert coord.conversation_kernel is not None
    assert coord.ai_runtime_enabled is True
    assert coord.ai_chat_orchestrator is not None
    assert coord.ai_autonomous_orchestrator is not None

    # 3. Test Inbound Dispatch for Chat Event
    result = coord.incoming_dispatcher.dispatch({
        "type": "chat",
        "message": "Chào Eris, hôm nay học gì thế?",
        "timestamp": 1000.0,
    })

    assert result.handled is True
    assert len(coord.chat_history) >= 2
    assert coord.chat_history[-2]["role"] == "user"
    assert coord.chat_history[-1]["role"] == "assistant"
    assert coord.last_conversation_run is not None
    assert coord.last_conversation_run["status"] in {"delivering", "delivered", "completed", "speculative_ready"}
    # Transport should have received the response for Godot
    assert len(transport.sent_messages) > 0
