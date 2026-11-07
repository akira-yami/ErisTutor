# Hệ Thống Trọng Số, Chấm Điểm và Định Tuyến Bộ Nhớ Eris (Memory Scoring & Weights)

**Mã tài liệu**: `ARCH-MEM-01`  
**Ngày hiệu lực**: `08/11/2026`  
**Module tham chiếu**: `src/memory/memory_scoring.py`, `src/memory/memory_cue.py`, `src/memory/memory_candidate.py`, `src/memory/memory_record.py`  
**Mục tiêu**: Phân tích chi tiết toán học, cấu trúc trọng số, chính sách bề mặt (surfacing policy), và các cơ chế phạt trong hệ thống tính điểm ký ức hợp nhất (`Unified MemoryScorer`).

---

## 1. Triết Lý Cốt Lõi Của MemoryScorer

Hệ thống bộ nhớ của Eris được thiết kế xung quanh 4 nguyên tắc bất biến:
1. **Tách biệt Relevance (Độ liên quan) và Prior (Tiên nghiệm)**:
   - Một ký ức dù có độ tin cậy hay cảm xúc cao đến đâu cũng không được phép trồi lên nếu không có liên quan tới ngữ cảnh hiện tại.
   - *"Prior không được cứu ký ức không liên quan!"* Nếu $\text{relevance} < \text{min\_relevance}$, điểm tổng $\text{total} = 0.0$.
2. **Ký ức Nhân Quả (Causal Memory), không phải quy tắc cấm đoán (Hard Blocking)**:
   - Các biến cố từng gây ra sửa sai (`repair`), tổn thương (`rupture`), hoặc tích lũy niềm tin (`trust`) tạo ra áp lực tâm lý tự nhiên lên Eris.
   - Ký ức định hướng hành vi thông qua trọng số và định tuyến thiên kiến ngầm (`silent_bias`), không phải bằng những câu lệnh if-else cứng nhắc.
3. **Chính sách riêng biệt cho từng tầng ký ức (Layered Memory Profiles)**:
   - Mỗi tầng ký ức phục vụ một mục đích nhận thức riêng biệt trong mô hình tâm trí của Eris.
4. **Bảo vệ Ngân sách Token và Ngữ cảnh Thật (Anti-Hallucination Guard)**:
   - Ký ức không được phép cạnh tranh hoặc nói thay cho bằng chứng thực tế trên màn hình hoặc file code đang mở (`current_screen` / `needs_current_evidence`).

---

## 2. Trọng Số Các Tầng Ký Ức (Layer Profiles)

Mỗi tầng ký ức trong `DEFAULT_LAYER_PROFILES` được cấu hình với các tham số đo lường chính xác:

| Tầng (`layer`) | Trọng số (`weight`) | Ngưỡng tối thiểu (`min_relevance`) | Kênh định tuyến (`bias_routes`) | Chính sách hiển thị (`default_surface_policy`) | Có thể nói ra (`can_surface`) | Chạy nền (`background_allowed`) |
| :--- | :---: | :---: | :--- | :--- | :---: | :---: |
| **`repair`** | **1.12** | 0.05 | `wording`, `attention` | `surface_if_needed_for_fix` | **True** | False |
| **`procedural`** | **1.02** | 0.07 | `wording` | `silent_bias` | False | **True** |
| **`relational`** | **0.92** | 0.12 | `attention`, `appraisal`, `silence` | `silent_bias` | False | **True** |
| **`semantic`** | **0.90** | 0.09 | `attention`, `wording` | `surface_if_contextual` | **True** | False |
| **`affective_residue`** | **0.86** | 0.16 | `attention`, `appraisal`, `silence` | `never_direct_claim` | False | **True** |
| **`episodic`** | **0.74** | 0.18 | `context` | `surface_if_user_asks` | **True** | False |

