"""Finalize auditable evidence for the completed real Colab repair pass."""
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path('/content/video-dubbing-translator-main')
OUT = Path('/content/regression-evidence')
sys.path.insert(0, str(ROOT))
from engine.reports import render_html
import soundfile as sf
import torch

validation = json.loads((OUT/'full-validation.json').read_text())
run = Path(validation['run_root'])
report = json.loads((run/'quality_report.json').read_text())
calls = json.loads((run/'tts-requests.json').read_text())
translations = json.loads((run/'translation-requests.json').read_text())
assert validation['passed']
assert len(report['groups']) == len(validation['groups']) == 11
assert all(row['stop_reason'] == 'eos' for row in translations)
assert all(not row['quality']['hard_failures'] for row in validation['groups'])
assert abs(float(validation['final_probe']['format']['duration'])-72.133333) < .30
assert report['source_language'] == 'en' and report['target_language'] == 'tr'
trace = []
for row in validation['groups']:
    index = row['group']
    output = Path(row['synthesis']['output_path'])
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    matching = [call for call in calls if Path(call['output']).name.startswith(f'{index}.')]
    selected = [call for call in matching if call['sha256'] == digest]
    assert selected, f'No actual TTS request maps to group {index}'
    info = sf.info(output)
    assert info.samplerate == 48000 and info.channels == 1
    trace.append({
        'group_index': index, 'source_text': row['source'], 'selected_translation': row['selected_translation'],
        'selected_tts_request_ids': [call['request_id'] for call in selected],
        'selected_tts_payloads': selected, 'all_candidate_request_ids': [call['request_id'] for call in matching],
        'wav_sha256': digest, 'wav_seconds': info.duration, 'sample_rate': info.samplerate,
        'quality': row['quality'], 'reconstruction_input': str(run/'wav'/f'{index}.wav'),
    })
    target_report = report['groups'][index-1]
    target_report.update(source_text=row['source'], selected_translation=row['selected_translation'],
        tts_request_ids=[call['request_id'] for call in selected],
        speaker_reference_sha256=selected[0]['reference_sha256'],
        conditioning=selected[0]['conditioning'])

report['performance'].update({
    'gpu': torch.cuda.get_device_name(0), 'cuda': torch.version.cuda,
    'versions': {name:importlib.metadata.version(name) for name in ('torch','voxcpm','transformers','pyannote.audio','faster-whisper')},
    'source_duration_seconds':72.133333, 'final_duration_seconds':float(validation['final_probe']['format']['duration']),
    'e2e_warm_wall_seconds':validation['wall_seconds'], 'tts_request_count':len(calls),
    'reference_file_cache_hits':sum(call['reference_file_cache_hit'] for call in calls),
    'cache_scope':'Reference file path only. Native conditioning is rebuilt per call; no generated-audio cache.',
    'tts_generation_wall_seconds':sum(call['wall_seconds'] for call in calls),
    'tts_generated_audio_seconds':sum(call['audio_seconds'] for call in calls),
    'qa_retry_count':sum(row['quality']['retry_count'] for row in validation['groups']),
    'community1':'Real CUDA result reused from the immediately preceding acceptance test.',
    'transcript':'Existing real WhisperX aligned transcript for this source fixture.',
    'inference_timesteps':10, 'prosody_style':False,
})
(run/'quality_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
(run/'quality_report.html').write_text(render_html(report))
(run/'group-trace.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2))
first = translations[0]
group1 = {key:first.get(key) for key in ('prompt_tokens','generated_tokens','max_new_tokens','eos_seen','stop_reason','generation_wall_seconds')}
group1.update(decoded_translation=first['output'], target_speech_duration_seconds=validation['groups'][0]['synthesis']['preferred_duration'],
    output_word_count=len(first['output'].split()), output_character_count=len(''.join(first['output'].split())),
    semantic_sanity='Complete acknowledgment, busy period, writing, learning and teaching AI are present.')
(run/'group1-generation-diagnostics.json').write_text(json.dumps(group1, ensure_ascii=False, indent=2))

# Keep the real failed and selected regenerated QA candidates as short audio
# evidence. The full candidate archive remains only on the Colab disk.
qa_group = next(row for row in validation['groups'] if row['quality']['retry_count'])
index = qa_group['group']
for label, suffix in [('qa-initial', '.refined.wav'), ('qa-regenerated', '.qa_retry.wav')]:
    call = next(call for call in calls if Path(call['output']).name == f'{index}{suffix}')
    shutil.copyfile(call['preserved_candidate'], OUT/f'{label}.wav')

manifest = {'passed':True,'run_root':str(run),'final_video':validation['final_video'],
    'final_sha256':hashlib.sha256(Path(validation['final_video']).read_bytes()).hexdigest(),
    'quality_report_groups':11,'tts_requests':len(calls),'translation_requests':len(translations),
    'all_translation_stops':'eos','qa_retry_group':index,'group1':group1,
    'report_performance':report['performance']}
(OUT/'final-audit.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
with tarfile.open(OUT/'reports-and-traces.tar.gz','w:gz') as archive:
    for name in ('validation.json','quality_report.json','quality_report.html','quality_report.csv','tts-requests.json','translation-requests.json','group-trace.json','group1-generation-diagnostics.json','context.sqlite3'):
        archive.add(run/name, arcname='repaired/'+name)
    for name in ('runtime.json','short-comparison.json','translation-isolation.json','conditioning.json','mux-variants.json',
                 'isolation.json','acceptance-gate.json','independent-comparison.json','aligned-comparison.json','final-audit.json','unit-suite.log','verification.json'):
        archive.add(OUT/name, arcname='evidence/'+name)
print(json.dumps(manifest, ensure_ascii=False))
