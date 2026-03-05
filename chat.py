#!/usr/bin/env python3
"""
LLM Chat with Agent Support
────────────────────────────
Green-aesthetic curses TUI | Streaming | Tool use | Context compaction | Sub-agents

Usage:
    python chat.py                     # uses config.json
    python chat.py --config my.json
    python chat.py --tools-dir tools

Config keys (config.json):
    base_url            OpenRouter or local llama-server base URL
    api_key             API key (or set OPENROUTER_API_KEY / LLM_API_KEY env var)
    model               Model identifier
    max_tokens          Context window size
    compaction_threshold  Fraction of max_tokens before compaction (e.g. 0.82)
    compaction_keep_recent  How many recent messages to preserve verbatim
    max_agent_depth     Maximum sub-agent nesting depth
    compaction_strategy "smart" (LLM summarization) or "sliding" (drop oldest)
"""

import argparse
import curses
import importlib.util
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Optional

try:
    import httpx
except ImportError:
    print("Missing dependency: httpx\nRun:  pip install httpx")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULTS: dict = {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "",
    "model": "openai/gpt-4o-mini",
    "max_tokens": 8192,
    "compaction_threshold": 0.82,
    "compaction_keep_recent": 4,
    "max_agent_depth": 2,
    "compaction_strategy": "smart",
    "http_referer": "https://localhost",
    "app_title": "LLM Chat",
}


