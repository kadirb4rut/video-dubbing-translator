"""Summarize recorded real session evidence; never assign semantic acceptance."""
from pathlib import Path
from collections import Counter
import argparse,json,math


def statistics(values):
    xs=sorted(float(x) for x in values if isinstance(x,(int,float)) and math.isfinite(x))
    if not xs:return {'n':0,'p50':None,'p95':None,'max':None}
    def q(p):
        k=(len(xs)-1)*p;i=int(k);j=min(i+1,len(xs)-1)
        return xs[i]+(xs[j]-xs[i])*(k-i)
    return {'n':len(xs),'p50':q(.5),'p95':q(.95),'max':max(xs)}


def summarize(directory):
    d=Path(directory)
    events=[json.loads(line) for line in (d/'events.jsonl').read_text().splitlines()]
    ready=[e for e in events if e['type']=='phrase_ready']
    played=[e for e in events if e['type']=='played']
    telemetry=[e for e in events if e['type']=='playback_metrics']
    metrics={k:statistics(e['metrics'].get(k) for e in ready) for k in (
        'speech_end_to_asr_seconds','intentional_endpoint_delay_seconds',
        'asr_to_translation_start_seconds','translation_seconds',
        'translation_to_tts_request_seconds','tts_first_chunk_seconds','tts_rtf',
        'total_tts_seconds','total_qa_seconds','commit_to_usable_audio_seconds')}
    metrics.update({k:statistics(e.get(k) for e in played) for k in (
        'playback_queue_seconds','capture_to_playback_seconds',
        'speech_end_to_playback_seconds','observed_av_offset_seconds',
        'generated_audio_seconds','presentation_audio_seconds','silence_padding_seconds')})
    metrics['absolute_end_of_phrase_av_offset_seconds']=statistics(
        abs(e['observed_av_offset_seconds']) for e in played
        if isinstance(e.get('observed_av_offset_seconds'),(int,float)))
    for k in ('buffer_seconds','queue_depth','buffer_bytes','video_playback_rate'):
        metrics[k]=statistics(e.get(k) for e in telemetry)
    intervals=[];pause=None
    for e in events:
        if e['type']=='pause':pause=e['server_elapsed']
        if e['type']=='resume' and pause is not None:
            intervals.append(e['server_elapsed']-pause);pause=None
    phrases=[]
    for p in d.glob('*.json'):
        if len(p.stem)!=32:continue
        value=json.loads(p.read_text())
        if 'metrics' in value:phrases.append(value)
    modes={}
    for name in ('original','translated'):
        selected=[e for e in telemetry if e.get('original_audio_enabled') is (name=='original')]
        modes[name]={'samples':len(selected),'preview_muted_values':sorted(set(e.get('source_video_muted') for e in selected),key=str),
            'translated_gain_values':sorted(set(e.get('translated_gain') for e in selected),key=str)}
    return {'scope':'Recorded real browser session evidence. Not a semantic correctness or production-readiness certificate.',
        'directory':str(d),'event_counts':dict(Counter(e['type'] for e in events)),
        'runtime':next((e for e in events if e['type']=='ready'),None),
        'metrics':metrics,'paired_pause_intervals_seconds':intervals,
        'unmatched_pause':pause is not None,'audio_mode_observations':modes,
        'commits':[e for e in events if e['type']=='committed'],
        'phrases':phrases,'played_ids':[e['phrase_id'] for e in played],
        'duplicate_playback_acknowledgements':len(played)-len({e['phrase_id'] for e in played}),
        'source_states':[e for e in events if e['type']=='source_state'],
        'errors':[e for e in events if e['type']=='error'],
        'summary':json.loads((d/'session-summary.json').read_text()) if (d/'session-summary.json').exists() else None,
        'acceptance_status':'requires_manual_semantic_control_and_continuity_review',
        'timing_caveat':'A/V offsets compare actual video frame callbacks with the generated-audio endpoint map, not physical output latency or phoneme-level lipsync.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory');parser.add_argument('--output')
    args=parser.parse_args();result=summarize(args.directory)
    if args.output:Path(args.output).write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps({k:v for k,v in result.items() if k not in ('phrases','commits')},indent=2,ensure_ascii=False))
