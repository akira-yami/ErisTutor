# ErisTutor

<p align="left">
  <img src="https://img.shields.io/badge/AWS_Cloud-Deployed-FF9900?style=for-the-badge&logo=amazon-aws&logoColor=white" alt="AWS Cloud" />
  <img src="https://img.shields.io/badge/Deployment-AWS_Fargate_Serverless-232F3E?style=for-the-badge&logo=amazon-ecs&logoColor=white" alt="AWS Fargate" />
  <img src="https://img.shields.io/badge/AI_Gateway-DeepSeek_Broker_Active-brightgreen?style=for-the-badge&logo=fastapi&logoColor=white" alt="AI Gateway" />
  <img src="https://img.shields.io/badge/Status-Production_Ready-success?style=for-the-badge&logo=statuspal" alt="Status" />
</p>

> 🚀 **Cloud Infrastructure**: ErisTutor tích hợp **AWS Cloud AI Gateway** làm trung tâm điều phối khóa bảo mật (DeepSeek Key Broker), thu thập dữ liệu quan trắc sư phạm (Telemetry Observability) và quản lý hạn mức truy cập thời gian thực.

ErisTutor là lớp học tập của **Eris AI** — một người bạn đồng hành trên desktop
Windows, có thân thể Live2D (Godot/C#) và một runtime nhận thức viết bằng Python.
Phần Python nắm cảm nhận, bộ nhớ, hội thoại và lõi học tập xác định; phần Godot
dựng nhân vật và làm cầu nối UI (Learning Canvas web, Learner Portrait) với
backend qua HTTP loopback + UDP.

Hệ học tập **không phải** một chatbot dạy học độc lập. Nó là tập hợp năng lực cắm
vào vòng hội thoại chính của Root: một Learning Core dựa trên SQLite là nguồn sự
thật duy nhất cho bằng chứng và mức nắm vững (mastery), còn LLM/UI chỉ đóng vai
read-model hoặc nội dung đề xuất.

> **Trạng thái dựa trên bằng chứng, không tô hồng.** Tài liệu trong [`docs/`](docs/)
> dùng nhãn tường minh — `CÓ CODE` (có mã nguồn), `TEST OK` (test offline đã qua),
> `CHƯA E2E` (chưa kiểm chứng toàn luồng desktop/Gemini), `ĐỀ XUẤT` (chưa triển khai).
> Xem [D05 — Kiểm kê hiện tại](docs/ERISTUTOR_20261107_V02_D05_CURRENT_AUDIT.md)
> để biết đâu là đã chứng minh, đâu là kế hoạch.

Mọi máy đích đều là **Windows**; một phần runtime dùng API riêng của Windows
(`msvcrt`, `tasklist`, `CREATE_NO_WINDOW`), nên CI chạy trên `windows-latest`.

## Có gì bên trong

| Mảng | Vị trí | Vai trò |
|------|--------|---------|
| Learning Core | `src/runtime/learning_core/` | Chuyển bằng chứng → mastery/review một cách xác định; SQLite là nguồn sự thật |
| Learning Canvas | `src/runtime/learning_canvas/` | Duyệt đồ thị tri thức, bài tập, snapshot read-model |
| Tutor | `src/runtime/tutor/` | Đề xuất bước dạy cho Root; không chiếm quyền phát ngôn |
| Memory | `src/memory/` | Bộ nhớ nhận thức: claim, provenance, episodic/semantic/living-user (xem PDF bên dưới) |
| Cortex | `src/cortex/` | Nhận thức bậc cao và mô hình hóa chủ thể |
| Runtime | `src/runtime/` | Coordinator, kernel hội thoại, TTS, các cầu nối giao thức |
| LLM | `src/llm/` | Dịch vụ model, gồm lane Gemini Web |
| Godot body | `src/body/`, `Eris.csproj`, `project.godot` | Nhân vật Live2D, chuyển động, UDP listener |

## Kiến trúc (Hybrid 6 lớp & AWS Cloud Gateway)

```
[ Client Desktop Windows ]                                [ AWS Cloud Gateway ]
UI (Learning Canvas Web / Learner Portrait)            ┌───────────────────────────┐
        │  HTTP loopback + UDP                         │  • AWS Fargate Serverless │
Cầu nối Godot C#  (src/body/UdpListener*.cs)           │  • DeepSeek Key Broker    │
        │                                              │  • Telemetry Observability│
Coordinator / Root  ── TutorExecutive (đề xuất dạy)    └─────────────┬─────────────┘
        │                                                            │
Learning Canvas  ── Knowledge Pack (ví dụ: java-di)                  ▼
        │                                                   [ Official DeepSeek ]
Learning Core (xác định: evidence, mastery, review)
        │
SQLite LearningStore  ← nguồn sự thật duy nhất
```

Các ranh giới quyền sở hữu không được phá (xem
[D02 — Bản đồ kiến trúc](docs/ERISTUTOR_20261107_V02_D02_SYSTEM_KNOWLEDGE_ATLAS.md)):

- **Root** sở hữu lời nói/TTS. Tutor chỉ đề xuất; không có agent loop cạnh tranh.
- **Learning Core** sở hữu bằng chứng và mastery. UI/C#/Gemini không ghi mastery trực tiếp.
- **Gemini/UI** là read-model hoặc nội dung *đề xuất* — một nhận xét tự tin của model
  không phải bằng chứng cho việc tự làm đúng độc lập.
- **Bộ nhớ living-user** lưu sở thích người dùng đã nêu, tách khỏi mastery đã chứng minh.

## Yêu cầu môi trường

- **Windows** (có dùng API riêng của Windows)
- **Python >= 3.11** (CI và dev cục bộ ghim 3.12)
- Thư viện runtime: `numpy` (embedding cho memory), `requests` (HTTP cho LLM),
  `fsrs==6.3.2` (bộ lập lịch ôn tập giãn cách). Xem [`requirements.txt`](requirements.txt).
- Extras tùy chọn, lazy-import (OCR/thị giác màn hình): `mss`, `Pillow`,
  `pytesseract` (+ binary Tesseract), `uiautomation`. Không cần để import hoặc test lõi.
- Godot body: dự án C# (`Eris.csproj`) và asset Live2D; cài qua
  [`install_live2d_body.bat`](install_live2d_body.bat).

## Bố cục mã nguồn

```
src/            Runtime nhận thức Python + Godot body (src/body)
  runtime/        coordinator, learning_core, learning_canvas, tutor, conversation
  memory/         hệ con bộ nhớ nhận thức (105 module)
  cortex/         nhận thức bậc cao, mô hình chủ thể
  llm/            dịch vụ model (gồm lane Gemini Web)
docs/           Bản đồ tri thức D01–D08 (+ PDF memory) — đọc trước tiên
.agents/tasks/  Bản đồ phụ thuộc và kế hoạch rebuild theo branch
tools/          Công cụ hỗ trợ (repo_context, whale2api, …)
vision.py       Orchestrator cấp cao / entrypoint
```

## Chiến lược build & branch

Mã nguồn được dựng từng lớp theo thứ tự phụ thuộc
([`.agents/tasks/dependency-map-and-branch-plan.md`](.agents/tasks/dependency-map-and-branch-plan.md)),
mỗi nhóm chức năng nằm trên một branch `feat/*` riêng, qua CI, rồi merge độc lập
vào `main`.

| Lớp | Branch | Gói |
|-----|--------|-----|
| 1. Nền tảng | `feat/foundation` | `domain`, `transport`, `core`, `screen`, `app_logging`, `eris_secrets`, `failure_meta`, `secret_versions`, `runtime.secure_redaction` |
| 2. Lõi nhận thức | `feat/core-memory`, `feat/llm`, `feat/mind-and-cognition` | `llm`, `memory`, `cortex` |
| 3. Runtime & điều phối | `feat/runtime-orchestration`, `feat/tutor-executive` | `runtime`, `actions`, `parser_grounding`, `telemetry`, `vision.py` |

Vòng phụ thuộc `llm ↔ memory ↔ cortex ↔ runtime` gắn kết chặt nên được một chủ sở
hữu giữ trọn thay vì chia nhỏ qua nhiều branch. `runtime` là lớp tích hợp và dựng
sau cùng.

## Chạy kiểm tra CI cục bộ

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "src"

.\.venv\Scripts\ruff.exe check src tests       # lint
python run_tests.py                          # kiểm thử unit và integration toàn diện
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) chạy đúng các cổng này
— ruff lint và run_tests.py — trên `windows-latest` cho mỗi lần push lên
`main` / `feat/**` và trên pull request.

