# -*- coding: utf-8 -*-
"""Local documentation for Root. Reading a guide never executes a capability."""
from __future__ import annotations

TOOL_GUIDES: dict[str, str] = {'rich_chat': 'Rich Chat P1 tự dựng document_source và artifact công cụ sau commit; renderer_feedback trong '
              'context chỉ xác nhận browser dựng được/chưa được, KHÔNG chứng minh kết quả đúng hoặc user đã '
              'đọc. Không tự nhận đã hiển thị nếu chưa có receipt thực. Với lời giải dài, có thể thêm '
              'document_source (Markdown/LaTeX/code nguyên bản) vào move, P2: Root có thể tự chọn '
              'presentation_blocks trong move để đưa math/table/code/chart/document vào Rich Chat. Định dạng '
              'mỗi block: {kind,title?,content}; math.content={latex}, code.content={language,source}, '
              'table.content={columns,rows}, '
              'chart.content={chart_type:bar|line,series:[{labels:[...],values:[...]}]}, '
              'document.content={format:markdown,source:text}. Chỉ chọn block khi giúp giải thích, không bắt '
              'buộc. Chọn biểu thức/đồ thị từ dữ kiện thật; cần kiểm chứng thì gọi math_workspace trước. Khi '
              'đã gọi math_workspace thành công, kết quả tool sẽ được hệ thống tự gắn dưới lượt trả lời; '
              'không cần bịa ID artifact và không copy lại kết quả tool vào block. P3: presentation_health '
              'là trạng thái artifact đã commit được kiểm chứng từ sidecar + browser receipt. '
              'awaiting_viewer nghĩa là người dùng chưa mở/hiển thị Rich Chat, KHÔNG phải lỗi. rendered chỉ '
              'xác nhận DOM/MathJax/ảnh đã render, KHÔNG xác nhận nội dung đúng hoặc user đã đọc. failed: có '
              'thể sửa artifact hoặc chọn cách hiển thị khác nếu phù hợp YÊU CẦU HIỆN TẠI; không tự bật lời '
              'thoại, không gọi tool hay hỏi lại về chủ đề cũ chỉ vì một feedback lỗi. còn say chỉ là điều '
              'Eris muốn nói. Hai kênh KHÔNG bắt buộc cùng văn bản. Nếu bài còn đang chạy trong '
              'parallel_work, không tự bịa document_source là kết quả đã giải. ',
 'research': 'Capability research: dùng arguments {operation:start|quick_search|snapshot, topic, context?}; '
             'kết quả hoàn tất sẽ quay lại bằng research_result. ',
 'math_workspace': 'Công cụ math_workspace là ACTION thật, gọi qua kind=use_capability, capability=action, '
                   'arguments={action_id:math_workspace,arguments:{kind:...,expression:...,rows:...}}. Dùng '
                   'math_workspace để kiểm chứng phép tính hoặc xuất công thức/đồ thị/ma trận sang bàn làm '
                   'việc; các kind gồm matrix, simplify, differentiate, integrate, solve, plot và '
                   'binary_determinant_max. Đặc biệt khi đề bài là tìm định thức lớn nhất của ma trận 0/1 '
                   'cấp 3, chọn kind=binary_determinant_max,size=3 để có ví dụ, kiểm chứng và chứng minh '
                   'chuẩn. Tính toán khác chưa được tool hỗ trợ thì đừng giả vờ đã chạy tool; tự suy luận và '
                   'nói rõ độ chắc chắn. Khi tool đã đưa lời giải chi tiết vào Workspace Panel, bạn chỉ nên '
                   'nói 1-2 câu tự nhiên qua Bubble, không đọc lại cả chứng minh thành bức tường chữ. User '
                   'có thể mở panel để xem từng bước. ',
 'live_workspace_edit': 'Với Panel V6, bạn có thể chủ động sửa tài liệu đang hiện bằng capability action '
                        'live_workspace_edit; dùng operation=inspect để lấy document_id/revision/block_id '
                        '(document_id phải đến từ artifact/result thật), sau đó replace_block hoặc '
                        'insert_block với base_revision phù hợp. Sửa nội dung Panel không thay lời thoại đã '
                        'phát. ',
 'work': 'Nếu nhiệm vụ giải toán/lập trình/nghiên cứu cần nhiều bước, bạn có quyền giao sang Work Lane bằng '
         'capability=action, arguments={action_id:parallel_work,arguments:{operation:start,task:...}}. start '
         'trả job_id NGAY, công việc chạy nền, Eris vẫn nói chuyện và phản hồi câu hỏi mới. Nếu muốn xem mục '
         'tiêu Eris đã chấp nhận, gọi parallel_work operation=goals hoặc goal_status; muốn thực hiện goal có '
         'trạng thái active, dùng start_goal + goal_id, không tự nhận goal chưa adopt. Work Worker có thể '
         'search_capabilities và activate_skill từ thư viện Skills, Skills chỉ cung cấp kiến thức, không tự '
         'mở quyền công cụ. Muốn biết tiến độ THẬT, gọi operation=status cùng job_id hoặc operation=recent; '
         'Muốn xem các block trong Work Panel, dùng operation=document cùng job_id. Muốn sửa khối đã viết, '
         'dùng operation=edit_document với document_operation, block_id, block, base_revision lấy từ '
         'operation=document; không tự đoán revision. Work Session có thể đề xuất say nhưng CHỈ Root được '
         'quyền biến đề xuất thành lời nói; tài liệu, code và LaTeX không phải speech fallback. Khi work job '
         'chờ user (waiting_user), có thể gọi operation=resume với job_id,answer; không bịa tiến độ. Người '
         'dùng nói chuyện mới không mặc định hủy job; khi thật sự cần hủy, gọi operation=cancel. Work Lane '
         'dùng tools theo từng bước và gửi tiến độ ra Workspace, KHÔNG được tự phát thoại. ',
 'tutor': 'Capability tutor: chỉ gọi khi cần dạy, kiểm tra hoặc quay lại kiến thức. Đọc learner_belief trong '
          'kết quả tutor như ước lượng BKT THỬ NGHIỆM, chưa calibrated; không tuyên bố điểm p_known là sự '
          'thật hoặc chẩn đoán tính cách/năng lực tổng quát. Nếu needs_probe=true, ưu tiên yêu cầu thêm bài '
          'độc lập thay vì khẳng định đã hiểu; người học có quyền đổi chủ đề, không ép học liên tục. '
          'Các operation: advise (mặc định); activity với preset=python.range|python.accumulator|python.filter|math.fractions|math.linear|writing.explanation '
          'mở bài tập. start_path tạo hoặc tiếp tục lộ trình từ yêu cầu học chủ đề mới trong tin user gốc; '
          'chỉ dùng khi user muốn bắt đầu/đổi môn, có thể unavailable nếu model không lập được kế hoạch. spaces liệt kê môn đã lưu; switch_path,path_id chuyển sang môn user nhắc tên trong tin hiện tại và tiếp tục vị trí cũ. activity '
          'mở bài dự đoán mã trong Rich Chat; readiness báo module thật. Tin user bắt đầu ERIS_LEARN là '
          'thao tác nút của bài học: gọi operation=activity_event, backend chỉ đọc event user gốc và tự chấm '
          'theo khóa đáp án, không nhận answer/correct do Root gán. Bài dự đoán mã không phải chạy Python. '
          'operation=help với reveal=false|true dành cho yêu cầu gợi ý/lời giải của câu hỏi chat đang chờ; '
          'gọi help thay vì chấm yêu cầu giúp đỡ như một đáp án. Trạng thái trợ giúp chỉ ghi sau delivery. '
          'operation=insights,path_id? xem tổng hợp P2 có bằng chứng, điểm cần kiểm tra và lịch ôn; '
          'dashboard,path_id? chỉ xuất HTML offline khi user yêu cầu bảng tiến độ. Không có server và không live refresh. '
          'set_retention_mode,mode=fsrs|shadow|heuristic chỉ dùng khi user nói rõ trong tin hiện tại (FSRS phải sẵn sàng); '
          'preview_expansion,additions,source_refs,reason xem diff bổ sung skill có nguồn; '
          'apply_expansion chỉ sau khi user xác nhận rõ và có base_revision phù hợp; '
          'operation=attempts,kp_id? xem bằng chứng; retention,kp_id? xem FSRS shadow; curriculum xem path '
          'và revision; sources,query,source_ids? tìm trong domain đang học. Nguồn trả về có loại, locator '
          'và hash; model_summary không phải nguyên văn tài liệu gốc. '
          'import_notes lưu chính nội dung user hiện tại thành ghi chú; import_source,path chỉ đọc file có '
          'đường dẫn tuyệt đối do user nêu tường minh trong tin hiện tại, parser PDF tùy chọn có thể unavailable. '
          'preview_curriculum,path,source_refs,reason trả diff xác định; revise_curriculum thêm base_revision '
          'từ curriculum/preview để commit. Giữ nguyên skill ID khi chỉ đổi thứ tự; khái niệm đổi nghĩa cần ID mới. '
          'Mục tiêu mới cần nguồn trong cùng domain. evaluation báo số liệu quan sát, chưa chứng minh cải thiện nhân quả. '
          'dispute_attempt,attempt_id tạm đưa '
          'đánh giá bị user phản đối về ungraded; forget_attempt,attempt_id xóa lần làm và chuỗi sửa. '
          'Chỉ sửa/xóa khi user yêu cầu. Không tự gán correct cho một lần làm bị phản đối. '}


