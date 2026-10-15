#!/usr/bin/env python3
"""Export original User/Agent text from the invoking Codex session's ancestry.

History boundary traversal is adapted from Ann-Diana/codex-project-chat-exporter;
see ../references/upstream.md and ../LICENSE.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import filecmp
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID


class ExportError(Exception):
    pass


UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
CONTEXT_TAGS = (
    "environment_context", "permissions instructions", "collaboration_mode",
    "apps_instructions", "skills_instructions", "plugins_instructions", "skill",
    "external_codex_apps_open_page",
)
CONTEXT_BLOCK = re.compile(
    r"^\s*<(?P<tag>" + "|".join(map(re.escape, CONTEXT_TAGS))
    + r")(?:\s[^>]*)?>.*?</(?P=tag)>", re.S,
)
AGENTS_BLOCK = re.compile(
    r"^\s*# AGENTS\.md instructions(?: for [^\r\n]+)?[ \t]*\r?\n\s*"
    r"(?:<INSTRUCTIONS>.*?</INSTRUCTIONS>|<!-- AGENTMD_START -->.*?<!-- AGENTMD_END -->)",
    re.S,
)
INJECTED_CONTENT_KINDS = {
    "agents_md.instructions", "environments.environment_context",
    "additional_content.codex_apps_open_page",
}


@dataclass(frozen=True)
class Source:
    path: Path
    rollout_id: str
    meta: dict
    first_ordinal: int | None
    size: int

    @property
    def session_id(self):
        return self.meta["id"]


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    session_id: str
    turn: str
    kind: str
    item_id: str | None = None
    phase: str | None = None
    raw_text: str = ""
    authored: bool = False


@dataclass
class Conversation:
    messages: list[Message]
    completed_answers: dict[tuple[str, str], set[str]]


def clean_response_text(text: str) -> str:
    """Remove leading harness blocks, leaving quoted/authored prose untouched."""
    while match := CONTEXT_BLOCK.match(text) or AGENTS_BLOCK.match(text):
        text = text[match.end():]
    return text


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(
        part["text"] for part in content or []
        if isinstance(part, dict)
        and part.get("type") in {"input_text", "output_text", "text"}
        and isinstance(part.get("text"), str)
    )


def user_content(payload: dict, content) -> tuple[str, bool]:
    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    kinds = metadata.get("content_item_kinds", [])
    if isinstance(content, list) and kinds and len(kinds) == len(content):
        parts = []
        for part, kind in zip(content, kinds):
            if kind in INJECTED_CONTENT_KINDS:
                continue
            text = text_content([part])
            parts.append(text if kind == "user.text" else clean_response_text(text))
        return "\n\n".join(text for text in parts if text), "user.text" in kinds
    return clean_response_text(text_content(content)), False


def conversation_record(record: dict, session_id: str, turn: str) -> Message | None:
    payload = record.get("payload", {})
    record_type, item_type = record.get("type"), payload.get("type")
    authored = False
    if record_type == "response_item" and item_type == "message":
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            return None
        content = payload.get("content")
        raw_text = text_content(content)
        text = raw_text
        if role == "user":
            text, authored = user_content(payload, content)
        kind = "response"
    elif record_type == "event_msg" and item_type in {"user_message", "agent_message"}:
        role = "user" if item_type == "user_message" else "assistant"
        raw_text = text = payload.get("message", "")
        if role == "user":
            text, authored = user_content(payload, text)
        kind = "event"
    else:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    return Message(role, text, session_id, metadata.get("turn_id") or turn,
                   kind, payload.get("id"), payload.get("phase"), raw_text, authored)


def remove_mirrors(messages: list[Message]) -> list[Message]:
    """Pair occurrences by raw or cleaned text; preserve provenance and phase."""
    def keys(message):
        return [(message.session_id, message.turn, message.role, text)
                for text in dict.fromkeys((message.text.strip(), message.raw_text.strip()))]

    events = defaultdict(deque)
    for index, message in enumerate(messages):
        if message.kind == "event":
            for key in keys(message):
                events[key].append(index)
    paired = set()
    replacements = {}
    for index, response in enumerate(messages):
        if response.kind != "response":
            continue
        for key in keys(response):
            candidates = events[key]
            while candidates and candidates[0] in paired:
                candidates.popleft()
            if not candidates:
                continue
            event_index = candidates.popleft()
            event = messages[event_index]
            if response.role == "assistant":
                chosen = replace(response, phase=response.phase or event.phase)
            else:
                chosen = response if response.authored else event
            paired.update((index, event_index))
            replacements[min(index, event_index)] = chosen
            break

    seen_ids = set()
    result = []
    for index, message in enumerate(messages):
        if index in replacements:
            message = replacements[index]
        elif index in paired:
            continue
        if message.item_id:
            identity = message.session_id, message.turn, message.role, message.item_id
            if identity in seen_ids:
                continue
            seen_ids.add(identity)
        result.append(message)
    return result


class SessionStore:
    def __init__(self, codex_home: Path):
        self.paths = sorted(
            path for folder in ("sessions", "archived_sessions")
            for path in (codex_home / folder).rglob("rollout-*.jsonl*")
            if path.is_file()
        )
        self.sources: dict[Path, Source] = {}
        self.created: dict[str, str] = {}
        self.lineages: dict[str, list[str]] = {}

    def source(self, path: Path) -> Source:
        if path in self.sources:
            return self.sources[path]
        if path.suffix == ".zst":
            raise ExportError(f"Compressed rollout is not supported: {path}")
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            line = handle.readline(size)
        record = self.decode(line, path, 1)
        if record.get("type") != "session_meta" or not record.get("payload", {}).get("id"):
            raise ExportError(f"Missing session metadata: {path}")
        ids = UUID_PATTERN.findall(path.name)
        if not ids:
            raise ExportError(f"Missing rollout identity in filename: {path}")
        source = Source(path, ids[-1].lower(), record["payload"], record.get("ordinal"), size)
        self.sources[path] = source
        timestamp = source.meta.get("timestamp")
        if timestamp:
            previous = self.created.get(source.session_id)
            if previous is None or timestamp < previous:
                self.created[source.session_id] = timestamp
        return source

    @staticmethod
    def decode(line: bytes, path: Path, number: int) -> dict:
        if not line.endswith(b"\n"):
            raise ExportError(f"Incomplete JSONL record at {path}:{number}; retry after the writer finishes")
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ExportError(f"Invalid JSONL record at {path}:{number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ExportError(f"Expected a JSON object at {path}:{number}")
        return record

    def records(self, source: Source, end: int | None = None):
        cutoff = source.size if end is None else end
        with source.path.open("rb") as handle:
            number = 0
            while handle.tell() < cutoff:
                line = handle.readline(cutoff - handle.tell())
                number += 1
                if not line:
                    raise ExportError(f"History byte boundary exceeds source length: {source.path}")
                if line.strip():
                    yield self.decode(line, source.path, number)

    def candidates(self, identifier: str, *, rollout: bool):
        candidates = []
        for path in self.paths:
            ids = [value.lower() for value in UUID_PATTERN.findall(path.name)]
            if not ids or identifier.lower() != ids[-1 if rollout else 0]:
                continue
            source = self.source(path)
            if rollout or source.session_id.lower() == identifier.lower():
                candidates.append(source)
        return candidates

    @staticmethod
    def collapse_identical(candidates):
        unique = []
        for source in candidates:
            if not any(filecmp.cmp(source.path, other.path, shallow=False) for other in unique):
                unique.append(source)
        return unique

    def current(self, identifier: str) -> Source:
        candidates = self.collapse_identical(self.candidates(identifier, rollout=False))
        if not candidates:
            raise ExportError(f"Session not found: {identifier}")
        referenced = {
            s.meta["history_base"]["thread_id"].lower()
            for s in candidates if s.meta.get("history_base")
        }
        terminals = [s for s in candidates if s.rollout_id not in referenced]
        if len(terminals) != 1:
            raise ExportError(f"Ambiguous session history for {identifier}; cannot choose a unique terminal rollout")
        terminal = terminals[0]
        connected = {terminal.rollout_id}
        cursor = terminal
        by_id = {s.rollout_id: s for s in candidates}
        while cursor.meta.get("history_base"):
            parent_id = cursor.meta["history_base"]["thread_id"].lower()
            if parent_id not in by_id:
                break
            if parent_id in connected:
                raise ExportError(f"Cyclic session history for {identifier}")
            connected.add(parent_id)
            cursor = by_id[parent_id]
        if len(connected) != len(candidates):
            raise ExportError(f"Unlinked rollout versions for {identifier}; refusing to omit history")
        return terminal

    def referenced_source(self, base: dict) -> Source:
        if not isinstance(base, dict) or not isinstance(base.get("thread_id"), str):
            raise ExportError("Invalid history_base reference")
        end, ordinal = base.get("end_byte_offset"), base.get("end_ordinal_exclusive")
        if type(end) is not int or end <= 0 or type(ordinal) is not int or ordinal <= 0:
            raise ExportError("Invalid history byte/ordinal boundary")
        candidates = self.candidates(base["thread_id"], rollout=True)
        if not candidates:
            raise ExportError(f"Missing parent rollout: {base['thread_id']}")
        for source in candidates:
            if source.meta.get("history_mode") != "paginated":
                raise ExportError(f"Referenced parent is not paginated: {source.path}")
            last = None
            for record in self.records(source, end):
                value = record.get("ordinal")
                if type(value) is not int or value < 0 or value >= ordinal:
                    raise ExportError(f"History ordinal/byte boundary mismatch: {source.path}")
                last = value
            if last != ordinal - 1:
                raise ExportError(f"History ordinal/byte boundary mismatch: {source.path}")
        first = candidates[0]
        for other in candidates[1:]:
            with first.path.open("rb") as left, other.path.open("rb") as right:
                remaining = end
                while remaining:
                    size = min(remaining, 1024 * 1024)
                    if left.read(size) != right.read(size):
                        raise ExportError(f"Conflicting parent prefixes: {base['thread_id']}")
                    remaining -= size
        return first

    def segment_messages(self, source: Source, end: int | None):
        messages = []
        completed_answers = {}
        turn = f"initial:{source.rollout_id}"
        paginated = source.meta.get("history_mode") == "paginated"
        base = source.meta.get("history_base")
        expected = base["end_ordinal_exclusive"] if base else 0
        if paginated and source.first_ordinal != expected:
            raise ExportError(f"Unexpected segment metadata ordinal: {source.path}")
        for index, record in enumerate(self.records(source, end)):
            payload = record.get("payload", {})
            if index == 0:
                continue
            if paginated and (type(record.get("ordinal")) is not int or record["ordinal"] < 0):
                raise ExportError(f"Invalid rollout ordinal: {source.path}")
            if record.get("type") == "turn_context" or (
                record.get("type") == "event_msg" and payload.get("type") == "task_started"
            ):
                turn = payload.get("turn_id") or turn
            if record.get("type") == "event_msg" and payload.get("type") == "task_complete":
                key = source.session_id, payload.get("turn_id") or turn
                completed = completed_answers.setdefault(key, set())
                final_text = payload.get("last_agent_message")
                if isinstance(final_text, str) and final_text.strip():
                    completed.add(final_text.strip())
            message = conversation_record(record, source.session_id, turn)
            if message:
                messages.append(message)
        return Conversation(messages, completed_answers)

    def history(self, source: Source, end=None, stack=()) -> Conversation:
        if source.rollout_id in stack:
            raise ExportError(f"Cyclic history reference: {source.rollout_id}")
        stack = (*stack, source.rollout_id)
        own = self.segment_messages(source, end)
        if base := source.meta.get("history_base"):
            if source.meta.get("history_mode") != "paginated":
                raise ExportError(f"history_base requires paginated mode: {source.path}")
            parent = self.referenced_source(base)
            inherited = self.history(parent, base["end_byte_offset"], stack)
            self.lineages[source.rollout_id] = list(dict.fromkeys(
                self.lineages[parent.rollout_id] + [source.session_id]
            ))
            if inherited.messages and parent.session_id == source.session_id:
                initial = f"initial:{source.rollout_id}"
                last_turn = inherited.messages[-1].turn
                own.messages = [replace(m, turn=last_turn) if m.turn == initial else m for m in own.messages]
                pending = own.completed_answers.pop((source.session_id, initial), None)
                if pending is not None:
                    own.completed_answers.setdefault((source.session_id, last_turn), set()).update(pending)
            for key, completed in inherited.completed_answers.items():
                own.completed_answers.setdefault(key, set()).update(completed)
            return Conversation(inherited.messages + own.messages, own.completed_answers)
        if parent_id := source.meta.get("forked_from_id"):
            # Copied forks already contain their inherited text. Match a verified
            # parent prefix to assign source sections, never append it a second time.
            parent = self.current(parent_id)
            parent_history = self.history(parent, stack=stack)
            inherited = remove_mirrors(parent_history.messages)
            self.lineages[source.rollout_id] = list(dict.fromkeys(
                self.lineages[parent.rollout_id] + [source.session_id]
            ))
            own.messages = remove_mirrors(own.messages)
            matched = 0
            for left, right in zip(inherited, own.messages):
                if (left.role, left.text) != (right.role, right.text):
                    break
                own.messages[matched] = replace(
                    right, session_id=left.session_id, turn=left.turn,
                    phase=left.phase if left.phase is not None else right.phase,
                )
                old_key, key = (right.session_id, right.turn), (left.session_id, left.turn)
                if old_key != key and old_key in own.completed_answers:
                    own.completed_answers.setdefault(key, set()).update(own.completed_answers.pop(old_key))
                # Borrow evidence only for an inherited answer actually copied,
                # never for the parent's later conversation or a future answer.
                parent_completed = parent_history.completed_answers.get(key)
                if parent_completed is not None:
                    if not parent_completed:
                        own.completed_answers.setdefault(key, set())
                    elif left.role == "assistant" and left.text.strip() in parent_completed:
                        own.completed_answers.setdefault(key, set()).add(left.text.strip())
                matched += 1
            if inherited and not matched:
                raise ExportError(f"Cannot establish copied history boundary for {source.session_id}")
            return own
        self.lineages[source.rollout_id] = [source.session_id]
        return own

    def final_conversation(self, conversation: Conversation) -> list[Message]:
        selected = []
        for message in remove_mirrors(conversation.messages):
            if message.role == "user" or message.phase == "final_answer":
                selected.append(message)
            elif message.phase == "commentary":
                continue
            elif message.phase is None:
                completed = conversation.completed_answers.get((message.session_id, message.turn))
                if completed is None:
                    raise ExportError(f"Cannot identify final answer in session {message.session_id}, turn {message.turn}")
                if message.text.strip() in completed:
                    selected.append(message)
            else:
                raise ExportError(f"Unsupported assistant phase: {message.phase}")
        originals = defaultdict(set)
        for message in selected:
            if message.role == "assistant":
                originals[message.session_id, message.turn].add(message.text.strip())
        for key, completed in conversation.completed_answers.items():
            if completed - originals[key]:
                raise ExportError(f"Missing original final answer in session {key[0]}, turn {key[1]}")
        return selected


def format_time(value: str | None) -> str:
    if not value:
        return "unknown"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ExportError(f"Source timestamp has no timezone: {value}")
    return parsed.isoformat()


def export_session(session_id: str, codex_home: Path, output_dir: Path) -> Path:
    session_id = str(UUID(session_id))
    store = SessionStore(codex_home)
    source = store.current(session_id)
    messages = store.final_conversation(store.history(source))
    if not messages:
        raise ExportError("No User/Agent text was found")
    lineage = store.lineages[source.rollout_id]
    header = (
        f"# Session {session_id}\n\n"
        f"- Session: `{session_id}`\n"
        f"- Created: {format_time(store.created.get(session_id))}\n"
        f"- Exported: {datetime.now(timezone.utc).isoformat()}\n"
        f"- Lineage: `{' → '.join(lineage)}`\n"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"session-{session_id}.md"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_dir,
            prefix=".session-to-md-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(header)
            previous = None
            for message in messages:
                if message.session_id != previous:
                    section = "Current" if message.session_id == session_id else "History"
                    handle.write(f"\n## {section} · {message.session_id}\n")
                    previous = message.session_id
                role = "User" if message.role == "user" else "Agent"
                handle.write(f"\n### {role}\n\n")
                handle.write(message.text)
                handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return target.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "session-exports")
    args = parser.parse_args()
    if not args.session_id:
        parser.error("Current session ID is unavailable; supply the invoking chat's --session-id")
    try:
        target = export_session(args.session_id, args.codex_home.expanduser(), args.output_dir.expanduser())
    except (ExportError, OSError, ValueError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
