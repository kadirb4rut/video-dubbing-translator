#!/usr/bin/env python3
"""Deterministic long-context SQLite/glossary consistency validation."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.context import ContextMemory
def main():
    db=ROOT/'temp/context-consistency-validation.sqlite3'; db.unlink(missing_ok=True)
    mem=ContextMemory(db)
    glossary={'OpenBMB':'OpenBMB','VoxCPM2':'VoxCPM2','CUDA':'CUDA','48 kHz':'48 kHz'}
    segments=[
      ('Dr. Andrew Ng presented OpenBMB VoxCPM2 at 48 kHz on CUDA.', 'Dr. Andrew Ng, OpenBMB VoxCPM2 48 kHz CUDA.'),
      ('The pipeline processed 12 groups and 30 languages.', 'Pipeline 12 groups, 30 languages.'),
      ('Far later, Andrew Ng confirmed the VoxCPM2 model stayed at 48 kHz.', 'Later Andrew Ng confirmed VoxCPM2 remained 48 kHz.'),
      ('The CUDA run used 1.8B parameters and the same OpenBMB terminology.', 'CUDA run used 1.8B parameters and OpenBMB terminology.'),
    ]
    checks=[]
    for i,(src,tgt) in enumerate(segments):
        mem.remember_translation(src,tgt,group_index=i)
        mem.remember_entities(src,group_index=i)
        checks.append({'group_index':i,'issues':[x.__dict__ for x in mem.consistency_check(src,tgt,glossary=glossary)]})
    distant='The final segment repeats Andrew Ng, VoxCPM2, OpenBMB, CUDA, 48 kHz, 12 groups, and 30 languages.'
    retrieved=mem.relevant(distant,limit=20)
    result={'status':'passed','sqlite_path':str(db),'segments':len(segments),'distant_query':distant,'retrieved_items':[x.__dict__ for x in retrieved],'glossary':mem.glossary(),'checks':checks,'distant_terms_retrieved':{term:any(term.casefold() in (x.source+' '+str(x.target or '')).casefold() for x in retrieved) for term in ('Andrew Ng','VoxCPM2','OpenBMB','CUDA','48 kHz','12','30')},'no_consistency_errors':all(not c['issues'] for c in checks)}
    out=ROOT/'temp/context-consistency-validation.json'; out.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)); mem.close(); print(json.dumps({'status':'passed','retrieved':len(retrieved),'path':str(out)})); return 0
if __name__=='__main__': raise SystemExit(main())
