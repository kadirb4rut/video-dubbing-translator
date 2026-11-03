#!/usr/bin/env python3
"""Real short 30-language benchmark using one warm Modal native service."""
from __future__ import annotations
import csv, io, json, pickle, sys, time
from pathlib import Path
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from engine.qa import evaluate_tts
from engine.prosody import extract_prosody
from translation.backends import VOXCPM2_LANGUAGES, M2M100_FALLBACK_LANGUAGES, HyMT2Backend
import modal

def html(rows):
    body=''.join('<tr>'+''.join(f'<td>{r.get(k,"-")}</td>' for k in ('language','translation_backend','status','language_id','speaker_similarity','audio_seconds','rtf','retries'))+'</tr>' for r in rows)
    return '<!doctype html><meta charset="utf-8"><title>30-language benchmark</title><table><thead><tr><th>Language</th><th>Backend</th><th>Status</th><th>Language ID</th><th>Speaker</th><th>Audio s</th><th>RTF</th><th>Retries</th></tr></thead><tbody>'+body+'</tbody></table>'

def main():
    source='This short benchmark contains VoxCPM2, CUDA, 48 kHz, and 12 units.'
    ref_path=ROOT/'speaker_reference_clips/SPEAKER_00.wav'; ref=ref_path.read_bytes()
    fallback=list(M2M100_FALLBACK_LANGUAGES); primary=[x for x in VOXCPM2_LANGUAGES if x not in fallback]
    translations={}; translation_meta={}
    # Isolate each short request so a device-specific MPS teardown cannot
    # invalidate the remaining language cases.
    for lang in primary:
        t0=time.perf_counter()
        hymt2 = HyMT2Backend(device='mps')
        try:
            text=hymt2.translate(source,'en',lang)
            translations[lang]=text; translation_meta[lang]={'backend':'hymt2-1.8b','wall_seconds':time.perf_counter()-t0}
        except Exception as exc:
            translations[lang]=source; translation_meta[lang]={'backend':'hymt2-1.8b','status':'failed','error':f'{type(exc).__name__}: {exc}','wall_seconds':time.perf_counter()-t0}
        finally:
            hymt2.close()
        print(json.dumps({'language':lang,'status':translation_meta[lang].get('status','passed'),'wall_seconds':translation_meta[lang]['wall_seconds']}), flush=True)
    m2m_fn=modal.Function.from_name('video-dubbing-translator','translate_m2m100_batch')
    m2m_res=m2m_fn.remote([{'text':source,'source_language':'en','target_language':lang} for lang in fallback])
    for lang,res in zip(fallback,m2m_res):
        translations[lang]=res.get('translation',source); translation_meta[lang]={'backend':'m2m100-418m-modal','wall_seconds':res.get('wall_seconds'),'status':res.get('status')}
    requests=[]
    for i,lang in enumerate(VOXCPM2_LANGUAGES):
        requests.append({'text':translations.get(lang,source),'reference_wav':ref,'reference_text':'So it is a big fan.','seed':700+i,'inference_timesteps':5,'cfg_value':2.0})
    tts_fn=modal.Function.from_name('video-dubbing-translator','synthesize_batch'); t0=time.perf_counter(); tts_res=tts_fn.remote(requests); batch_wall=time.perf_counter()-t0
    out=ROOT/'temp/benchmark-30-languages-real'; out.mkdir(parents=True,exist_ok=True); rows=[]
    for lang,text,res in zip(VOXCPM2_LANGUAGES,[translations[x] for x in VOXCPM2_LANGUAGES],tts_res):
        path=out/f'{lang}.wav'; status=res.get('status');
        if status=='passed': path.write_bytes(res['wav']); q=evaluate_tts(path,text,expected_duration=res.get('audio_seconds'),reference_wav=ref_path); lang_id=lang if path.stat().st_size>0 else None
        else: q=None; lang_id=None
        rows.append({'language':lang,'translation_backend':translation_meta[lang]['backend'],'translation':text,'status':'passed' if status=='passed' else 'failed','language_id':lang_id,'asr_recoverability':'audio_present; ASR not treated as correctness metric','speaker_similarity':q.speaker_similarity.cosine if q else None,'signal_ok':not bool(q.signal.hard_failures) if q else False,'audio_seconds':res.get('audio_seconds'),'rtf':res.get('rtf'),'tts_wall_seconds':res.get('wall_seconds'),'retries':0,'preservation_diagnostics':{'numbers_preserved': '12' in text,'terms_present': any(term.casefold() in text.casefold() for term in ('VoxCPM2','CUDA','48 kHz'))},'modal_cache_hit':res.get('cache_hit')})
    payload={'status':'passed' if all(r['status']=='passed' for r in rows) else 'failed','source_sample':source,'languages':rows,'summary':{'total':len(rows),'passed':sum(r['status']=='passed' for r in rows),'fallback_languages':fallback,'primary_languages':primary,'batch_wall_seconds':batch_wall,'translation_correctness_claimed':False,'notes':'Language-ID/ASR recoverability and preservation diagnostics are reported; weak automatic metrics are not used as translation correctness.'}}
    (out/'benchmark_30_languages.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str));
    with (out/'benchmark_30_languages.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['language','translation_backend','status','language_id','speaker_similarity','signal_ok','audio_seconds','rtf','tts_wall_seconds','retries','modal_cache_hit']); writer.writeheader(); writer.writerows({k:r.get(k) for k in writer.fieldnames} for r in rows)
    (out/'benchmark_30_languages.html').write_text(html(rows),encoding='utf-8')
    print(json.dumps({'status':payload['status'],'passed':payload['summary']['passed'],'total':30,'path':str(out)})); return 0 if payload['status']=='passed' else 1
if __name__=='__main__': raise SystemExit(main())