### Ý nghĩa nhận thức của từng tầng:
- **`repair` (Trọng số 1.12 - Cao nhất)**: Ghi nhớ những lần người dùng sửa lỗi cho Eris, ranh giới bị vi phạm, hoặc hiểu nhầm trong quá khứ. Trọng số cao nhất đảm bảo Eris tuyệt đối không lặp lại cùng một sai lầm đã bị nhắc nhở.
- **`procedural` (Trọng số 1.02)**: Quy chuẩn làm việc, thói quen kỹ thuật của người dùng (ví dụ: thích dùng async, ghét code dài dòng, phong cách viết hàm). Chạy nền để uốn nắn văn phong trả lời mà không cần khoe mẽ.
- **`relational` (Trọng số 0.92)**: Mối quan hệ giữa Eris và người dùng (sự tin tưởng, độ quen thuộc, khoảng cách giao tiếp).
- **`semantic` (Trọng số 0.90)**: Tri thức ổn định về sự thật, thông tin cá nhân của người học, các khái niệm đã được xác thực (crystallized knowledge).
- **`affective_residue` (Trọng số 0.86)**: Dư chấn cảm xúc (người dùng đang kiệt sức, bực tức, hoặc đang rất hăng say). Tuyệt đối không bao giờ claim trực tiếp ("Em thấy anh đang giận"), mà âm thầm hạ thấp áp lực câu hỏi hoặc chọn im lặng đồng hành.
- **`episodic` (Trọng số 0.74)**: Các sự kiện cụ thể xảy ra trong phiên tương tác. Trọng số thấp hơn và ngưỡng lọc cao hơn (0.18) để tránh việc Eris nhắc lại chuyện vặt vãnh không cần thiết.

---

## 3. Công Thức Tính Điểm Liên Quan (Relevance Formulation)

Độ liên quan giữa một tín hiệu gợi nhớ (`MemoryCue`) và ứng viên ký ức (`MemoryCandidate`) được tính toán thông qua nhiều tầng biểu diễn văn bản và ngữ nghĩa:

$$\begin{aligned}
\text{relevance} = \text{clamp}_{0..1} \Big( & 0.42 \times \text{lexical} + 0.18 \times \text{phrase} + 0.22 \times \text{anchor} \\
&+ 0.22 \times \text{semantic} + 0.20 \times \text{bigram} + 0.03 \times \text{temporal} \\
&+ \mathbf{1}_{[\text{matched\_terms} \neq \emptyset]} \times 0.08 + \mathbf{1}_{[\text{matched\_anchors} \neq \emptyset]} \times 0.08 \Big)
\end{aligned}$$

Trong đó:
1. **$\text{lexical}$ (Hệ số 0.42)**: So khớp token sau chuẩn hóa (loại bỏ dấu tiếng Việt, loại bỏ stopwords, tính token overlap có trọng số theo độ dài).
2. **$\text{anchor}$ (Hệ số 0.22)**: So khớp trực tiếp với các thẻ neo ngữ cảnh quan trọng (ID, tên module, tên thuật toán, nhãn kỹ thuật).
3. **$\text{semantic}$ (Hệ số 0.22)**: So khớp theo họ ngữ nghĩa (`cue.semantic_family`) và nhãn sửa sai (`repair_tags`).
4. **$\text{bigram}$ (Hệ số 0.20)**: So khớp các cặp từ liền kề, giúp bảo toàn ngữ nghĩa của các cụm từ kỹ thuật hoặc biểu đạt liên tục.
5. **$\text{phrase}$ (Hệ số 0.18)**: So khớp cả cụm từ chính xác (exact substring match sau chuẩn hóa).
6. **$\text{temporal}$ (Hệ số 0.03)**: Điểm thời gian tương thích giữa sự kiện và câu hỏi.
7. **Bonus Term / Anchor Match (+0.08 mỗi loại)**: Thưởng điểm nếu có ít nhất 1 từ khóa cốt lõi hoặc 1 thẻ neo khớp chính xác.

---

## 4. Thành Phần Tiên Nghiệm (Priors)

Nếu ký ức vượt qua ngưỡng $\text{min\_relevance}$, các thành phần tiên nghiệm được cộng thêm vào:

$$\text{prior} = \text{confidence\_prior} + \text{salience\_prior} + \text{affective\_prior} + \text{relational\_prior} + \text{repair\_prior} + \text{recency\_prior}$$

Chi tiết các tham số tiên nghiệm:
- $\text{confidence\_prior} = \text{clamp}_{0..1}(\text{record.confidence}) \times 0.08$
- $\text{salience\_prior} = \text{clamp}_{0..1}(\text{record.salience}) \times 0.08$
- $\text{repair\_prior} = \text{clamp}_{0..1}\Big(\max\big(\text{record.repair\_weight}, 0.70 \times \mathbf{1}_{[\text{layer} = \text{"repair"}]}\big)\Big) \times 0.10$
- $\text{relational\_prior} = \text{clamp}_{0..1}(\text{record.relational\_weight}) \times 0.06$
- $\text{affective\_prior} = \text{clamp}_{0..1}(\text{record.emotional\_weight}) \times 0.05$
- $\text{recency\_prior} = \text{temporal} \times 0.04$