class Config:
    """
    Configuration loader with three-tier priority:
        1. Values in config.json  (lowest)
        2. Environment variables  (override file)
        3. (CLI args handled externally and applied after construction)
    """

    def __init__(self, config_file: str = "config.json") -> None:
        self.data: dict = dict(_DEFAULTS)

        path = Path(config_file)
        if path.exists():
            try:
                with path.open() as fh:
                    self.data.update(json.load(fh))
            except Exception:
                pass  # malformed JSON — use defaults silently

        env_map = {
            "OPENROUTER_API_KEY": "api_key",
            "LLM_API_KEY": "api_key",
            "LLM_BASE_URL": "base_url",
            "LLM_MODEL": "model",
        }
        for env_key, cfg_key in env_map.items():
            val = os.environ.get(env_key)
            if val:
                self.data[cfg_key] = val

    def __getattr__(self, name: str):
        if name == "data":
            raise AttributeError(name)
        try:
            return self.data[name]
        except KeyError:
            raise AttributeError(f"Config has no attribute '{name}'")

    def save(self, path: str = "config.json") -> None:
        with open(path, "w") as fh:
            json.dump(self.data, fh, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL LOADING
# ═══════════════════════════════════════════════════════════════════════════════


class Tool:
    """
    Wraps an external tool module.

    Each tool lives in  tools/<name>/
        description.md  – injected verbatim into agent system prompts
        tool.py         – must expose:
                            TOOL_SCHEMA : dict  (OpenAI function schema)
                            run(**kwargs) -> str
    """

    def __init__(
        self,
        name: str,
        schema: dict,
        run_fn: Callable,
        description: str = "",
    ) -> None:
        self.name = name
        self.schema = schema
        self.run_fn = run_fn
        self.description = description

    def run(self, **kwargs) -> str:
        return str(self.run_fn(**kwargs))

    def to_api_format(self) -> dict:
        return {"type": "function", "function": self.schema}


def load_tools(tools_dir: str = "tools") -> tuple[list[Tool], list[str]]:
    """
    Scan *tools_dir* for sub-directories, load each as a Tool.
    Returns (loaded_tools, error_messages).
    Any directory that fails to load is excluded; the error is reported.
    """
    tools: list[Tool] = []
    errors: list[str] = []

    base = Path(tools_dir)
    if not base.exists():
        return tools, [f"Tools directory '{tools_dir}' not found"]

    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        name = entry.name
        desc_file = entry / "description.md"
        impl_file = entry / "tool.py"
        try:
            if not desc_file.exists():
                raise FileNotFoundError("description.md not found")
            if not impl_file.exists():
                raise FileNotFoundError("tool.py not found")

            description = desc_file.read_text(encoding="utf-8")

            spec = importlib.util.spec_from_file_location(f"_tool_{name}", impl_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not hasattr(mod, "TOOL_SCHEMA"):
                raise AttributeError("tool.py missing TOOL_SCHEMA")
            if not hasattr(mod, "run"):
                raise AttributeError("tool.py missing run() function")

            tools.append(
                Tool(name=name, schema=mod.TOOL_SCHEMA, run_fn=mod.run, description=description)
            )
        except Exception as exc:
            errors.append(f"[{name}] {exc}")

    return tools, errors


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════════


class LLMClient:
    """
    Thin thread-safe wrapper around any OpenAI-compatible HTTP API.
    Works with OpenRouter, local llama-server (llama.cpp), Ollama, vLLM, etc.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client = httpx.Client(timeout=180.0)
        self._lock = threading.Lock()

    @property
    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        if "openrouter" in self.config.base_url.lower():
            h["HTTP-Referer"] = self.config.http_referer
            h["X-Title"] = self.config.app_title
        return h

    def _url(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    # ── Non-streaming (used for compaction summarisation) ────────────────────

    def complete(
        self,
        messages: list[dict],
        max_tokens: int = 0,
        tools: list[dict] | None = None,
    ) -> str:
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens or (self.config.max_tokens // 2),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with self._lock:
            resp = self._client.post(self._url(), headers=self._headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"].get("content") or ""

    # ── Streaming ────────────────────────────────────────────────────────────

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool_call: Callable[[dict], None] | None = None,
    ) -> dict:
        """
        Stream a completion.
        Calls on_token(chunk) for every text chunk received.
        Returns the complete assistant message dict (may include 'tool_calls').
        """
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        full_content = ""
        tc_acc: dict[int, dict] = {}  # index → accumulator

        with self._lock:
            with self._client.stream(
                "POST", self._url(), headers=self._headers, json=payload
            ) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines():
                    if not raw or not raw.startswith("data: "):
                        continue
                    data_str = raw[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})

                    if delta.get("content"):
                        full_content += delta["content"]
                        if on_token:
                            on_token(delta["content"])

                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tc_acc:
                            tc_acc[idx] = {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        acc = tc_acc[idx]
                        if tc_delta.get("id"):
                            acc["id"] = tc_delta["id"]
                        fn = tc_delta.get("function", {})
                        acc["function"]["name"] += fn.get("name") or ""
                        acc["function"]["arguments"] += fn.get("arguments") or ""

        msg: dict = {"role": "assistant", "content": full_content or None}
        if tc_acc:
            tool_calls = [tc_acc[i] for i in sorted(tc_acc)]
            msg["tool_calls"] = tool_calls
            if on_tool_call:
                for tc in tool_calls:
                    on_tool_call(tc)
        return msg

    def close(self) -> None:
        self._client.close()


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT MANAGER
# ═══════════════════════════════════════════════════════════════════════════════


class ContextManager:
    """
    Manages conversation history for a single agent.

    Two compaction strategies:

    "smart"   – (default) Triggered when active context reaches ~82% of max_tokens.
                  1. Keep last `compaction_keep_recent` messages verbatim.
                  2. Summarise everything older via a separate LLM call:
                       • 2-4 sentence narrative of direction & topic
                       • ALL code / config / verbatim snippets reproduced exactly
                         inside fenced blocks, each preceded by a one-line context label
                       • Key decisions and conclusions
                  3. Target ~50% token usage after compaction.
                  4. Store the complete untruncated history in full_history forever.
                  Falls back to "sliding" if the summarisation LLM call fails.

    "sliding" – Drop the oldest messages (not the system prompt) until under target.
                  No additional LLM call required.
    """

    def __init__(self, config: Config, system_prompt: str = "") -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.active: list[dict] = []        # working context sent to the LLM
        self.full_history: list[dict] = []  # immutable full record
        self.estimated_tokens: int = 0
        self.compaction_count: int = 0
        self._lock = threading.RLock()

    # ── Public API ───────────────────────────────────────────────────────────

    def push(self, message: dict) -> None:
        with self._lock:
            self.active.append(message)
            self.full_history.append(message)
            self._recount()

    def get_messages(self) -> list[dict]:
        """Return [system] + active messages ready to pass to the LLM."""
        with self._lock:
            out: list[dict] = []
            if self.system_prompt:
                out.append({"role": "system", "content": self.system_prompt})
            out.extend(self.active)
            return out

    def needs_compaction(self) -> bool:
        threshold = int(self.config.max_tokens * self.config.compaction_threshold)
        return self.estimated_tokens >= threshold

    def compact(self, llm: LLMClient) -> None:
        strategy = self.config.data.get("compaction_strategy", "smart")
        if strategy == "smart":
            self._compact_smart(llm)
        else:
            self._compact_sliding()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _recount(self) -> None:
        chars = len(self.system_prompt)
        for m in self.active:
            chars += len(json.dumps(m))
        self.estimated_tokens = chars // 4

    def _compact_sliding(self) -> None:
        with self._lock:
            keep = self.config.compaction_keep_recent
            target = int(self.config.max_tokens * 0.50)
            while self.estimated_tokens > target and len(self.active) > keep:
                self.active.pop(0)
                self._recount()
            self.compaction_count += 1

    def _compact_smart(self, llm: LLMClient) -> None:
        with self._lock:
            keep = self.config.compaction_keep_recent
            if len(self.active) <= keep:
                self._compact_sliding()
                return
            to_summarise = list(self.active[:-keep])
            to_keep = list(self.active[-keep:])

        # Build human-readable transcript from old messages
        lines: list[str] = []
        for m in to_summarise:
            role = m.get("role", "?").upper()
            content = m.get("content")

            if content is None:
                # Assistant message that only contains tool_calls
                tcs = m.get("tool_calls", [])
                content = " | ".join(
                    f"{tc['function']['name']}({tc['function']['arguments'][:80]})"
                    for tc in tcs
                )
            elif isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )

            lines.append(f"[{role}]:\n{content}")

        transcript = "\n\n".join(lines)

        summarise_prompt = (
            "You are summarising a conversation to preserve context while reducing its length.\n\n"
            "Rules:\n"
            "1. Write 2-4 sentences describing the overall topic and direction.\n"
            "2. For ANY code, configuration, file content, command output, or data that "
            "must be reproduced character-perfectly: quote it EXACTLY inside a fenced "
            "code block. Immediately BEFORE each such block write one line:\n"
            "   Context: <one sentence explaining how this snippet relates to the conversation>\n"
            "3. List key decisions, facts, and conclusions reached.\n"
            "4. Target roughly 50% of the original length.\n\n"
            f"CONVERSATION TO SUMMARISE:\n{transcript}\n\n"
            "SUMMARY:"
        )

        try:
            summary = llm.complete(
                messages=[{"role": "user", "content": summarise_prompt}],
                max_tokens=max(512, self.config.max_tokens // 4),
            )
        except Exception:
            self._compact_sliding()
            return

        summary_msg: dict = {
            "role": "system",
            "content": (
                f"[CONTEXT SUMMARY — {time.strftime('%H:%M:%S')} "
                f"(compaction #{self.compaction_count + 1})]\n"
                f"{summary}\n"
                "[END OF SUMMARY — recent conversation continues below]"
            ),
        }

        with self._lock:
            self.active = [summary_msg] + to_keep
            self._recount()
            self.compaction_count += 1


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT
# ═══════════════════════════════════════════════════════════════════════════════

_SPAWN_AGENT_SCHEMA: dict = {
    "name": "spawn_agent",
    "description": (
        "Spawn a sub-agent to autonomously handle a complex sub-task. "
        "The sub-agent has its own isolated context and runs to completion, "
        "returning its final answer as a string. "
        "Use this to delegate tasks that benefit from fresh context or parallel reasoning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Full description of the task for the sub-agent.",
            },
            "system_prompt": {
                "type": "string",
                "description": "Optional custom system prompt for the sub-agent.",
            },
            "tool_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names of tools to give the sub-agent. "
                    "Omit to inherit the same tool set as the parent."
                ),
            },
        },
        "required": ["task"],
    },
}


class Agent:
    """
    An LLM agent instance.

    Each agent has:
    - Its own ContextManager (completely isolated history)
    - A specific set of tools (may differ from parent)
    - Optional sub-agent spawning capability (depth-limited)
    - Event emission via on_event callback (for UI / logging)

    Events emitted:
        {"type": "token",       "text": str}
        {"type": "tool_start",  "name": str, "args": dict}
        {"type": "tool_end",    "name": str, "result": str}
        {"type": "agent_spawn", "depth": int, "task_preview": str}
        {"type": "error",       "message": str}
        {"type": "done"}
    """

    def __init__(
        self,
        config: Config,
        llm: LLMClient,
        tools: list[Tool],
        system_prompt: str = "",
        max_depth: int = 2,
        depth: int = 0,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = list(tools)
        self.max_depth = max_depth
        self.depth = depth
        self.on_event = on_event or (lambda _: None)
        self._tools_map: dict[str, Tool] = {t.name: t for t in tools}

        full_prompt = self._build_system_prompt(system_prompt)
        self.context = ContextManager(config, system_prompt=full_prompt)

    # ── Public ───────────────────────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """Send a user message and return the final text response."""
        self.context.push({"role": "user", "content": user_input})
        if self.context.needs_compaction():
            self.context.compact(self.llm)
        return self._agentic_loop()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _build_system_prompt(self, base: str) -> str:
        parts = [base.strip() if base.strip() else "You are a helpful AI assistant."]

        all_tools: list[Tool] = list(self.tools)
        if self.depth < self.max_depth:
            all_tools.append(
                Tool(
                    "spawn_agent",
                    _SPAWN_AGENT_SCHEMA,
                    self._spawn_agent_fn,
                    description=_SPAWN_AGENT_SCHEMA["description"],
                )
            )

        if all_tools:
            parts.append("\n\n## Available Tools\n")
            for t in all_tools:
                parts.append(f"### `{t.name}`\n{t.description.strip()}\n")

        return "\n".join(parts)

    def _api_tools(self) -> list[dict] | None:
        tlist = [t.to_api_format() for t in self.tools]
        if self.depth < self.max_depth:
            tlist.append({"type": "function", "function": _SPAWN_AGENT_SCHEMA})
        return tlist if tlist else None

    def _emit(self, event_type: str, **kwargs) -> None:
        self.on_event({"type": event_type, "depth": self.depth, **kwargs})

    def _agentic_loop(self, max_rounds: int = 12) -> str:
        for _ in range(max_rounds):
            messages = self.context.get_messages()
            tools_payload = self._api_tools()

            streamed: list[str] = []

            def on_token(tok: str) -> None:
                streamed.append(tok)
                self._emit("token", text=tok)

            try:
                response_msg = self.llm.stream(
                    messages=messages,
                    tools=tools_payload,
                    on_token=on_token,
                )
            except Exception as exc:
                self._emit("error", message=str(exc))
                return f"[Error communicating with LLM: {exc}]"

            tool_calls = response_msg.get("tool_calls")

            if tool_calls:
                # Store the full assistant message (content may be None)
                self.context.push(response_msg)

                for tc in tool_calls:
                    result = self._execute_tool_call(tc)
                    self.context.push(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        }
                    )

                if self.context.needs_compaction():
                    self.context.compact(self.llm)
                # Continue loop for next completion round

            else:
                # No tool calls — this is the final answer
                final = "".join(streamed)
                self.context.push({"role": "assistant", "content": final})
                if self.context.needs_compaction():
                    self.context.compact(self.llm)
                return final

        return "[Reached maximum tool-use rounds without a final answer]"

    def _execute_tool_call(self, tc: dict) -> str:
        name = tc["function"]["name"]
        args_str = tc["function"].get("arguments", "{}")
        try:
            args = json.loads(args_str) if args_str.strip() else {}
        except json.JSONDecodeError:
            args = {}

        self._emit("tool_start", name=name, args=args)

        if name == "spawn_agent":
            result = self._spawn_agent_fn(**args)
        elif name in self._tools_map:
            try:
                result = self._tools_map[name].run(**args)
            except Exception as exc:
                result = f"Tool error ({name}): {exc}\n{traceback.format_exc(limit=3)}"
        else:
            result = f"Unknown tool: '{name}'"

        self._emit("tool_end", name=name, result=result[:400])
        return result

    def _spawn_agent_fn(
        self,
        task: str,
        system_prompt: str = "",
        tool_names: list[str] | None = None,
    ) -> str:
        if self.depth >= self.max_depth:
            return (
                f"Cannot spawn sub-agent: already at maximum depth ({self.max_depth}). "
                "Handle this task directly."
            )

        if tool_names is not None:
            sub_tools = [self._tools_map[n] for n in tool_names if n in self._tools_map]
        else:
            sub_tools = list(self.tools)

        self._emit("agent_spawn", task_preview=task[:120])

        sub = Agent(
            config=self.config,
            llm=self.llm,
            tools=sub_tools,
            system_prompt=system_prompt or "You are a focused sub-agent. Complete the assigned task thoroughly.",
            max_depth=self.max_depth,
            depth=self.depth + 1,
            on_event=lambda e: self._emit("sub_event", sub_event=e),
        )
        return sub.chat(task)


# ═══════════════════════════════════════════════════════════════════════════════
# CURSES UI
# ═══════════════════════════════════════════════════════════════════════════════

# Color pair IDs
_CP_HEADER     = 1   # black on bright green — status bar
_CP_USER       = 2   # bright green          — user messages
_CP_AI         = 3   # green                 — assistant messages
_CP_AI_STREAM  = 4   # green (dim)           — streaming in progress
_CP_TOOL       = 5   # yellow                — tool call events
_CP_SYS        = 6   # cyan                  — system / info
_CP_INPUT      = 7   # bright white          — input line
_CP_ERROR      = 8   # red                   — errors
_CP_AGENT      = 9   # magenta               — sub-agent spawns
_CP_BORDER     = 10  # green                 — dividers / dim text

_ROLE_COLOR: dict[str, int] = {
    "user":            _CP_USER,
    "assistant":       _CP_AI,
    "assistant_stream":_CP_AI_STREAM,
    "tool_start":      _CP_TOOL,
    "tool_end":        _CP_TOOL,
    "system":          _CP_SYS,
    "info":            _CP_SYS,
    "error":           _CP_ERROR,
    "agent_spawn":     _CP_AGENT,
}

_ROLE_LABEL: dict[str, str] = {
    "user":             "YOU",
    "assistant":        "AI ",
    "assistant_stream": "AI ",
    "tool_start":       ">>>",
    "tool_end":         "<<<",
    "system":           "SYS",
    "info":             "INF",
    "error":            "ERR",
    "agent_spawn":      "AGT",
}

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _UIMsg:
    __slots__ = ("role", "content", "ts")

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content
        self.ts = time.strftime("%H:%M:%S")

    def color(self) -> int:
        return _ROLE_COLOR.get(self.role, _CP_AI)

    def label(self) -> str:
        return _ROLE_LABEL.get(self.role, "???")


def _wrap_text(text: str, width: int) -> list[str]:
    """Wrap text to *width* columns, preserving explicit newlines."""
    if width <= 0:
        return [text]
    result: list[str] = []
    for para in text.split("\n"):
        if not para:
            result.append("")
            continue
        while len(para) > width:
            result.append(para[:width])
            para = para[width:]
        result.append(para)
    return result


class ChatUI:
    """
    Full-screen curses chat interface.

    Layout:
        ┌──── status bar (1 line) ────┐
        │                             │
        │      message area           │  ← scrollable
        │                             │
        ├──── divider ────────────────┤
        │ > input                     │
        └─────────────────────────────┘

    Key bindings:
        Enter      — send message
        ↑ / ↓      — scroll 3 lines
        PgUp/PgDn  — scroll half-page
        Home / End — jump to top / bottom
        Esc        — clear input buffer
        Ctrl+L     — clear display
        Ctrl+C     — quit
    """

    def __init__(self, agent: Agent, config: Config) -> None:
        self.agent = agent
        self.config = config
        self.messages: list[_UIMsg] = []
        self.scroll: int = 0          # lines from bottom; 0 = newest visible
        self.input_buf: str = ""
        self.loading: bool = False
        self.spinner_i: int = 0
        self.events: Queue = Queue()
        self.agent.on_event = lambda e: self.events.put(e)

    # ── Message helpers ───────────────────────────────────────────────────────

    def _push(self, role: str, content: str) -> None:
        self.messages.append(_UIMsg(role, content))

    def _flush_stream(self) -> None:
        if self.messages and self.messages[-1].role == "assistant_stream":
            self.messages[-1].role = "assistant"

    # ── Event processing ──────────────────────────────────────────────────────

    def _drain(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                t = ev.get("type", "")

                if t == "token":
                    txt = ev.get("text", "")
                    if self.messages and self.messages[-1].role == "assistant_stream":
                        self.messages[-1].content += txt
                    else:
                        self.messages.append(_UIMsg("assistant_stream", txt))

                elif t == "tool_start":
                    self._flush_stream()
                    n = ev.get("name", "?")
                    a = json.dumps(ev.get("args", {}))
                    a = (a[:100] + "…") if len(a) > 100 else a
                    self._push("tool_start", f"{n}({a})")

                elif t == "tool_end":
                    n = ev.get("name", "?")
                    r = ev.get("result", "")
                    r = (r[:160] + "…") if len(r) > 160 else r
                    self._push("tool_end", f"{n} → {r}")

                elif t == "agent_spawn":
                    self._flush_stream()
                    depth = ev.get("depth", "?")
                    task = ev.get("task_preview", "")
                    self._push("agent_spawn", f"[depth {depth}] {task}")

                elif t == "error":
                    self._flush_stream()
                    self._push("error", ev.get("message", "unknown error"))
                    self.loading = False

                elif t == "done":
                    self._flush_stream()
                    self.loading = False

        except Empty:
            pass

    # ── Worker ────────────────────────────────────────────────────────────────

    def _worker(self, text: str) -> None:
        try:
            self.agent.chat(text)
            self.events.put({"type": "done"})
        except Exception as exc:
            self.events.put({"type": "error", "message": str(exc)})

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _build_render_lines(self, w: int) -> list[tuple[str, int, bool]]:
        """Flatten all messages into (text, color_pair, bold) tuples."""
        lines: list[tuple[str, int, bool]] = []
        indent = "    "
        for msg in self.messages:
            cp = msg.color()
            header = f"[{msg.ts}] {msg.label()} "
            lines.append((header, cp, True))
            cw = max(1, w - len(indent))
            for wl in _wrap_text(msg.content, cw):
                lines.append((indent + wl, cp, False))
            lines.append(("", _CP_BORDER, False))
        return lines

    def _render_messages(self, win, h: int, w: int) -> int:
        if w < 10 or h < 1:
            return 0
        lines = self._build_render_lines(w)
        total = len(lines)
        max_scroll = max(0, total - h)
        self.scroll = min(self.scroll, max_scroll)
        start = max(0, total - h - self.scroll)
        visible = lines[start: start + h]

        win.erase()
        for i, (text, cp, bold) in enumerate(visible):
            if i >= h:
                break
            attr = curses.color_pair(cp)
            if bold:
                attr |= curses.A_BOLD
            try:
                win.addnstr(i, 0, text, w - 1, attr)
            except curses.error:
                pass
        return total

    def _render_header(self, win, w: int) -> None:
        model = self.config.model.split("/")[-1]
        tok = self.agent.context.estimated_tokens
        max_tok = self.config.max_tokens
        pct = int(tok / max_tok * 100) if max_tok else 0
        comp = self.agent.context.compaction_count
        spin = (" " + _SPINNER[self.spinner_i % len(_SPINNER)]) if self.loading else ""
        self.spinner_i += 1

        bar = (
            f"  LLM CHAT  │  {model}  │  tokens ~{tok}/{max_tok} ({pct}%)  │"
            f"  compactions {comp}  │  msgs {len(self.messages)}{spin}  "
        )
        bar = bar[: w - 1].ljust(w - 1)
        try:
            win.erase()
            win.addnstr(0, 0, bar, w - 1, curses.color_pair(_CP_HEADER) | curses.A_BOLD)
        except curses.error:
            pass

    def _render_input(self, win, w: int) -> None:
        try:
            win.erase()
            win.attron(curses.color_pair(_CP_BORDER))
            win.hline(0, 0, curses.ACS_HLINE, w - 1)
            win.attroff(curses.color_pair(_CP_BORDER))

            prompt = "> "
            max_buf_w = w - len(prompt) - 2
            buf = self.input_buf[-max_buf_w:] if len(self.input_buf) > max_buf_w else self.input_buf

            win.addstr(1, 0, prompt, curses.color_pair(_CP_HEADER) | curses.A_BOLD)
            win.addnstr(1, len(prompt), buf, max_buf_w, curses.color_pair(_CP_INPUT) | curses.A_BOLD)

            cx = len(prompt) + min(len(self.input_buf), max_buf_w)
            try:
                win.move(1, cx)
            except curses.error:
                pass
        except curses.error:
            pass

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, stdscr) -> None:
        curses.start_color()
        curses.use_default_colors()

        # Green-terminal aesthetic
        curses.init_pair(_CP_HEADER,    curses.COLOR_BLACK,   curses.COLOR_GREEN)
        curses.init_pair(_CP_USER,      curses.COLOR_GREEN,   -1)
        curses.init_pair(_CP_AI,        curses.COLOR_GREEN,   -1)
        curses.init_pair(_CP_AI_STREAM, curses.COLOR_GREEN,   -1)
        curses.init_pair(_CP_TOOL,      curses.COLOR_YELLOW,  -1)
        curses.init_pair(_CP_SYS,       curses.COLOR_CYAN,    -1)
        curses.init_pair(_CP_INPUT,     curses.COLOR_WHITE,   -1)
        curses.init_pair(_CP_ERROR,     curses.COLOR_RED,     -1)
        curses.init_pair(_CP_AGENT,     curses.COLOR_MAGENTA, -1)
        curses.init_pair(_CP_BORDER,    curses.COLOR_GREEN,   -1)

        curses.curs_set(1)
        stdscr.nodelay(True)
        stdscr.keypad(True)

        model_short = self.config.model.split("/")[-1]
        tool_names = [t.name for t in self.agent.tools]
        self._push("system", f"LLM Chat ready  │  model: {self.config.model}")
        self._push("system", f"Tools loaded: {tool_names if tool_names else 'none'}")
        self._push(
            "info",
            "↑↓ scroll  │  PgUp/PgDn half-page  │  Home/End  │  Esc clear input  │  Ctrl+L clear  │  Ctrl+C quit",
        )

        while True:
            H, W = stdscr.getmaxyx()
            h_hdr   = 1
            h_input = 3
            h_msgs  = max(1, H - h_hdr - h_input)

            hdr_win = stdscr.derwin(h_hdr,   W, 0,             0)
            msg_win = stdscr.derwin(h_msgs,  W, h_hdr,         0)
            inp_win = stdscr.derwin(h_input, W, h_hdr + h_msgs, 0)

            self._drain()
            self._render_header(hdr_win, W)
            total_lines = self._render_messages(msg_win, h_msgs, W)
            self._render_input(inp_win, W)

            stdscr.noutrefresh()
            hdr_win.noutrefresh()
            msg_win.noutrefresh()
            inp_win.noutrefresh()
            curses.doupdate()

            try:
                key = stdscr.getch()
            except curses.error:
                key = -1

            if key == -1:
                time.sleep(0.033)   # ~30 fps
                continue

            if key == 3:                             # Ctrl+C — quit
                break
            elif key == 12:                          # Ctrl+L — clear display
                self.messages.clear()
                self.scroll = 0

            elif key == curses.KEY_PPAGE:            # PgUp
                self.scroll += h_msgs // 2
            elif key == curses.KEY_NPAGE:            # PgDn
                self.scroll = max(0, self.scroll - h_msgs // 2)
            elif key == curses.KEY_UP:
                self.scroll += 3
            elif key == curses.KEY_DOWN:
                self.scroll = max(0, self.scroll - 3)
            elif key == curses.KEY_HOME:
                self.scroll = total_lines           # scroll to top
            elif key == curses.KEY_END:
                self.scroll = 0                     # scroll to bottom

            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.input_buf = self.input_buf[:-1]
            elif key == 27:                          # Escape — clear input
                self.input_buf = ""

            elif key in (ord("\n"), curses.KEY_ENTER):
                text = self.input_buf.strip()
                if text and not self.loading:
                    self.input_buf = ""
                    self.scroll = 0
                    self._push("user", text)
                    self.loading = True
                    threading.Thread(
                        target=self._worker, args=(text,), daemon=True
                    ).start()

            elif 32 <= key <= 126:                   # Printable ASCII
                self.input_buf += chr(key)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM Chat with Agent Support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python chat.py\n"
            "  python chat.py --config local.json\n"
            "  python chat.py --tools-dir ./my_tools\n"
            "  python chat.py --host 192.168.1.10 --port 8080\n"
            "  python chat.py --host localhost --port 11434   # Ollama\n"
            "\n"
            "Priority for base_url: --host/--port  >  LLM_BASE_URL env  >  config.json\n"
            "For OpenRouter set base_url to https://openrouter.ai/api/v1\n"
        ),
    )
    parser.add_argument("--config",    default="config.json", help="Path to config file")
    parser.add_argument("--tools-dir", default="tools",       help="Path to tools directory")
    parser.add_argument("--host",      default=None,          help="llama-server host IP or hostname")
    parser.add_argument("--port",      default=None, type=int, help="llama-server port (e.g. 8080)")
    args = parser.parse_args()

    config = Config(args.config)

    # CLI host/port override — highest priority
    if args.host or args.port:
        host = args.host or "localhost"
        port = args.port or 8080
        config.data["base_url"] = f"http://{host}:{port}/v1"
        # llama-server/Ollama don't require a real key
        if not config.api_key or config.api_key == "YOUR_API_KEY_HERE":
            config.data["api_key"] = "not-needed"

    if not Path(args.config).exists():
        config.save(args.config)
        print(f"Created default config: {args.config}")
        print("Edit it to set your API key and model, then run again.")
        return

    if not config.api_key:
        print(f"No API key configured.")
        print(f"  Set 'api_key' in {args.config}")
        print(f"  or export OPENROUTER_API_KEY=sk-...")
        print(f"  or use --host / --port for a local server (no key required)")
        return

    # Load tools
    print("Loading tools...")
    tools, tool_errors = load_tools(args.tools_dir)
    for err in tool_errors:
        print(f"  ✗  {err}")
    if tools:
        print(f"  ✓  Loaded: {[t.name for t in tools]}")
    else:
        print("  (no tools loaded — continuing in plain chat mode)")

    time.sleep(0.3)

    llm = LLMClient(config)
    agent = Agent(
        config=config,
        llm=llm,
        tools=tools,
        system_prompt="You are a helpful, knowledgeable AI assistant.",
        max_depth=config.max_agent_depth,
    )

    ui = ChatUI(agent, config)
    try:
        curses.wrapper(ui.run)
    except KeyboardInterrupt:
        pass
    finally:
        llm.close()
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
