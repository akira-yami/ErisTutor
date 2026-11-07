# -*- coding: utf-8 -*-
import time
from types import SimpleNamespace

from memory.living_user.models import ProfileRecord
from memory.living_user.extractor import UserProfileExtractor
from memory.memory_admission import MemoryAdmissionPolicy


def test_model_owns_conditional_preference_meaning_not_keyword_veto():
    value = "Khi được yêu cầu đọc một câu, giữ nguyên câu đó."
    adapter = SimpleNamespace(propose_profile_operations=lambda payload: {"operations":[{
        "op":"add", "path":"communication.response_rules", "value":value,
        "confidence":0.9, "explicitness":"explicit",
    }]})
    extractor = UserProfileExtractor(model_adapter=adapter)
    ops = extractor.from_turn("Đó là quy tắc tôi muốn giữ lâu dài", evidence_ids=("u1",))
    assert len(ops) == 1
    assert ops[0].value == value
    record = ProfileRecord(id="p1", path=ops[0].path, value=value, explicitness="explicit", evidence_ids=("u1",))
    assert MemoryAdmissionPolicy().decide_profile_record(record).admitted


def test_no_model_never_generates_keyword_preferences():
    extractor = UserProfileExtractor()
    assert extractor.from_turn("Từ giờ trả lời ngắn gọn, gửi ZIP, tôi đang học C#", evidence_ids=("u1",)) == ()


def test_profile_boundary_still_requires_evidence_and_honors_expiry():
    policy = MemoryAdmissionPolicy()
    record = ProfileRecord(id="p1", path="preferences.style", value="ngắn", explicitness="explicit")
    assert not policy.decide_profile_record(record).admitted
    record.evidence_ids = ("u1",)
    record.expires_at = time.time() - 1
    assert not policy.decide_profile_record(record).admitted