---

## 5. Các Khoản Phạt (Penalties & Risk Controls)

Để ngăn ngừa hành vi sai lệch, mô hình áp dụng ba hình phạt nghiêm ngặt:

### 5.1. Hình phạt Quyền riêng tư (`privacy_penalty`)
```python
def _privacy_penalty(self, cue: MemoryCue, record: Any) -> float:
    if cue.privacy_sensitivity < 0.5:
        return 0.0
    if getattr(record, "privacy_level", "normal") in {"private", "sensitive"}:
        return 0.35
    return 0.08
```
- Khi người dùng đang nói về vấn đề nhạy cảm hoặc ở không gian công cộng, các ký ức mang nhãn `private`/`sensitive` bị trừ ngay $0.35$ điểm, tránh vô tình để lộ bí mật.

### 5.2. Hình phạt Rủi ro Giả lập Thực tế (`risk_penalty`)
```python
def _risk_penalty(self, cue: MemoryCue, candidate: MemoryCandidate) -> float:
    if cue.needs_current_evidence and candidate.record.layer in {"semantic", "episodic"} and not cue.needs_exact_recall:
        return 0.55
    return 0.0
```
- Khi câu hỏi của người dùng đòi hỏi bằng chứng màn hình/code hiện tại (`needs_current_evidence = True`), Eris không được phép dùng ký ức cũ để trả lời bừa. Mức phạt $0.55$ gần như vô hiệu hóa các ứng viên semantic/episodic cũ, buộc Eris phải inspect workspace thật.

### 5.3. Hình phạt Lạm dụng / Nhắc lại Quá nhiều (`overuse_penalty`)
$$\text{overuse\_penalty} = \min\big(0.10, \max(0, \text{record.access\_count}) \times 0.012\big)$$
- Ký ức càng bị gọi lên nhiều lần trong các phiên gần đây mà không có ngữ cảnh mới càng bị trừ điểm (tối đa $-0.10$). Điều này giúp Eris không biến thành người nói chuyện nhàm chán, lặp đi lặp lại một câu chuyện cũ.

---

## 6. Tổng Hợp Điểm Số Cuối Cùng (Total Score & Permission)

$$\text{total} = \text{clamp}_{0..1}\Big(\text{relevance} \times \text{profile.weight} + \text{prior} - \text{privacy\_penalty} - \text{risk\_penalty} - \text{overuse\_penalty}\Big)$$

Các chỉ số phái sinh:
- **`surfaceability`**: Điểm số cho phép ký ức được phát ngôn trực tiếp thành lời:
  $$\text{surfaceability} = \begin{cases} \text{total} & \text{nếu } \text{profile.can\_surface} = \text{True} \\ 0.0 & \text{ngược lại} \end{cases}$$
- **`permission`**: Mức độ an toàn để cho phép ký ức tham gia vào suy luận:
  $$\text{permission} = \text{clamp}_{0..1}(1.0 - \text{privacy\_penalty} - \text{risk\_penalty})$$

---

## 7. Xử Lý Tình Huống Truy Vấn Rỗng (No-Query / Silence Context)

Khi người dùng không nhập gì (phiên làm việc im lặng, background idle):
1. **Background Layers** (`procedural`, `relational`, `affective_residue`):
   Được phép tạo một dòng ảnh hưởng nhẹ (`background_bias`):
   $$\text{total} = \text{clamp}_{0..1}\Big(\big(0.12 + \text{confidence\_prior} + \text{salience\_prior} + \text{affective\_prior} + \text{relational\_prior}\big) \times \text{profile.weight}\Big)$$
2. **Hot Key Facts**:
   Các sự thật quan trọng có `fact_key` và `importance >= 0.70` vẫn được duy trì ở trạng thái "hot retention" để Eris luôn biết về bối cảnh sống còn của người học ngay cả khi chưa có câu hỏi nào được đặt ra:
   $$\text{total} = \text{clamp}_{0..1}\Big(\big(0.08 + \text{confidence\_prior} + \text{importance} \times 0.06\big) \times \text{profile.weight}\Big)$$

