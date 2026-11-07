# -*- coding: utf-8 -*-
"""Gemini API decision engine cho LangGraph harness.

Tận dụng WebAI-to-API chạy local (localhost:8081) để gọi model không tốn phí,
thay vì dùng subprocess CLI hay gọi thẳng Google API.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
import requests
from pathlib import Path
from typing import Any, Iterable


class GeminiCliError(RuntimeError):
    pass


class GeminiCliDecisionExecutor:
    def __init__(
        self,
        *,
        workspace: str | Path | None = None,
        model: str = "",
        executable: str = "", # Giữ nguyên signature cho coordinator
        timeout: int | None = None,
        logger: Any = None,
    ) -> None:
        self.workspace = Path(workspace).resolve() if workspace else None
        self.model = str(model or os.environ.get("ERIS_GEMINI_AGENT_MODEL", "auto")).strip() or "auto"
        if self.model == "auto":
            self.model = "gemini-3-pro"  # WebAPI backend (config.conf: backend=webapi)
            
        self.api_base = os.environ.get("ERIS_WEBAI_URL", "http://localhost:8081/v1").rstrip("/")
        # Đây là read-timeout của một lượt model, không phải deadline của cả task.
        # 120s quá ngắn cho WebAI/Gemini khi prompt code lớn; task dài đã có
        # cancellation/permission lifecycle ở LangGraph nên cho model đủ thời gian trả lời.
        self.timeout = int(timeout or os.environ.get("ERIS_GEMINI_AGENT_TIMEOUT", "300") or 300)
        self.max_tool_calls = max(1, min(
            int(os.environ.get("ERIS_GEMINI_AGENT_MAX_TOOL_CALLS", "4") or 4),
            8,
        ))
        self.logger = logger

    def health(self) -> tuple[bool, str]:
        # Kiem tra xem WebAI-to-API co dang chay khong
        try:
            resp = requests.get(f"{self.api_base}/models", timeout=5)
            if resp.status_code == 200:
                return True, f"webai_api_ready ({self.api_base})"
        except requests.exceptions.RequestException:
            pass
        return False, f"webai_api_not_found_at_{self.api_base}"

    def invoke_with_tools(
        self,
        messages: Iterable[Any],
        *,
        tools: list[dict[str, Any]],
        goal: str = "",
        active_skills: list[str] | None = None,
        workspace: str | Path | None = None,
        ide_context: dict[str, Any] | None = None,
    ):
        from langchain_core.messages import AIMessage

        ok, reason = self.health()
        if not ok:
            raise GeminiCliError(reason)

        resolved_workspace = Path(workspace).expanduser().resolve() if workspace else self.workspace
        prompt = self._build_prompt(
            messages,
            tools=tools,
            goal=goal,
            active_skills=active_skills or [],
            workspace=resolved_workspace,
        )

        url = f"{self.api_base}/chat/completions"
        image_parts = self._image_content_parts(ide_context or {})
        content: Any = prompt
        if image_parts:
            # WebAI-to-API là OpenAI-compatible. Chỉ khi user thực sự attach ảnh
            # mới chuyển message sang multimodal; task text bình thường giữ payload
            # cũ để không tăng overhead hay làm backend non-vision khó chịu.
            content = [{"type": "text", "text": prompt}, *image_parts]

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
        }

        try:
            # Connect fail nhanh, nhưng đừng giết một inference hợp lệ chỉ vì model
            # mất hơn hai phút. requests tuple = (connect timeout, read timeout).
            resp = requests.post(url, json=payload, timeout=(10, self.timeout))
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                raise GeminiCliError("Lỗi 401: Cookie Google của WebAI-to-API đã hết hạn/sai. Vui lòng mở http://localhost:8081 để cập nhật lại Cookie.") from exc
            raise GeminiCliError(f"webai_api_failed:{type(exc).__name__}:{exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise GeminiCliError(f"webai_api_failed:{type(exc).__name__}:{exc}") from exc
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise GeminiCliError(f"webai_api_invalid_response:{json.dumps(data)}")

        decision = self.parse_decision(text)

        action = str(decision.get("action", "final") or "final").lower()
        summary = str(decision.get("summary", "") or "").strip()
        if action == "tool":
            raw_calls = decision.get("tool_calls")
            if isinstance(raw_calls, list):
                calls = [item for item in raw_calls if isinstance(item, dict)]
            else:
                legacy = decision.get("tool_call")
                calls = [legacy] if isinstance(legacy, dict) else []
            if not calls:
                raise GeminiCliError("gemini_decision_missing_tool_name")

            disclosed = {
                str(item.get("name", "") or ""): item
                for item in tools
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            }
            normalized: list[dict[str, Any]] = []
            for call in calls[: self.max_tool_calls]:
                name = str(call.get("name", "") or "").strip()
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                if not name:
                    raise GeminiCliError("gemini_decision_missing_tool_name")
                if name not in disclosed:
                    raise GeminiCliError(f"gemini_requested_undisclosed_tool:{name}")
                normalized.append({"name": name, "args": args})

            # Batching chỉ dành cho quan sát độc lập. Side effect vẫn đúng triết lý
            # checkpoint-per-action: chạy một write/control/exec rồi model phải nhìn
            # observation mới trước khi quyết định tiếp. Nhờ vậy ta giảm round-trip
            # đọc file/search mà không biến autopilot thành máy bắn patch mù.
            read_effects = {"observe", "inspect", "read"}
            if len(normalized) > 1:
                batch_is_read_only = all(
                    str(disclosed[item["name"]].get("effect", "inspect") or "inspect").lower() in read_effects
                    and item["name"] != "ask_user_choice"
                    for item in normalized
                )
                if not batch_is_read_only:
                    normalized = normalized[:1]

            tool_calls = [{
                "name": item["name"],
                "args": item["args"],
                "id": f"gemini_{uuid.uuid4().hex[:12]}",
                "type": "tool_call",
            } for item in normalized]
            names = ", ".join(item["name"] for item in normalized)
            return AIMessage(
                content=summary or f"Use tool(s): {names}.",
                tool_calls=tool_calls,
                additional_kwargs={"gemini_cli_stats": data.get("usage", {})},
            )

        final = str(decision.get("final", "") or summary or text).strip()
        return AIMessage(content=final, additional_kwargs={"gemini_cli_stats": data.get("usage", {})})

    @staticmethod
    def _image_content_parts(ide_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Build bounded OpenAI-compatible image_url parts from explicit user attachments.

        Extension Host already copied pasted images to local storage and only sends
        paths after a user gesture. We still re-check type/size here because crossing
        a process boundary is not a good place to develop optimism.
        """
        raw = ide_context.get("attachments") if isinstance(ide_context, dict) else None
        attachments = raw if isinstance(raw, list) else []
        parts: list[dict[str, Any]] = []
        allowed_mime = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        for item in attachments[:8]:
            if not isinstance(item, dict) or str(item.get("kind", "")) != "image":
                continue
            file_path = Path(str(item.get("path", "") or "")).expanduser()
            try:
                resolved = file_path.resolve(strict=True)
                size = resolved.stat().st_size
            except (OSError, RuntimeError):
                continue
            if size <= 0 or size > 8 * 1024 * 1024:
                continue
            declared = str(item.get("mime", "") or "").lower().strip()
            guessed = str(mimetypes.guess_type(resolved.name)[0] or "").lower()
            mime = declared if declared in allowed_mime else guessed
            if mime not in allowed_mime:
                continue
            try:
                encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
            except OSError:
                continue
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
            if len(parts) >= 4:
                break
        return parts

    @staticmethod
    def parse_decision(text: str) -> dict[str, Any]:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start < 0 or end <= start:
                raise GeminiCliError(f"gemini_decision_not_json:{cleaned[:1000]}")
            try:
                payload = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError as exc:
                raise GeminiCliError(f"gemini_decision_not_json:{cleaned[:1000]}") from exc
        if not isinstance(payload, dict):
            raise GeminiCliError("gemini_decision_must_be_object")
        return payload

    def _build_prompt(
        self,
        messages: Iterable[Any],
        *,
        tools: list[dict[str, Any]],
        goal: str,
        active_skills: list[str],
        workspace: Path | None = None,
    ) -> str:
        history: list[dict[str, str]] = []
        for msg in list(messages)[-24:]:
            role = getattr(msg, "type", msg.__class__.__name__).lower()
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            history.append({"role": role, "content": content[-10000:]})

        contract = {
            "action": "tool | final",
            "summary": "short public-safe execution summary, no private chain of thought",
            "tool_calls": [{"name": "exact disclosed tool name", "args": {}}],
            "final": "final task result when no more tools are needed",
        }
        return (
            "You are Gemini Executor, the backstage execution specialist inside Eris.\n"
            "Return ONLY one JSON object matching DECISION CONTRACT. No markdown.\n"
            "Never roleplay Eris and never expose private chain-of-thought.\n"
            f"You may batch up to {self.max_tool_calls} INDEPENDENT read/inspect/observe tools in one turn. "
            "For any write/control/execute tool or ask_user_choice, choose exactly ONE tool so LangGraph can checkpoint after the side effect.\n"
            "You may call ONLY tools listed under DISCLOSED TOOLS.\n"
            "If the needed capability is not disclosed yet, use search_capabilities first; then activate_skill.\n"
            "Do not use built-in shell/file tools in this lane. All execution must flow through the disclosed Eris tools.\n"
            "You are running UNATTENDED AUTOPILOT. Never ask the user for routine permission to inspect/read/write project files, run tests/builds, or run workspace-bounded development commands. Use the disclosed tool directly.\n"
            "If a protected operation is denied, do not stop to request confirmation; replan toward a safe/reversible alternative and report the protected boundary only if it blocks completion.\n"
            "A persistent Repository Context is injected by the harness before execution. Treat its full inventory/symbol/import map as orientation authority. "
            "Resolve ambiguous or misspelled user paths against that map instead of silently switching to a similarly named module.\n"
            "The repository index is NOT enough evidence to edit implementation: deep-read the target file and relevant callers/dependencies first. "
            "For file creation/modification prefer project_apply_patch so the runtime can snapshot, verify and emit changed-file evidence; do not fake file writes through a shell command.\n"
            "After every tool observation, reconsider the goal and choose the next smallest useful action. Verify before final. "
            "Never claim a file was created/modified unless the runtime returned verified artifact evidence.\n\n"
            f"GOAL:\n{goal}\n\n"
            f"LOCKED WORKSPACE:\n{str(workspace) if workspace else '(none)'}\n"
            "This task is bound to that workspace. Do not switch projects mid-task.\n\n"
            f"ACTIVE SKILLS:\n{json.dumps(active_skills, ensure_ascii=False)}\n\n"
            f"DISCLOSED TOOLS:\n{json.dumps(tools, ensure_ascii=False, indent=2)}\n\n"
            f"RECENT GRAPH MESSAGES:\n{json.dumps(history, ensure_ascii=False)}\n\n"
            f"DECISION CONTRACT:\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
            "For action=tool, provide tool_calls (an array). For action=final, provide final and omit tool_calls."
        )

__all__ = ["GeminiCliDecisionExecutor", "GeminiCliError"]
