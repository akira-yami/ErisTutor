# -*- coding: utf-8 -*-
"""Cửa kiểm chứng cơ học cho lời xã giao *không có factual claim*.

Đây KHÔNG phải bộ phân loại ngôn ngữ tự do. Chỉ những mẫu nguyên câu,
đã liệt kê tường minh, mới được nhận là lời xã giao không cần model verifier.
Không dựa vào ``claim_manifest=[]`` để tự kết luận câu nói an toàn:
phải xem lại đúng display, memory và từng đoạn speech cuối sau Stage/TTS.

Mọi câu khác, kể cả câu hỏi gài tiền đề, ký ức, hành động và câu chào có
thêm mệnh đề, sẽ tiếp tục đi qua semantic verifier như V1 hiện hành.
"""
from __future__ import annotations

import unicodedata
import re

from runtime.claim_domain_contract import ClaimManifestEnvelope
from .claim_verifier import ClaimVerificationResult
from .public_output_artifact import PreparedPublicOutput, tts_clean, validate_prepared


# Mẫu cực hẹp, KHÔNG chứa động từ diễn tả sự kiện như 'đã', 'nhớ', 'gửi',
# cũng không có dấu chấm lửng giữa các mệnh đề không rõ nghĩa.
_PHATIC_VI = frozenset({
    'chào', 'xin chào', 'chào cậu', 'chào bạn', 'chào nha', 'chào cậu nha',
    'chào cậu nè', 'chào cậu nhé', 'chào bạn nha', 'ơ chào cậu',
    'này chào cậu', 'hí chào cậu', 'hé lô', 'hê lô', 'hello', 'hi',
    'hey', 'halo', 'haloo', 'hallo', 'hí', 'ừm', 'hừm', 'hử',
    'ơ', 'à', 'hmm', 'hm', 'haha', 'hehe', 'hihi', 'hê hê',
})
_PHATIC_JA = frozenset({
    'こんにちは', 'やっほー', 'やっほ', 'おはよう', 'こんばんは',
    'はーい', 'はい', 'うん', 'ふふ', 'ふふっ', 'えへへ', 'えへ',
    'えっ', 'ねえ', 'あれ', 'もしもし', 'おっやっほー',
})


def _plain(text: str) -> str:
    """Chuẩn hóa chỉ để SO mẫu; tuyệt đối không đổi text đưa TTS."""
    value = unicodedata.normalize('NFKC', text).casefold().strip()
    if not value or len(value) > 96 or any(ch in value for ch in '\n\r\t'):
        return ''
    # Chỉ bỏ dấu câu/ký hiệu trang trí; chữ số, chữ và nội dung vẫn nguyên.
    # Nhờ so nguyên chuỗi với allowlist, 'chào cậu. tôi đã...' vẫn bị bác.
    return ' '.join(''.join(' ' if unicodedata.category(ch).startswith(('P', 'S'))
                            else ch for ch in value).split())


_PHATIC_JA_COMPACT = frozenset(
    ''.join(ch for ch in unicodedata.normalize('NFKC', item).casefold()
            if not unicodedata.category(ch).startswith(('P', 'S')))
    for item in _PHATIC_JA
)


def _is_phatic(text: str) -> bool:
    """Nhận diện một utterance toàn vẹn, không suy đoán từ một substring."""
    plain = _plain(text)
    if plain in _PHATIC_VI:
        return True
    # Tiếng Nhật không có khoảng cách giữa các từ; khoảng trắng tùy chọn
    # được bỏ chỉ khi toàn bộ còn lại khớp một mẫu cố định.
    compact = plain.replace(' ', '')
    return compact in _PHATIC_JA_COMPACT


def _manifest_can_be_format_only(envelope: ClaimManifestEnvelope) -> bool:
    """Không coi manifest có claim/sai schema với claim ẩn là 'rỗng'."""
    if envelope.structural_status == 'valid':
        return not envelope.normalized
    if envelope.structural_status == 'missing':
        return True
    # DS2API có thể trả literal "[]" thay vì JSON array. Chỉ đây và null
    # được coi là lỗi biểu diễn một *manifest rỗng*, không parse tùy ý.
    return envelope.structural_status == 'invalid' and (
        envelope.raw is None or envelope.raw == '[]'
    )


def can_normalize_empty_manifest(envelope: ClaimManifestEnvelope) -> bool:
    """Cấp phép chuẩn hóa tạm; sau prepare VẪN PHẢI chứng minh phatic."""
    return _manifest_can_be_format_only(envelope)


