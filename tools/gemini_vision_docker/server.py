import os
import re
import json
import base64
import time
import uuid
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

CACHE_DIR = Path(os.environ.get("GEMINI_CACHE_DIR", "/app/cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CREDENTIALS_FILE = CACHE_DIR / "gemini_credentials.json"

app = FastAPI(title="Gemini Vision Decomposition Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Gemini Client reference
gemini_client = None
is_authenticated = False
account_info = "Chưa khởi tạo"

class CookiePayload(BaseModel):
    psid: Optional[str] = None
    psidts: Optional[str] = None
    raw_cookie: Optional[str] = None

def parse_cookie_string(raw: str) -> dict:
    result = {}
    # Match __Secure-1PSID
    m_psid = re.search(r"__Secure-1PSID=([^;]+)", raw)
    if m_psid:
        result["psid"] = m_psid.group(1).strip()
    m_psidts = re.search(r"__Secure-1PSIDTS=([^;]+)", raw)
    if m_psidts:
        result["psidts"] = m_psidts.group(1).strip()
    return result

async def initialize_client(psid: Optional[str] = None, psidts: Optional[str] = None) -> bool:
    global gemini_client, is_authenticated, account_info
    from gemini_webapi import GeminiClient

    try:
        if psid:
            gemini_client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
        else:
            gemini_client = GeminiClient()

        await gemini_client.init(auto_refresh=True)
        is_authenticated = True
        account_info = "Đã kết nối (Sẵn sàng Vision & Chat)"

        # Save credentials to cache if provided
        if psid:
            CREDENTIALS_FILE.write_text(json.dumps({"psid": psid, "psidts": psidts or ""}), encoding="utf-8")

        return True
    except Exception as e:
        is_authenticated = False
        account_info = f"Lỗi khởi tạo: {str(e)}"
        return False

@app.on_event("startup")
async def startup_event():
    # Attempt to load credentials from cache or env
    psid = os.environ.get("GEMINI_SECURE_1PSID")
    psidts = os.environ.get("GEMINI_SECURE_1PSIDTS")

    if not psid and CREDENTIALS_FILE.exists():
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            psid = data.get("psid")
            psidts = data.get("psidts")
        except Exception:
            pass

    if psid:
        await initialize_client(psid, psidts)

@app.get("/api/status")
async def get_status():
    models = []
    if gemini_client and hasattr(gemini_client, "list_models"):
        avail = gemini_client.list_models()
        if avail:
            models = [
                {
                    "name": m.model_name,
                    "display_name": getattr(m, "display_name", m.model_name),
                    "description": getattr(m, "description", "")
                }
                for m in avail
            ]

    return {
        "authenticated": is_authenticated,
        "status": account_info,
        "has_cached_creds": CREDENTIALS_FILE.exists(),
        "models": models
    }

@app.post("/api/cookies")
async def set_cookies(payload: CookiePayload):
    psid = payload.psid
    psidts = payload.psidts

    if payload.raw_cookie:
        parsed = parse_cookie_string(payload.raw_cookie)
        psid = parsed.get("psid", psid)
        psidts = parsed.get("psidts", psidts)

    if not psid:
        raise HTTPException(status_code=400, detail="Không tìm thấy __Secure-1PSID trong dữ liệu gửi lên.")

    success = await initialize_client(psid, psidts)
    if not success:
        raise HTTPException(status_code=401, detail=f"Không thể xác thực với Google: {account_info}")

    return {"message": "Cập nhật Cookie thành công!", "authenticated": True}

DEFAULT_DECOMPOSE_PROMPT = """
Hãy đóng vai trò là chuyên gia phân tích và giải mã thị giác (Visual & Context Analyst).
Nhiệm vụ của bạn là đọc hiểu thông tin bức ảnh, đồng thời PHỎNG ĐOÁN NỘI DUNG, NGỮ CẢNH VÀ Ý NGHĨA đằng sau bức ảnh đó.

Trả về kết quả dưới dạng cấu trúc JSON chính xác:
{
  "summary": "Mô tả toàn diện về bức ảnh: ai/cái gì là tâm điểm, bối cảnh ở đâu, sự việc hay hành động gì đang diễn ra.",
  "inference": {
    "topic": "Chủ đề (ví dụ: đời sống thường nhật, meme/hài hước, anime/manga, gaming, công việc, tin tức...)",
    "inferred_story": "Phỏng đoán chi tiết: Bức ảnh này kể câu chuyện gì? Điều gì vừa diễn ra hoặc sắp diễn ra? Hàm ý hoặc ẩn ý đằng sau là gì?",
    "mood_and_intent": "Không khí, cảm xúc và ý đồ truyền tải (ví dụ: khoe thành tích, than thở, hài hước, hoài niệm, nghiêm túc...)"
  },
  "key_elements": [
    {
      "name": "Chi tiết / nhân vật / đồ vật đáng chú ý",
      "significance": "Ý nghĩa hoặc vai trò của chi tiết này trong toàn bộ câu chuyện của ảnh"
    }
  ],
  "ocr_and_text": {
    "detected_text": ["Toàn bộ chữ viết, phụ đề, hội thoại xuất hiện trong ảnh"],
    "text_context": "Ý nghĩa của phần chữ đó liên quan gì đến nội dung bức ảnh"
  },
  "tags": ["3 đến 6 từ khóa liên quan"]
}

LƯU Ý: Tập trung sâu vào việc SUY ĐOÁN Ý NGHĨA và BỐI CẢNH LIÊN QUAN, không cần tọa độ bounding box hay vị trí kỹ thuật. Chỉ trả về duy nhất chuỗi JSON hợp lệ.
"""

@app.post("/api/vision/decompose")
async def decompose_image(
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form("gemini-flash"),
    thinking: Optional[str] = Form("fast")
):
    global gemini_client, is_authenticated
    if not gemini_client or not is_authenticated:
        raise HTTPException(
            status_code=401,
            detail="Gemini Web chưa được xác thực Cookie. Vui lòng nạp Cookie trước tại / hoặc qua extension."
        )

    run_prompt = prompt or DEFAULT_DECOMPOSE_PROMPT
    suffix = Path(file.filename or "image.png").suffix or ".png"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    try:
        # Mặc định tư duy nhanh (extended_thinking = False) để phản hồi siêu tốc
        is_extended = thinking in ["high", "extended"]
        kwargs = {
            "prompt": run_prompt,
            "files": [tmp_path],
            "extended_thinking": is_extended
        }
        
        # Chỉ truyền model nếu model được đăng ký hợp lệ trong session
        if model and model not in ["auto", "default"]:
            try:
                resolved = gemini_client.resolve_model(model)
                if resolved:
                    kwargs["model"] = resolved
            except Exception:
                # Nếu không tìm thấy tên model (ví dụ do session dùng tiếng Việt), fallback về mặc định
                pass

        response = await gemini_client.generate_content(**kwargs)
        raw_text = getattr(response, "text", str(response)).strip()

        # Clean markdown fences if Gemini wrapped it in ```json ... ```
        clean_text = raw_text
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        # Try to parse as JSON
        try:
            parsed_json = json.loads(clean_text)
            return JSONResponse(content={"status": "success", "data": parsed_json})
        except json.JSONDecodeError:
            return JSONResponse(content={"status": "success", "raw_output": raw_text})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý Vision: {str(e)}")

# -------------------------------------------------------------------------
# OpenAI Compatible Endpoints (Cho Eris và các client OpenAI-compatible)
# -------------------------------------------------------------------------

class OpenAIChatCompletionRequest(BaseModel):
    model: Optional[str] = "local-vision"
    messages: List[Dict[str, Any]] = []
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 512
    stream: Optional[bool] = False

@app.get("/v1/models")
async def list_models_openai():
    return {
        "object": "list",
        "data": [
            {"id": "local-vision", "object": "model", "created": 1700000000, "owned_by": "local"},
            {"id": "gemini-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
            {"id": "gemini-flash-lite", "object": "model", "created": 1700000000, "owned_by": "google"}
        ]
    }

@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OpenAIChatCompletionRequest):
    global gemini_client, is_authenticated, account_info
    if not gemini_client or not is_authenticated:
        raise HTTPException(
            status_code=401,
            detail="Gemini Web chưa được xác thực Cookie. Vui lòng nạp Cookie trước qua extension hoặc Web UI."
        )

    prompt_parts = []
    image_paths = []
    temp_files = []

    try:
        for msg in req.messages:
            content = msg.get("content")
            if isinstance(content, str):
                if content.strip():
                    prompt_parts.append(content.strip())
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    ptype = part.get("type", "")
                    if ptype == "text":
                        text_val = part.get("text", "").strip()
                        if text_val:
                            prompt_parts.append(text_val)
                    elif ptype == "image_url":
                        image_info = part.get("image_url", {})
                        url = image_info.get("url", "") if isinstance(image_info, dict) else str(image_info)
                        if url.startswith("data:"):
                            try:
                                header, b64_data = url.split(",", 1)
                                ext = ".png"
                                if "jpeg" in header or "jpg" in header:
                                    ext = ".jpg"
                                elif "webp" in header:
                                    ext = ".webp"
                                raw_bytes = base64.b64decode(b64_data)
                                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                                    tmp.write(raw_bytes)
                                    tmp_path = Path(tmp.name)
                                    image_paths.append(tmp_path)
                                    temp_files.append(tmp_path)
                            except Exception as parse_err:
                                print(f"Error parsing image data URI: {parse_err}")
                        elif url.startswith(("http://", "https://")):
                            try:
                                import urllib.request
                                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                                    urllib.request.urlretrieve(url, tmp.name)
                                    tmp_path = Path(tmp.name)
                                    image_paths.append(tmp_path)
                                    temp_files.append(tmp_path)
                            except Exception as dl_err:
                                print(f"Error downloading image URL: {dl_err}")

        combined_prompt = "\n\n".join(prompt_parts) if prompt_parts else "Describe what is visible on this screen in concrete detail."

        kwargs = {
            "prompt": combined_prompt,
            "extended_thinking": False
        }
        if image_paths:
            kwargs["files"] = image_paths

        resolved_model = None
        if req.model and req.model not in ["auto", "default", "local-vision"]:
            try:
                resolved_model = gemini_client.resolve_model(req.model)
            except Exception:
                pass
        if resolved_model:
            kwargs["model"] = resolved_model

        response = await gemini_client.generate_content(**kwargs)
        raw_text = getattr(response, "text", str(response)).strip()

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model or "local-vision",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": raw_text
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": len(combined_prompt) // 4,
                "completion_tokens": len(raw_text) // 4,
                "total_tokens": (len(combined_prompt) + len(raw_text)) // 4
            }
        }
    except Exception as e:
        err_msg = str(e)
        if "UNAUTHENTICATED" in err_msg or "Session is not authenticated" in err_msg:
            is_authenticated = False
            account_info = "Cookie đã hết hạn! Vui lòng đồng bộ lại qua extension."
            raise HTTPException(
                status_code=401,
                detail=f"Gemini Web Cookie expired: {err_msg}. Vui lòng mở gemini.google.com và bấm đồng bộ trong extension."
            )
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý Vision: {err_msg}")
    finally:
        for tf in temp_files:
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass

@app.get("/", response_class=HTMLResponse)
async def index_page():
    return HTML_UI

HTML_UI = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gemini Web Vision Decomposer</title>
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --border: #475569;
            --primary: #38bdf8;
            --primary-hover: #0284c7;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --success: #22c55e;
            --danger: #ef4444;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }
        .container { max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 20px; }
        
        header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
        h1 { font-size: 1.5rem; display: flex; align-items: center; gap: 10px; }
        .status-badge { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 999px; font-size: 0.85rem; font-weight: 600; background: #374151; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; }
        .online { background: rgba(34, 197, 94, 0.2); color: var(--success); }
        .online .status-dot { background: var(--success); box-shadow: 0 0 8px var(--success); }
        .offline { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
        .offline .status-dot { background: var(--danger); }

        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }

        .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
        .card-title { font-size: 1.1rem; font-weight: 600; color: var(--primary); display: flex; align-items: center; gap: 8px; }

        label { font-size: 0.85rem; color: var(--text-muted); }
        input[type="text"], textarea { width: 100%; background: #0b1120; border: 1px solid var(--border); border-radius: 8px; padding: 10px; color: var(--text); font-size: 0.9rem; }
        textarea { resize: vertical; min-height: 80px; font-family: monospace; }
        
        button { background: var(--primary); color: #000; border: none; border-radius: 8px; padding: 10px 18px; font-weight: 600; cursor: pointer; transition: 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
        button:hover { background: var(--primary-hover); color: #fff; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }

        .dropzone { border: 2px dashed var(--border); border-radius: 10px; padding: 30px; text-align: center; cursor: pointer; background: rgba(0,0,0,0.2); transition: 0.2s; }
        .dropzone:hover { border-color: var(--primary); background: rgba(56, 189, 248, 0.05); }
        .dropzone img { max-width: 100%; max-height: 200px; border-radius: 8px; margin-top: 10px; }

        pre { background: #020617; border: 1px solid var(--border); border-radius: 8px; padding: 14px; overflow-x: auto; font-family: Consolas, monospace; font-size: 0.85rem; color: #38bdf8; max-height: 480px; }
        
        .tip-box { background: rgba(56, 189, 248, 0.1); border-left: 4px solid var(--primary); padding: 10px 14px; border-radius: 4px; font-size: 0.85rem; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔮 Gemini Vision Decomposer</h1>
            <div id="statusBadge" class="status-badge offline">
                <span class="status-dot"></span>
                <span id="statusText">Đang kiểm tra...</span>
            </div>
        </header>

        <div class="grid">
            <!-- Left: Config & Auth -->
            <div class="card">
                <div class="card-title">🔑 Cấu hình Cookie</div>
                <div class="tip-box">
                    💡 <b>Cách nhanh nhất:</b> Dùng extension 1-click hoặc dán trực tiếp chuỗi Cookie/F12 vào ô bên dưới rồi bấm Lưu.
                </div>
                <div>
                    <label>Chuỗi Raw Cookie hoặc __Secure-1PSID:</label>
                    <textarea id="cookieInput" placeholder="__Secure-1PSID=...; __Secure-1PSIDTS=..."></textarea>
                </div>
                <button id="saveCookieBtn" onclick="saveCookies()">💾 Cập nhật Cookie</button>
                <div id="cookieMsg" style="font-size:0.85rem; color:var(--text-muted);"></div>
            </div>

            <!-- Right: Vision Playground -->
            <div class="card">
                <div class="card-title">🖼️ Bóc tách & Phân rã hình ảnh</div>
                <div class="dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
                    <div id="dropzonePrompt">📁 Kéo thả ảnh vào đây hoặc bấm để chọn ảnh</div>
                    <input type="file" id="fileInput" accept="image/*" style="display:none" onchange="previewImage(this)">
                    <img id="imagePreview" style="display:none;">
                </div>
                <div style="margin-top: 10px;">
                    <label>Mô hình phân rã (Model):</label>
                    <select id="modelSelect" style="width:100%; background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:10px; color:var(--text); font-size:0.9rem; margin-top:4px;">
                        <option value="gemini-flash" selected>⚡ 3.8 Flash (Tốc độ cao nhất, bóc tách nhạy)</option>
                        <option value="auto">Tự động (Google Default)</option>
                        <option value="gemini-pro">3.1 Pro (Suy luận sâu)</option>
                        <option value="gemini-argon">4 Argon (Nhiệm vụ phức tạp)</option>
                    </select>
                </div>
                <div style="margin-top: 10px;">
                    <label>Cấp độ tư duy (Thinking):</label>
                    <select id="thinkingSelect" style="width:100%; background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:10px; color:var(--text); font-size:0.9rem; margin-top:4px;">
                        <option value="fast" selected>🚀 Nhanh (Bỏ qua suy nghĩ sâu, trả lời tức thì)</option>
                        <option value="high">🧠 Cao (Suy nghĩ sâu & đo đạc kỹ)</option>
                    </select>
                </div>
                <button id="decomposeBtn" onclick="runDecompose()" disabled>🚀 Bắt đầu Phân rã ảnh</button>
            </div>
        </div>

        <!-- Result Section -->
        <div class="card">
            <div class="card-title">📊 Kết quả Phân rã (JSON Structured Output)</div>
            <pre id="jsonResult">// Kết quả phân rã cấu trúc thị giác sẽ hiển thị ở đây...</pre>
        </div>
    </div>

    <script>
        let selectedFile = null;

        async function checkStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                const badge = document.getElementById('statusBadge');
                const text = document.getElementById('statusText');
                if (data.authenticated) {
                    badge.className = 'status-badge online';
                    text.innerText = 'Gemini Web: Đã kết nối';
                    document.getElementById('decomposeBtn').disabled = !selectedFile;
                } else {
                    badge.className = 'status-badge offline';
                    text.innerText = 'Gemini Web: Chưa có Cookie';
                    document.getElementById('decomposeBtn').disabled = true;
                }
            } catch (e) {
                document.getElementById('statusText').innerText = 'Không kết nối được server';
            }
        }

        async function saveCookies() {
            const raw = document.getElementById('cookieInput').value.trim();
            if (!raw) return alert('Vui lòng nhập chuỗi cookie!');
            const btn = document.getElementById('saveCookieBtn');
            const msg = document.getElementById('cookieMsg');
            btn.disabled = true;
            btn.innerText = 'Đang xác thực với Google...';
            msg.innerText = '';

            try {
                const res = await fetch('/api/cookies', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ raw_cookie: raw })
                });
                const data = await res.json();
                if (res.ok) {
                    msg.style.color = 'var(--success)';
                    msg.innerText = '✅ Xác thực thành công!';
                    checkStatus();
                } else {
                    msg.style.color = 'var(--danger)';
                    msg.innerText = '❌ Lỗi: ' + (data.detail || 'Không hợp lệ');
                }
            } catch (e) {
                msg.style.color = 'var(--danger)';
                msg.innerText = 'Lỗi kết nối: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.innerText = '💾 Cập nhật Cookie';
            }
        }

        function previewImage(input) {
            if (input.files && input.files[0]) {
                selectedFile = input.files[0];
                const reader = new FileReader();
                reader.onload = function(e) {
                    const img = document.getElementById('imagePreview');
                    img.src = e.target.result;
                    img.style.display = 'block';
                    document.getElementById('dropzonePrompt').style.display = 'none';
                };
                reader.readAsDataURL(selectedFile);
                checkStatus();
            }
        }

        async function runDecompose() {
            if (!selectedFile) return alert('Chưa chọn ảnh!');
            const btn = document.getElementById('decomposeBtn');
            const resultBox = document.getElementById('jsonResult');
            btn.disabled = true;
            btn.innerText = '⏳ Đang upload & Gemini đang phân tích...';
            resultBox.innerText = '// Đang gửi ảnh lên Gemini Web, vui lòng chờ trong giây lát...';

            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('model', document.getElementById('modelSelect').value);
            formData.append('thinking', document.getElementById('thinkingSelect').value);

            try {
                const res = await fetch('/api/vision/decompose', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    resultBox.innerText = JSON.stringify(data.data || data.raw_output, null, 2);
                } else {
                    resultBox.innerText = 'Lỗi: ' + (data.detail || 'Không xác định');
                }
            } catch (e) {
                resultBox.innerText = 'Lỗi kết nối: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.innerText = '🚀 Bắt đầu Phân rã ảnh';
            }
        }

        checkStatus();
        setInterval(checkStatus, 8000);
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
