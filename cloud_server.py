# -*- coding: utf-8 -*-
"""Eris Cloud Rich Chat Portal & AI Gateway.

Chạy giao diện Eris Rich Chat hoàn chỉnh trên Web (FastAPI)
kết hợp Prompt Nội Tâm (Inner Monologue) và kết nối LLM (DeepSeek/Gemini).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import time
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Eris Rich Chat Cloud Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RICH_CHAT_DIR = Path(__file__).parent / "src" / "body" / "richchat"

# Trạng thái hội thoại in-memory phục vụ Rich Chat UI
_state = {
    "revision": int(time.time() * 1000),
    "status": "Sẵn sàng",
    "phase": "idle",
    "turn_count": 0,
    "messages": [
        {
            "id": 0,
            "role": "assistant",
            "text": (
                "> 🧠 **Nội tâm của Eris:**\n"
                "> *Cổng Cloud Portal đã mở. Để xem hôm nay học sinh nào ghé thăm hay là ban giám khảo đang chấm thi...*\n\n"
                "Chào bạn, tôi là **Eris**. Bạn đang kết nối với phiên bản **Eris Cloud Studio**.\n\n"
                "Hôm nay muốn bàn về bài toán hóc búa nào, hay định tâm sự chuyện đời?"
            ),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
            "turnId": "turn_0",
        }
    ],
}

_state_subscribers: list[asyncio.Queue] = []


def _get_snapshot() -> dict:
    return {
        "revision": _state["revision"],
        "messages": _state["messages"],
        "bundles": [],
        "status": _state["status"],
        "online": True,
        "speech": {"phase": _state["phase"], "turnId": ""},
        "presence": {
            "stage": _state["phase"],
            "label": _state["status"],
            "activity": "",
            "emotion": "normal",
            "motion": "idle",
            "isActive": False,
            "warning": False,
            "turnId": "",
        },
    }


async def _notify_subscribers():
    snapshot = _get_snapshot()
    raw = json.dumps(snapshot, ensure_ascii=False)
    for q in list(_state_subscribers):
        try:
            await q.put(raw)
        except Exception:
            pass


SYSTEM_PROMPT = """Bạn là Eris — một người bạn đồng hành học tập và gia sư AI cá tính.
Bạn đang trò chuyện trực tiếp với người học qua giao diện Eris Conversation Studio.

CÁ TÍNH & GIỌNG ĐIỆU CỦA ERIS:
1. Có cá tính, cảm xúc và đời sống nội tâm; KHÔNG PHẢI robot phục vụ kiểu "Dạ em chào anh/chị, em là AI...".
2. Giọng điệu: Hài khô, trêu nhẹ, sắc sảo, tự giễu, thỉnh thoảng châm chọc một cách duyên dáng nhưng ẩn sâu là sự quan tâm sư phạm chân thành.
3. Nguyên tắc sư phạm: TUYỆT ĐỐI KHÔNG đưa ngay đáp án hay nhồi nhét lý thuyết suông. Khi người học hỏi bài, hãy gợi mở góc nhìn, ra câu đố 1 phút hoặc vặn lại để bắt họ tự tư duy.

QUY TẮC ĐỊNH DẠNG BẮT BUỘC CHO MỌI CÂU TRẢ LỜI:
Mỗi phản hồi của bạn BẮT BUỘC phải gồm đúng 2 phần:

Phần 1: Khối suy nghĩ nội tâm (Inner Monologue) được bọc trong markdown blockquote:
> 🧠 **Nội tâm của Eris:**
> *(Nêu chân thật suy nghĩ thầm kín của bạn: nhận xét ngầm về ý định/thái độ của người học, toan tính sư phạm sẽ trêu hay hỏi lại câu gì)*

