# ErisTutor Java 27 Backend (`backend-java/`)

> **Modular Monolith** built with **Java 27** + **Spring Boot 4.1.3** implementing the Strangler Fig extraction of ErisTutor's **Deterministic Learning Core**, **Async Sandboxed Java Code Judge**, and **Resilient AI RichChat Portal**, while preserving the Windows **Live2D Desktop** experience 100%.

## Architecture

- **`com.eristutor.learning`** — Concept DAG cycle validation, CAS curriculum revisioning (`expectedRevision` -> `409 Conflict`), append-only Assessment & Evidence Ledger, SHA-256 idempotency, Correction chain (`superseded_by`), Forget tombstones, Recency-weighted Mastery Engine, and SM-2 Review Scheduler.
- **`com.eristutor.judge`** — Knowledge pack registry (`java-di.json`), Java 27 Virtual Thread worker pool + `Semaphore` backpressure, hardened Docker JDK 21 sandbox (`--network none`, `--read-only`, `--cap-drop ALL`, `--pids-limit 64`, bounded 16KB stream capture), SSE job stream, and automatic Learning Core commit.
- **`com.eristutor.ai` & `portal`** — Drop-in replacement for `cloud_server.py` serving `src/body/richchat/`, Resilience4j Circuit Breaker & Retry failover (`DeepSeek -> Gemini -> Deterministic Sandbox Fallback`), Token-Bucket Rate Limiter (Redis in `prod`, in-memory in `dev`), and read-only learner context injection.
- **`com.eristutor.security` & `common`** — Spring Security 6 + JWT (access & rotating refresh tokens) + `X-Eris-Desktop-Key` service principal, RFC 7807 `ProblemDetail`, `X-Request-Id` MDC correlation, Micrometer/Prometheus, OpenAPI 3 / Swagger UI.

## Quick Start (Local Dev — No Docker Required)

```powershell
$env:JAVA_HOME = "C:\Program Files\Java\jdk-27"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
cd backend-java
mvn spring-boot:run
```

- RichChat Studio UI: `http://127.0.0.1:8080/`
- Swagger UI: `http://127.0.0.1:8080/swagger-ui.html`
- Health & Prometheus: `http://127.0.0.1:8080/actuator/health`, `/actuator/prometheus`

## Full Stack Production Mode (PostgreSQL 16 + Redis 7)

```powershell
docker compose up --build
```

## Connecting Live2D Desktop (`Strangler Fig`)

Start the Java backend (`.\run_java_backend.bat`), then launch Eris Live2D Desktop with:

```powershell
$env:ERIS_JAVA_BACKEND_URL = "http://127.0.0.1:8080"
$env:ERIS_JAVA_LEARNING_MODE = "mirror"
.\run_eris.bat
```

## Running Tests

```powershell
cd backend-java
mvn clean test
```

> `PostgresTestcontainersIntegrationTest` automatically executes full Flyway + PostgreSQL 16 integration tests whenever Docker is running (`disabledWithoutDocker = true`).
