"""Regression checks at the installed Bash CLI boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from tests import test_dream_workflow as workflow_tests
from tests import test_dream_export as export_tests


class ReviewIntegration(unittest.TestCase):
    def setUp(self):
        self.f = workflow_tests.DreamWorkflowCLI()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.cli = self.f.cli

    def manifest(self, **changes):
        path = self.f.transcript.parent / 'manifest.json'
        data = json.loads(path.read_text())
        data.update(changes)
        path.write_text(json.dumps(data))
        return data

    def test_ack_next_invalid_limit_does_not_acknowledge(self):
        self.f.begin()
        self.cli('read', '--thread', str(self.f.transcript), '--limit', '5')
        before = self.f.state()
        for limit in ('-1', '0', '12001'):
            result = self.cli('ack', '--thread', str(self.f.transcript), '--next', '--limit', limit)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(self.f.state(), before)
        self.assertEqual(self.cli('ack', '--thread', str(self.f.transcript), '--next', '--limit', '5').stdout, 'cript')
        logs = [json.loads(line) for p in (self.f.root / '.dream/logs').glob('*.jsonl') for line in p.read_text().splitlines()]
        self.assertTrue(any(e['stage'] == 'read' and e.get('through_character') == 5 for e in logs))
        self.assertTrue(any(e['stage'] == 'read' and e.get('start_character') == 5 for e in logs))

    def test_source_cli_bounds_tamper_and_terminal_read(self):
        (self.f.transcript.parent / "body.json").unlink()
        self.f.transcript.write_text('SESSION t fixture\n\ncompact body\n')
        source = self.f.transcript.parent / 't.json'
        source.write_text(json.dumps({'id': 't', 'messages': [{'role': 'tool', 'content': 'x' * 20000}]}, sort_keys=True) + '\n')
        self.manifest(transcript_hashes={'t.txt': hashlib.sha256(self.f.transcript.read_bytes()).hexdigest()}, source_files={'t.json': hashlib.sha256(source.read_bytes()).hexdigest()})
        self.f.begin()
        before = self.f.state()
        for limit in ('-1', '0', '12001'):
            result = self.cli('source', '--thread', 't', '--limit', limit)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.f.state(), before)
        result = self.cli('source', '--thread', 't', '--limit', '12000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, source.read_text()[:12000])
        self.assertEqual(self.f.state(), before)
        self.cli('read', '--thread', str(self.f.transcript))
        self.cli('ack', '--thread', str(self.f.transcript))
        self.cli('complete', '--thread', str(self.f.transcript), '--status', 'analyzed', '--gaps', 'original history unavailable')
        done = self.cli('commit')
        self.assertEqual(done.returncode, 0, done.stderr)
        result = self.cli('source', '--thread', 't', '--offset', '12000', '--limit', '12000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, source.read_text()[12000:24000])
        source.write_text(source.read_text() + 'tamper')
        self.assertNotEqual(self.cli('source', '--thread', 't').returncode, 0)

    def test_sidecar_tamper_after_begin_blocks_commit(self):
        (self.f.transcript.parent / "body.json").unlink()
        self.f.transcript.write_text('SESSION t fixture\n')
        source = self.f.transcript.parent / 't.json'
        source.write_text('{"id":"t","messages":[]}')
        self.manifest(transcript_hashes={'t.txt': hashlib.sha256(self.f.transcript.read_bytes()).hexdigest()}, source_files={'t.json': hashlib.sha256(source.read_bytes()).hexdigest()})
        self.f.begin()
        self.cli('read', '--thread', str(self.f.transcript))
        self.cli('ack', '--thread', str(self.f.transcript))
        self.assertEqual(self.cli('recover', '--thread', 't').returncode, 0)
        self.assertEqual(self.cli('complete', '--thread', str(self.f.transcript), '--status', 'analyzed', '--source-complete').returncode, 0)
        source.write_text('changed')
        self.assertNotEqual(self.cli('commit').returncode, 0)

    def test_legacy_partial_receipt_can_finalize_without_resolving_gap(self):
        self.manifest(source='alma', advances_watermark=True)
        self.f.begin()
        self.f.finish_transcript(gaps='missing original tool output')
        saved = self.cli('commit')
        self.assertEqual(saved.returncode, 0, saved.stderr)
        state = self.f.state()
        # Old code has the same state/receipt shape, but always ends as partial
        # when a source gap exists, even with no remaining compact reads.
        state['phase'] = 'partial'
        state['prepared']['outcome'] = 'partial'
        (self.f.root / '.dream/workflow.json').write_text(json.dumps(state))
        (self.f.root / '.dream/processed-batches/batch-one.json').unlink()
        (self.f.root / '.dream/unresolved-backlog/batch-one.json').unlink()
        (self.f.root / '.dream/last-reviewed-alma').unlink()
        before = self.f.state()
        result = self.cli('commit')
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.f.state()
        self.assertEqual(after['phase'], 'reviewed_with_gaps')
        self.assertEqual(after['transcripts'], before['transcripts'])
        self.assertEqual(after['gaps'], before['gaps'])
        self.assertFalse((self.f.root / '.dream/last-run-alma').exists())
        self.assertTrue((self.f.root / '.dream/unresolved-backlog/batch-one.json').exists())

    def test_backlog_blocks_full_frontier_but_review_cursor_moves_monotonically(self):
        self.manifest(source='alma', advances_watermark=True, source_boundary='2026-11-09T12:00:00+00:00')
        self.f.begin()
        full = self.f.root / '.dream/last-run-alma'
        full.write_text('2026-11-08T12:00:00+00:00\n')
        self.f.finish_transcript(gaps='missing original')
        self.assertEqual(self.cli('commit').returncode, 0)
        first = self.f.state()
        batch2 = self.f.root / '.raw/batch-two'
        batch2.mkdir()
        t = batch2 / 't.txt'
        t.write_text('transcript body\n')
        (batch2 / 'manifest.json').write_text(json.dumps({'batch_id': 'batch-two', 'source': 'alma', 'advances_watermark': True, 'source_boundary': '2026-11-10T12:00:00+00:00', 'source_hash': 'second', 'transcripts': ['t.txt'], 'transcript_hashes': {'t.txt': hashlib.sha256(t.read_bytes()).hexdigest()}}))
        source = batch2 / 'body.json'
        source.write_bytes((self.f.transcript.parent / 'body.json').read_bytes())
        metadata = json.loads((batch2 / 'manifest.json').read_text())
        metadata['source_files'] = {'body.json': hashlib.sha256(source.read_bytes()).hexdigest()}
        (batch2 / 'manifest.json').write_text(json.dumps(metadata))
        self.assertEqual(self.cli('begin', '--batch', str(batch2)).returncode, 0)
        self.f.transcript = t
        self.f.finish_transcript()
        self.assertEqual(self.cli('commit').returncode, 0)
        self.assertEqual(full.read_text().strip(), '2026-11-08T12:00:00+00:00')
        reviewed = self.f.root / '.dream/last-reviewed-alma'
        self.assertEqual(reviewed.read_text().strip(), '2026-11-10T12:00:00+00:00')
        self.assertTrue((self.f.root / '.dream/unresolved-backlog/batch-one.json').exists())
        (self.f.root / '.dream/workflow.json').write_text(json.dumps(first))
        self.assertEqual(self.cli('commit').returncode, 0)
        self.assertEqual(reviewed.read_text().strip(), '2026-11-10T12:00:00+00:00')

    def test_legacy_manifest_rejects_extra_files(self):
        (self.f.transcript.parent / 'unregistered.json').write_text('{}')
        self.assertNotEqual(self.cli('begin', '--batch', str(self.f.transcript.parent)).returncode, 0)

    def test_explicit_id_exclusion_skips_fetch_not_same_title_user_thread(self):
        exporter = export_tests.DreamExportTests()
        exporter.setUp()
        self.addCleanup(exporter.tearDown)
        bindir = Path(exporter.tmp.name) / 'bin'
        bindir.mkdir()
        stub = bindir / 'alma'
        stub.write_text("""#!/usr/bin/env python3