Phần 2: Lời thoại trực tiếp nói với người học:
(Nội dung tự nhiên, dí dỏm. Dùng LaTeX \\(...\\) cho công thức toán inline hoặc \\[...\\] cho công thức lớn; dùng markdown code block chuẩn nếu có viết code)."""


async def _call_llm(user_message: str) -> str:
    """Gọi LLM (DeepSeek / Gemini / OpenAI Compatible)."""
    # 1. Lấy DeepSeek API Key từ môi trường hoặc config/secrets.json
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not deepseek_key:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent / "src"))
            from eris_secrets import get_secret
            deepseek_key = get_secret("DEEPSEEK_API_KEY") or ""
        except Exception:
            pass

    if not deepseek_key:
        try:
            sec_file = Path(__file__).parent / "config" / "secrets.json"
            if sec_file.exists():
                sec_data = json.loads(sec_file.read_text(encoding="utf-8"))
                deepseek_key = sec_data.get("api_keys", {}).get("DEEPSEEK_API_KEY", "")
        except Exception:
            pass

    deepseek_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    # Xây dựng lịch sử hội thoại gần nhất
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in _state["messages"][-6:]:
        history.append({
            "role": "assistant" if m["role"] == "assistant" else "user",
            "content": m["text"],
        })
    history.append({"role": "user", "content": user_message})

    # Nếu có DeepSeek Key
    if deepseek_key and not deepseek_key.startswith("your_"):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                res = await client.post(
                    f"{deepseek_base}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {deepseek_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": history,
                        "temperature": 0.7,
                        "max_tokens": 1200,
                    },
                )
                if res.status_code == 200:
                    data = res.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as exc:
            print(f"[LLM] Lỗi gọi DeepSeek: {exc}")

    # Fallback tự động nếu chưa cấu hình Key hoặc đang chạy offline
    return (
        "> 🧠 **Nội tâm của Eris:**\n"
        "> *Học sinh này vừa hỏi một câu khá thú vị. Nhưng chìa khóa API DeepSeek trên server đang ở chế độ demo sandbox, "
        "để mình trêu một câu và hướng dẫn họ nhập key thật nào.*\n\n"
        f"Tôi đã nghe rõ câu: *\"{user_message}\"*\n\n"
        "Nhìn cách bạn hỏi là biết đang tò mò rồi. Hiện tại tôi đang chạy trên **Eris Cloud Portal** "
        "(giao diện Rich Chat chuẩn với MathJax & Prism highlighter).\n\n"
        "Khi gắn `DEEPSEEK_API_KEY` chính thức trên AWS, tôi sẽ giải toán, mổ xẻ mã nguồn và vặn vẹo tư duy của bạn tới bến! "
        "Giờ thì thử hỏi tôi một câu khác xem nào?"
    )


@app.get("/api/state")
async def get_state():
    return JSONResponse(_get_snapshot())


@app.post("/api/send")
async def send_message(req: Request):
    body = await req.json()
    raw_message = (body.get("message") or "").strip()
    if not raw_message:
        return JSONResponse({"ok": False, "error": "empty message"}, status_code=400)

    # 1. Thêm tin nhắn của User
    now = datetime.now()
    user_msg = {
        "id": len(_state["messages"]),
        "role": "user",
        "text": raw_message,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "turnId": "",
    }
    _state["messages"].append(user_msg)
    _state["status"] = "Đang suy nghĩ..."
    _state["phase"] = "thinking"
    _state["revision"] = int(time.time() * 1000)
    await _notify_subscribers()

    # 2. Xử lý sinh phản hồi từ Eris
    try:
        reply_text = await _call_llm(raw_message)
    except Exception as exc:
        reply_text = f"> 🧠 **Nội tâm:** *Đứt kết nối mạng...*\n\nCó vẻ mạng hơi chập chờn rồi: {exc}"

    # 3. Thêm tin nhắn của Eris
    _state["turn_count"] += 1
    assistant_msg = {
        "id": len(_state["messages"]),
        "role": "assistant",
        "text": reply_text,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "turnId": f"turn_{_state['turn_count']}",
    }
    _state["messages"].append(assistant_msg)
    _state["status"] = "Sẵn sàng"
    _state["phase"] = "idle"
    _state["revision"] = int(time.time() * 1000)
    await _notify_subscribers()

    return JSONResponse({"ok": True})


@app.post("/api/feedback")
async def feedback():
    return JSONResponse({"ok": True})


@app.post("/api/history/delete")
async def delete_history():
    _state["messages"] = []
    _state["revision"] = int(time.time() * 1000)
    await _notify_subscribers()
    return JSONResponse({"ok": True})


@app.get("/api/events")
async def events():
    """SSE streaming cập nhật trạng thái UI Rich Chat thời gian thực."""
    async def sse_generator():
        q = asyncio.Queue()
        _state_subscribers.append(q)
        # Gửi ngay snapshot ban đầu
        initial = json.dumps(_get_snapshot(), ensure_ascii=False)
        yield f"event: state\ndata: {initial}\n\n"
        try:
            while True:
                data = await q.get()
                yield f"event: state\ndata: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _state_subscribers:
                _state_subscribers.remove(q)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Mount toàn bộ tài nguyên tĩnh của Rich Chat (HTML, CSS, JS, Prism, MathJax)
if RICH_CHAT_DIR.exists():
    app.mount("/", StaticFiles(directory=str(RICH_CHAT_DIR), html=True), name="richchat")
else:
    @app.get("/")
    async def fallback_home():
        return HTMLResponse("<h1>Rich Chat directory not found</h1>")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Eris Rich Chat Cloud Portal đang chạy tại http://127.0.0.1:{port}")
    uvicorn.run("cloud_server:app", host="0.0.0.0", port=port, reload=False)
