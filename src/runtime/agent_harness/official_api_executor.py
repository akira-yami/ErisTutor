# -*- coding: utf-8 -*-
"""Dedicated official-API decision engine for Eris coding tasks.

This adapter deliberately owns *reasoning only*.  Filesystem, terminal, tests,
patches, checkpoints and permissions remain inside :class:`AgentHarness` and
Eris native capabilities.  Keeping that boundary small prevents WebAI or another cognition lane from silently becoming the task owner.
"""
from __future__ import annotations

import base64
import copy
import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.inference_broker import inference_context


class OfficialApiDecisionError(RuntimeError):
    pass


class OfficialApiDecisionExecutor:
    """Translate the LangGraph tool loop to one dedicated remote LLM client."""

    def __init__(
        self,
        client: Any,
        *,
        workspace: str | Path | None = None,
        model: str = "",
        timeout: int | None = None,
        logger: Any = None,
    ) -> None:
        if client is None or not callable(getattr(client, "create_chat_completion", None)):
            raise OfficialApiDecisionError("coding_client_missing_create_chat_completion")
        self.client = client
        self.workspace = Path(workspace).resolve() if workspace else None
        self.model = str(
            model
            or getattr(client, "model_name", "")
            or getattr(client, "configured_model_name", "")
            or "coding-api"
        ).strip() or "coding-api"
        self.timeout = int(timeout or os.environ.get("ERIS_CODING_TIMEOUT", "300") or 300)
        self.max_tool_calls = max(1, min(
            int(os.environ.get("ERIS_CODING_MAX_TOOL_CALLS", "4") or 4),
            8,
        ))
        self.max_tokens = max(512, min(
            int(os.environ.get("ERIS_CODING_MAX_TOKENS", "8192") or 8192),
            65536,
        ))
        self.logger = logger

    def health(self) -> tuple[bool, str]:
        if not callable(getattr(self.client, "create_chat_completion", None)):
            return False, "coding_client_invalid"
        base_url = str(getattr(self.client, "base_url", "") or "").strip()
        return True, f"official_api_ready model={self.model} base={base_url or 'provider-managed'}"

    def resolve_model(self, model: str = "") -> str:
        """Resolve one task's model without mutating the shared coding lane."""
        resolved = str(model or "").strip()
        return self.model if not resolved or resolved.lower() == "auto" else resolved

    def _client_for_model(self, model: str) -> Any:
        """Create a request-local transport view for a task model snapshot.

        Coding tasks may run concurrently. Mutating ``self.client.model_name`` from
        a VS Code picker would make one task silently change another task's model.
        A shallow transport clone preserves credentials/pools/circuits while keeping
        model identity local to the current request thread.
        """
        resolved = self.resolve_model(model)
        if resolved == self.model:
            return self.client
        try:
            client = copy.copy(self.client)
        except Exception as exc:
            raise OfficialApiDecisionError(f"coding_client_not_cloneable:{exc}") from exc
        if hasattr(client, "configured_model_name"):
            client.configured_model_name = resolved
        if hasattr(client, "model_name"):
            client.model_name = resolved
        return client

    def invoke_with_tools(
        self,
        messages: Iterable[Any],
        *,
        tools: list[dict[str, Any]],
        goal: str = "",
        active_skills: list[str] | None = None,
        workspace: str | Path | None = None,
        ide_context: dict[str, Any] | None = None,
        model: str = "",
        role: str = "coder",
        role_context: str = "",
        reasoning_effort: str = "high",
    ):
        from langchain_core.messages import AIMessage

        resolved_workspace = Path(workspace).expanduser().resolve() if workspace else self.workspace
        prompt = self._build_prompt(
            messages,
            tools=tools,
            goal=goal,
            active_skills=active_skills or [],
            workspace=resolved_workspace,
            role=role,
            role_context=role_context,
        )
        content: Any = prompt
        image_parts = self._image_content_parts(ide_context or {})
        if image_parts:
            content = [{"type": "text", "text": prompt}, *image_parts]

        request_messages = [{
            "role": "user",
            "content": content,
        }]
        request_model = self.resolve_model(model)
        request_client = self._client_for_model(request_model)
        try:
            with inference_context(
                "coding",
                optional=False,
                timeout_seconds=float(self.timeout),
                project=str(resolved_workspace or ""),
                credential_pool="coding",
                metadata={"session_scope": "coding_agent", "model": request_model},
            ):
                response = request_client.create_chat_completion(
                    messages=request_messages,
                    temperature=0.0,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                    timeout=self.timeout,
                    reasoning_effort=(str(reasoning_effort or "high").strip().lower() if str(reasoning_effort or "high").strip().lower() in {"low", "medium", "high"} else "high"),
                    reasoning_format="hidden",
                )
        except Exception as exc:
            raise OfficialApiDecisionError(
                f"coding_api_failed:{type(exc).__name__}:{exc}"
            ) from exc

        text = self._extract_response_text(response)
        decision = self.parse_decision(text)
        action = str(decision.get("action", "final") or "final").strip().lower()
        summary = str(decision.get("summary", "") or "").strip()
        usage = self._extract_usage(response)

        if action == "tool":
            raw_calls = decision.get("tool_calls")
            if isinstance(raw_calls, list):
                calls = [item for item in raw_calls if isinstance(item, dict)]
            else:
                legacy = decision.get("tool_call")
                calls = [legacy] if isinstance(legacy, dict) else []
            if not calls:
                raise OfficialApiDecisionError("coding_decision_missing_tool_call")

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
                    raise OfficialApiDecisionError("coding_decision_missing_tool_name")
                if name not in disclosed:
                    raise OfficialApiDecisionError(f"coding_requested_undisclosed_tool:{name}")
                normalized.append({"name": name, "args": args})

            # Multiple observations may be batched.  Side effects stay one at a
            # time so LangGraph can checkpoint, verify and expose a deterministic
            # state transition after every mutation.
            read_effects = {"observe", "inspect", "read"}
            if len(normalized) > 1:
                read_only_batch = all(
                    str(disclosed[item["name"]].get("effect", "inspect") or "inspect").lower() in read_effects
                    and item["name"] != "ask_user_choice"
                    for item in normalized
                )
                if not read_only_batch:
                    normalized = normalized[:1]

            tool_calls = [
                {
                    "name": item["name"],
                    "args": item["args"],
                    "id": f"coding_{uuid.uuid4().hex[:12]}",
                    "type": "tool_call",
                }
                for item in normalized
            ]
            names = ", ".join(item["name"] for item in normalized)
            return AIMessage(
                content=summary or f"Use tool(s): {names}.",
                tool_calls=tool_calls,
                additional_kwargs={"coding_api_usage": usage, "coding_model": request_model},
            )

        if action == "handoff":
            handoff = decision.get("handoff") if isinstance(decision.get("handoff"), dict) else {}
            target = str(handoff.get("target", "") or decision.get("target", "") or "").strip().lower()
            artifact = str(handoff.get("artifact", "") or decision.get("artifact", "") or summary or "").strip()
            findings = handoff.get("findings") if isinstance(handoff.get("findings"), list) else []
            verdict = str(handoff.get("verdict", "") or "").strip().lower()
            if not target:
                raise OfficialApiDecisionError("coding_handoff_missing_target")
            return AIMessage(
                content=artifact or summary or f"Handoff to {target}.",
                additional_kwargs={
                    "coding_api_usage": usage,
                    "coding_model": request_model,
                    "coding_handoff": {
                        "target": target,
                        "artifact": artifact,
                        "findings": findings[:20],
                        "verdict": verdict,
                    },
                },
            )

        final = str(decision.get("final", "") or summary or text).strip()
        return AIMessage(
            content=final,
            additional_kwargs={"coding_api_usage": usage, "coding_model": request_model},
        )

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
                raise OfficialApiDecisionError(f"coding_decision_not_json:{cleaned[:1000]}")
            try:
                payload = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError as exc:
                raise OfficialApiDecisionError(f"coding_decision_not_json:{cleaned[:1000]}") from exc
        if not isinstance(payload, dict):
            raise OfficialApiDecisionError("coding_decision_must_be_object")
        return payload

    def _build_prompt(
        self,
        messages: Iterable[Any],
        *,
        tools: list[dict[str, Any]],
        goal: str,
        active_skills: list[str],
        workspace: Path | None,
        role: str = "coder",
        role_context: str = "",
    ) -> str:
        history: list[dict[str, str]] = []
        raw_messages = list(messages)
        marker = f"[TEAM ROLE START:{str(role or 'coder').strip().upper()}]"
        marker_index = -1
        for index in range(len(raw_messages) - 1, -1, -1):
            content = getattr(raw_messages[index], "content", "")
            if isinstance(content, str) and content.lstrip().startswith(marker):
                marker_index = index
                break
        if marker_index >= 0:
            raw_messages = raw_messages[marker_index:]
        for msg in raw_messages[-28:]:
            message_role = getattr(msg, "type", msg.__class__.__name__).lower()
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            history.append({"role": message_role, "content": content[-12000:]})

        from .coding_team import role_instructions, normalize_topology
        topology = normalize_topology(None, "execute")
        role_text = str(role_context or "").strip()
        # role_context is supplied by AgentHarness and already contains topology-specific
        # instructions plus prior specialist handoffs. Keep this executor transport-only.
        contract = {
            "action": "tool | handoff | final",
            "summary": "short public-safe execution summary; never private chain-of-thought",
            "tool_calls": [{"name": "exact disclosed tool name", "args": {}}],
            "handoff": {"target": "next specialist id", "artifact": "concise specialist handoff", "verdict": "optional", "findings": []},
            "final": "final task result when the current topology is complete",
        }
        return (
            "You are an official-API specialist inside Eris' coding team.\n"
            "Return ONLY one JSON object matching DECISION CONTRACT. No markdown and no private chain-of-thought.\n"
            "Eris owns the coding workflow. IDE integrations, filesystem, terminal and test systems are executors/sensors only. Do not delegate task ownership to another coding agent/runtime.\n"
            f"SPECIALIST ROLE: {str(role or 'coder').strip().lower()}\n"
            f"SPECIALIST CONTRACT AND TEAM MEMORY:\n{role_text or role_instructions(str(role or 'coder'), topology=topology, autopilot=True)}\n\n"
            f"You may batch up to {self.max_tool_calls} independent read/inspect/observe tools in one turn. "
            "For any write/control/execute tool or ask_user_choice, choose exactly ONE tool so the runtime can checkpoint after the side effect.\n"
            "Call ONLY tools listed under DISCLOSED TOOLS. If a needed capability is hidden, use search_capabilities then activate_skill.\n"
            "Use repository context for orientation, but deep-read task-relevant implementation and callers before changing code. Prefer semantic search for conceptual discovery when available, then verify exact source with read/search tools.\n"
            "Prefer project_apply_patch for modifications so snapshots, changed-file evidence and rollback stay inside the transaction spine.\n"
            "After every observation, choose the smallest next useful action. For implementation work follow observe -> edit -> test -> diff/verify. Never claim a change without verified runtime evidence.\n"
            "For explanation/review questions, inspect and answer without editing. For explicit fix/create/refactor requests, make and verify the change without asking the user to choose a fake view/code mode.\n\n"
            f"GOAL:\n{goal}\n\n"
            f"LOCKED WORKSPACE:\n{str(workspace) if workspace else '(none)'}\n"
            "The task is bound to that workspace for its lifetime.\n\n"
            f"ACTIVE SKILLS:\n{json.dumps(active_skills, ensure_ascii=False)}\n\n"
            f"DISCLOSED TOOLS:\n{json.dumps(tools, ensure_ascii=False, indent=2)}\n\n"
            f"RECENT GRAPH MESSAGES:\n{json.dumps(history, ensure_ascii=False)}\n\n"
            f"DECISION CONTRACT:\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
            "For action=tool provide tool_calls. For action=handoff provide handoff and omit tool_calls. For action=final provide final and omit tool_calls."
        )

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        if isinstance(response, str):
            return response
        if not isinstance(response, Mapping):
            return str(response or "")

        choices = response.get("choices") or []
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first, Mapping) else {}
            if isinstance(message, Mapping):
                content = message.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, Mapping) and item.get("text")
                    )

        candidates = response.get("candidates") or []
        if isinstance(candidates, list) and candidates:
            first = candidates[0] if isinstance(candidates[0], Mapping) else {}
            content = first.get("content") if isinstance(first, Mapping) else {}
            parts = content.get("parts") if isinstance(content, Mapping) else []
            if isinstance(parts, list):
                text = "\n".join(
                    str(part.get("text", ""))
                    for part in parts
                    if isinstance(part, Mapping) and part.get("text")
                ).strip()
                if text:
                    return text

        return str(response.get("output_text", "") or response.get("text", "") or "")

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            return {}
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            return dict(usage)
        usage = response.get("usageMetadata")
        return dict(usage) if isinstance(usage, Mapping) else {}

    @staticmethod
    def _image_content_parts(ide_context: dict[str, Any]) -> list[dict[str, Any]]:
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
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            if len(parts) >= 4:
                break
        return parts


__all__ = ["OfficialApiDecisionExecutor", "OfficialApiDecisionError"]
