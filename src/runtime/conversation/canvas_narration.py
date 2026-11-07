"""Writer composition for Root-tagged Canvas parts; ordinary moves are unchanged."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

from .delivery import SurfaceEnvelope
from .public_output_artifact import reseal_prepared, validate_prepared, tts_clean
from .writer import RenderedConversationMove


def bind_prepared(prepared, scope, pack, *, block_id=""):
    if not scope:
        return prepared
    try:
        allowed = {b["id"] for b in pack.node(scope["node_id"])["blocks"]}
    except ValueError:
        return prepared
    binding = {**scope, "generation": prepared.generation, "block_id": block_id if block_id in allowed else ""}
    frames = tuple(replace(f, delivery={**f.delivery, "learning_canvas": binding}) for f in prepared.segments)
    return reseal_prepared(replace(prepared, segments=frames))


def realize_canvas(writer, *, turn, event, recent_history, runtime_context, parts):
    outputs = []
    for part in parts:
        kernel = getattr(writer.host, "conversation_kernel", None)
        if kernel is not None and kernel.snapshot(turn.session_id).active_run_id != turn.run_id:
            raise RuntimeError("v03_canvas_focus_changed")
        child = replace(turn, move=replace(turn.move, say=part["say"], canvas_segments=()))
        result = writer._realize_one(turn=child, event=event, recent_history=recent_history, runtime_context=runtime_context)
        prepared = result.metadata.get("prepared_public_output")
        if prepared is None:
            raise RuntimeError("v03_canvas_requires_prepared_output")
        prepared = bind_prepared(prepared, turn.canvas_scope, writer.host.learning_canvas_service.pack, block_id=part["block_id"])
        outputs.append((result, prepared))
    first, base = outputs[0]
    frames = tuple(replace(frame, segment_id=i) for i, frame in enumerate(
        frame for _, output in outputs for frame in output.segments))
    display = "\n\n".join(output.display_text for _, output in outputs)
    memory = "\n\n".join(output.memory_text for _, output in outputs)
    speech = " ".join(tts_clean(frame.tts_text) for frame in frames)
    decision = copy.copy(base.decision)
    decision.text, decision.speech_text = display, speech
    root_frame = replace(base.stage_frame, display_text=display, memory_text=memory, tts_text=speech)
    payload = {**base.payload, "text": display, "memory_text": memory, "speech_text": speech}
    payload.update(root_frame.to_payload_patch())
    payload["stage"]["segment_count"] = len(frames)
    from transport.udp import MAX_UDP_PAYLOAD_BYTES, _sanitize_udp_payload
    if len(json.dumps(_sanitize_udp_payload(payload), ensure_ascii=False).encode("utf-8")) > MAX_UDP_PAYLOAD_BYTES:
        raise RuntimeError("v03_udp_payload_overflow")
    combined = reseal_prepared(replace(base, decision=decision, display_text=display, memory_text=memory,
        speech_text=speech, stage_frame=root_frame, segments=frames, payload=payload))
    validate_prepared(combined)
    return RenderedConversationMove(surface=SurfaceEnvelope(display_text=display, speech_text=speech,
        language=first.surface.language), decision_payload=turn.move.to_dict(),
        metadata={**first.metadata, "prepared_public_output": combined})
