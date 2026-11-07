# Boundary của `learning_core` trong Eris

`runtime/learning_core/` là **learning kernel deterministic**, không phải một agent thứ hai.

## Kernel được quyền sở hữu

- `LearningEvidence`
- `MasteryRecord`
- mastery/gate computation
- `LearningPath` / `KnowledgePoint`
- pending interaction ledger
- deterministic choice grading
- review scheduler / retrievability
- `TutorStep` navigation policy

## Kernel không được sở hữu

- foreground agent loop
- quyết định có nên kích hoạt tutor trong cuộc trò chuyện hay không
- public writer
- TTS / voice pacing
- cách chia một câu trả lời thành nhiều lượt hội thoại
- social/personality behavior
- tool/coding/research execution

## Luồng đúng

```text
TurnAgentLoop
    ↓
TutorRole / TutorReasoner
    ↓ đề xuất pedagogical intent
ConversationDirector
    ↓ quyết định nhịp nói
Public writer / TTS
    ↓ user phản hồi
Semantic assessor
    ↓ đề xuất evidence
LearningService
    ↓ deterministic commit/recompute
LearningStore
```

Quy tắc ngắn gọn: **LLM đề xuất; kernel commit.**
