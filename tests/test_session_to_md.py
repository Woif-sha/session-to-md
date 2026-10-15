import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_to_md.py"
SPEC = importlib.util.spec_from_file_location("session_to_md", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)

ROOT = "11111111-1111-7111-8111-111111111111"
CHILD = "22222222-2222-7222-8222-222222222222"
CURRENT = "33333333-3333-7333-8333-333333333333"
ROLLOUT = "44444444-4444-7444-8444-444444444444"
OTHER = "55555555-5555-7555-8555-555555555555"
STAMP = "2026-10-15T01:00:00Z"


def meta(sid, ordinal=0, **extra):
    return {"type": "session_meta", "ordinal": ordinal, "payload": {
        "id": sid, "timestamp": STAMP, "history_mode": "paginated", **extra,
    }}


def message(role, text, ordinal=1, item_id=None):
    return {"type": "response_item", "ordinal": ordinal, "payload": {
        "type": "message", "role": role, "id": item_id,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }}


def event(role, text, ordinal=1):
    return {"type": "event_msg", "ordinal": ordinal, "payload": {
        "type": "user_message" if role == "user" else "agent_message", "message": text,
    }}


def turn(identifier, ordinal):
    return {"type": "turn_context", "ordinal": ordinal, "payload": {"turn_id": identifier}}


