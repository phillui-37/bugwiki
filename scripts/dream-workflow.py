#!/usr/bin/env python3
"""Durable, manifest-scoped dream run progress and commit receipt."""
import argparse, datetime as dt, hashlib, json, os, subprocess, sys
from pathlib import Path


def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for block in iter(lambda:f.read(65536),b''): h.update(block)
    return h.hexdigest()


def save(p, obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    os.replace(tmp,p)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); ap.add_argument('action',choices=['begin','status','read','ack','complete','own','commit']); ap.add_argument('--batch'); ap.add_argument('--thread'); ap.add_argument('--limit',type=int,default=12000); ap.add_argument('--offset',type=int); ap.add_argument('--status'); ap.add_argument('--source-complete',action='store_true'); ap.add_argument('--gaps',default=''); ap.add_argument('-m',default=''); a=ap.parse_args()
    root=Path(a.root).resolve(); state=root/'.dream'/'workflow.json'
    try:
        run=json.loads(state.read_text()) if state.exists() else None
        if a.action=='begin':
            if not a.batch: raise ValueError('--batch required')
            if run and run.get('phase')!='success': raise ValueError('pending run exists; resume it before exporting another batch')
            batch=Path(a.batch).resolve()
            files=sorted(p for p in batch.rglob('*.txt') if p.is_file())
            if not files: raise ValueError('batch contains no transcripts')
            ident=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            run={'id':ident,'phase':'processing','batch':str(batch),'transcripts':{},'owned':{},'gaps':[],'started':dt.datetime.now(dt.timezone.utc).isoformat()}
            for p in files:
                key=str(p); run['transcripts'][key]={'hash':digest(p),'next':0,'ack':0,'analysis':'pending','source_complete':False}
            save(state,run); print(ident); return 0
        if a.action=='status':
            print(json.dumps({**(run or {'phase':'idle','receipt':json.loads((root/'.dream'/'receipt.json').read_text()) if (root/'.dream'/'receipt.json').exists() else None}), 'source_checkpoints':{p.name.removeprefix('last-run-'):p.read_text().strip() for p in (root/'.dream').glob('last-run-*')}, 'legacy_checkpoint':(root/'.dream'/'last-run').read_text().strip() if (root/'.dream'/'last-run').exists() else None},indent=2,sort_keys=True)); return 0
        if not run: raise ValueError('no active dream run; use dream begin --batch DIR')
        if a.action in ('read','ack'):
            if not a.thread or a.thread not in run['transcripts']: raise ValueError('--thread must be a manifest transcript path')
            rec=run['transcripts'][a.thread]; p=Path(a.thread)
            if digest(p)!=rec['hash']: raise ValueError('transcript hash changed; run rejected')
            text=p.read_text(encoding='utf-8'); cur=rec['next'] if a.action=='read' else rec['ack']
            if a.offset is not None and a.offset!=cur: raise ValueError(f'non-contiguous offset; expected {cur}')
            if a.action=='read':
                if a.limit<1: raise ValueError('--limit must be positive')
                chunk=text[cur:cur+a.limit]; rec['next']=cur+len(chunk); save(state,run); sys.stdout.write(chunk); return 0
            if rec['next']<=rec['ack']: raise ValueError('no unread chunk to acknowledge')
            rec['ack']=rec['next']; save(state,run); print(f"acknowledged through character {rec['ack']}"); return 0
        if a.action=='complete':
            if not a.thread or a.thread not in run['transcripts']: raise ValueError('--thread must be a manifest transcript path')
            rec=run['transcripts'][a.thread]
            if digest(a.thread)!=rec['hash']: raise ValueError('transcript hash changed; run rejected')
            if a.status not in ('analyzed','skipped'): raise ValueError('--status must be analyzed or skipped')
            if a.status=='analyzed' and rec['ack']!=Path(a.thread).stat().st_size and rec['ack']!=len(Path(a.thread).read_text(encoding='utf-8')): raise ValueError('transcript has unread or unacknowledged content')
            content=Path(a.thread).read_text(encoding='utf-8')
            if a.status=='analyzed' and not a.source_complete: raise ValueError('analyzed completion requires explicit --source-complete acknowledgement')
            if a.status=='analyzed' and ('thread truncated' in content or 'chars cut]' in content): raise ValueError('compact transcript contains a known truncation; source coverage remains incomplete')
            rec['analysis']=a.status; rec['source_complete']=a.status=='analyzed' and a.source_complete and not a.gaps
            if a.gaps: run['gaps'].append({'thread':a.thread,'reason':a.gaps})
            save(state,run); return 0
        if a.action=='own':
            for rel in sys.stdin.read().splitlines():
                p=root/rel
                if not p.is_file() or not str(p.resolve()).startswith(str(root)+os.sep): raise ValueError(f'invalid owned path: {rel}')
                run['owned'][rel]=digest(p)
            save(state,run); return 0
        if a.action=='commit':
            incomplete=[p for p,r in run['transcripts'].items() if r['analysis']!='analyzed' or not r['source_complete']]
            if incomplete: raise ValueError('incomplete transcript analysis/source coverage: '+', '.join(incomplete))
            if run['gaps']: raise ValueError('source gaps remain; full success is forbidden')
            owned=run['owned'];
            for rel, expected in owned.items():
                p=root/rel
                if not p.is_file() or digest(p)!=expected: raise ValueError(f'owned artifact changed or missing: {rel}')
            if subprocess.run(['bash',str(Path(__file__).with_name('wiki')),'verify'],env=dict(os.environ,WIKI_HOME=str(root))).returncode: raise ValueError('wiki verify failed')
            if subprocess.run(['bash',str(Path(__file__).with_name('wiki')),'lint'],env=dict(os.environ,WIKI_HOME=str(root))).returncode: raise ValueError('wiki lint failed')
            gitdir=root/'.git'
            if not gitdir.exists(): raise ValueError('wiki content root must be a git repo')
            paths=list(owned)
            if paths:
                env=dict(os.environ,GIT_INDEX_FILE=str(root/'.dream'/f'index-{run["id"]}'))
                subprocess.run(['git','-C',str(root),'read-tree','HEAD'],check=True,env=env)
                subprocess.run(['git','-C',str(root),'add','--',*paths],check=True,env=env)
                if subprocess.run(['git','-C',str(root),'diff','--cached','--quiet'],env=env).returncode:
                    subprocess.run(['git','-C',str(root),'commit','-m',a.m or f'dream {run["id"]}'],check=True,env=env)
                commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
                subprocess.run(['git','-C',str(root),'reset','-q','HEAD','--',*paths],check=True)
            else: commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
            run['phase']='success'; run['commit']=commit; run['completed']=dt.datetime.now(dt.timezone.utc).isoformat(); save(state,run)
            receipt={'run':run['id'],'commit':commit,'phase':'success','owned':paths,'completed':run['completed']}; save(root/'.dream'/'receipt.json',receipt); print(json.dumps(receipt)); return 0
    except (OSError,ValueError,subprocess.CalledProcessError,json.JSONDecodeError) as e:
        print(f'dream {a.action}: {e}',file=sys.stderr); return 1

if __name__=='__main__': sys.exit(main())
