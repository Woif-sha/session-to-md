---
name: session-to-md
description: >
  Export the current Codex conversation and its pre-branch history as original
  User/Agent Markdown for another agent to read. Use for "导出当前会话",
  "保留 branch 之前的全部对话", or "export full session to Markdown", including
  requests to preserve original practice records and global conversation context.
---

# Session to MD

## Always Read

None.

## Common Tasks

- Export the invoking session: run the bundled Python exporter, then read back the file's Session field and confirm it matches the invoking chat. The script owns history reconstruction, filtering, deduplication, and Markdown rendering; the model does not rewrite the transcript.

```powershell
python <skill-dir>/scripts/session_to_md.py --output-dir <destination>
```

The current session comes from `CODEX_THREAD_ID`. If it is unavailable, obtain the current chat's actual ID from trusted task context and pass `--session-id <id>`. Never substitute the newest file, another chat, or a parent ID. `CODEX_HOME` is respected, defaulting to `~/.codex`.

Use the user's destination when supplied; otherwise use `<current-workspace>/session-exports`. Return the absolute path of the single `session-<current-session-id>.md`, without pasting the entire transcript into chat.

## Content contract

- Preserve User and Agent text, including commentary and final answers, in source order and original language. Preserve code, whitespace, lists, tables, and links. Do not summarize, translate, extract lessons, shorten, or truncate answers.
- Include only the invoking session's ancestry, excluding parent changes after each fork and sibling branches.
- Remove system/developer messages, injected environment/AGENTS/skill context, reasoning, tool traffic, and mirrored log copies. Preserve actual repeated conversation messages and user-authored quotations of instructions.
- Generate only English framing: `Session`, `Created`, `Exported`, `Lineage`, `History`, `Current`, `User`, `Agent`, and `unknown`. Each source session gets one History section; the invoking session gets Current. Same-session rollout continuations do not create new branch sections.

## Known Gotchas

- A session ID can span several rollout files. `history_base` refers to a rollout identity and a byte/ordinal cutoff, not necessarily a different session. The exporter resolves these references before selecting visible messages; never export only the latest file.
- Missing sources, ambiguous lineage, invalid boundaries, and malformed complete records are errors. Report the concrete failure; do not replace missing original text with compacted summaries or claim completeness.
- The export freezes the available source prefixes. It cannot include the answer written after it runs. An unfinished JSONL record is an error; retry when the writer has finished it.

The script needs Python 3.10+ and the standard library. It reads local Codex files and writes one Markdown file; no uploads, imports, source modifications, or model API calls occur. When auditing or changing history resolution, read [the upstream algorithm provenance](references/upstream.md); ordinary exports do not need that reference.
