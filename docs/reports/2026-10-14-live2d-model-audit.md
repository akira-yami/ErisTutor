# Kiểm tra model Live2D INFJ

Ngày kiểm tra: 14/10/2026, Asia/Saigon.

## Kết luận

Cột “thông số chuẩn sau khi chỉnh” trong bảng được cung cấp chưa khớp với các
file hiện có. Đã xác nhận sai miền miệng trong runtime, sai dấu mắt trong motion,
thiếu tác dụng kết hợp của khóe miệng/mím môi, cấu hình physics tóc chưa chỉnh,
và thiếu ánh xạ một số nhãn biểu cảm. Các kết luận về hình được kiểm tra bằng
render native; không chỉ dựa vào mô tả JSON hoặc test dùng model giả.

Đây là lượt kiểm tra. Không thay đổi model, file authoring, motion, physics hoặc
mã sản phẩm. Chỉ tạo báo cáo, metadata và ảnh kiểm tra.

## Bản được kiểm tra

- Repo: `C:/Users/Gtvkun/Documents/GitHub/eris/src/assets/live2d/infj`.
- Bản chạy: `C:/Users/Gtvkun/Documents/eris/src/assets/live2d/infj`.
- Bản gốc người dùng cung cấp: `C:/Users/Gtvkun/Documents/infj`.
- File authoring: `C:/Users/Gtvkun/Documents/infj/infj pro demo.cmo3`.

Toàn bộ 29 file trong gói INFJ của repo và bản chạy có SHA-256 giống nhau,
không thiếu/thừa file. Cả bốn bản compiled sau giống từng byte:

1. Repo `infj.moc3`.
2. Bản chạy `infj.moc3`.
3. Bản gốc `infj.moc3`.
4. Bản gốc `infj pro demo.moc3`.

Kích thước: 138.240 byte. SHA-256:
`693c71b40edc003c640efb6a0d10c115899b604b73ebd3d64364e7ed3eeb8560`.

Do đó các kiểm tra native bên dưới áp dụng cho cả bản gốc và bản đang chạy.

## Phương pháp và bằng chứng

- Nạp trực tiếp `.moc3` bằng `live2d.v3.Model`, Cubism Core 05.01.0000.
- Đọc min/max/default của 25 parameter, 42 drawable và 9 part.
- Render 20 tư thế kiểm soát và 18 tổ hợp CornerL × CornerR × LipPress trong
  context OpenGL ẩn. Tắt tự chớp mắt, tự thở và motion; reset parameter trước
  mỗi tư thế. Framebuffer kiểm tra: 512 × 768.
- So sánh pixel vùng miệng và hướng di chuyển iris giữa các tư thế.
- Đối chiếu JSON asset, runtime, nhãn từ planner và test.
- Chạy bốn file test Live2D hiện tại: **54 passed**. Kết quả này chưa chứng minh
  rig đúng, vì các test chưa nạp binary để xác minh miền/keyform.

Bằng chứng:

- [Metadata native](2026-10-14-live2d-model-metadata.json).
- [20 tư thế toàn thân](live2d-audit-20261014/contact-sheet.png).
- [Phóng to khuôn mặt](live2d-audit-20261014/face-contact-sheet.png).
- [18 tổ hợp khóe miệng và mím môi](live2d-audit-20261014/corner-combinations.png).
- [Tham số từng tư thế](live2d-audit-20261014/poses.json).
- [So sánh mím môi từng tổ hợp](live2d-audit-20261014/corner-combinations.json).

## 1. Miền miệng: xác nhận sai trong runtime

Native `ParamMouthOpenY`: **min 0, max 1, default 0**. Render giá trị −1 cho ảnh
giống hệt giá trị đóng 0; giá trị .5 và 1 cho độ há tăng dần.

`src/live2d_body/parameter_profile.py:47` vẫn đặt đóng/nửa mở/mở là **−1/0/1**.
Curve hiện tại là `.51 + .41 × voiced^.68`, rồi đổi sang miền −1..1. Tương đương
độ mở có tiếng khoảng `.02 + .82 × voiced^.68`; khoảng im xuất −1 và bị native
kẹp về 0. Đây chưa phải curve `.22..88` trong bảng.