def certify_phatic(*, prepared: PreparedPublicOutput,
                   original_manifest: ClaimManifestEnvelope) -> ClaimVerificationResult | None:
    """Chỉ trả PASS khi độc lập chứng minh cả 3 surface không chứa fact.

    Câu 'Chào cậu, tớ đã gửi file' / tiếng Nhật chứa lời hứa sẽ không qua.
    Câu display an toàn nhưng memory bị thêm lịch sử cũng không qua.

    Verifier receipt được tạo y như semantic path, có method riêng để audit.
    """
    if not _manifest_can_be_format_only(original_manifest):
        return None
    validate_prepared(prepared)
    # Lời chào không được làm vỏ cho tool/action hoặc preset audio ngoài
    # transcript đã verify. Các cử chỉ Live2D thuần nonverbal vẫn được phép.
    decision = prepared.decision
    if any(getattr(decision, key, None) for key in (
        'system_action', 'action_input', 'capability_arguments', 'audio_asset',
        'action_result_meta',
    )):
        return None
    if any(prepared.payload.get(key) for key in (
        'system_action', 'action_input', 'tool_intent', 'tool_input',
        'action_result', 'self_capability_claims',
    )):
        return None
    if prepared.memory_text != prepared.display_text:
        return None
    if not _is_phatic(prepared.display_text):
        return None
    if not prepared.segments or len(prepared.segments) > 2:
        return None
    if any(not _is_phatic(tts_clean(segment.tts_text)) for segment in prepared.segments):
        return None
    if not _is_phatic(prepared.speech_text):
        return None
    return ClaimVerificationResult(
        status='pass', findings=(), surface_equivalence=True,
        memory_equivalence=True, reason_codes=('DETERMINISTIC_PHATIC',),
        verification_method='deterministic_phatic',
    )


# Rào phụ cho những khẳng định dễ gây hậu quả nếu Writer không khai claim.
# Chỉ chặn một tập cấu trúc RÕ RÀNG (hành động hoàn thành/lịch sử cá nhân),
# không giả làm hệ thống semantic classification toàn ngôn ngữ.
_SELF_ACTION = re.compile(
    r'(?i)(?:\b(?:tớ|tôi|mình|eris)\s+)?đã\s+'
    r'(?:gửi|đặt|mua|lưu|xóa|xoá|chuyển|gọi|thanh toán|đăng ký|kiểm tra)\b'
    r'|\bI\s+(?:sent|booked|bought|saved|deleted|called|paid)\b'
    r'|(?:送った|予約した|保存した|削除した|支払った)'
)
_SHARED_HISTORY = re.compile(
    r'(?i)\b(?:chúng ta|hai đứa|cậu và tớ)\s+(?:đã|từng)\b'
    r'|\b(?:cậu|bạn)\s+(?:từng|đã nói|đã bảo)\b'
    r'|\b(?:tớ|tôi|mình)\s+nhớ\s+(?:lần|hồi)\b'
    r'|\b(?:we (?:met|went|used to)|you (?:said|told me)|I remember when)\b'
    r'|(?:一緒に行った|前に会った|君が言った|あなたが言った)'
)


def undeclared_high_risk_claims(*, prepared: PreparedPublicOutput,
                               manifest: ClaimManifestEnvelope) -> tuple[str, ...]:
    """Đòi nguồn thật với dấu hiệu quá khứ/action rõ; không tin [] của model.

    Phát hiện sơ bộ là bảo thủ, không khẳng định phát hiện toàn bộ claim.
    Manifest có domain+ref liên quan vẫn cần typed gate và semantic verifier.
    """
    declared = tuple(manifest.normalized) if manifest.structural_status == 'valid' else ()
    surfaces = (prepared.display_text, prepared.memory_text,
                *(tts_clean(segment.tts_text) for segment in prepared.segments))
    issues = []
    if any(_SELF_ACTION.search(part) for part in surfaces):
        if not any(entry['domain'] == 'self_capability' and entry['evidence_refs']
                   for entry in declared):
            issues.append('UNDECLARED_SELF_ACTION')
    if any(_SHARED_HISTORY.search(part) for part in surfaces):
        if not any(entry['domain'] in (
            'shared_episode', 'conversation_history', 'user_statement', 'user_profile',
        ) and entry['evidence_refs'] for entry in declared):
            issues.append('UNDECLARED_SHARED_HISTORY')
    return tuple(issues)


__all__ = ['can_normalize_empty_manifest', 'certify_phatic', 'undeclared_high_risk_claims']
