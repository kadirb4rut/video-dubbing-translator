"""Re-evaluate existing real WAVs with auto LID; do not regenerate any audio."""
from pathlib import Path
import builtins
import json
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from engine.qa import word_error_rate

root=Path('/content/video-dubbing-translator-main/temp')
directory=sorted(root.glob('live-cache-gate-*'),key=lambda path:path.stat().st_mtime)[-1]
result=json.loads((directory/'result.json').read_text())
models=builtins._adaptive_live_models
for row in result['rows']:
    audio,rate=sf.read(row['wav'],dtype='float32')
    down=resample_poly(audio,16000,rate)
    pcm=(np.clip(down,-1,1)*32767).astype('<i2').tobytes()
    recovered=models.asr.transcribe(pcm)  # no source-language override
    row.update(recovered=recovered.text,language=recovered.language,
        language_probability=recovered.language_probability,
        wer=word_error_rate(row['target'],recovered.text),asr_language_mode='automatic')
result['passed']=all(row['wer']<=.15 and row['language']=='tr' for row in result['rows'])
result['correction']='Original cache-test recognizer forced the source language (en). Those language/ASR findings were invalid. Original WAVs and generation metrics are unchanged; only independent ASR was rerun.'
(directory/'result-auto-asr.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({'output':str(directory),**result},ensure_ascii=False))