def encoded(records):
    return b"".join(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n" for r in records)


def boundary(rollout, records):
    return {"thread_id": rollout, "end_byte_offset": len(encoded(records)),
            "end_ordinal_exclusive": records[-1]["ordinal"] + 1}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex"
        self.output = self.root / "output"

    def write(self, sid, records, *, archived=False, rollout_id=None):
        folder = self.home / ("archived_sessions" if archived else "sessions") / "2026/10/15"
        folder.mkdir(parents=True, exist_ok=True)
        suffix = f"_{rollout_id}" if rollout_id else ""
        path = folder / f"rollout-2026-10-15T01-00-00-{sid}{suffix}.jsonl"
        path.write_bytes(encoded(records))
        return path

    def export(self, sid=CURRENT):
        path = exporter.export_session(sid, self.home, self.output)
        self.assertEqual(path.name, f"session-{sid}.md")
        self.assertEqual(list(self.output.iterdir()), [path])
        return path.read_text(encoding="utf-8")

    def test_original_replies_survive_without_system_or_tool_traffic(self):
        text = "  完整回复\n\n```python\nprint('中文')\n```\n" + "实践内容🚀" * 15000 + "\n  "
        self.write(CURRENT, [meta(CURRENT), turn("a", 1),
            message("system", "SYSTEM_SECRET", 2), message("developer", "DEVELOPER_SECRET", 3),
            message("user", "# AGENTS.md instructions for C:/project\nINJECTED_RULE", 4),
            message("user", "<environment_context>INJECTED_ENV</environment_context>", 5),
            message("user", "<external_codex_apps_open_page>{}</external_codex_apps_open_page>", 6),
            message("user", "用户提问", 7),
            {"type": "response_item", "ordinal": 8, "payload": {"type": "function_call_output", "output": "TOOL_SECRET"}},
            message("assistant", "过程中的实践判断", 9), message("assistant", text, 10)])
        output = self.export()
        self.assertIn(text, output)
        self.assertIn("过程中的实践判断", output)
        for secret in ("SYSTEM_SECRET", "DEVELOPER_SECRET", "INJECTED_RULE", "INJECTED_ENV", "TOOL_SECRET", "external_codex_apps_open_page"):
            self.assertNotIn(secret, output)

    def test_multilevel_ancestry_honors_each_fork_boundary(self):
        parent = [meta(ROOT), message("user", "ROOT_QUESTION", 1), message("assistant", "ROOT_ANSWER", 2)]
        self.write(ROOT, parent + [message("user", "ROOT_AFTER_FORK", 3)], archived=True)
        child = [meta(CHILD, 3, forked_from_id=ROOT, history_base=boundary(ROOT, parent)),
                 message("user", "CHILD_QUESTION", 4), message("assistant", "CHILD_ANSWER", 5)]
        self.write(CHILD, child + [message("assistant", "CHILD_AFTER_FORK", 6)])
        self.write(CURRENT, [meta(CURRENT, 6, forked_from_id=CHILD, history_base=boundary(CHILD, child)),
                             message("user", "CURRENT_QUESTION", 7), message("assistant", "CURRENT_ANSWER", 8)])
        self.write(OTHER, [meta(OTHER), message("assistant", "UNRELATED_NEWEST_SESSION", 1)])
        output = self.export()
        expected = ["ROOT_QUESTION", "ROOT_ANSWER", "CHILD_QUESTION", "CHILD_ANSWER", "CURRENT_QUESTION", "CURRENT_ANSWER"]
        positions = [output.index(text) for text in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(all(output.count(text) == 1 for text in expected))
        self.assertNotIn("AFTER_FORK", output)
        self.assertNotIn("UNRELATED_NEWEST_SESSION", output)
        self.assertIn(f"- Lineage: `{ROOT} → {CHILD} → {CURRENT}`", output)
        for sid in (ROOT, CHILD):
            self.assertIn(f"## History · {sid}", output)
        self.assertIn(f"## Current · {CURRENT}", output)

    def test_same_session_rollout_continuation_is_not_a_new_branch(self):
        prefix = [meta(CURRENT), message("user", "BEFORE_CONTINUATION", 1)]
        self.write(CURRENT, prefix + [message("assistant", "DISCARDED_SUFFIX", 2)])
        self.write(CURRENT, [meta(CURRENT, 2, timestamp="2026-10-15T02:00:00Z", history_base=boundary(CURRENT, prefix)),
                             message("assistant", "AFTER_CONTINUATION", 3)], rollout_id=ROLLOUT)
        output = self.export()
        self.assertIn("BEFORE_CONTINUATION", output)
        self.assertIn("AFTER_CONTINUATION", output)
        self.assertNotIn("DISCARDED_SUFFIX", output)
        self.assertEqual(output.count("## Current ·"), 1)
        self.assertNotIn("## History ·", output)
        self.assertIn("- Created: 2026-10-15T01:00:00+00:00", output)

    def test_mirrors_removed_but_real_repeated_messages_retained(self):
        self.write(CURRENT, [meta(CURRENT), turn("a", 1),
            event("user", "same question", 2), message("user", "same question", 3, "user-a"),
            message("assistant", "same answer", 4, "answer-a"), event("assistant", "same answer", 5),
            message("assistant", "same answer", 6, "answer-a"),
            turn("b", 7), event("user", "same question", 8), message("user", "same question", 9, "user-b"),
            message("assistant", "same answer", 10, "answer-b"), event("assistant", "same answer", 11)])
        output = self.export()
        self.assertEqual(output.count("same question"), 2)
        self.assertEqual(output.count("same answer"), 2)

    def test_user_authored_instruction_quotes_are_not_injection(self):
        text = "# AGENTS.md instructions for example\n这段是用户主动提供的材料。"
        self.write(CURRENT, [meta(CURRENT), turn("a", 1), event("user", text, 2),
                             message("user", text, 3), message("assistant", "答复", 4)])
        self.assertIn(text, self.export())

    def test_mirror_can_straddle_same_session_rollout_boundary(self):
        prefix = [meta(CURRENT), turn("a", 1), event("user", "QUESTION_ONCE", 2)]
        self.write(CURRENT, prefix)
        self.write(CURRENT, [meta(CURRENT, 3, history_base=boundary(CURRENT, prefix)),
                             message("user", "QUESTION_ONCE", 4), message("assistant", "ANSWER_ONCE", 5)], rollout_id=ROLLOUT)
        self.assertEqual(self.export().count("QUESTION_ONCE"), 1)

    def test_copied_forks_attribute_prefix_without_appending_parent_suffix(self):
        parent = [meta(ROOT, history_mode="legacy"), message("user", "ORIGINAL_QUESTION", 1), message("assistant", "ORIGINAL_ANSWER", 2)]
        self.write(ROOT, parent + [message("user", "PARENT_LATER", 3)])
        child = [meta(CHILD, history_mode="legacy", forked_from_id=ROOT), *parent,
                 message("user", "CHILD_QUESTION", 3), message("assistant", "CHILD_ANSWER", 4)]
        self.write(CHILD, child + [message("user", "CHILD_LATER", 5)])
        self.write(CURRENT, [meta(CURRENT, history_mode="legacy", forked_from_id=CHILD), *child,
                             message("user", "CURRENT_QUESTION", 5), message("assistant", "CURRENT_ANSWER", 6)])
        output = self.export()
        self.assertNotIn("PARENT_LATER", output)
        self.assertNotIn("CHILD_LATER", output)
        self.assertEqual(output.count("ORIGINAL_ANSWER"), 1)
        self.assertIn(f"- Lineage: `{ROOT} → {CHILD} → {CURRENT}`", output)

    def test_missing_parent_does_not_publish_a_partial_export(self):
        self.write(CURRENT, [meta(CURRENT, 2, history_base={"thread_id": ROOT, "end_byte_offset": 100, "end_ordinal_exclusive": 2}),
                             message("assistant", "CHILD_ONLY", 3)])
        with self.assertRaisesRegex(exporter.ExportError, "Missing parent"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_invalid_byte_or_ordinal_boundary_is_an_error(self):
        parent = [meta(ROOT), message("user", "prefix", 1)]
        self.write(ROOT, parent)
        for change in ({"end_byte_offset": len(encoded(parent)) - 1}, {"end_ordinal_exclusive": 9}, {"end_byte_offset": 999999}):
            with self.subTest(change=change):
                base = {**boundary(ROOT, parent), **change}
                self.write(CURRENT, [meta(CURRENT, base["end_ordinal_exclusive"], history_base=base)])
                with self.assertRaises(exporter.ExportError):
                    self.export()
                self.assertFalse(self.output.exists())

    def test_ambiguous_rollouts_are_not_selected_by_modification_time(self):
        self.write(CURRENT, [meta(CURRENT), message("assistant", "first", 1)])
        self.write(CURRENT, [meta(CURRENT), message("assistant", "second", 1)], rollout_id=ROLLOUT)
        with self.assertRaisesRegex(exporter.ExportError, "Ambiguous"):
            self.export()

    def test_unfinished_source_leaves_previous_export_intact(self):
        source = self.write(CURRENT, [meta(CURRENT), message("user", "valid", 1)])
        previous = self.export()
        with source.open("ab") as handle:
            handle.write(b'{"type":"response_item"')
        with self.assertRaisesRegex(exporter.ExportError, "Incomplete JSONL"):
            self.export()
        target = self.output / f"session-{CURRENT}.md"
        self.assertEqual(target.read_text(encoding="utf-8"), previous)

    def test_cli_uses_invoking_session_environment_and_requires_it(self):
        self.write(CURRENT, [meta(CURRENT), message("user", "current question", 1), message("assistant", "current answer", 2)])
        env = {**os.environ, "CODEX_THREAD_ID": CURRENT, "CODEX_HOME": str(self.home)}
        result = subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(self.output)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).name, f"session-{CURRENT}.md")
        env.pop("CODEX_THREAD_ID")
        result = subprocess.run([sys.executable, str(SCRIPT), "--output-dir", str(self.output)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Current session ID is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
