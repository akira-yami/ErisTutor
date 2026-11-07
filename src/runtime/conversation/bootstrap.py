# -*- coding: utf-8 -*-
"""Production wiring for the single V12 conversation authority."""
from __future__ import annotations

from typing import Any

from runtime.open_loop_store import ensure_open_loop_store
from runtime.ai_orchestrated.atomic_state import atomic_store_from_env
from .agent import DeterministicRootAgent, RuntimeModelRootAgent
from .continuation import ContinuationLeaseScheduler
from .delivery_adapter import RuntimeConversationDeliveryAdapter
from .receipts import DeliveryReceiptLedger
from .move_outcomes import MoveOutcomeLedger
from .kernel import ConversationKernel
from .runtime_writer import RuntimeConversationWriter
from .session_store import ConversationSessionStore


def _install_canvas_links(host, kernel):
    from runtime.learning_canvas.events import scope_is_current
    kernel.focus_scope_is_current = lambda scope: scope_is_current(host, scope)

    def settled(delivery_id, successful, cancelled):
        canvas = getattr(host, "learning_canvas_service", None)
        send = getattr(host, "_send_runtime_json", None)
        if canvas is not None:
            packet = canvas.playback.finish(delivery_id, successful=successful, cancelled=cancelled)
            if packet and callable(send):
                send(packet, channel="learning_canvas")
        wake = getattr(host, "_wake_autonomous_loop", None)
        if getattr(host, "_pending_conversation_capability_events", None) and callable(wake):
            wake("capability_events_after_delivery")
    kernel.on_settled = settled


def ensure_conversation_kernel(host: Any) -> ConversationKernel:
    current = getattr(host, "conversation_kernel", None)
    if isinstance(current, ConversationKernel):
        _install_canvas_links(host, current)
        # Kernel có thể được tạo bởi một signal sớm hơn phase AI cuối;
        # đến lúc router sẵn sàng vẫn phải đăng ký warm-up một lần.
        preload = getattr(current.agent, "preload_self", None)
        if callable(preload):
            preload(host)
        return current

    logger = getattr(host, "logger", None)
    router = getattr(host, "ai_model_router", None)
    root = RuntimeModelRootAgent(router, logger=logger) if router is not None else DeterministicRootAgent()

    # V12 dùng chung SQLite atomic backend đã được runtime kiểm chứng, nhưng mỗi
    # loại state có namespace key riêng. Đây chỉ là persistence cơ học:
    # session/lease/receipt tuyệt đối không chứa câu public hay semantic thread.
    # Trong pytest, helper này tự trả None trừ khi test chủ động cấp DB để giữ
    # suite hermetic như trước.
    atomic_store = atomic_store_from_env(logger=logger)
    kernel = ConversationKernel(
        root,
        store=ConversationSessionStore(atomic_store=atomic_store, logger=logger),
        continuation=ContinuationLeaseScheduler(atomic_store=atomic_store,
            open_loop_store=ensure_open_loop_store(host),
            logger=logger,
        ),
        receipts=DeliveryReceiptLedger(atomic_store=atomic_store, logger=logger),
        outcomes=MoveOutcomeLedger(atomic_store=atomic_store, logger=logger),
        writer=RuntimeConversationWriter(host),
        delivery=RuntimeConversationDeliveryAdapter(host),
        logger=logger,
    )
    host.conversation_kernel = kernel
    _install_canvas_links(host, kernel)
    # Warm-up chỉ một lần khi kernel thật được lắp. Đọc Self đã có và gửi
    # toàn bộ vào DeepSeek trước lượt chat; không sinh database/snapshot khác.
    preload = getattr(root, "preload_self", None)
    if callable(preload):
        preload(host)
    return kernel


__all__ = ["ensure_conversation_kernel"]