Tốc độ đáp ứng hiện tại: **88 mở / 96 đóng**, không phải 48/64
(`stage_state.py:262`, `:727`).

Phần sửa lip-sync trước đó đã sửa ownership và thời điểm start/done/cancel,
nhưng vẫn dùng profile miệng cũ. Các test cũng đang xác nhận −1 là đóng, nên
đã không phát hiện bất tương thích với binary thực tế.

## 2. Khóe miệng và mím môi: tái hiện thiếu tổ hợp

Native có đủ parameter:

| Parameter | Min | Max | Default |
|---|---:|---:|---:|
| ParamErisMouthCornerL | −1 | 1 | 0 |
| ParamErisMouthCornerR | −1 | 1 | 0 |
| ParamErisLipPress | 0 | 1 | 0 |
| ParamErisTongue | 0 | 1 | 0 |

Ở `MouthOpenY=.3`, bật **CornerL=1, CornerR=1** cho ảnh giống từng pixel với
**CornerL=1, CornerR=0**. Bên thứ hai không tạo thêm tác dụng trong mẫu này.

Đã thử cả 18 tổ hợp L/R ∈ {−1,0,1}, Press ∈ {0,1}. Thay Press từ 0 lên 1 chỉ
đổi vùng miệng khi **L=0, R=0**. Cả **8 tổ hợp còn lại** cho vùng miệng giống
nhau giữa Press=0 và Press=1. Như vậy lỗi mất hiệu ứng mím môi khi kết hợp
khóe miệng được tái hiện trên model compiled.

Điều này xác nhận lỗi đầu ra ở các tổ hợp đã thử; chưa xác nhận tên object
`Warp9` hoặc số keyform authoring bên trong `.cmo3`.

Runtime có truyền corner và tongue qua ExpressionTarget, snapshot và renderer.
Tuy nhiên sanitizer chưa clamp bốn parameter riêng; LipPress không có target
runtime khác 0. File expression có giá trị LipPress, nhưng renderer hiện không
gọi `SetExpression` để phát các expression asset đó.

## 3. Hướng mắt: xác nhận dấu đối xứng

Render và đo iris xác nhận mỗi mắt dùng **âm vào mũi / dương ra ngoài**:

| Pose | Iris trái trên ảnh | Iris phải trên ảnh | Kết quả |
|---|---:|---:|---|
| Neutral | 231.68 px | 268.23 px | Mốc so sánh |
| Cả hai −.6 | 232.83 px | 266.78 px | Hội tụ vào trong |
| Cả hai +.6 | 229.56 px | 270.12 px | Phân kỳ ra ngoài |
| xl=−.6, xr=+.6 | 229.56 px | 266.78 px | Cùng nhìn sang trái ảnh |
| xl=+.6, xr=−.6 | 232.83 px | 270.12 px | Cùng nhìn sang phải ảnh |

Các motion hiện vẫn đặt hai trục X **cùng dấu và cùng giá trị**:

| Motion | Giá trị X hai mắt | Head X lớn nhất | Head Z lớn nhất |
|---|---|---:|---:|
| glance_left | −.36, −.34 | .62 | .42 |
| glance_right | +.36, +.34 | .54 | .38 |
| ponder_left | −.26, −.24 | .40 | .72 |
| ponder_right | +.26, +.24 | .38 | .68 |
| scan_short | −.28 rồi +.22 | .24 | Không có |

Vì vậy các curve này hội tụ/phân kỳ khi phát riêng. Cần đảo một phía để nhìn
ngang đồng hướng. Runtime hiện tự ghi parameter gaze và không gọi `StartMotion`,
nên dữ liệu motion sai không đồng nghĩa mọi lần chạy hiện tại đều phát đúng
các curve sai này.

Default runtime `eye_strength_x/y/inner_ratio` là **.30/.18/.78**, chưa phải
.44/.30/.82 (`config.py:53`). Environment override có thể thay các default này.

Chưa xác minh độc lập graph Warp2/3 hoặc toàn bộ keyform kết hợp mí/iris/head
bên trong file authoring.

## 4. Physics tóc và chuyển động đầu

