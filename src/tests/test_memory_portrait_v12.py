from __future__ import annotations

from types import SimpleNamespace

from memory.living_user import LivingUserMemoryService, MemoryPortraitPresenter
from runtime.living_user_panel import build_memory_expression_lens, build_memory_panel_snapshot


class _Client:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.content}}]}


def test_memory_portrait_uses_surface_writer_and_drops_unsupported_items():
    client = _Client(
        '{'
        '"opening":"Bạn đúng là khó chiều thật đấy… nhưng tôi nhớ chuyện đó.",'
        '"aside":"…m-mong là bạn không soi kỹ.",'
        '"sections":[{'
        '"kind":"how_i_see_you","title":"Trong mắt tôi","items":['
        '{"title":"Khó tính","body":"Bạn không thích thứ làm cho xong.","aside":"Hừm…","source_refs":["mem:communication.response_style:global"]},'
        '{"title":"Bịa","body":"Bạn thích sao Hỏa.","aside":"","source_refs":["mem:preferences.mars:global"]}'
        ']}]}'
    )
    service = SimpleNamespace(surface_client=client)
    presenter = MemoryPortraitPresenter(service, client=client)

    result = presenter.render(
        records=[{
            "path": "communication.response_style",
            "scope_key": "",
            "category": "communication",
            "label": "Cách bạn muốn ERIS trả lời",
            "display_value": "có quan điểm riêng, không làm cho xong",
            "explicitness": "explicit",
            "confidence": 0.95,
        }],
        expression_lens={"playfulness": "được chọc nhẹ một nhịp"},
        force=True,
    )

    assert result.renderer == "deepseek_surface"
    assert result.opening.startswith("Bạn đúng là")
    assert len(result.sections) == 1
    assert len(result.sections[0].items) == 1
    assert result.sections[0].items[0].title == "Khó tính"
    assert client.calls
    system = client.calls[0]["messages"][0]["content"]
    assert "SURFACE WRITER" in system
    assert "spam tiếng Nhật" in system


def test_memory_expression_lens_never_exports_raw_thought_seed():
    relation = SimpleNamespace(
        familiarity=0.72,
        trust=0.70,
        preferred_distance=0.30,
        attachment_visibility=0.44,
        exposure_recoil=0.28,
        dignity_defense=0.10,
        user_tolerates_strangeness=0.66,
    )
    state = SimpleNamespace(
        relationship_dynamics=relation,
        attachment=0.62,
        tenderness=0.58,
        playful_pressure=0.55,
        restraint_pressure=0.24,
        irritation=0.08,
        affective_state={"embarrassment": 0.52},
        last_thought_seed="RAW SECRET THOUGHT MUST NEVER LEAK",
        recent_thought_seeds=("also private",),
    )

    lens = build_memory_expression_lens(state)
    blob = repr(lens)
    assert "RAW SECRET" not in blob
    assert "recent_thought" not in blob
    assert "đã khá quen" in lens["relationship"]
    assert "ngượng" in lens["self_exposure"]


def test_memory_panel_v5_puts_character_portrait_in_front(tmp_path):
    memory = LivingUserMemoryService(tmp_path)
    memory.correct(
        "preferences.music_rating_rule",
        "5 sao là bài nghe lặp lại nhiều lần vẫn không chán",
        evidence_ids=("turn_1",),
    )

    class Presenter:
        def render(self, **kwargs):
            assert kwargs["records"][0]["path"] == "preferences.music_rating_rule"
            return {
                "opening": "Cái này tôi nhớ khá rõ. Bạn khó cho 5 sao hơn vẻ ngoài đấy.",
                "aside": "…đừng bắt tôi kiểm tra playlist nhé.",
                "sections": [{
                    "kind": "things_you_like",
                    "title": "Mấy thứ bạn thích",
                    "items": [{
                        "title": "Luật 5 sao",
                        "body": "Hay thôi chưa đủ; phải nghe lại mãi vẫn không chán.",
                        "aside": "",
                        "source_refs": ["mem:preferences.music_rating_rule:global"],
                    }],
                }],
                "renderer": "test_surface",
            }

    snapshot = build_memory_panel_snapshot(memory, portrait_presenter=Presenter())
    assert snapshot["schema_version"] == 4
    assert snapshot["summary_text"].startswith("Cái này tôi nhớ")
    assert snapshot["portrait"]["sections"][0]["kind"] == "things_you_like"
    # Canonical truth vẫn còn cho mode Quản lý; prose không thay thế record.
    assert snapshot["records"][0]["display_value"] == "5 sao là bài nghe lặp lại nhiều lần vẫn không chán"