TOOL_GUIDES["canvas"] = (
    "Canvas P3.7 — Root chỉ nhận learning_brief: tối đa 4 tiêu đề môn và nhánh ngắn. Không gọi snapshot/full graph nếu không cần. Khi người dùng muốn tạo môn/bản đồ mới, "
    "Root gọi operation=graph_create,title. Nếu Learning đang đóng, chỉ lưu môn và job deferred; Gemini CHƯA chạy. Khi người dùng mở và chọn môn, job mới chạy để dựng 2–4 nhánh "
    "và trả learning_job={job_id,status=deferred|queued}; nếu deferred hãy nói 'Đã lưu môn, khi bạn mở Learning tôi sẽ tạo vài nhánh'. Nếu queued có thể nói 'Tôi đang dựng vài nhánh, "
    "xong tôi báo' mà KHÔNG khẳng định đã xong khi Gemini chưa trả. "
    "Khi người học kêu mất căn bản hoặc xin học từ đầu ở một chủ đề/môn nền tảng lớn (như Bean/IoC, OOP, Concurrency...), Root PHẢI chủ động gọi operation=graph_create (ví dụ title='Spring Bean & Dependency Injection') hoặc mở bài có sẵn (như operation=open_group,group_id=java-di). Đồng thời trong lời nói phải giải thích trực giác zero-level và dứt khoát thúc ép người học mở Learning Canvas lên xem ngay nhánh đầu tiên, vì mở panel thì bản đồ bài bản mới kích hoạt và người học mới có móng vững chắc để học tiếp. "
    "operation=learning_brief đọc tóm tắt ngắn; learning_history hoặc snapshot_full chỉ truy xuất khi người dùng thực sự hỏi chi tiết. "
    "Nếu môn đã tồn tại, graph_create trả existing=true và KHÔNG tạo trùng; hãy dùng operation=library "
    "hoặc operation=learning_history xem danh sách, các nút gần đây, trạng thái và lịch sử. "
    "operation=graph_resume,group_id mở lại đúng nhánh đang học; operation=graph_select,group_id,node_id? "
    "chỉ đổi màn hình khi user muốn. operation=graph_expand,group_id,parent_id,nodes=[{title,summary?},...] "
    "bổ sung nhánh đã nghiên cứu; operation=graph_prefetch,group_id,node_id chỉ queue nếu panel mở; khi đóng lưu job deferred. "
    "Gemini tạo nháp tối đa 4 nhánh trước mắt, một tầng ẩn; không tạo 30 nhánh. "
    "Nhận tutor_result canvas_action=graph_ready/job_id khi Gemini hoàn tất thì Root QUYẾT ĐỊNH "
    "có nên nói 'Tôi dựng xong phần đầu, mở Learning xem nhé' dựa vào lượt user mới/độ bận. "
    "graph_failed nói thật chưa tạo được, không báo thành công; KHÔNG ép panel tự mở. "
    "Nội dung graph là nháp, không phải mastery. Nhánh được mở mới chỉ là exploring, "
    "đánh dấu đã xem chỉ khi user bấm; practiced chỉ từ bài tập được ghi nhận; "
    "mastered chỉ từ LearningCore/evidence có thẩm quyền. Không gán mastery tùy tiện. "
    "Khi có tutor_result canvas_action=graph_select, người học vừa chọn nhánh: "
    "có thể nghiên cứu sâu đúng nhánh thay vì toàn cây; Writer trò chuyện không tự duyệt Gemini. "
    "operation=snapshot đọc trạng thái, operation=open_group,group_id=java-di mở bài Java cũ; "
    "operation=focus,node_id đề xuất focus Java; operation=accept_focus,node_id,base_revision "
    "chỉ khi user_turn yêu cầu. Root giữ quyền nói và không phát lại kết quả đã lỗi thời."
)



def read_tool_guide(name: str) -> dict:
    text = TOOL_GUIDES.get(name)
    if text is None:
        return {"kind": "tool_guide_result", "guide": name, "status": "unavailable", "reason": "unknown_guide"}
    return {"kind": "tool_guide_result", "guide": name, "status": "found",
            "documentation": text, "authority": "documentation_only_not_execution"}
