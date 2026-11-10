#!/usr/bin/env python3
"""Offline contract tests through the public wiki dream export command."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PKG = Path(__file__).resolve().parents[1]
WIKI = PKG / "scripts" / "wiki"


class DreamExportTests(unittest.TestCase):
    def setUp(self):
        temp_base = Path(os.environ.get("TMPDIR", PKG / ".agents" / "test-tmp"))
        temp_base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=temp_base)
        self.root = Path(self.tmp.name) / "wiki"
        self.root.mkdir()
        self.env = dict(os.environ, WIKI_HOME=str(self.root))
        (self.root / ".raw").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_wiki(self, *args, path=None):
        return subprocess.run(["bash", str(WIKI), "dream", "export", *args], env=dict(self.env, PATH=(str(path) + os.pathsep if path else "") + os.environ["PATH"]), capture_output=True, text=True)

    def write_input(self, data):
        path = Path(self.tmp.name) / "threads.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def sample(self):
        return {"threads": [{"id": "t-one", "title": "Example", "updatedAt": "2026-11-09T12:00:00+02:00", "messages": [
            {"id": "u", "role": "user", "timestamp": "2026-11-09T10:00:00+02:00", "timezone": "Europe/Paris", "content": "hello"},
            {"id": "a", "role": "assistant", "content": [
                {"type": "tool_call", "name": "Bash", "input": {"cmd": "true"}},
                {"type": "tool_result", "name": "Bash", "output": 0},
                {"type": "tool_result", "name": "Read", "output": False}]}]}]}

    def test_file_and_stdin_inputs_render_times_and_falsey_results(self):
        for stdin in (False, True):
            data = self.sample()
            file = self.write_input(data)
            args = ["--input", "-" if stdin else str(file), "--since", "2026-11-09"]
            if stdin:
                alternate = Path(self.tmp.name) / "stdin-root"
                alternate.mkdir()
                args = ["--content-root", str(alternate), *args]
            result = subprocess.run(["bash", str(WIKI), "dream", "export", *args], input=json.dumps(data) if stdin else None, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.root / ".dream" / "last-run-input").exists(), "export is not processed success")
        transcript = next((self.root / ".raw").glob("*/*/t-one-example.txt")).read_text()
        self.assertEqual(transcript.count("USER [id=u; timestamp=2026-11-09T10:00:00+02:00; timezone=Europe/Paris]"), 1)
        self.assertIn("ASSISTANT [id=a]:", transcript)
        self.assertIn("TOOL RESULT (Bash):\n0", transcript)
        self.assertIn("TOOL RESULT (Read):\nfalse", transcript)

    def test_alma_nonprogress_does_not_advance_checkpoint_or_output(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        alma = bindir / "alma"
        alma.write_text("""#!/bin/sh
