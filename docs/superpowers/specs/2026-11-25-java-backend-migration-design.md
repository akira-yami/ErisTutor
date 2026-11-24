# ErisTutor Java 27 Backend Migration Design (Option 3)

**Date:** 2026-11-25  
**Target Stack:** Java 27 · Spring Boot 4.1.3 · Maven · PostgreSQL 16 · Flyway · Redis 7 · Resilience4j · Springdoc OpenAPI 3 · Docker Sandbox  
**Migration Strategy:** Strangler Fig (incremental extraction preserving ErisTutor Live2D Desktop 100%)

---

## 1. Intent & Career Positioning

ErisTutor's existing codebase is a ~304k-LOC Python desktop cognitive runtime paired with a C#/.NET 8 Godot Live2D body and HTML/JS RichChat & Learning Canvas UI. Rewriting the entire Windows desktop cognitive/audio/UIA runtime into Java is high-risk and unnecessary. Instead, **Option 3** extracts the three high-value, backend-intensive domains into a clean **Java 27 Modular Monolith** (`backend-java/`):

1. **Deterministic Learning Core (`com.eristutor.learning`)**: Concept DAG validation, CAS revisioning, Append-only Assessment & Evidence Ledger, SHA-256 idempotency, Correction chain (`superseded_by`), Forget tombstones, Recency-weighted Mastery Engine, and SM-2 Review Scheduler.
2. **Async Sandboxed Java Code Judge (`com.eristutor.judge`)**: Virtual-Thread-backed worker pool with `Semaphore` backpressure, hardened Docker JDK 21 container isolation (`--network none`, `--read-only`, `--cap-drop ALL`, `--pids-limit 64`, bounded 16KB stream reading, guaranteed cleanup), SSE progress stream, and automatic attempt commit into Learning Core.
3. **Resilient AI Gateway & RichChat Cloud Portal (`com.eristutor.ai`, `com.eristutor.portal`)**: Multi-user DB-backed replacement for `cloud_server.py` serving `src/body/richchat/`, Resilience4j Circuit Breaker/Retry failover (`DeepSeek -> Gemini -> Deterministic Sandbox Fallback`), Token-Bucket Rate Limiter (Redis in `prod`, in-memory in `dev`/`test`), and read-only learner context injection.
4. **Enterprise Cross-Cutting Layer (`com.eristutor.security`, `com.eristutor.common`)**: Spring Security 6 + JWT (Access & Refresh tokens) + `X-Eris-Desktop-Key` service principal, RFC 7807 `ProblemDetail` error handling, `X-Request-Id` MDC correlation, Actuator + Prometheus metrics, OpenAPI 3 / Swagger UI.

## 2. Approaches Considered

| Approach | Scope | Trade-off | Decision |
| :--- | :--- | :--- | :--- |
| Option 1 | Learning Core + Java Code Judge | Focused scope, leaves `cloud_server.py` in Python | Not chosen |
| Option 2 | Java Code Judge only | Fastest, minimal backend depth for CV | Not chosen |
| **Option 3** | **Learning Core + Java Judge + Resilient AI RichChat Portal + Live2D Adapter** | Full-stack Java backend showcase (ACID ledger, concurrency, Docker sandbox, Resilience4j, Security, SSE) while preserving Live2D Desktop | **Selected** |

## 3. Non-Goals & Architectural Invariants

* **Zero disruption to Live2D Desktop**: Godot C# (`src/body/*.cs`), Live2D (`src/live2d_body/`), Cortex (`src/cortex/`), Memory (`src/memory/`), TTS (`src/runtime/tts_engine.py`), Win32 native (`src/native/`), and static `src/body/richchat/` files remain untouched.
* **D04 Invariants Preserved**:
  * *"LLM proposes; deterministic kernel commits"*: AI Gateway reads learner context read-only and never directly mutates mastery.
  * **Idempotency**: Duplicate `attemptId` with matching canonical SHA-256 fingerprint replays cleanly without duplicating evidence; conflicting payload raises `409 Conflict`.
  * **Anti-fake-mastery**: `hinted`, `answer_revealed`, and `partial` attempts never count as independent mastery evidence.
  * **Correction & Tombstones**: Corrections preserve original `recordedAt` via `superseded_by`; forget tombstones block replay resurrection and recompute mastery deterministically.
  * **CAS Curriculum Revisioning**: Stale `expectedRevision` raises `409 Conflict`.

## 4. Live2D Desktop Integration (Strangler Fig Adapter)

Python desktop runtime connects via `src/runtime/java_backend_client.py` (stdlib-only HTTP client) hooked cleanly at the two single composition seams:
* `src/runtime/learning_canvas/exercises.py` -> `RemoteJavaJudgeWorkspace` (delegates Java grading when `ERIS_JAVA_BACKEND_URL` is configured, falls back to local `DockerJavaWorkspace`).
* `src/runtime/tutor/composition.py` -> `JavaLearningMirrorProxy` (mirrors Learning Core mutations when `ERIS_JAVA_LEARNING_MODE=mirror`, keeping local responsiveness and offline safety).
