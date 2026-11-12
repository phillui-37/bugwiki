"""Public CLI regressions for resumable dream processing and publication."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "scripts" / "wiki"
TEMP_ROOT = ROOT / ".agents" / "test-tmp"


class DreamWorkflowCLI(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.root = Path(self.tmp.name) / "wiki"
        (self.root / ".raw" / "batch").mkdir(parents=True)
        for folder in ("facts", "solutions", "reports"):
            (self.root / folder).mkdir()
        self.env = dict(os.environ, WIKI_HOME=str(self.root))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        (self.root / "README.md").write_text("base\n")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        self.transcript = self.root / ".raw" / "batch" / "t.txt"
        self.transcript.write_text("transcript body\n")
        manifest = {"batch_id": "batch-one", "source": "input", "source_boundary": "2026-11-10T00:00:00+00:00", "source_hash": "abc", "transcripts": ["t.txt"], "transcript_hashes": {"t.txt": hashlib.sha256(self.transcript.read_bytes()).hexdigest()}, "advances_watermark": False}
        source = self.transcript.parent / "body.json"
        source.write_text(json.dumps({"id": "body", "messages": [{"id": "m1", "role": "user", "content": "transcript body\n"}]}))
        manifest["source_files"] = {"body.json": hashlib.sha256(source.read_bytes()).hexdigest()}
        (self.transcript.parent / "manifest.json").write_text(json.dumps(manifest))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, input=None):
        return subprocess.run(["bash", str(WIKI), "dream", *args], env=self.env, input=input, capture_output=True, text=True)

    def begin(self):
        result = self.cli("begin", "--batch", str(self.transcript.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / ".dream" / "workflow.json").read_text())

    def finish_transcript(self, gaps=""):
        path = str(self.transcript)
        first = self.cli("read", "--thread", path, "--limit", "5")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "trans")
        self.assertEqual(self.cli("ack", "--thread", path).returncode, 0)
        remainder = self.cli("read", "--thread", path)
        self.assertEqual(remainder.stdout, "cript body\n")
        self.assertEqual(self.cli("ack", "--thread", path).returncode, 0)
        if not gaps:
            result = self.cli("recover", "--thread", str(self.transcript))
            self.assertEqual(result.returncode, 0, result.stderr)
        complete_args = ["complete", "--thread", path, "--status", "analyzed", "--source-complete"]
        if gaps:
            complete_args.extend(["--gaps", gaps])
        result = self.cli(*complete_args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def own_fact(self, rel="facts/known.md"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntitle: Known fact\nrecurrence: 1\nsource: thread-1\nstatus: open\n---\n\n" + "This evidence comes from the identified transcript and records the verified scope of the operational observation. " * 4 + "\n")
        result = self.cli("own", input=rel + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unacknowledged_read_replays_identical_chunk_and_caps_limit(self):
        self.begin()
        args = ("read", "--thread", str(self.transcript), "--limit", "5")
        first = self.cli(*args)
        again = self.cli(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "trans")
        self.assertEqual(again.stdout, first.stdout)
        self.assertNotEqual(self.cli("read", "--thread", str(self.transcript), "--limit", "999999").returncode, 0)
        self.assertNotEqual(self.cli("complete", "--thread", str(self.transcript), "--status", "analyzed", "--source-complete").returncode, 0)


    def test_all_unread_transcript_cannot_finalize(self):
        self.begin()
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("use --partial", result.stderr)

    def test_ack_next_emits_only_the_next_chunk(self):
        self.begin()
        path = str(self.transcript)
        first = self.cli("read", "--thread", path, "--limit", "5")
        self.assertEqual(first.stdout, "trans")
        next_chunk = self.cli("ack", "--thread", path, "--next", "--limit", "5")
        self.assertEqual(next_chunk.stdout, "cript")
        state = self.state()
        rec = state["transcripts"][path]
        self.assertEqual(rec["ack"], 5)
        self.assertEqual(rec["outstanding"], {"start": 5, "end": 10})

    def test_transcript_change_after_analysis_rejects_commit(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        self.transcript.write_text("changed after analysis")
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)

    def test_scope_traversal_readme_and_promoted_paths_rejected(self):
        self.begin()
        (self.root / "facts" / "promoted.md").write_text("---\nmaturity: 'promoted'\n---\nKeep this.\n")
        for rel in ("README.md", "facts/../README.md", "facts/promoted.md", "AGENTS.md", ".dream/config.md"):
            result = self.cli("own", input=rel + "\n")
            self.assertNotEqual(result.returncode, 0, rel)

    def test_preexisting_dirty_and_staged_paths_cannot_be_adopted(self):
        path = self.root / "facts" / "dirty.md"
        path.write_text("preexisting dirty\n")
        staged = self.root / "solutions" / "staged.md"
        staged.write_text("staged\n")
        subprocess.run(["git", "-C", str(self.root), "add", "solutions/staged.md"], check=True)
        self.begin()
        self.assertNotEqual(self.cli("own", input="facts/dirty.md\n").returncode, 0)
        self.assertNotEqual(self.cli("own", input="solutions/staged.md\n").returncode, 0)

    def test_mixed_owned_staged_and_unstaged_rejected_without_losing_index(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        fact = self.root / "facts" / "known.md"
        fact.write_text(fact.read_text() + "staged\n")
        subprocess.run(["git", "-C", str(self.root), "add", "facts/known.md"], check=True)
        fact.write_text(fact.read_text() + "unstaged\n")
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)

    def test_report_no_facts_checkpoint_commit_retry_and_idempotence(self):
        self.begin()
        self.finish_transcript()
        first = self.cli("commit")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        receipt = json.loads(first.stdout.strip().splitlines()[-1])
        report = self.root / receipt["report"]
        content = report.read_text()
        self.assertIn("Analyzed: 1", content)
        self.assertIn("Skipped: 0", content)
        self.assertEqual(receipt["report_hash"], __import__("hashlib").sha256(report.read_bytes()).hexdigest())
        head = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        state_path = self.root / ".dream" / "workflow.json"
        state = json.loads(state_path.read_text())
        state["phase"] = "committing"
        state.pop("completed", None)
        state.pop("commit", None)
        state_path.write_text(json.dumps(state))
        (self.root / ".dream" / "receipts" / f"{receipt['run']}.json").unlink()
        (self.root / ".dream" / "processed-batches" / "batch-one.json").unlink()
        reconciled = self.cli("commit")
        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        recovered_receipt = json.loads(reconciled.stdout.strip().splitlines()[-1])
        self.assertEqual(recovered_receipt["commit"], receipt["commit"])
        self.assertEqual(recovered_receipt["report_hash"], receipt["report_hash"])
        retry = self.cli("commit")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        retry_receipt = json.loads((self.root / ".dream" / "receipts" / f"{receipt['run']}.json").read_text())
        self.assertEqual(retry_receipt, recovered_receipt)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip(), head)
        self.assertFalse((self.root / ".dream" / f"index-{receipt['run']}").exists())
        self.assertFalse((self.root / ".dream" / "last-run-input").exists(), "source-independent input must not change watermarks")
        self.assertNotEqual(self.cli("begin", "--batch", str(self.transcript.parent)).returncode, 0)

    def test_verify_failure_keeps_run_pending_and_creates_no_commit(self):
        self.begin()
        self.finish_transcript()
        fact = self.root / "facts" / "invalid.md"
        fact.write_text("---\nstatus: impossible\nsource: thread-1\n---\n\nBad schema.\n")
        self.assertEqual(self.cli("own", input="facts/invalid.md\n").returncode, 0)
        head = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        state = json.loads((self.root / ".dream" / "workflow.json").read_text())
        self.assertEqual(state["phase"], "processing")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip(), head)

    def test_lint_failure_keeps_run_resumable(self):
        self.begin()
        self.finish_transcript()
        fact = self.root / "facts" / "thin.md"
        fact.write_text("---\ntitle: Thin\nslug: thin\nstatus: open\nsource: thread-1\nrecurrence: 1\n---\n\nShort.\n")
        self.assertEqual(self.cli("own", input="facts/thin.md\n").returncode, 0)
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wiki lint failed", result.stderr)
        state = json.loads((self.root / ".dream" / "workflow.json").read_text())
        self.assertEqual(state["phase"], "processing")

    def test_skipped_transcript_has_reason_and_is_reported(self):
        self.begin()
        result = self.cli("complete", "--thread", str(self.transcript), "--status", "skipped", "--reason", "dream job transcript excluded")
        self.assertEqual(result.returncode, 0, result.stderr)
        committed = self.cli("commit")
        self.assertEqual(committed.returncode, 0, committed.stderr + committed.stdout)
        report = next((self.root / "reports").glob("*.dream.md")).read_text()
        self.assertIn("Skipped: 1", report)
        self.assertIn("dream job transcript excluded", report)
        self.assertIn("Unresolved gaps: 0", report)

    def test_source_watermark_advances_to_captured_boundary_after_commit(self):
        manifest_path = self.transcript.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["source"] = "alma"
        manifest["advances_watermark"] = True
        manifest["source_boundary"] = "2026-11-09T12:00:00+00:00"
        manifest_path.write_text(json.dumps(manifest))
        self.begin()
        self.finish_transcript(gaps="missing evidence")
        partial = self.cli("commit")
        self.assertEqual(partial.returncode, 0, partial.stderr + partial.stdout)
        self.assertEqual(json.loads((self.root / ".dream" / "workflow.json").read_text())["phase"], "reviewed_with_gaps")
        self.assertFalse((self.root / ".dream" / "last-run-alma").exists())
        self.assertEqual((self.root / ".dream" / "last-reviewed-alma").read_text().strip(), manifest["source_boundary"])
        backlog = json.loads(next((self.root / ".dream" / "unresolved-backlog").glob("*.json")).read_text())
        self.assertEqual(backlog["gaps"][0]["reason"], "missing evidence")

    def test_failed_completion_command_is_checked(self):
        self.begin()
        result = self.cli("complete", "--thread", str(self.transcript), "--status", "analyzed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unread or unacknowledged", result.stderr)


    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def state(self):
        return json.loads((self.root / ".dream" / "workflow.json").read_text())

    def test_unrelated_index_blobs_modes_and_deletion_are_preserved(self):
        for name in ("remove.md", "staged.sh", "unstaged.md"):
            (self.root / name).write_text("baseline\n")
        self.git("add", "remove.md", "staged.sh", "unstaged.md")
        self.git("commit", "-qm", "unrelated baseline")
        self.git("rm", "remove.md")
        staged = self.root / "staged.sh"
        staged.write_text("staged bytes\n")
        staged.chmod(0o755)
        self.git("add", "staged.sh")
        staged.write_text("later unstaged bytes\n")
        (self.root / "unstaged.md").write_text("unstaged bytes\n")
        unrelated = ["remove.md", "staged.sh", "unstaged.md", "README.md"]
        index_before = self.git("ls-files", "--stage", "-z", "--", *unrelated)
        diff_before = self.git("diff", "--cached", "--binary", "--", *unrelated)
        self.begin()
        self.finish_transcript()
        self.own_fact()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("ls-files", "--stage", "-z", "--", *unrelated), index_before)
        self.assertEqual(self.git("diff", "--cached", "--binary", "--", *unrelated), diff_before)
        self.assertEqual(staged.read_text(), "later unstaged bytes\n")
        self.assertEqual((self.root / "unstaged.md").read_text(), "unstaged bytes\n")
        self.assertFalse((self.root / "remove.md").exists())
        owned = ["facts/known.md", self.state()["report"]]
        self.assertEqual(self.git("status", "--porcelain", "--", *owned), "")
        self.assertEqual(set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()), set(owned))

    def test_normal_owned_index_reconciles_after_head_advanced_before_receipt(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        parent = state["prepared"]["parent"]
        self.git("reset", "-q", parent, "--", *state["prepared"]["paths"])
        state["phase"] = "committing"
        (self.root / ".dream" / "workflow.json").write_text(json.dumps(state))
        retried = self.cli("commit")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(self.git("status", "--porcelain", "--", *state["prepared"]["paths"]), "")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), state["prepared"]["commit"])

    def test_crash_before_ref_publication_reuses_prepared_commit(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        prepared = state["prepared"]
        self.git("update-ref", "HEAD", prepared["parent"], prepared["commit"])
        self.git("reset", "-q", prepared["parent"], "--", *prepared["paths"])
        state["phase"] = "committing"
        (self.root / ".dream" / "workflow.json").write_text(json.dumps(state))
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), prepared["commit"])

    def test_other_commit_with_same_paths_is_not_adopted(self):
        self.begin()
        self.finish_transcript()
        self.own_fact()
        self.assertEqual(self.cli("commit").returncode, 0)
        state = self.state()
        prepared = state["prepared"]
        self.git("update-ref", "HEAD", prepared["parent"], prepared["commit"])
        # A different commit with the same parent and files was accepted by the old recovery code.
        other = self.git("commit-tree", prepared["tree"], "-p", prepared["parent"], "-m", "unrelated transaction").strip()
        self.git("update-ref", "HEAD", other, prepared["parent"])
        state["phase"] = "committing"
        (self.root / ".dream" / "workflow.json").write_text(json.dumps(state))
        result = self.cli("commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HEAD moved", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), other)

    def test_success_retry_repairs_missing_derived_metadata_even_with_receipt(self):
        manifest_path = self.transcript.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(source="alma", advances_watermark=True)
        manifest_path.write_text(json.dumps(manifest))
        self.begin()
        self.finish_transcript()
        self.assertEqual(self.cli("commit").returncode, 0)
        (self.root / ".dream" / "processed-batches" / "batch-one.json").unlink()
        (self.root / ".dream" / "last-run-alma").unlink()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / ".dream" / "last-run-alma").read_text(), manifest["source_boundary"] + "\n")
        self.assertTrue((self.root / ".dream" / "processed-batches" / "batch-one.json").exists())

    def test_partial_unread_run_can_resume_and_preserves_both_receipts(self):
        self.begin()
        self.assertNotEqual(self.cli("commit").returncode, 0)
        partial = self.cli("commit", "--partial")
        self.assertEqual(partial.returncode, 0, partial.stderr)
        self.assertEqual(self.state()["phase"], "partial")
        partial_commit = self.state()["commit"]
        self.finish_transcript()
        final = self.cli("commit")
        self.assertEqual(final.returncode, 0, final.stderr)
        state = self.state()
        self.assertEqual(state["phase"], "success")
        self.assertNotEqual(state["commit"], partial_commit)
        for commit in (partial_commit, state["commit"]):
            self.assertTrue((self.root / ".dream" / "receipts" / f"{state['id']}-{commit}.json").exists())

    def test_failed_verify_can_be_repaired_with_explicit_hash_amend(self):
        self.begin()
        self.finish_transcript()
        fact = self.root / "facts" / "fix.md"
        fact.write_text("---\ntitle: Repaired fact\nrecurrence: 1\nstatus: invalid\nsource: thread-1\n---\n" + "The evidence and its limitations are retained for verification against the original source. " * 4)
        self.assertEqual(self.cli("own", input="facts/fix.md\n").returncode, 0)
        old_hash = self.state()["owned"]["facts/fix.md"]
        self.assertNotEqual(self.cli("commit").returncode, 0)
        fact.write_text(fact.read_text().replace("status: invalid", "status: open"))
        self.assertNotEqual(self.cli("own", input="facts/fix.md\n").returncode, 0)
        self.assertNotEqual(self.cli("amend", "--path", "facts/fix.md", "--expected-hash", "wrong").returncode, 0)
        amendment = self.cli("amend", "--path", "facts/fix.md", "--expected-hash", old_hash)
        self.assertEqual(amendment.returncode, 0, amendment.stderr)
        committed = self.cli("commit")
        self.assertEqual(committed.returncode, 0, committed.stderr)

    def test_manifest_hashes_and_inventory_reject_changed_export_before_begin(self):
        self.transcript.write_text("tampered before begin")
        self.assertNotEqual(self.cli("begin", "--batch", str(self.transcript.parent)).returncode, 0)

    def test_skip_requires_explicit_reason_instead_of_inventing_dream_topic(self):
        self.begin()
        result = self.cli("complete", "--thread", str(self.transcript), "--status", "skipped")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()["transcripts"][str(self.transcript)]["analysis"], "pending")

    def test_unicode_offsets_and_unacknowledged_replay_do_not_skip_content(self):
        content = "廣東話😀\n" * 10
        self.transcript.write_text(content)
        metadata_path = self.transcript.parent / "manifest.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["source_files"] = {}
        (self.transcript.parent / "body.json").unlink()
        metadata["transcript_hashes"]["t.txt"] = hashlib.sha256(self.transcript.read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata))
        self.begin()
        read = self.cli("read", "--thread", str(self.transcript), "--limit", "4")
        self.assertEqual(read.stdout, content[:4])
        replay = self.cli("read", "--thread", str(self.transcript), "--limit", "100")
        self.assertEqual(replay.stdout, read.stdout)
        self.assertEqual(self.cli("ack", "--thread", str(self.transcript)).returncode, 0)
        self.assertEqual(self.state()["transcripts"][str(self.transcript)]["ack"], 4)
        self.assertNotEqual(self.cli("read", "--thread", str(self.transcript), "--offset", "5").returncode, 0)

    def test_zero_transcript_batch_gets_a_verified_no_fact_report(self):
        self.transcript.unlink()
        metadata_path = self.transcript.parent / "manifest.json"
        metadata = json.loads(metadata_path.read_text())
        metadata.update(transcripts=[], transcript_hashes={})
        metadata_path.write_text(json.dumps(metadata))
        self.begin()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = (self.root / self.state()["report"]).read_text()
        self.assertIn("Analyzed: 0", report)
        self.assertIn("Owned fact/solution files: 0", report)



    def test_first_dream_can_commit_to_an_unborn_wiki_branch(self):
        import shutil
        shutil.rmtree(self.root / ".git")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.begin()
        self.finish_transcript()
        self.own_fact()
        result = self.cli("commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "1")
        self.assertEqual(self.git("status", "--porcelain", "--", "facts/known.md", self.state()["report"]), "")



    def events(self):
        return [json.loads(line) for path in (self.root / ".dream" / "logs").glob("*.jsonl") for line in path.read_text().splitlines()]

    def test_timestamped_events_cover_review_verify_commit_and_hide_content(self):
        import datetime as dt
        self.begin()
        self.finish_transcript()
        self.own_fact()
        marker = "private-commit-message-fixture"
        result = self.cli("commit", "-m", marker)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertNotIn(marker, json.dumps(events))
        self.assertNotIn("transcript body", json.dumps(events))
        for stage, status in (("read", "emitted"), ("read", "acknowledged"), ("analysis", "recorded"), ("verify", "succeeded"), ("lint", "succeeded"), ("commit", "prepared"), ("commit", "published"), ("run", "success")):
            self.assertTrue(any(e["stage"] == stage and e["status"] == status for e in events), (stage, status))
        for e in events:
            self.assertIsNotNone(dt.datetime.fromisoformat(e["timestamp"]).tzinfo)
            self.assertGreaterEqual(e["elapsed_ms"], 0)
        receipt = self.state()
        completed = [e for e in events if e["stage"] == "run"][-1]
        self.assertEqual(completed["commit"], receipt["commit"])
        self.assertEqual(completed["run_id"], receipt["id"])
        self.assertGreaterEqual(completed["run_elapsed_ms"], 0)

    def test_rejected_command_logs_failure_and_read_stdout_stays_exact(self):
        self.begin()
        read = self.cli("read", "--thread", str(self.transcript), "--limit", "5")
        self.assertEqual(read.stdout, "trans")
        rejected = self.cli("complete", "--thread", str(self.transcript), "--status", "analyzed", "--source-complete")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertTrue(any(e["stage"] == "validation" and e["status"] == "failed" for e in self.events()))
        self.assertEqual(self.events()[-1]["exit_code"], 1)

    def test_read_only_status_writes_no_events(self):
        result = self.cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ".dream").exists())
        self.assertIn("event_logs", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