if [ "$1" = threads ]; then echo '[{"id":"t","updatedAt":"2026-11-09T12:00:00Z"}]'; else echo '{"messages":[{"id":"m"}],"hasMore":true,"nextOffset":0}'; fi
""", encoding="utf-8")
        alma.chmod(0o755)
        (self.root / ".dream").mkdir()
        checkpoint = self.root / ".dream" / "last-run-alma"
        checkpoint.write_text("sentinel\\n")
        env = dict(self.env, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--source", "alma", "--since", "2026-11-09"], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not advance", result.stderr)
        self.assertEqual(checkpoint.read_text(), "sentinel\\n")
        self.assertEqual(list((self.root / ".raw").iterdir()), [])

    def test_no_source_does_not_call_alma(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        marker = Path(self.tmp.name) / "invoked"
        alma = bindir / "alma"
        alma.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        alma.chmod(0o755)
        result = self.run_wiki(path=bindir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choose a source", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / ".dream" / "pending-batch.json").exists())
        self.assertTrue(list((self.root / ".dream" / "logs").glob("*.jsonl")))

    def test_malformed_and_missing_requested_thread_leave_state_unchanged(self):
        state_dir = self.root / ".dream"
        state_dir.mkdir()
        state = state_dir / "last-run-input"
        state.write_text("checkpoint\n")
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{")
        result = self.run_wiki("--input", str(bad))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state.read_text(), "checkpoint\n")
        good = self.write_input(self.sample())
        result = self.run_wiki("--input", str(good), "--thread", "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)
        self.assertEqual(state.read_text(), "checkpoint\n")

    def test_export_retry_refuses_to_overwrite_frozen_batch(self):
        file = self.write_input(self.sample())
        first = self.run_wiki("--input", str(file))
        self.assertEqual(first.returncode, 0, first.stderr)
        output = next((self.root / ".raw").glob("*/*/*.txt"))
        original = output.read_bytes()
        retry = self.run_wiki("--input", str(file))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("existing immutable batch", retry.stdout)
        self.assertEqual(output.read_bytes(), original)

    def test_manifest_boundary_is_captured_upper_bound_and_not_since(self):
        file = self.write_input(self.sample())
        started = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        result = self.run_wiki("--input", str(file), "--since", "2001-01-01T00:00:00Z")
        ended = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(next((self.root / ".raw").glob("*/*/manifest.json")).read_text())
        import datetime as dt
        boundary = dt.datetime.fromisoformat(manifest["source_boundary"])
        self.assertGreaterEqual(boundary, started)
        self.assertLessEqual(boundary, ended)
        self.assertEqual(manifest["selected_at"], manifest["source_boundary"])
        self.assertEqual(manifest["since"], "2001-01-01T00:00:00+00:00")
        self.assertFalse(manifest["advances_watermark"])
        self.assertIn("t-one-example.txt", manifest["transcript_hashes"])
        self.assertTrue((self.root / ".dream" / "pending-batch.json").exists())

    def test_forced_thread_does_not_advance_watermark_and_duplicates_rejected(self):
        file = self.write_input(self.sample())
        result = self.run_wiki("--input", str(file), "--thread", "t-one")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(next((self.root / ".raw").glob("*/*/manifest.json")).read_text())
        self.assertFalse(manifest["advances_watermark"])
        duplicate = self.write_input({"threads": self.sample()["threads"] * 2})
        second = Path(self.tmp.name) / "duplicate"
        second.mkdir()
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--content-root", str(second), "--input", str(duplicate)], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate thread id", result.stderr)

    def test_retry_detects_tampered_immutable_transcript(self):
        file = self.write_input(self.sample())
        self.assertEqual(self.run_wiki("--input", str(file)).returncode, 0)
        transcript = next((self.root / ".raw").glob("*/*/*.txt"))
        transcript.write_text("tampered", encoding="utf-8")
        result = self.run_wiki("--input", str(file))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed verification", result.stderr)

    def test_dry_run_writes_nothing(self):
        file = self.write_input(self.sample())
        result = self.run_wiki("--input", str(file), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ".dream").exists())
        self.assertEqual(list((self.root / ".raw").iterdir()), [])

    def test_since_and_thread_selection(self):
        data = self.sample()
        old = dict(data["threads"][0], id="t-old", updatedAt="2020-01-01T00:00:00Z")
        data["threads"].append(old)
        file = self.write_input(data)
        result = self.run_wiki("--input", str(file), "--since", "2026-01-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.root / ".raw").glob("*/*/*.txt"))), 1)
        other = Path(self.tmp.name) / "selected"
        other.mkdir()
        env = dict(self.env, WIKI_HOME=str(other))
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--input", str(file), "--thread", "t-old"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in (other / ".raw").glob("*/*/*.txt")], ["t-old-example.txt"])

    def test_visible_truncation_markers(self):
        data = self.sample()
        data["threads"][0]["messages"] = [{"role": "assistant", "content": "x" * 4000} for _ in range(110)]
        file = self.write_input(data)
        result = self.run_wiki("--input", str(file))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = next((self.root / ".raw").glob("*/*/*.txt")).read_text()
        self.assertIn("thread truncated", text)
        data["threads"][0]["messages"] = [{"role": "user", "content": "y" * 5000}]
        file = self.write_input(data)
        second = Path(self.tmp.name) / "other"
        second.mkdir()
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--content-root", str(second), "--input", str(file)], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = next((second / ".raw").glob("*/*/*.txt")).read_text()
        self.assertIn("chars cut", text)

    def test_explicit_alma_source_paginates(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        log = Path(self.tmp.name) / "calls"
        alma = bindir / "alma"
        alma.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALL_LOG'], 'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
a=sys.argv[1:]
if a[0]=='threads': print(json.dumps([{'id':'alma-1','title':'Alma example','updatedAt':'2026-11-09T12:00:00Z'}]))
elif a[0:2]==['thread','messages']:
 off=int(a[-1]); print(json.dumps({'messages':[{'id':str(off),'message':{'role':'assistant','createdAt':'2026-11-09T11:00:00Z','parts':[{'type':'text','text':str(off)}]}}], 'hasMore':off==0, 'nextOffset':off+1}))
""", encoding="utf-8")
        alma.chmod(0o755)
        env = dict(self.env, PATH=str(bindir) + os.pathsep + os.environ["PATH"], CALL_LOG=str(log))
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--source", "alma", "--since", "2026-11-09"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text()
        self.assertIn("--offset 0", calls)
        self.assertIn("--offset 1", calls)
        text = next((self.root / ".raw").glob("*/*/*.txt")).read_text()
        self.assertIn("2026-11-09T11:00:00Z", text)

    def test_alma_metadata_sent_at_timezone_is_preserved(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        raw = {"id": "m-source", "timestamp": "2026-11-09T05:53:46.632Z",
               "metadata": {"sentAtTimeZone": "Asia/Hong_Kong"},
               "message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}}
        page = json.dumps({"messages": [raw], "hasMore": False})
        alma = bindir / "alma"
        alma.write_text("#!/usr/bin/env python3\nprint(" + repr(page) + ")\n", encoding="utf-8")
        alma.chmod(0o755)
        result = self.run_wiki("--source", "alma", "--thread", "observed-shape", path=bindir)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = next((self.root / ".raw").glob("*/*/*.txt")).read_text()
        self.assertIn("timestamp=2026-11-09T05:53:46.632Z", text)
        self.assertIn("timezone=Asia/Hong_Kong", text)

    def test_status_shows_source_checkpoints_and_labeled_legacy_state(self):
        state = self.root / ".dream"
        state.mkdir()
        (state / "last-run-alma").write_text("2026-11-09T12:00:00Z")
        (state / "last-run-input").write_text("2026-11-09T13:00:00Z")
        (state / "last-run").write_text("2026-11-09T11:00:00Z")
        result = subprocess.run(["bash", str(WIKI), "dream", "status"], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"alma": "2026-11-09T12:00:00Z"', result.stdout)
        self.assertIn('"input": "2026-11-09T13:00:00Z"', result.stdout)
        self.assertIn('"legacy_checkpoint": "2026-11-09T11:00:00Z"', result.stdout)
        self.assertNotIn("last run:     never", result.stdout)

    def test_tool_only_message_keeps_header_metadata(self):
        data = self.sample()
        data["threads"][0]["messages"] = [{"id": "tool-msg", "role": "assistant", "timestamp": "2026-11-09T11:00:00Z", "timezone": "UTC", "content": [{"type": "tool_call", "name": "Bash", "input": {"cmd": "true"}}]}]
        file = self.write_input(data)
        result = self.run_wiki("--input", str(file), "--since", "2026-11-09")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = next((self.root / ".raw").glob("*/*/*.txt")).read_text()
        self.assertIn("ASSISTANT [id=tool-msg; timestamp=2026-11-09T11:00:00Z; timezone=UTC]:", text)
        self.assertIn("TOOL CALL Bash:", text)

    def test_utc_checkpoint_overlap_filters_input(self):
        state_dir = self.root / ".dream"
        state_dir.mkdir()
        (state_dir / "last-run-input").write_text("2026-11-09T03:00:00Z")
        data = self.sample()
        data["threads"] = [
            dict(data["threads"][0], id="fresh", updatedAt="2026-11-09T02:45:00Z"),
            dict(data["threads"][0], id="old", updatedAt="2026-11-09T02:00:00Z"),
        ]
        result = self.run_wiki("--input", str(self.write_input(data)))
        self.assertEqual(result.returncode, 0, result.stderr)
        names = {p.name for p in (self.root / ".raw").glob("*/*/*.txt")}
        self.assertIn("fresh-example.txt", names)
        self.assertNotIn("old-example.txt", names)

    def test_invalid_timestamp_and_unsafe_id_leave_output_and_checkpoint_unchanged(self):
        (self.root / ".dream").mkdir()
        checkpoint = self.root / ".dream" / "last-run-input"
        checkpoint.write_text("sentinel\n")
        old_out = self.root / ".raw" / "previous"
        old_out.mkdir()
        (old_out / "keep.txt").write_text("keep")
        for thread in (
            dict(self.sample()["threads"][0], id="../escape"),
            dict(self.sample()["threads"][0], updatedAt="not-a-date"),
            dict(self.sample()["threads"][0], messages=[{"id": "bad", "role": "user", "timestamp": "not-a-date", "content": "x"}]),
        ):
            result = self.run_wiki("--input", str(self.write_input({"threads": [thread]})), "--since", "2026-01-01")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(checkpoint.read_text(), "sentinel\n")
            self.assertEqual((old_out / "keep.txt").read_text(), "keep")

    def test_alma_filters_listing_before_fetch_and_carries_checkpoint(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        log = Path(self.tmp.name) / "calls"
        alma = bindir / "alma"
        alma.write_text("""#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALL_LOG'], 'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')
a=sys.argv[1:]
if a[0]=='threads': print(json.dumps([{'id':'new','title':'New','updatedAt':'2026-11-09T02:45:00Z'},{'id':'old','title':'Old','updatedAt':'2026-11-09T02:00:00Z'}]))
elif a[0:2]==['thread','messages']: print(json.dumps({'messages':[], 'hasMore':False}))
""", encoding="utf-8")
        alma.chmod(0o755)
        (self.root / ".dream").mkdir()
        (self.root / ".dream" / "last-run-alma").write_text("2026-11-09T03:00:00Z")
        env = dict(self.env, PATH=str(bindir) + os.pathsep + os.environ["PATH"], CALL_LOG=str(log))
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--source", "alma"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text()
        self.assertIn("messages new", calls)
        self.assertNotIn("messages old", calls)

    def test_adapter_failure_does_not_advance_checkpoint_or_output(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        alma = bindir / "alma"
        alma.write_text("#!/bin/sh\nif [ \"$1\" = threads ]; then echo '[{\"id\":\"t\",\"updatedAt\":\"2026-11-09T12:00:00Z\"}]'; else exit 7; fi\n", encoding="utf-8")
        alma.chmod(0o755)
        (self.root / ".dream").mkdir()
        checkpoint = self.root / ".dream" / "last-run-alma"
        checkpoint.write_text("sentinel\n")
        env = dict(self.env, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(WIKI), "dream", "export", "--source", "alma", "--since", "2026-11-09"], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(checkpoint.read_text(), "sentinel\n")
        self.assertEqual(list((self.root / ".raw").iterdir()), [])

    def test_generic_input_never_invokes_alma(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        marker = Path(self.tmp.name) / "invoked"
        alma = bindir / "alma"
        alma.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 9\n", encoding="utf-8")
        alma.chmod(0o755)
        result = self.run_wiki("--input", str(self.write_input(self.sample())), "--since", "2026-11-09", path=bindir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())


    def test_publication_interruption_never_exposes_partial_batch_and_retry_recovers(self):
        source = self.write_input(self.sample())
        injection = Path(self.tmp.name) / "failure-injection"
        injection.mkdir()
        # Inject an actual process death at the atomic publication boundary.
        (injection / "sitecustomize.py").write_text(
            "import os\noriginal = os.replace\n"
            "def replace(src, dst):\n"
            "    if os.path.isdir(src) and '.dream-stage-' in str(src): os._exit(74)\n"
            "    return original(src, dst)\n"
            "os.replace = replace\n")
        env = dict(self.env, PYTHONPATH=str(injection))
        failed = subprocess.run(["bash", str(WIKI), "dream", "export", "--input", str(source)], env=env, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 74, failed.stderr)
        visible = [p for p in (self.root / ".raw").glob("*/*") if not p.name.startswith(".")]
        self.assertEqual(visible, [])
        self.assertFalse((self.root / ".dream" / "pending-batch.json").exists())
        retry = self.run_wiki("--input", str(source))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        pointer = json.loads((self.root / ".dream" / "pending-batch.json").read_text())
        batch = self.root / pointer["path"]
        self.assertTrue((batch / "manifest.json").exists())
        # Now simulate death after directory publication but before pointer publication.
        (self.root / ".dream" / "pending-batch.json").unlink()
        source.unlink()
        retry = self.run_wiki("--input", str(source))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("source selection was not rerun", retry.stdout)
        self.assertEqual(json.loads((self.root / ".dream" / "pending-batch.json").read_text())["path"], pointer["path"])

    def test_pending_export_is_resumed_even_if_new_source_input_changes(self):
        source = self.write_input(self.sample())
        self.assertEqual(self.run_wiki("--input", str(source)).returncode, 0)
        pointer = (self.root / ".dream" / "pending-batch.json").read_text()
        self.write_input({"threads": []})
        retry = self.run_wiki("--input", str(source))
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("source selection was not rerun", retry.stdout)
        self.assertEqual(json.loads((self.root / ".dream" / "pending-batch.json").read_text()), json.loads(pointer))

    def test_real_export_begin_no_fact_commit_moves_automatic_cursor_once(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        (self.root / "README.md").write_text("fixture")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        bindir = Path(self.tmp.name) / "empty-source"
        bindir.mkdir()
        (bindir / "alma").write_text("#!/bin/sh\necho '[]'\n")
        (bindir / "alma").chmod(0o755)
        exported = self.run_wiki("--source", "alma", "--since", "2020-01-01", path=bindir)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        pointer = json.loads((self.root / ".dream" / "pending-batch.json").read_text())
        batch = self.root / pointer["path"]
        manifest = json.loads((batch / "manifest.json").read_text())
        self.assertTrue(manifest["advances_watermark"])
        checkpoint = self.root / ".dream" / "last-run-alma"
        self.assertFalse(checkpoint.exists())
        for args in (("begin", "--batch", str(batch)), ("commit",)):
            result = subprocess.run(["bash", str(WIKI), "dream", *args], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(checkpoint.read_text().strip(), manifest["source_boundary"])
        self.assertNotEqual(checkpoint.read_text().strip(), manifest["since"])
        self.assertFalse((self.root / ".dream" / "pending-batch.json").exists())



    def test_export_logs_fetch_publication_timestamps_without_transcript_text(self):
        import datetime as dt
        data = self.sample()
        marker = "fixture-private-content-not-for-logs"
        data["threads"][0]["messages"][0]["content"] = marker
        result = self.run_wiki("--input", str(self.write_input(data)))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = "".join(p.read_text() for p in (self.root / ".dream" / "logs").glob("*.jsonl"))
        self.assertNotIn(marker, text)
        events = [json.loads(line) for line in text.splitlines()]
        stages = {(e["stage"], e["status"]) for e in events}
        self.assertIn(("source_fetch", "started"), stages)
        self.assertIn(("source_fetch", "succeeded"), stages)
        self.assertIn(("batch", "published"), stages)
        self.assertEqual(events[-1]["exit_code"], 0)
        for e in events:
            self.assertIsNotNone(dt.datetime.fromisoformat(e["timestamp"]).tzinfo)
            self.assertGreaterEqual(e["elapsed_ms"], 0)
            self.assertTrue(e["description"])


if __name__ == "__main__": unittest.main()