import json,sys
if sys.argv[1] == 'threads':
 print(json.dumps([{'id':i,'title':'Nightly Dream','updatedAt':'2026-11-10T00:00:00Z'} for i in ['self-job','ordinary-user']]))
else:
 if sys.argv[3] == 'self-job': raise SystemExit(43)
 print(json.dumps({'messages':[{'id':'m1','message':{'role':'user','parts':[{'type':'text','text':'ordinary same-title conversation'}]}}],'hasMore':False}))
""")
        stub.chmod(0o755)
        result = exporter.run_wiki('--source', 'alma', '--since', '2026-11-09', '--exclude-thread', 'self-job', path=bindir)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest_path = next((exporter.root / '.raw').glob('*/*/manifest.json'))
        batch = manifest_path.parent
        self.assertEqual(json.loads((batch / 'self-job.json').read_text())['messages'], [])
        ordinary = json.loads((batch / 'ordinary-user.json').read_text())
        self.assertNotIn('exclusion', ordinary)
        self.assertIn('ordinary same-title', ordinary['messages'][0]['content'][0]['text'])
        subprocess.run(['git', 'init', '-q', str(exporter.root)], check=True)
        begun = subprocess.run(['bash', str(export_tests.WIKI), 'dream', 'begin', '--batch', str(batch)], env=exporter.env, capture_output=True, text=True)
        self.assertEqual(begun.returncode, 0, begun.stderr)
        state = json.loads((exporter.root / '.dream/workflow.json').read_text())
        outcomes = {Path(name).name:record for name,record in state['transcripts'].items()}
        excluded = next(r for n,r in outcomes.items() if n.startswith('self-job-'))
        retained = next(r for n,r in outcomes.items() if n.startswith('ordinary-user-'))
        self.assertEqual(excluded['analysis'], 'skipped')
        self.assertEqual(excluded['ack'], 0)
        self.assertIn('not reviewed', excluded['reason'])
        self.assertEqual(retained['analysis'], 'pending')

    def test_clipped_tool_result_is_retained_in_full_snapshot(self):
        exporter = export_tests.DreamExportTests()
        exporter.setUp()
        self.addCleanup(exporter.tearDown)
        data = exporter.sample()
        payload = 'START' + 'x' * 5000 + 'END-OF-AVAILABLE-TOOL-RESULT'
        data['threads'][0]['messages'][1]['content'][1]['output'] = payload
        result = exporter.run_wiki('--input', str(exporter.write_input(data)), '--since', '2026-11-09')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = next((exporter.root / '.raw').glob('*/*/manifest.json'))
        snapshot = json.loads((manifest.parent / 't-one.json').read_text())
        self.assertEqual(snapshot['messages'][1]['content'][1]['output'], payload)
        compact = (manifest.parent / 't-one-example.txt').read_text()
        self.assertNotIn('END-OF-AVAILABLE-TOOL-RESULT', compact)
        self.assertIn('chars cut', compact)


if __name__ == '__main__':
    unittest.main()
