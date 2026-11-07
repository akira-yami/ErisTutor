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


def test_canvas_course_deletion(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    try:
        # Create a course
        created = canvas.handle({
            "request_id": "c1",
            "action": "graph_create",
            "group_id": "custom-ml",
            "title": "Machine Learning",
            "focus": "AI foundations",
        })
        assert created["status"] == "ok"
        groups = created["learning_canvas"]["graph_workspace"]["groups"]
        assert any(g.get("title") == "Machine Learning" for g in groups)

        # Delete the custom course by title
        deleted = canvas.handle({
            "request_id": "d1",
            "action": "graph_delete",
            "title": "Machine Learning",
        })
        assert deleted["status"] == "ok"
        remaining_groups = deleted["learning_canvas"]["graph_workspace"]["groups"]
        assert not any(g.get("title") == "Machine Learning" for g in remaining_groups)

        # Delete/hide legacy course java dependency
        del_legacy = canvas.handle({
            "request_id": "d2",
            "action": "delete_group",
            "title": "java dependency",
        })
        assert del_legacy["status"] == "ok"
        final_groups = del_legacy["learning_canvas"]["graph_workspace"]["groups"]
        assert not any(g.get("group_id") == "java-di" for g in final_groups)
    finally:
        canvas.close()
        store.close()

