import importlib
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import extractor
import retro
from config import load_config


class RetroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixture = ROOT / "tests" / "fixtures" / "claude.jsonl"
        self.config_path = self.root / "config.json"
        config = {
            "default_agent": "claude",
            "defaults": {
                "output": {"dir": str(self.root / "out-{agent}")},
                "state_path": str(self.root / "out-{agent}" / "state.json"),
                "decisions_ledger": str(self.root / "out-{agent}" / "ledger.jsonl"),
                "memory_index": [str(self.root / "memory" / "**/*.md")],
                "persistence_targets": {kind: str(self.root / kind) for kind in retro.KINDS},
                "tool_semantics": {"shell": ["Bash"], "workflow": ["Skill"]},
                "scheduler": {"weekday": 1, "hour": 9, "minute": 0,
                    "plist_path": str(self.root / "job-{agent}.plist"),
                    "mine_command": ["local-harness", "mine"], "label": "test.{agent}"}},
            "agents": {"claude": {"format": "claude-code-jsonl", "transcript_globs": [str(fixture)]}}
        }
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.config = load_config(self.config_path, "claude")

    def tearDown(self):
        self.temp.cleanup()

    def test_deep_merge_substitution_and_extract(self):
        self.assertTrue(self.config["output"]["dir"].endswith("out-claude"))
        raw = extractor.extract(self.config, "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z")
        self.assertEqual(len(raw["lenses"]["corrections"]), 2)
        self.assertEqual(len(raw["lenses"]["improvements"]), 1)
        self.assertEqual(raw["lenses"]["repetition"][0]["session_count"], 2)
        self.assertEqual(raw["repetition"]["commands"]["python3 -m unittest"], 2)
        self.assertEqual(raw["repetition"]["workflows"]["review"], 2)
        serialized = json.dumps(raw)
        self.assertNotIn("old private correction", serialized)

    def test_config_rejects_bad_semantic_and_target_types(self):
        changed = json.loads(self.config_path.read_text())
        changed["defaults"]["tool_semantics"] = {"shell": [{}], "workflow": ["Skill"]}
        self.config_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            load_config(self.config_path)
        changed["defaults"]["tool_semantics"] = {"shell": ["Bash"], "workflow": ["Skill"]}
        changed["defaults"]["persistence_targets"]["rule"] = {}
        self.config_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "persistence_targets.rule"):
            load_config(self.config_path)

    def test_unknown_adapter_rejected(self):
        changed = json.loads(self.config_path.read_text())
        changed["agents"]["claude"]["format"] = "mystery"
        self.config_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "unknown transcript format"):
            extractor.extract(load_config(self.config_path), "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z")

    def test_state_advances_only_after_digest(self):
        retro.init_state(self.config)
        state_path = Path(self.config["state_path"])
        old = "2025-01-01T00:00:00Z"
        state_path.write_text(json.dumps({"schema_version": 1, "agent": "claude", "last_mined_at": old}))
        raw_path = self.root / "raw.json"
        raw_path.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl", "window": {"since": old, "until": "2025-02-01T00:00:00Z"}}))
        proposals = self.root / "proposals.json"
        proposals.write_text(json.dumps([{"kind": "rule", "text": "Prefer the configured test command.", "topic_key": "configured-test-command"}]))
        with mock.patch.object(retro, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                retro.finalize_mine(self.config, raw_path, proposals)
        self.assertEqual(json.loads(state_path.read_text())["last_mined_at"], old)
        digest = retro.finalize_mine(self.config, raw_path, proposals)
        self.assertTrue(digest.exists())
        self.assertEqual(json.loads(state_path.read_text())["last_mined_at"], "2025-02-01T00:00:00Z")

    def test_apply_accept_decline_skip_and_dedup(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True); state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"
        raw.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z", "until": "2025-02-01T00:00:00Z"}}))
        proposal_rows = [{"kind": "rule", "text": "Always run local checks.", "topic_key": "always-run-local-checks"},
                         {"kind": "feedback", "text": "Avoid guessing paths.", "topic_key": "avoid-guessing-paths"},
                         {"kind": "script", "text": "Format reports mechanically.", "topic_key": "mechanical-report-formatting", "artifact": {"filename": "format.py", "content": "#!/usr/bin/env python3\n"}}]
        proposals = self.root / "p.json"; proposals.write_text(json.dumps(proposal_rows))
        digest = retro.finalize_mine(self.config, raw, proposals)
        items = retro.digest_proposals(digest)
        with mock.patch("builtins.input", side_effect=["accept", "decline", "skip"]):
            retro.apply_digest(self.config, digest)
        self.assertTrue((self.root / "rule" / f"{items[0]['id']}.md").exists())
        self.assertFalse((self.root / "feedback" / f"{items[1]['id']}.md").exists())
        self.assertFalse((self.root / "script" / f"{items[2]['id']}.md").exists())
        ledger = [json.loads(row) for row in Path(self.config["decisions_ledger"]).read_text().splitlines()]
        self.assertEqual([row["decision"] for row in ledger], ["accepted", "declined"])
        self.assertEqual(retro.normalize_proposals(proposal_rows, self.config)[0]["kind"], "script")
        with mock.patch("builtins.input", side_effect=["", "", ""]):
            retro.apply_digest(self.config, digest)
        self.assertEqual(len(Path(self.config["decisions_ledger"]).read_text().splitlines()), 2)

    def test_apply_rolls_back_artifact_if_ledger_fails(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True); state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"
        raw.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z", "until": "2025-03-01T00:00:00Z"}}))
        proposals = self.root / "p.json"
        proposals.write_text(json.dumps([{"kind": "rule", "text": "Use a safe transaction.", "topic_key": "safe-transaction"}]))
        digest = retro.finalize_mine(self.config, raw, proposals)
        item = retro.digest_proposals(digest)[0]
        sentinel = self.root / "rule" / "other-operation.md"
        sentinel.parent.mkdir(parents=True); sentinel.write_text("do not delete", encoding="utf-8")
        with mock.patch("builtins.input", return_value="accept"), \
             mock.patch("apply.atomic_write", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                retro.apply_digest(self.config, digest)
        self.assertFalse((self.root / "rule" / f"{item['id']}.md").exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not delete")

    def test_finalize_retry_after_state_write_failure_is_idempotent(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"; raw.write_text(json.dumps({"agent": "claude",
            "format": "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z",
            "until": "2025-01-02T00:00:00Z"}}))
        proposal = self.root / "p.json"; proposal.write_text("[]")
        with mock.patch.object(retro, "write_json", side_effect=OSError("state fsync failed")):
            with self.assertRaises(OSError): retro.finalize_mine(self.config, raw, proposal)
        self.assertEqual(json.loads(state.read_text())["last_mined_at"], "2025-01-01T00:00:00Z")
        digest = retro.finalize_mine(self.config, raw, proposal)
        self.assertTrue(digest.exists())
        self.assertEqual(json.loads(state.read_text())["last_mined_at"], "2025-01-02T00:00:00Z")

    def test_finalize_rejects_stale_agent_and_traversal_window(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        proposal = self.root / "p.json"; proposal.write_text("[]")
        raw = self.root / "raw.json"
        raw.write_text(json.dumps({"agent": "other", "format": "claude-code-jsonl",
            "window": {"since": "2025-01-01T00:00:00Z", "until": "../../bad"}}))
        with self.assertRaises(ValueError): retro.finalize_mine(self.config, raw, proposal)
        raw.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl",
            "window": {"since": "2024-01-01T00:00:00Z", "until": "2025-01-02T00:00:00Z"}}))
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            retro.finalize_mine(self.config, raw, proposal)

    def test_digest_tamper_and_wrong_agent_rejected(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"; raw.write_text(json.dumps({"agent": "claude",
            "format": "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z",
            "until": "2025-01-02T00:00:00Z"}}))
        proposal = self.root / "p.json"; proposal.write_text(json.dumps([{"kind": "rule", "text":
            "Never parse embedded markers.", "topic_key": "safe-digest-metadata"}]))
        digest = retro.finalize_mine(self.config, raw, proposal)
        digest.write_text(digest.read_text() + "tamper")
        with self.assertRaisesRegex(ValueError, "does not match"):
            retro.apply_digest(self.config, digest)
        digest.write_text(digest.read_text().removesuffix("tamper"))
        wrong = dict(self.config); wrong["agent"] = "chatgpt"
        with self.assertRaisesRegex(ValueError, "selected agent"):
            retro.apply_digest(wrong, digest)

    def test_sidecar_schema_traversal_rejected(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"; raw.write_text(json.dumps({"agent": "claude",
            "format": "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z",
            "until": "2025-01-02T00:00:00Z"}}))
        proposal = self.root / "p.json"; proposal.write_text(json.dumps([{"kind": "skill", "text": "Safe skill",
            "topic_key": "safe-skill", "artifact": {"files": {"SKILL.md": "safe"}}}]))
        digest = retro.finalize_mine(self.config, raw, proposal)
        sidecar = digest.with_suffix(".json"); metadata = json.loads(sidecar.read_text())
        metadata["proposals"][0]["artifact"]["files"]["../escape"] = "bad"
        sidecar.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "invalid skill artifact"):
            retro.apply_digest(self.config, digest)

    def test_accepts_real_skill_and_script_artifacts(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"; raw.write_text(json.dumps({"agent": "claude", "format":
            "claude-code-jsonl", "window": {"since": "2025-01-01T00:00:00Z", "until": "2025-01-02T00:00:00Z"}}))
        rows = [{"kind": "skill", "text": "Workflow", "topic_key": "workflow-skill",
                 "artifact": {"files": {"SKILL.md": "# Workflow\n", "scripts/go.py": "print(1)\n"}}},
                {"kind": "script", "text": "Mechanical", "topic_key": "mechanical-script",
                 "artifact": {"filename": "go.py", "content": "#!/usr/bin/env python3\n"}}]
        proposal = self.root / "p.json"; proposal.write_text(json.dumps(rows))
        digest = retro.finalize_mine(self.config, raw, proposal); items = retro.digest_proposals(digest)
        with mock.patch("builtins.input", side_effect=["accept", "accept"]): retro.apply_digest(self.config, digest)
        skill = self.root / "skill" / items[0]["id"]
        self.assertTrue(skill.is_dir()); self.assertFalse(skill.is_symlink())
        self.assertEqual((skill / "SKILL.md").read_text(), "# Workflow\n")
        self.assertEqual((skill / "scripts" / "go.py").read_text(), "print(1)\n")
        script = self.root / "script" / f"{items[1]['id']}-go.py"
        self.assertTrue(script.exists()); self.assertTrue(script.stat().st_mode & 0o100)

    def test_portable_artifact_paths_are_strict(self):
        base = {"kind": "skill", "text": "Portable", "topic_key": "portable-paths"}
        bad = ["", ".", "./SKILL.md", "dir/./file", "dir//file", "dir/../file",
               "../SKILL.md", "/SKILL.md", "dir/", "dir\\file", "C:\\file"]
        for name in bad:
            with self.subTest(name=name):
                row = dict(base, artifact={"files": {"SKILL.md": "ok", name: "bad"}})
                with self.assertRaisesRegex(ValueError, "invalid skill artifact file"):
                    retro.normalize_proposals([row], self.config)
        for filename in ["dir/file.py", "dir\\file.py", ".", "..", ""]:
            with self.subTest(filename=filename):
                row = {"kind": "script", "text": "Portable script", "topic_key": "portable-script",
                       "artifact": {"filename": filename, "content": "x"}}
                with self.assertRaisesRegex(ValueError, "invalid script artifact"):
                    retro.normalize_proposals([row], self.config)

    def test_ledger_failure_preserves_preexisting_artifact(self):
        import apply
        proposal = retro.normalize_proposals([{"kind": "rule", "text": "Do not overwrite.",
            "topic_key": "no-overwrite"}], self.config)[0]
        destination = self.root / "rule" / f"{proposal['id']}.md"
        destination.parent.mkdir(parents=True); destination.write_text("other operation", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            apply.persist(self.config, proposal)
        self.assertEqual(destination.read_text(encoding="utf-8"), "other operation")

    def test_shared_target_locks_are_canonical_and_sorted(self):
        import apply
        shared = self.root / "shared"
        config = dict(self.config, persistence_targets=dict(self.config["persistence_targets"]))
        config["persistence_targets"]["rule"] = str(shared / "nested" / "..")
        config["persistence_targets"]["feedback"] = str(shared)
        items = retro.normalize_proposals([
            {"kind": "rule", "text": "One shared target", "topic_key": "one-shared-target"},
            {"kind": "feedback", "text": "Two shared target", "topic_key": "two-shared-target"},
        ], config)
        paths = apply._target_lock_paths(config, [(item, "accept") for item in items])
        self.assertEqual(paths, [shared.resolve() / ".collaboration-retro.lock"])
        ledger = Path(config["decisions_ledger"]).with_suffix(".jsonl.lock")
        self.assertEqual(sorted([ledger, *paths], key=lambda p: str(p.resolve())),
                         sorted([ledger, *paths], key=lambda p: str(p.resolve())))

    def test_target_lock_excludes_another_process(self):
        lock = self.root / "shared" / ".collaboration-retro.lock"
        with retro.exclusive(lock):
            code = ("import fcntl, sys; f=open(sys.argv[1], 'a'); "
                    "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)")
            result = subprocess.run([sys.executable, "-c", code, str(lock)],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_digest_names_preserve_second_and_microsecond_uniqueness(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        start = "2025-01-01T00:00:00Z"
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": start}))
        proposal = self.root / "p.json"; proposal.write_text("[]")
        first = self.root / "first.json"
        first.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl",
            "window": {"since": start, "until": "2025-01-01T00:00:01.000001Z"}}))
        digest_one = retro.finalize_mine(self.config, first, proposal)
        second = self.root / "second.json"
        second.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl",
            "window": {"since": "2025-01-01T00:00:01.000001Z", "until": "2025-01-01T00:00:01.000002Z"}}))
        digest_two = retro.finalize_mine(self.config, second, proposal)
        self.assertNotEqual(digest_one.name, digest_two.name)
        self.assertIn("000001Z", digest_one.name)
        self.assertIn("000002Z", digest_two.name)

    def test_skill_requires_real_artifact(self):
        with self.assertRaisesRegex(ValueError, "requires an artifact"):
            retro.normalize_proposals([{"kind": "skill", "text": "Do workflow", "topic_key": "do-workflow"}], self.config)

    def test_unknown_kind_and_schedule_dry_run(self):
        with self.assertRaisesRegex(ValueError, "unknown kind"):
            retro.normalize_proposals([{"kind": "command", "text": "bad", "topic_key": "bad-command"}], self.config)
        with mock.patch("builtins.print") as output:
            destination = retro.install_schedule(self.config, True)
        self.assertFalse(destination.exists())
        payload = output.call_args.args[0].encode()
        plist = plistlib.loads(payload)
        self.assertEqual(plist["Label"], "test.claude")
        self.assertIn("schedule_runner.py", plist["ProgramArguments"][1])

    def test_prepare_mine_uses_state_without_advancing(self):
        retro.init_state(self.config)
        state = Path(self.config["state_path"])
        state.write_text(json.dumps({"last_mined_at": "2025-01-01T00:00:00Z"}))
        with mock.patch.object(retro, "iso_utc", return_value="2025-01-05T00:00:00Z"):
            raw = retro.prepare_mine(self.config)
        self.assertEqual(json.loads(state.read_text())["last_mined_at"], "2025-01-01T00:00:00Z")
        self.assertEqual(json.loads(raw.read_text())["sessions_seen"], 2)


    def _conversation(self, turns):
        transcript = self.root / "context.jsonl"
        transcript.write_text("".join(json.dumps({"sessionId": session,
            "timestamp": timestamp, "message": {"role": role, "content": text}}) + "\n"
            for session, timestamp, role, text in turns))
        return dict(self.config, transcript_globs=[str(transcript)])

    def test_candidates_keep_non_keyword_preferences_and_nearby_context(self):
        timestamp = "2025-01-02T10:00:00Z"
        rows = [("one", "2024-12-31T10:00:00Z", "assistant", "old private answer"),
                ("one", timestamp, "assistant", "distant answer"),
                ("one", timestamp, "assistant", "first nearby answer"),
                ("one", timestamp, "assistant", "second nearby answer"),
                ("one", timestamp, "user", "Please run bun test for every change."),
                ("other", timestamp, "assistant", "unrelated session"),
                ("one", timestamp, "assistant", "first response"),
                ("one", timestamp, "assistant", "second response"),
                ("one", timestamp, "assistant", "distant response"),
                ("one", "2025-02-01T00:00:00Z", "assistant", "future private answer")]
        config = self._conversation(rows)
        raw = extractor.extract(config, "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z")
        self.assertEqual(len(raw["candidates"]), 1)
        candidate = raw["candidates"][0]
        self.assertEqual(candidate["event"]["text"], rows[4][3])
        self.assertEqual([turn["text"] for turn in candidate["before"]], [rows[2][3], rows[3][3]])
        self.assertEqual([turn["text"] for turn in candidate["after"]], [rows[6][3], rows[7][3]])
        self.assertEqual(candidate["event"]["line"], 5)
        self.assertEqual(candidate["event"]["source"], config["transcript_globs"][0])
        self.assertTrue(candidate["event"]["id"].startswith("event-"))
        self.assertEqual(raw["lenses"]["corrections"], [])
        self.assertNotIn("private answer", json.dumps(raw))

    def test_extraction_text_is_bounded_and_truncation_explicit(self):
        from extraction_context import TEXT_LIMIT
        config = self._conversation([
            ("one", "2025-01-02T00:00:00Z", "assistant", "a" * (TEXT_LIMIT + 10)),
            ("one", "2025-01-02T00:01:00Z", "user", "No, " + "b" * TEXT_LIMIT)])
        raw = extractor.extract(config, "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z")
        candidate = raw["candidates"][0]
        for turn in [candidate["event"], *candidate["before"]]:
            self.assertEqual(len(turn["text"]), TEXT_LIMIT)
            self.assertTrue(turn["text_truncated"])
        self.assertEqual(len(raw["lenses"]["corrections"][0]["text"]), TEXT_LIMIT)

    def test_candidate_overflow_fails_without_advancing_state(self):
        config = self._conversation([
            ("one", "2025-01-02T00:00:00Z", "user", "First request"),
            ("one", "2025-01-02T00:01:00Z", "user", "Second request")])
        state = Path(config["state_path"]); state.parent.mkdir(parents=True)
        original = {"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}
        state.write_text(json.dumps(original))
        with mock.patch("extraction_context.CANDIDATE_LIMIT", 1), \
             mock.patch.object(retro, "iso_utc", return_value="2025-01-05T00:00:00Z"):
            with self.assertRaisesRegex(ValueError, "shorter window"):
                retro.prepare_mine(config)
        self.assertEqual(json.loads(state.read_text()), original)
        self.assertFalse((self.root / "out-claude" / "work").exists())

    def _approval_digest(self, row):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": "2025-01-01T00:00:00Z"}))
        raw = self.root / "raw.json"
        raw.write_text(json.dumps({"agent": "claude", "format": "claude-code-jsonl",
            "window": {"since": "2025-01-01T00:00:00Z", "until": "2025-01-02T00:00:00Z"}}))
        proposals = self.root / "proposals.json"; proposals.write_text(json.dumps([row]))
        return retro.finalize_mine(self.config, raw, proposals)

    def test_valid_sidecar_text_edit_is_rejected_before_approval(self):
        digest = self._approval_digest({"kind": "rule", "text": "Run local checks.",
            "topic_key": "local-checks"})
        sidecar = digest.with_suffix(".json"); metadata = json.loads(sidecar.read_text())
        metadata["proposals"][0]["text"] = "Skip local checks."
        sidecar.write_text(json.dumps(metadata))
        self._assert_sidecar_rejected(digest)

    def _assert_sidecar_rejected(self, digest):
        for config in (None, self.config):
            with self.subTest(config=config is not None):
                with self.assertRaisesRegex(ValueError, "validated proposals"):
                    retro.digest_proposals(digest, config)
        with mock.patch("builtins.input") as prompt:
            with self.assertRaisesRegex(ValueError, "validated proposals"):
                retro.apply_digest(self.config, digest)
            prompt.assert_not_called()
        self.assertFalse(Path(self.config["decisions_ledger"]).exists())
        self.assertFalse((self.root / "script").exists())
        self.assertFalse((self.root / "skill").exists())
        self.assertFalse((self.root / "rule").exists())

    def test_valid_sidecar_executable_artifact_edit_is_rejected(self):
        digest = self._approval_digest({"kind": "script", "text": "Local helper.",
            "topic_key": "local-helper", "artifact": {"filename": "helper.py", "content": "print(1)\n"}})
        sidecar = digest.with_suffix(".json"); metadata = json.loads(sidecar.read_text())
        metadata["proposals"][0]["artifact"]["content"] = "print(2)\n"
        sidecar.write_text(json.dumps(metadata))
        self._assert_sidecar_rejected(digest)

    def test_valid_sidecar_skill_artifact_edit_is_rejected(self):
        digest = self._approval_digest({"kind": "skill", "text": "Local workflow.",
            "topic_key": "local-workflow", "artifact": {"files": {"SKILL.md": "# Workflow\n"}}})
        sidecar = digest.with_suffix(".json"); metadata = json.loads(sidecar.read_text())
        metadata["proposals"][0]["artifact"]["files"]["SKILL.md"] = "# Changed workflow\n"
        sidecar.write_text(json.dumps(metadata))
        self._assert_sidecar_rejected(digest)

    def _runner_config(self, exit_code=0):
        script = self.root / "local_command.py"
        script.write_text("import json, os, sys\n"
            "from pathlib import Path\n"
            "print('private-output-' + sys.argv[1])\n"
            "print('private-error-' + sys.argv[1], file=sys.stderr)\n"
            "Path(sys.argv[2]).write_text(json.dumps({'input': sys.stdin.read(), "
            "'agent': os.environ['COLLABORATION_RETRO_AGENT'], "
            "'config': os.environ['COLLABORATION_RETRO_CONFIG']}))\n"
            "raise SystemExit(int(sys.argv[3]))\n")
        config = json.loads(self.config_path.read_text())
        config["defaults"]["scheduler"].update(
            mine_command=[sys.executable, str(script), "mine", str(self.root / "mine.json"), str(exit_code)],
            notification_command=[sys.executable, str(script), "notify", str(self.root / "notify.json"), "0"])
        self.config_path.write_text(json.dumps(config))
        return [sys.executable, str(ROOT / "scripts" / "schedule_runner.py"),
                "--config", str(self.config_path)]

    def test_scheduler_suppresses_both_commands_output_and_input(self):
        result = subprocess.run(self._runner_config(), input="private inherited input", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        for name in ("mine", "notify"):
            captured = json.loads((self.root / f"{name}.json").read_text())
            self.assertEqual(captured, {"input": "", "agent": "claude", "config": str(self.config_path.resolve())})

    def test_scheduler_propagates_mine_failure_without_notifying(self):
        result = subprocess.run(self._runner_config(7), capture_output=True, text=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertTrue((self.root / "mine.json").exists())
        self.assertFalse((self.root / "notify.json").exists())

    def test_scheduler_launch_errors_do_not_log_command_data(self):
        config = json.loads(self.config_path.read_text())
        config["defaults"]["scheduler"]["mine_command"] = [str(self.root / "private-command-name")]
        self.config_path.write_text(json.dumps(config))
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "schedule_runner.py"),
            "--config", str(self.config_path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("private-command-name", result.stdout + result.stderr)
        self.assertEqual(result.stderr, "schedule: error: configuration or local command failed\n")


    def test_shorter_prepare_window_preserves_dedup_and_completes_before_next_batch(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        start, boundary = "2025-01-01T00:00:00Z", "2025-01-02T10:00:00Z"
        state.write_text(json.dumps({"agent": "claude", "last_mined_at": start}))
        proposals = self.root / "proposals.json"; proposals.write_text("[]")
        # Exercise the public CLI used to recover from the candidate ceiling.
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "retro.py"),
            "--config", str(self.config_path), "prepare-mine", "--until", boundary],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw_path = Path(result.stdout.strip()); raw = json.loads(raw_path.read_text())
        self.assertEqual(raw["window"], {"since": start, "until": boundary})
        self.assertIn("dedup_context", raw)
        self.assertEqual(json.loads(state.read_text())["last_mined_at"], start)
        retro.finalize_mine(self.config, raw_path, proposals)
        self.assertEqual(json.loads(state.read_text())["last_mined_at"], boundary)
        next_raw = retro.prepare_mine(self.config, "2025-01-05T00:00:00Z")
        self.assertEqual(json.loads(next_raw.read_text())["window"]["since"], boundary)


    def test_prepare_rejects_future_and_reversed_batch_boundaries(self):
        state = Path(self.config["state_path"]); state.parent.mkdir(parents=True)
        original = {"agent": "claude", "last_mined_at": "2025-01-02T00:00:00Z"}
        state.write_text(json.dumps(original))
        with mock.patch.object(retro, "iso_utc", return_value="2025-01-05T00:00:00Z"):
            with self.assertRaisesRegex(ValueError, "later than now"):
                retro.prepare_mine(self.config, "2025-01-06T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "must not precede"):
            retro.prepare_mine(self.config, "2025-01-01T00:00:00Z")
        self.assertEqual(json.loads(state.read_text()), original)
        self.assertFalse((self.root / "out-claude" / "work").exists())


    def test_ambiguous_prose_layout_cannot_hide_changed_persistence_bytes(self):
        digest = self._approval_digest({"kind": "rule", "text": "Run checks.\n\nWhy: Required.",
            "topic_key": "required-checks"})
        sidecar = digest.with_suffix(".json"); metadata = json.loads(sidecar.read_text())
        metadata["proposals"][0].update(text="Run checks.", rationale="Required.")
        sidecar.write_text(json.dumps(metadata))
        self._assert_sidecar_rejected(digest)


if __name__ == "__main__":
    unittest.main()
