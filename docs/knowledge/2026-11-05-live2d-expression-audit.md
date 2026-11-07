# Kiểm tra biểu cảm INFJ và xuất lại qua PSD2Live — 05/11/2026

## Atlas 2048 từ file Cubism gốc — 06/11/2026

Các thử nghiệm vẽ lại miệng ở dưới là lịch sử. Trước lượt làm sạch này, MOC3 và atlas chạy trên repo lẫn bản portable đã trùng lại bản xuất gốc. Lần này dùng bản sao CMO3 gốc, xếp lại texture ở 100% kích thước ảnh nguồn trên atlas 2048 × 2048 rồi xuất bằng Cubism Editor 4.2. Các lớp đo được có thêm khoảng 24% pixel theo mỗi chiều. [Hướng dẫn atlas Cubism 4.2](https://docs.live2d.com/4.2/en/cubism-editor-manual/texture-atlas-edit/)

- MOC3 mới chỉ khác vùng tọa độ UV. Hình học, keyform và 27 tham số của rig được giữ nguyên. Gói chạy giữ 23 biểu cảm, 11 chuyển động, nhóm blink/lip sync và physics hiện có.
- Đã render 15 tư thế mỗi bản bằng renderer portable. Ảnh đối chiếu cho thấy cải thiện nhẹ ở nét tóc và viền mắt khi nhìn cận, giữ dáng mặt ở các mẫu há miệng, quay đầu và chớp mắt. Đây là kiểm theo tư thế, chưa chạy hội thoại/TTS thật. [Ảnh đối chiếu trước và sau](../assets/live2d-clean-2048-20261106/texture-before-after.png)
- Ba file atlas, MOC3 và model3 đã đồng bộ sang repo và `Documents/eris/src`, có xác nhận SHA-256. Kiểm native consistency đạt ở cả hai gói thử trước/sau. 93 test Live2D đạt, loại một ca physics đã tái hiện lỗi ở gói cũ. Ruff đạt.
- Bản CMO3 chỉnh sửa, backup hai gói, kết quả render và `sync-receipt.json` nằm trong `C:/Users/Gtvkun/Documents/infj-PSD2Live-test-20261105-170430/texture-cleanup-2026-11-06/`.

## Cập nhật sau nhận xét môi còn cần nâng cấp

User nhận xét cần nâng cấp môi. Đã xem lại hai lớp da/môi và native render; nét xám quanh môi còn dày, trong khi thay artwork bên trong chưa đổi dáng khép/há. Thử nét anime nâu dịu bằng ImageGen, chuyển sắc độ nét qua coverage của đường môi đang đăng ký trên mesh, bỏ bớt halo xám. Không dùng trực tiếp contour miếng da AI để tránh nét thừa đã thấy ở lượt thử trước.

- Hai vùng atlas môi đổi tổng 111 pixel RGB; ngoài hai vùng và toàn bộ alpha không đổi. Không sửa binary rig hoặc code speech. Đã sync texture source và portable sau kiểm hash baseline.
- Tự xem ảnh native trước/sau của khép, há 0.3/0.6/1, smirk, lazy, teasing nói và teehee nghỉ: 8 trạng thái mỗi bản. Đây là kiểm render cô lập, chưa phải cửa sổ realtime/TTS thật ở lượt này; không thêm hay chạy lại unit test cho đổi màu texture.
- Thay trạng thái “viền môi giữ nguyên” của lượt trước: nét/halo môi đã đổi nhẹ, hình học và keyform môi vẫn giữ nguyên. Khác biệt về dáng vẫn nhỏ; muốn dáng anime rõ hơn cần author keyform của môi trên/dưới, khóe và liên kết răng/khoang miệng khi há, rồi kiểm chuyển động. Không coi lần đổi nét này là đã tái rig môi.
- Ảnh đối chiếu: [lips-before-after.png](../assets/live2d-lips-20261105/lips-before-after.png); [prompt ImageGen](../assets/live2d-lips-20261105/prompt.md). Backup texture, bản thử, renderer harness và `sync-receipt.json` ở `C:/Users/Gtvkun/Documents/infj-PSD2Live-test-20261105-170430/anime-lips-v2/`.

## Cập nhật mới nhất: miệng anime và phối mí theo yêu cầu user

User tiếp tục yêu cầu vẽ lại khoang miệng/răng theo anime 2D và xem phối mí rõ hơn. **Thay trạng thái “không thay texture” và các giá trị mí của lần cứu hộ bên dưới:** lần này đã đổi texture ở đúng năm đảo miệng, sửa sáu preset và stage target, đồng bộ tám file sang `Documents/eris/src`. Binary MOC3, UV, alpha atlas và CMO3 nguồn giữ nguyên.

- Dùng ImageGen tích hợp để vẽ sprite sheet mới: khoang miệng burgundy tối, răng trắng liền mảng bỏ vệt nướu đỏ, lưỡi hồng lì ít bóng. Đóng gói màu sprite đã vẽ vào đúng footprint atlas hiện có, giữ từng giá trị alpha. So pixel xác nhận 1.254 pixel RGB đổi, 0 pixel đổi ngoài năm đảo miệng, 0 alpha đổi. MOC3 vẫn SHA `0c4ef5c214a23c0e8bd0197039d3f6c85d8ad8d94b5bace6e17b5bd6221b7c0c`.
- Mí L/R mới: warm 0.86/0.88, amused 0.72/0.76, skeptical 0.58/0.90, smirk_disdain 0.56/0.78, teasing 0.62/0.86, lazy_deadpan 0.48/0.52. EyeOpen dương vẫn Multiply để giữ blink; stage dùng cùng giá trị cap. Teasing tăng lệch mày 0.26/-0.12; skeptical 0.55/-0.28. Không chỉnh thuật toán TTS/mouth-open.
- Tự xem 26 native render trước/sau (13 trạng thái mỗi bản), gồm bảy biểu cảm, ba mức há miệng, teasing nói, teehee nghỉ và bản right nói. Ảnh so sánh chỉ ghép/cắt từ framebuffer thật, không dùng AI để vẽ lại mặt trong ảnh so sánh.
- Tự xem cửa sổ renderer portable sau đồng bộ: smirk và teasing nói há lớn. Frame teasing opening≈0.836, tongue=0, mí≈0.62/0.843 khi phối speech; model có miệng mới và không thấy nét thừa ở trạng thái đã xem. Không coi đây là kiểm TTS có âm thanh hoặc kiểm mọi góc quay liên tục. Đã đóng renderer thử.
- 92 test pass, 1 deselected (ca physics đã chứng minh có sẵn ở lần trước); regression blink kiểm cả hai bên cho sáu preset với 1.080 frame mỗi preset. Ruff ba file Python thay đổi và `git diff --check` pass. Không thêm test mô phỏng chất lượng nghệ thuật bằng số.
- Artifact và prompt trong [bản lưu thiết kế](../assets/live2d-anime-20261105/README.md). Backup ngay trước lượt này, model thử, script đóng gói, render, log và receipt: `C:/Users/Gtvkun/Documents/infj-PSD2Live-test-20261105-170430/anime-mouth-v1/`. Receipt `sync-receipt.json` kiểm hash tám file source/portable.

Đây là thay artwork atlas và phối biểu cảm trên rig hiện có; chưa tái rig miệng hoặc nhập artwork trở lại CMO3. Viền môi/shape gốc vẫn giới hạn dáng miệng khi há lớn. Các kết quả trước đó bên dưới là lịch sử, không phải trạng thái atlas/mí hiện tại.

## Cập nhật cứu hộ theo yêu cầu tiếp theo của user

User đã yêu cầu sửa sau khi xem đánh giá. **Thay trạng thái “chưa sửa” của phần audit bên dưới:** đã sửa source và đồng bộ 8 file sang `Documents/eris/src`; không thay binary rig `.moc3` hoặc texture, không đưa bản PSD2Live xuất lại vào gói chạy.

- EyeOpen dương của `smirk_disdain` đổi Overwrite sang Multiply để giữ auto blink. Mắt đang mở của 4 preset wink/teehee cũng dùng Multiply=1; mắt cố ý nhắm vẫn Overwrite=0.
- Lớp mí sau native update dùng cap theo opening của stage, giữ giá trị nhỏ hơn giữa blink/native và stage thay vì nhân cùng mood hai lần. Teasing max≈0.72, smirk_disdain max≈0.8.
- Tongue overlay rút mượt theo độ há khi đang nói: giữ nguyên đến opening 0.12, smoothstep về 0 tại 0.36. Không sửa giá trị audio-owned MouthOpenY. Khi im lặng còn giữ được cử chỉ lè lưỡi.
- `lazy_deadpan` đổi sang mí 0.64, mày 0/0.10, MouthForm=-0.15; `sad_empathy` giữ nguyên. Native render sau sửa phân biệt được hai biểu cảm.
- Các biểu cảm nhẹ khác và giới hạn rig/convert PSD2Live không được coi là đã sửa toàn bộ.

Kiểm chứng sau sửa:

- Bộ Live2D 156 pass/2 fail; hai fail là physics vertex index `[3,3,3]` so expectation `[1,2,3]` và giá trị response miệng `0.317621...` so expectation `0.317553...`. Cả hai tái hiện y hệt trên bản source/asset sao lưu trước sửa trong sandbox riêng.
- Sau chỉnh cuối, ba file test có thay đổi chạy 89 pass/1 deselected (ca physics có sẵn); Ruff năm file Python sửa và `git diff --check` pass.
- Render lại 138 pose theo renderer mới, kiểm trực quan teasing/smirk/lazy và mô phỏng auto blink 23 preset. Native sample smirk_disdain mắt về 0, max≈0.8; teasing nói opening=1 vẫn giữ opening=1 nhưng tongue=0.
- Chạy riêng bằng `Documents/eris/python/python.exe` với code/assets đã sync: smirk_disdain min=0/max≈0.8, lazy min=0/max≈0.64, teasing min=0/max≈0.72; wink giữ mắt cố ý nhắm, mắt còn lại chớp được.
- Backup source và portable trước sửa ở `C:/Users/Gtvkun/Documents/infj-PSD2Live-test-20261105-170430/rescue-backup-20261105/`. Receipt `rescue-sync-receipt.json` xác nhận hash 8 file sync và MOC3 không đổi. Artifact sau sửa ở `rescue-validation/` và `rescue-portable-validation/` cạnh thư mục audit.

Ở thời điểm đồng bộ chưa có process `live2d_body.main` nên không restart avatar. Sau đó đã mở riêng renderer portable để tự kiểm trực quan như phần dưới; chưa chạy hội thoại/TTS thật hoặc nghe kiểm giọng.

### Tự kiểm cửa sổ portable sau cứu hộ

Theo yêu cầu user, assistant tự xem ảnh cửa sổ native qua Computer Use, cả toàn thân và cận mặt (scale 3, offset-y -1.8), không yêu cầu user kiểm hộ. Chạy `Documents/eris/python/python.exe` với code/asset production trong `Documents/eris/src`, cửa sổ báo khoảng 60 FPS. Harness `live_visual_check.py` chỉ quan sát parameter sau `_update_model_frame` và gửi event qua UDP riêng 4259; không thay thuật toán renderer, không gọi LLM hay phát âm thanh.

- Đã tự xem calm, smirk_disdain, teasing nghỉ/nói, wink_correct và bản right, sad_empathy, lazy_deadpan, teehee nghỉ, teehee_right nói, rồi trở về calm. Cận mặt thấy mắt nháy đúng bên, sad/lazy khác mí và mày, môi không bị lưỡi chồng lên khi giữ há lớn; chưa thấy rách mặt ở các trạng thái vừa xem.
- Log frame realtime sau bỏ 1.5 giây đầu mỗi chuyển: smirk mắt min=0/max≈0.8; lazy min=0/max≈0.64. Wink/teehee giữ mắt chủ động nhắm=0, mắt còn lại giảm theo auto blink.
- Teasing nghỉ giữ tongue=1. Trong 253 mẫu teasing đang nói có MouthOpenY≥0.36, tongue luôn=0; teehee_right nói giữ opening≈0.833 và tongue=0 trong 106 mẫu. Đã xem trực tiếp trạng thái giữ há lớn bên cạnh thử envelope biến thiên.
- Đây là event `audio_start` với envelope tổng hợp để kiểm renderer và đường nhận stage, **không phải** kiểm hội thoại/TTS có âm thanh. Một số preset nhẹ vẫn khó đọc khi nhìn toàn thân; không nâng kết luận thành mọi biểu cảm/mesh đều hoàn hảo.
- Sau kiểm đã đóng sạch renderer thử. Thư mục `live-visual-check/` cạnh artifact audit chứa `frames.jsonl`, `observed-summary.json`, stdout/stderr và lệnh thử. Ảnh Computer Use được xem trực tiếp trong phiên, không lưu lại thành artifact trong thư mục này.

Phần audit bên dưới mô tả lần kiểm **trước cứu hộ**; các lỗi mí/lưỡi/preset trùng đã được thay thế bằng kết quả sau sửa ở trên.

## Kết luận và phần thay thế nhận định trước

User yêu cầu đánh giá độ lỗi khi dùng biểu cảm, chưa yêu cầu sửa rig/runtime.

- Gói INFJ trong `Documents/eris/src/assets/live2d/infj` hiển thị được 23 biểu cảm bằng Cubism Native. Chưa thấy rách mặt lớn ở các pose đã xem; điều này không bảo đảm mọi tổ hợp liên tục đều đúng.
- Lỗi rõ nhất trong đường phối hiện tại: `smirk_disdain` không chớp mắt tự động khi giữ biểu cảm; `sad_empathy` và `lazy_deadpan` trùng hoàn toàn; mắt/lông mày/khóe miệng của nhiều preset nhẹ khó phân biệt.
- Lưỡi `teasing` ở MouthOpenY=1 có hình chồng mép miệng không tự nhiên. Đây là nhận xét trực quan của reviewer trên pose biên, không phải lỗi số học hoặc xác nhận nó xảy ra ở mọi nhịp TTS.
- **Thay nhận định chỉ dựa vào cảnh báo xuất:** xuất PSD2Live không chỉ có báo cáo `approximated`; khi nạp bản `.moc3` xuất lại bằng cùng Cubism Native, thấy sai khác rõ ở tóc, vùng mũi/miệng ngay neutral. Chưa dùng đầu ra này để thay asset Eris.
- **Thu hẹp khuyến nghị thử PSD2Live:** MCP kết nối và nhập model thành công không chứng minh giữ được rig khi xuất. Với model này chưa coi đây là pipeline chuyển đổi an toàn.

## Phân biệt các bản model

| Bản | Bằng chứng |
|---|---|
| `Documents/infj/infj.model3.json` | 11 biểu cảm được đăng ký; SHA-256 `infj.moc3` = `9e2cb1cef0a4df421b9530c1325f7715767e7cbaa569b2da60f194f3158b5949` |
| `Documents/eris/src/assets/live2d/infj` | 23 biểu cảm; SHA-256 `infj.moc3` = `0c4ef5c214a23c0e8bd0197039d3f6c85d8ad8d94b5bace6e17b5bd6221b7c0c` |
| Source checkout `src/assets/live2d/infj` | MOC3 cùng SHA-256 với gói Documents/eris tại thời điểm kiểm tra |
| `Documents/infj/infj pro demo.cmo3` | Nguồn đã nhập PSD2Live ở lượt trước; bản tham chiếu native để so chuyển đổi là `infj pro demo.moc3` đi kèm, không mặc định coi source này trùng asset Eris |

Không tìm thấy process `live2d_body` trong truy vấn process của lượt này. Báo cáo nói về asset/source đã đọc và mô phỏng offline, không xác nhận avatar đang chạy nhận đúng cùng dữ liệu.

## Cách kiểm tra và phạm vi

Không điều khiển avatar thật, không phát TTS, không gọi LLM. Dùng GLFW/OpenGL window ẩn để render model; không chụp màn hình desktop.

- Python 3.12, binding live2d-py 0.7.0.4 có sẵn; log Core `05.01.0000`.
- Copy gói Eris sang thư mục audit; bản JSON dành riêng cho kiểm tra bỏ motions/physics để loại trừ chuyển động ngẫu nhiên. File `.moc3`, texture và preset trong bản copy giữ nguyên.
- 23 preset × 12 điều kiện = 276 pose cô lập: rest, nói 0.5/1, đóng mắt, AngleX -30/+30, quay đầu kết hợp nói, AngleZ -30/+30, nhìn trái/phải ở biên dùng bởi gaze controller. Tính Add/Multiply/Overwrite từ default và đọc giới hạn parameter thực của MOC3.
- 23 × 6 = 138 pose qua hàm `_update_model_frame` của renderer trong gói Documents/eris. Dùng `ExpressionTarget` thật, `NativeAssetPlayback`, `Live2DLayerVisibility`; fixed step 1/60, 90 frame mỗi pose, không có sway/gaze nền hoặc TTS/audio thật. Mẫu blink hình ảnh là ép endpoint mí cuối cùng để kiểm hình, **không** phải bằng chứng automatic blink hoạt động.
- Kiểm riêng automatic blink bằng renderer và native auto blink bật: 1 giây warm-up, 17 giây mô phỏng được lấy mẫu mỗi preset; chưa phải video realtime 17 giây.
- 46 endpoint parameter đơn; 5 chuyển biểu cảm; 9 pose so ba gói native nguồn, PSD2Live xuất 4.2, và asset Eris.
- Có kiểm số liệu nhưng chưa quét hình học triangle cho mọi mesh. Sai khác pixel dùng để tìm khác biệt, không là phần trăm lỗi hay điểm chất lượng nghệ thuật.

## Phát hiện có thể tái hiện

### 1. smirk_disdain mất automatic blink — mức vừa

Trong mẫu 17 giây mô phỏng, hai mắt có min=max khoảng `0.64`, không có frame cả hai mắt đóng. Preset Overwrite EyeOpen=0.8; stage cũng dùng eye_open_left/right=0.8 nên sau lớp mí là 0.8×0.8=0.64. Overwrite biểu cảm chặn giá trị auto blink phía trước. Preset giữ lâu sẽ tạo ánh nhìn cố định; không coi đây là vỡ mesh.

`wink_correct`, `teehee` và hai bản right cũng giữ cố định một mắt đóng/mắt kia mở. Điều này phù hợp cử chỉ wink ngắn nhưng không có automatic blink của mắt còn mở nếu giữ pose; không quy tất cả thành lỗi rig.

### 2. Cộng/nhân hai lớp — cần phân biệt thiết kế và lỗi

`teasing` full-weight: EyeOpen=0.72×0.72≈0.5184, khóe miệng 0.12+0.12=0.24, MouthForm=0.08+0.08=0.16. Đây là kết quả từ stage + preset + eyelid adapter, không phải PSD2Live gây ra. Chưa kết luận mọi giá trị phối hai lớp đều sai ý đồ sản phẩm.

Ở miệng há tối đa và tongue=1, ảnh có khối lưỡi đỏ nhô, chồng lên môi; hình kém tự nhiên rõ hơn khi quay đầu. Cần ưu tiên kiểm curve/clip/lưỡi theo độ há trước khi tăng cường thêm preset.

### 3. sad_empathy và lazy_deadpan trùng — mức vừa về diễn đạt

Hai exp3 cùng MouthForm=-0.3 và BrowL/R=1; hai `ExpressionTarget` cũng trùng. Ảnh runtime rest giống từng pixel. Không thể kỳ vọng hai nhãn này tạo hình thái khác nhau bằng dữ liệu hiện có.

### 4. Preset nhẹ quá giống nhau — mức nhẹ đến vừa về khả năng nhận biết

`curious`, `surprised_soft`, `concerned`, `smirk_minor` chỉ thay đổi nhẹ khi đứng riêng. Curious và surprised dùng Multiply EyeOpen=1.05/1.08 trong khi native range là 0..1; đoạn render cuối có clamp nên không có hình mắt mở rộng vượt 1. Đây là mất độ biểu cảm, không phải mắt bung khỏi mesh.

Endpoint khóe miệng/lip press có thay đổi thật nhưng nhỏ: tại ảnh full-body 768×1152, MouthCornerL=1 chỉ đổi 76 pixel quá ngưỡng chênh RGBA 4; MouthCornerR=1 đổi 75; LipPress=1 đổi 68. Không suy ra parameter bị ngắt chỉ vì nhìn tổng thể khó thấy.

Hai parameter tóc Rotation8/9 đổi -30/+30 không làm pixel nào khác trong neutral cô lập này. Chưa quét mọi tổ hợp để kết luận chúng hoàn toàn vô dụng.

### 5. yan_warning còn ánh mắt sáng — giới hạn mapping

ParamEyeHighlight=0 thay đổi một phần highlight, nhưng không làm biến mất toàn bộ hiệu ứng mắt. Native Part IDs không có `mado`, `ArtMesh7`, `ArtMesh16`; các tên tương ứng tồn tại ở Drawable. `Live2DLayerVisibility` chỉ thao tác Part đã xác nhận nên nhánh ẩn ArtMesh7/16 không thực hiện được. Đây là giới hạn đã được ghi chú trong source, không phải discovery rằng adapter lỡ ẩn toàn mặt.

`blush_affection` vẫn đỏ má thấy rõ qua ParamCheek; không coi thiếu Part `mado` là toàn bộ blush không hoạt động.

### 6. Xuất PSD2Live làm sai hình — mức cao cho chuyển đổi

So native gói `infj pro demo.moc3` với `export-42-moc3/infj_pro_demo.moc3`, cùng camera và parameter:

- Có nét/đốm trắng hoặc viền lạ ở mũi/miệng, tóc và các đường nét mặt khác nguồn ngay neutral.
- Mắt/miệng khi blink, nói, gaze, smirk và tongue không giữ nguyên toàn bộ hình nguồn.
- Báo cáo compiler vẫn có 10 `parameter_grid` bị approximated do số trục 4–6 về 3. Cảnh báo texture rebuilt có ở CMO3 xuất lại.
- Cả ba MOC3 qua kiểm consistency nhưng consistency **không chứng minh** hình giống nguồn hoặc rig đúng về nghệ thuật.

Không thay thế model đang dùng bằng bản xuất này.

## Tóm tắt từng preset

| Preset | Đánh giá ở phạm vi mẫu |
|---|---|
| calm | Ổn, gần neutral; blink đóng được |
| warm | Ổn, hiệu ứng cười rất nhẹ |
| dry | Ổn về hình; khác biệt nhẹ |
| curious | Yếu; EyeOpen vượt range bị clamp |
| focused | Ổn, ít khác biệt |
| low | Mí thấp thấy được, không rách mặt ở mẫu |
| skeptical | Thay mày/mí nhẹ, khó đọc ở kích thước nhỏ |
| amused | Ổn, nụ cười vẫn nhẹ |
| concerned | Lo lắng thể hiện yếu |
| annoyed_soft | Mí thấp rõ hơn miệng; không thấy rách mặt |
| surprised_soft | Ngạc nhiên yếu; EyeOpen vượt range bị clamp |
| teasing | Cần chỉnh phối/lưỡi khi há lớn; mí bị nhân hai lớp |
| smirk_minor | Nhếch mép yếu dù parameter có tác dụng |
| smirk_disdain | Không auto blink khi giữ; mí cố định≈0.64 |
| blush_affection | Đỏ má rõ; không thấy lỗi lớn |
| cat_mouth | Có đổi sang dạng w nhỏ; khó đọc ở full-body |
| sad_empathy | Hình ổn nhưng trùng lazy_deadpan |
| wink_correct | Wink rõ; phù hợp giữ ngắn |
| teehee | Wink rõ; cần xem lưỡi khi phối speech |
| yan_warning | Highlight chỉ giảm một phần; nhánh ẩn drawable không có Part phù hợp |
| lazy_deadpan | Chưa có hình riêng so sad_empathy |
| wink_correct_right | Wink bên còn lại rõ; phù hợp giữ ngắn |
| teehee_right | Tương tự teehee, đổi bên wink |

## Dữ liệu kiểm chứng và bảo toàn

Thư mục artifact:
`C:/Users/Gtvkun/Documents/infj-PSD2Live-test-20261105-170430/expression-audit/`.

- `audit-summary.png`, `closeup-expressions.png`: bảng minh họa.
- `frames/`, `*-stress.png`, `static-audit.json`: pose cô lập.
- `runtime/stress-review-0.png`, `runtime/stress-review-12.png`: cận cảnh các pose runtime.
- `runtime/runtime-effective-parameters.json`, `auto-blink-audit.json`, `transition-audit.json`, `parameter-sweep.json`: dữ liệu đo.
- `conversion/conversion-comparison.png`, `conversion-differences.json`: so chuyển đổi; đây là so gói native nguồn đi kèm với bản xuất, chưa so trực tiếp mọi keyform của CMO3.
- `native-model-identity.json`: IDs và consistency.
- `source-unchanged.json`: 26 file gốc gồm model descriptor, MOC3, texture và 23 exp3 còn khớp SHA-256 với snapshot sau kiểm tra.

Không sửa asset/runtime, không sync portable, không restart Eris và không tự gửi báo lỗi lên GitHub. Chỉ thêm tài liệu kiểm chứng vào repository.
