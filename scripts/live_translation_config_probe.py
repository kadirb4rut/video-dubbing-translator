"""Bounded development comparison on existing Colab-only Hy-MT2 weights.

Does not change product defaults or claim automatic semantic correctness.
"""
from pathlib import Path
import os
import sys
import json
import time
import torch

ROOT = Path('/content/video-dubbing-translator-main')
sys.path.insert(0, str(ROOT))
os.environ['HYMT2_SEED'] = '42'
from transformers import AutoTokenizer, AutoModelForCausalLM
from translation.hymt2_worker import Runtime
from translation.prompts import translation_prompt, sanitize_translation_output
from translation.base import TranslationContext
from dubbing.speech_length import estimate_spoken_length

original = Path((ROOT/'temp/live-translation-candidate-latest.txt').read_text())/'result.json'
data = json.loads(original.read_text())
cases = [x for x in data['cases'] if x['style'] == 'baseline' and x['case'] in
         ('underline', 'actual_browser_number', 'causes', 'controlled_names_units')]
started = time.perf_counter()
runtime = Runtime(Path('/content/hymt2-1.8b'), 'cuda')
runtime.tokenizer = AutoTokenizer.from_pretrained(runtime.model_path, local_files_only=True, trust_remote_code=True)
runtime.model = AutoModelForCausalLM.from_pretrained(runtime.model_path, local_files_only=True,
    trust_remote_code=True, dtype=torch.bfloat16, low_cpu_mem_usage=True).to('cuda').eval()
runtime.device = torch.device('cuda')
load_seconds = time.perf_counter()-started
rows = []
for variant in ('bf16_official_simple', 'bf16_official_context', 'bf16_existing_greedy', 'fp16_official_simple'):
    if variant.startswith('fp16'):
        runtime.model.to(torch.float16)
    for case in cases:
        source, duration, previous = case['source'], case['duration'], case['previous_source_context']
        if 'existing' in variant:
            context = TranslationContext(previous_text=previous, preferred_duration_seconds=duration,
                target_spoken_units=max(3, duration*4.8), protected_tokens=tuple(case['protected']))
            prompt = translation_prompt(source, 'en', 'tr', context=context)
        elif 'context' in variant and previous:
            prompt = ('[Background Information]\n'+previous+
                '\nPlease translate the following text into Turkish, taking the provided background information '
                'into consideration.\n[Source Text]\n'+source)
        else:
            prompt = ('Translate the following text into Turkish. Note that you should only output the '
                      'translated result without any additional explanation:\n'+source)
        if 'greedy' not in variant:
            text, stats = runtime.generate(prompt)
        else:
            inputs = runtime.tokenizer.apply_chat_template([{'role':'user','content':prompt}],
                add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors='pt')
            inputs = {k:v.to('cuda') for k,v in inputs.items()}
            started = time.perf_counter()
            with torch.inference_mode():
                outputs = runtime.model.generate(**inputs, do_sample=False, repetition_penalty=1.05,
                    max_new_tokens=runtime._max_new_tokens(prompt))
            tokens = outputs[0][inputs['input_ids'].shape[-1]:]
            text = runtime.tokenizer.decode(tokens, skip_special_tokens=True)
            stats = {'generated_tokens':len(tokens), 'eos_seen':bool((tokens==runtime.tokenizer.eos_token_id).any()),
                     'generation_wall_seconds':time.perf_counter()-started, 'decode':'greedy'}
        text = sanitize_translation_output(text)
        length = estimate_spoken_length(text, 'tr')
        row = {'variant':variant, 'case':case['case'], 'source':source, 'target':text,
            'dtype':str(next(runtime.model.parameters()).dtype), 'generation':stats,
            'spoken_units':length.spoken_units, 'word_count':length.words, 'duration':duration,
            'semantic_review':'pending; execution/length do not establish meaning'}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
output = ROOT/'temp'/f'live-translation-config-probe-{int(time.time())}'
output.mkdir()
(output/'result.json').write_text(json.dumps({'model':'Hy-MT2-1.8B', 'load_seconds':load_seconds,
    'cases':rows, 'previous_probe':str(original)}, ensure_ascii=False, indent=2))
(ROOT/'temp/live-translation-config-latest.txt').write_text(str(output))