`infj.physics3.json` hiện có một chain, bốn vertex và tám output. Rotation2/3/4
lấy vertex **1/2/3**. Rotation5/6/7 vẫn lấy **3/3/3** tại dòng 88/99/110, chưa
đổi thành 1/2/3 như bảng. Rotation8/9 cũng lấy vertex 3.

Normalization Position và Angle đều **−10/0/+10**, chưa phải ±15
(dòng 183–190). Output vẫn `Type=Angle`, `Reflect=false`, scale/weight khác
nhau giữa các channel. Tên “nhánh tóc trái” cần xác minh trong authoring graph.

Native Head X/Z có miền ±30. Trong motion asset, biên X lớn nhất chỉ **.62**,
Z lớn nhất **1.65**. Speech offset giới hạn X ở **−1.8/+1.45**, Z ở
**−1.75/+1.85**; runtime còn cộng gaze và sway. Các con số trên là parameter
unit, không tự chứng minh góc vật lý của deformer trên màn hình.

Đã render Z=−30/+30 để đối chiếu hình. Chưa đọc được chính xác các key của
`Rotation/Def0`, nên chưa xác nhận graph đã hoặc chưa cân đối thành ±10°.

## 5. Lông mày

Native có `ParamBrowLAngle/RAngle` với miền −1..1; drawable IDs có ArtMesh35
và ArtMesh28. Đã render Brow=+1 ở HeadX=−30/0/+30 trong bộ 20 tư thế.

Không có quyền truy cập keyform/control point trong API wrapper đang cài.
File `.cmo3` gốc có header CAFF, không phải ZIP/JSON và không có parser sẵn
trong bốn tool đi kèm. Vì vậy chưa kết luận được tọa độ `kf5/6/8`, nguyên nhân
copy X từ −30 sang +30, hoặc khẳng định vòm mày đã được tổng hợp lại. Ảnh
render giúp đánh giá biểu hiện cuối cùng, không thay thế việc đọc graph
keyform trong Cubism Editor.

## 6. Ánh xạ emotion, motion và prosody

13/32 greeting preset dùng các label chưa được ánh xạ: `warm_playful`,
`mischief`, `deadpan`, `quiet_soft`, `curious_soft`, `tired_soft`. Chúng bị
normalize về neutral. Delivery planner còn có `flat_cold` chưa hỗ trợ.

`small_smile`, `blink_slow`, `nod_small` có handling trong speech, chưa có
choreography idle riêng. `focus`, `still`, `lean_back_small` chưa có handling
riêng. Các hint `restrained_warmth`, `gentle_low`, `cold_precision`,
`low_direct`, `surprised_soft` chưa có branch prosody chuyên biệt.

Renderer nạp/log 12 expression và 11 motion từ model3, nhưng hiện không gọi
`SetExpression` hoặc `StartMotion`. Các lớp runtime tự ghi parameter là nơi
đang quyết định phần diễn xuất hiện tại.

Toàn bộ 27 file reference trong model3 tồn tại. Curve metadata, curve ID và
expression ID nhất quán với JSON/CDI. CDI là metadata tên/nhóm, không chứng
minh keyform native hoặc chất lượng biểu diễn.

## Giới hạn và thứ tự xử lý

1. Ưu tiên sửa profile miệng về miền native 0..1 và thay test để đối chiếu binary.
2. Hoàn thiện tổ hợp khóe miệng/mím môi trong Cubism Editor và xuất lại moc3.
3. Sửa dấu gaze trong motion và xác định rõ quyền điều khiển motion/runtime.
4. Sửa mapping vertex physics và bổ sung label runtime còn thiếu.
5. Mở `.cmo3` để kiểm tra trực tiếp brow, deformer/head rotation và các tổ hợp mắt.

API wrapper đang dùng không expose graph deformer hoặc keyform per-object.
Core công khai có thể expose geometry và key theo parameter, nhưng cũng không
tự chứng minh 18 keyform của một deformer. Xem
[Core API reference](https://cubism.live2d.com/sdk-doc/reference/NativeCoreAPIReference_en_r15.pdf)
và [định dạng authoring/runtime của Live2D](https://docs.live2d.com/en/cubism-editor-manual/file-type-and-extension/).

Không phát TTS/audio và không sửa file gốc trong lượt kiểm tra này.
