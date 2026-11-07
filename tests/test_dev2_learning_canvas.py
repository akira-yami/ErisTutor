from runtime.learning_canvas import LearningCanvasService
from runtime.learning_canvas.lesson import validate_lesson
from runtime.learning_core import LearningService, LearningStore


def test_canvas_validates_lesson_and_keeps_code_whitespace(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    try:
        invalid = canvas.handle({"request_id": "bad", "action": "open", "client_id": "test-tab",
                                 "node_id": "missing"})
        assert invalid["status"] == "error" and not canvas.is_active()
        opened = canvas.handle({"request_id": "open", "action": "open", "client_id": "test-tab"})
        assert opened["status"] == "ok" and opened["learning_canvas"]["nodes"]
        lesson = validate_lesson({"blocks": [
            {"kind": "formula", "title": "Square", "latex": "x^2"},
            {"kind": "code", "title": "Code", "language": "python", "text": "    value = 2\n    print(value)\n"},
        ]}, group_id="math", node_id="square", title="Example")
        assert lesson["blocks"][0]["latex"] == "x^2"
        assert lesson["blocks"][1]["text"] == "    value = 2\n    print(value)\n"
    finally:
        canvas.close()
        store.close()
    assert canvas.handle({"request_id": "closed"})["message"] == "canvas_closed"
