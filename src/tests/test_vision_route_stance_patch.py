from types import SimpleNamespace


def test_screen_router_recognizes_vision_blindness_followup() -> None:
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    route = ScreenRelevanceRouter(ScreenEvidenceStore()).route(
        "là em còn mù vision hả",
        previous_assistant_text="Đang nhìn đây. Màn hình đang hiện gì thế?",
    )

    assert route.references_screen is True
    assert route.mode == "vision"


def test_chat_vision_salvages_screen_text_when_visual_claim_empty() -> None:
    from runtime.screen_vision_runtime import ScreenVisionRuntimeMixin
    from runtime.vision_grounding import VisionGroundingPolicy

    class Host(ScreenVisionRuntimeMixin):
        pass

    host = Host()
    host.vision_grounding_policy = VisionGroundingPolicy()
    host._chat_screen_route_decision = SimpleNamespace(references_screen=True, confidence=0.92)
    host._chat_screen_text_evidence = (
        SimpleNamespace(text="Gateway Keys", confidence=0.92),
        SimpleNamespace(text="ERIS_DS_SURFACE_API_KEY", confidence=0.91),
    )
    host.vision_semantic_compressor = None

    note = host._chat_screen_text_note_from_route()

    assert note is not None
    report = host.vision_grounding_policy.evaluate(note, requested=True)
    assert report.grounded_claims
    assert report.recommendation == "speak"


def test_vision_status_blocks_seen_claim_without_grounded_visual_claims() -> None:
    from runtime.chat_cycle import ChatCycleMixin

    class Host(ChatCycleMixin):
        pass

    host = Host()
    host._chat_screen_route_decision = SimpleNamespace(references_screen=True)
    host._chat_screen_text_evidence = (SimpleNamespace(text="Gateway Keys"),)
    host.last_vision_grounding_report = SimpleNamespace(grounded_claims=())

    context = host._chat_vision_status_prompt_context(
        screen_image_data_uri="data:image/png;base64,abc"
    )

    assert "grounded_visual_claims: 0" in context
    assert "Không được nói" in context


def test_screen_router_recognizes_music_and_media_queries() -> None:
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    store = ScreenEvidenceStore()
    router = ScreenRelevanceRouter(store)

    route = router.route("em biết bài tôi đang nghe hong")
    assert route.references_screen is True
    # Without non-chat OCR evidence, it escalates to vision
    assert route.mode == "vision"


def test_screen_router_filters_self_chat_bubbles() -> None:
    from screen.evidence_models import ScreenTextBlock
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    store = ScreenEvidenceStore()
    import time
    store.blocks = [ScreenTextBlock("id1", "[Chu nhan]: ne, xem di", "ocr", 0.85, "Eris", time.time())]


    router = ScreenRelevanceRouter(store)
    route = router.route("nè, xem đi")

    assert route.references_screen is True
    # Chat bubble text should be ignored, escalating to vision
    assert route.mode == "vision"