## Chạy ứng dụng

```powershell
run_eris.bat        # khởi chạy toàn bộ Eris desktop (Godot body + Python runtime)
run_runtime.bat     # chỉ khởi chạy runtime Python
python run_tests.py # chạy bộ kiểm thử toàn diện
```

## Tài liệu

Bắt đầu từ bản đồ tri thức trong [`docs/`](docs/):

| Tài liệu | Nội dung |
|----------|----------|
| [D01 — Bắt đầu từ đây](docs/ERISTUTOR_20261107_V02_D01_START_HERE.md) | Tổng quan và thứ tự đọc |
| [D02 — Bản đồ kiến trúc](docs/ERISTUTOR_20261107_V02_D02_SYSTEM_KNOWLEDGE_ATLAS.md) | Kiến trúc và ranh giới sở hữu |
| [D03 — Đồ thị tri thức](docs/ERISTUTOR_20261107_V02_D03_CURRICULUM_GRAPH.md) | Đồ thị kiến thức thật + ontology |
| [D04 — Dữ liệu & I/O](docs/ERISTUTOR_20261107_V02_D04_DATA_AND_IO.md) | Mô hình dữ liệu, luồng bằng chứng, invariant |
| [D05 — Kiểm kê hiện tại](docs/ERISTUTOR_20261107_V02_D05_CURRENT_AUDIT.md) | Đã có / còn thiếu / chưa kiểm chứng |
| [D06 — Lộ trình](docs/ERISTUTOR_20261107_V02_D06_ROADMAP.md) | Kế hoạch theo giai đoạn P0–P4 |
| [D07 — Backlog](docs/ERISTUTOR_20261107_V02_D07_IMPLEMENTATION_BACKLOG.md) | Tác vụ giao được + tiêu chí nghiệm thu |
| [D08 — Quản trị](docs/ERISTUTOR_20261107_V02_D08_GOVERNANCE.md) | Phiên bản, đặt tên, quyết định kiến trúc |
| [M01 — Hệ con Memory (PDF)](docs/ERISTUTOR_20261107_V02_M01_MEMORY_SUBSYSTEM.pdf) | Phân tích sâu hệ con bộ nhớ |

## Khoảng trống đã biết

Theo [D05](docs/ERISTUTOR_20261107_V02_D05_CURRENT_AUDIT.md), phần lõi (Learning
Core, Tutor, memory, portrait) đã có mã nguồn và qua **test offline Python**, nhưng
các mục sau **chưa** được kiểm chứng toàn luồng:

- E2E desktop đầy đủ trên Windows (build Godot .NET + HTTP/UDP thật + Gemini Web thật).
- Root chỉ giữ index khi panel Learning đóng (mục tiêu P0, chưa đạt).
- Canvas đa môn động — cầu nối C# hiện hard-code pack `java-di` và đồ thị giới hạn 16 node.
- FSRS chạy ở chế độ shadow; chưa benchmark trên dữ liệu thật.
