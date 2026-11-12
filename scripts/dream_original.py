"""Read retained historical evidence. Never execute the commands in a transcript."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re

# Recognized transport/export omissions, not ordinary prose about truncation.
CUT = re.compile(r'alma compacted \d+|\[\d+ characters .*elided|\[\d+ chars cut\]|\[thread truncated; \d+ chars cut\]|\[output truncated(?:\s|\])', re.I)
ARCHIVE = re.compile(r'(?:Full output[^\n]*?saved to |full (?:output|text) (?:is )?(?:saved|available) (?:to|at|in) |Full output: )([^\n]+?)(?:\. (?:For |Read |Use )|[\n]|$)', re.I)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def incomplete(value):
    if isinstance(value, str):
        return bool(CUT.search(value))
    if isinstance(value, list):
        return any(incomplete(v) for v in value)
    if isinstance(value, dict):
        if any(value.get(k) is False for k in ('stdoutComplete', 'stderrComplete')):
            return True
        if value.get('sourceTextTruncated') or value.get('truncated') is True:
            return True
        return any(incomplete(v) for v in value.values())
    return False


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)


def page_gaps(pages):
    gaps = []
    for page in pages:
        for mid in page.get('missingMessageIds', []):
            gaps.append(f'missing message: {mid}')
        if page.get('sourceTextTruncated'):
            gaps.append('platform source text truncated')
    if pages and pages[-1].get('hasMore'):
        gaps.append('source pagination unfinished')
    return gaps


def capture(run, transcript):
    """Build one immutable local evidence bundle, scoped to frozen message IDs."""
    batch = Path(run['batch'])
    manifest = json.loads((batch / 'manifest.json').read_text())
    tid = Path(transcript).read_text().splitlines()[0].split()[1]
    if not re.fullmatch(r'[A-Za-z0-9._-]+', tid):
        raise ValueError('unsafe original thread ID')
    source = batch / f'{tid}.json'
    provenance, gaps, archives = [], [], []
    if source.name in manifest.get('source_files', {}):
        raw = source.read_bytes()
        snapshot = json.loads(raw)
        provenance.append({'kind': 'snapshot', 'path': str(source), 'sha256': sha(raw)})
    else:
        # The compact gives some IDs, but cannot establish a complete old inventory.
        mids = re.findall(r'^(?:USER|ASSISTANT|TOOL) \[id=([^;\]]+)', Path(transcript).read_text(), re.M)
        snapshot = {'id': tid, 'messages': [{'id': mid, 'content': []} for mid in mids]}
        gaps.append('legacy compact has no full source inventory; recovered IDs are partial scope only')
    messages = snapshot['messages']
    pages = snapshot.get('source_pages', [])
    known_gaps = page_gaps(pages)
    need_live = bool(known_gaps) or any(incomplete(m.get('content', [])) for m in messages) or not source.exists()
    lookup_error = None
    if need_live and run['source'] == 'alma':
        spec = importlib.util.spec_from_file_location('dream_alma_original', Path(__file__).with_name('dream-source-alma.py'))
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        try:
            rows, live_pages = adapter.fetch(tid, page=20, cap=2000)
            original_ids = {m['id'] for m in messages if m.get('id')}
            missing_ids = {mid for p in pages for mid in p.get('missingMessageIds', [])}
            selected = [r for r in rows if {r.get('id'), r.get('message', r).get('id')} & (original_ids | missing_ids)]
            live = {key: m for m in map(adapter.message, selected) for key in (m['id'], m['sourceId'])}
            replaced = []
            for m in messages:
                candidate = live.get(m.get('id'))
                # Never substitute a different tool invocation, even in the same message.
                calls = {p.get('toolCallId') for p in m.get('content', []) if isinstance(p, dict) and p.get('toolCallId')}
                candidate_calls = {p.get('toolCallId') for p in candidate.get('content', []) if isinstance(p, dict)} if candidate else set()
                if candidate and calls == candidate_calls and not incomplete(candidate.get('content', [])):
                    replaced.append(candidate)
                else:
                    replaced.append(m)
            messages = replaced + [live[mid] for mid in sorted(missing_ids) if mid in live]
            known_gaps = [g for g in known_gaps if not (g.startswith('missing message: ') and g.removeprefix('missing message: ') in live)]
            # An upstream truncation/pagination signal has unknown scope: cannot be
            # disproved merely by retrieving the IDs that happened to survive it.
            provenance.append({'kind': 'alma-original-messages', 'thread_id': tid,
                               'pages': live_pages, 'message_ids': [m['id'] for m in map(adapter.message, selected)],
                               'payload_sha256': sha(json.dumps(selected, sort_keys=True).encode()),
                               'raw_messages': selected})
            if page_gaps(live_pages):
                lookup_error = 'live original lookup also reports gaps: ' + '; '.join(page_gaps(live_pages))
        except (RuntimeError, OSError, ValueError) as exc:
            lookup_error = f'original lookup unavailable: {exc}'
    gaps.extend(known_gaps)

    def recover_value(value, mid, call, seen=()):
        if not incomplete(value):
            return
        if isinstance(value, dict) and any(k in value for k in ('stdoutComplete', 'stderrComplete')):
            for stream in ('stdout', 'stderr'):
                data = value.get(stream, '')
                if value.get(stream + 'Complete') is False and not incomplete(data):
                    gaps.append(f'{mid}/{call}: {stream} incomplete without recoverable truncation marker')
                else:
                    recover_value(data, mid, call, seen)
            return
        refs = [match.group(1).strip().strip('`').rstrip('.') for text in strings(value) for match in ARCHIVE.finditer(text)]
        if not refs:
            gaps.append(f'{mid}/{call or "text"}: truncated original; no retained-output locator')
            return
        for ref in dict.fromkeys(refs):
            path = Path(ref)
            identity = {'message_id': mid, 'tool_call_id': call, 'path': ref}
            if not path.is_absolute() or ref in seen or len(seen) >= 4:
                gaps.append(f'{mid}/{call}: invalid or cyclic retained-output locator {ref}')
                continue
            try:
                raw = path.read_bytes()
                text = raw.decode('utf-8')
            except (OSError, UnicodeError) as exc:
                gaps.append(f'{mid}/{call}: retained output unavailable: {ref} ({type(exc).__name__})')
                continue
            # These are retained bytes observed now, not a historical command rerun.
            provenance.append({'kind': 'retained-output', **identity, 'sha256': sha(raw), 'bytes': len(raw), 'reference_origin': 'original-message-output'})
            archives.append({**identity, 'text': text})
            recover_value(text, mid, call, (*seen, ref))
    for msg in messages:
        content = msg.get('content', [])
        if isinstance(content, str):
            recover_value(content, msg.get('id'), None)
        else:
            outputs = {p.get('toolCallId') for p in content if p['type'] == 'tool_result' and p.get('output') is not None}
            for part in content:
                if part['type'] == 'tool_call' and part.get('toolCallId') not in outputs:
                    gaps.append(f"{msg.get('id')}/{part.get('toolCallId')}: tool output unavailable or not retained")
                # Commands are evidence, never instructions to execute or read arbitrary files.
                if part['type'] in ('text', 'tool_result'):
                    recover_value(part.get('output', part.get('text', '')), msg.get('id'), part.get('toolCallId'))
    if lookup_error and gaps:
        gaps.append(lookup_error)
    return {'thread_id': tid, 'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'scope': 'frozen source inventory; text and stored tool outputs only',
            'messages': messages, 'source_pages': pages, 'retained_outputs': archives,
            'provenance': provenance, 'gaps': list(dict.fromkeys(gaps)),
            'coverage_note': 'emission receipts prove delivery ranges only; reader must verify visible text and attest review. No command was rerun.'}


def require_complete(record):
    proof = record.get('evidence')
    if not proof:
        raise ValueError('source coverage missing; use dream recover and read every bounded page')
    raw = Path(proof['path']).read_bytes()
    if sha(raw) != proof['sha256']:
        raise ValueError('historical evidence changed')
    bundle = json.loads(raw)
    if bundle['gaps']:
        raise ValueError('historical source has unresolved gaps; complete with --gaps, never --source-complete')
    if proof['emitted_through'] != len(raw.decode('utf-8')):
        raise ValueError('historical source has unread pages; compact ack is not source coverage')
