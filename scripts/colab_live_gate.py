"""Development Gate A: actual real-time fixture arrival and local real models.

This is not browser/YouTube or actual playback evidence. It deliberately makes
those missing gates explicit, including when real model inference fails.
"""
from __future__ import annotations

import asyncio
import builtins
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import importlib.metadata
import inspect
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import traceback
import wave

ROOT = Path('/content/video-dubbing-translator-main')
sys.path.insert(0, str(ROOT))
os.environ.update(HYMT2_MODELS_DIR='/content', HYMT2_PYTHON=sys.executable,
    DIARIZATION_PYTHON=sys.executable, DIARIZATION_MODEL_DIR=str(ROOT/'third_party/diarization/community-1'),
    LIVE_SPEAKER_PYTHON=sys.executable, LIVE_SPEAKER_MODEL=str(ROOT/'third_party/diarization/community-1'),
    LIVE_VOXCPM_PATH='/content/voxcpm2', HF_HUB_OFFLINE='1')
# CTranslate2 uses CUDA-12 cuBLAS even though this development image's Torch
# uses CUDA 13. These are explicit Colab-only runtime dependencies.
import ctypes
import site
for library in ('libcublasLt.so.12', 'libcublas.so.12'):
    for package_root in site.getsitepackages():
        path = Path(package_root) / 'nvidia/cublas/lib' / library
        if path.is_file():
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            break


def percentile_summary(values):
    values = sorted(value for value in values
                    if isinstance(value,(int,float)) and math.isfinite(value))
    if not values:
        return {'n':0,'p50':None,'p95':None,'max':None}
    def quantile(fraction):
        position=(len(values)-1)*fraction
        lower=int(position);upper=min(lower+1,len(values)-1)
        return round(values[lower]+(values[upper]-values[lower])*(position-lower),3)
    return {'n':len(values),'p50':quantile(.5),'p95':quantile(.95),
            'max':round(values[-1],3)}


async def main():
    if os.environ.pop('LIVE_REFRESH_CODE', '') == '1':
        import importlib as _importlib
        _importlib.invalidate_caches()
        for module in ('live_dubbing.adaptive', 'live_dubbing.universal', 'live_dubbing.speech',
                       'live_dubbing.inference_scheduler',
                       'live_dubbing.speakers', 'live_dubbing.qwen_translation_worker',
                       'live_dubbing.qwen_translation', 'live_dubbing.simultaneous',
                       'live_dubbing.read_wait_write', 'live_dubbing.scheduling',
                       'translation.base', 'translation.prompts', 'translation.hymt2',
                       'translation.backends', 'live_dubbing.models', 'live_dubbing.session'):
            if module in sys.modules:
                _importlib.reload(sys.modules[module])
        if hasattr(builtins, '_adaptive_live_models'):
            builtins._adaptive_live_models.__class__ = sys.modules['live_dubbing.models'].LocalModels
            if not hasattr(builtins._adaptive_live_models, '_prompt_caches'):
                builtins._adaptive_live_models._prompt_caches = {}
    # A failed preparation may have imported older module definitions. Reload
    # source only before constructing the first persistent real model instance.
    rebuild = os.environ.pop('LIVE_REBUILD_ADAPTERS', '') == '1'
    saved_vox = saved_asr = None
    if rebuild and hasattr(builtins, '_adaptive_live_models'):
        old = builtins._adaptive_live_models
        saved_vox = getattr(old, 'voxcpm', None)
        old_asr = getattr(old, 'asr', None)
        saved_asr = getattr(old_asr, 'model', None) if old_asr is not None else None
        old.voxcpm = old.asr = None
        old.close()
        del builtins._adaptive_live_models
    existing_models = getattr(builtins, '_adaptive_live_models', None)
    if existing_models is not None and any(
            getattr(existing_models, name, None) is None
            for name in ('asr', 'voxcpm', 'memory')):
        # LiveSession closes model adapters after its end-of-stream sentinel.
        # A later bounded diagnostic must rebuild from local caches instead of
        # reusing the empty shell left in builtins. HF_HUB_OFFLINE below keeps
        # this path from downloading artifacts.
        del builtins._adaptive_live_models
    if not hasattr(builtins, '_adaptive_live_models'):
        for name in list(sys.modules):
            if name == 'live_dubbing' or name.startswith('live_dubbing.') or name in ('translation.hymt2', 'translation.backends'):
                del sys.modules[name]
    from live_dubbing.models import LocalModels
    from live_dubbing.session import LiveSession
    from live_dubbing.protocol import AudioPacket
    import torch
    mode = os.environ.get('LIVE_TEST_MODE', 'balanced')
    label = os.environ.get('LIVE_TEST_LABEL', 'a')
    audio_path = os.environ.get('LIVE_TEST_AUDIO_PATH')
    output = ROOT / 'temp' / f'live-gate-recorded-{label}-{mode}-{int(time.time())}'
    output.mkdir(parents=True, exist_ok=False)
    events = []
    phrase_metadata = {}
    playback_items = []
    playback_tasks = []
    playback_tail = None
    session = None
    capture_started = None

    def queue_playout(event):
        nonlocal playback_tail
        phrase_id = event.get('phrase_id')
        phrase = phrase_metadata.get(phrase_id)
        if phrase is None or session is None or capture_started is None:
            return
        metrics = event.get('metrics') or {}
        source_start, source_end = phrase.get('source_start'), phrase.get('source_end')
        if not all(isinstance(value, (int, float)) for value in (source_start, source_end)):
            return
        source_duration = max(0.0, source_end-source_start)
        speech_seconds = (metrics.get('generated_audio_seconds') or
                          (metrics.get('audio_seconds') if event.get('presentation_mode') == 'speech' else 0.0) or 0.0)
        if event.get('presentation_mode') == 'caption_only':
            presentation_seconds = source_duration
        else:
            presentation_seconds = max(float(metrics.get('presentation_audio_seconds') or 0.0),
                                       source_duration/1.5)
        ready_at = time.perf_counter()
        previous_end = (playback_items[-1]['source_end'] if playback_items else 0.0)
        gap_wait = max(0.0, source_start-previous_end)/1.5
        mode_minimum_delay = {'quality':4.0,'balanced':3.0,'low_latency':2.0}[mode]
        not_before = capture_started + source_start + mode_minimum_delay
        start_at = max(ready_at, playback_tail or capture_started, not_before)
        if playback_tail is not None:
            start_at = max(start_at, playback_tail + gap_wait)
        end_at = start_at + presentation_seconds
        item = {'phrase_id':phrase_id,'source_start':source_start,'source_end':source_end,
                'ready_at':ready_at,'start_at':start_at,'end_at':end_at,
                'speech_seconds':float(speech_seconds),'presentation_seconds':presentation_seconds,
                'mode':event.get('presentation_mode','speech')}
        playback_items.append(item)
        playback_tail = end_at

        async def finish_playout():
            await asyncio.sleep(max(0.0, end_at-time.perf_counter()))
            now = time.perf_counter()
            pending_speech = pending_presentation = 0.0
            for queued in playback_items:
                if queued['phrase_id'] == phrase_id:
                    continue
                elapsed = max(0.0, now-queued['start_at'])
                pending_speech += max(0.0, queued['speech_seconds']-elapsed)
                pending_presentation += max(0.0, queued['presentation_seconds']-elapsed)
            stages = metrics.get('stage_timestamps_monotonic') or {}
            speech_end = stages.get('speech_end_source_monotonic_at')
            await session.acknowledge({'phrase_id':phrase_id,
                'video_start':source_start,'video_end':source_end,
                'generated_audio_seconds':item['speech_seconds'],
                'presentation_audio_seconds':presentation_seconds,
                'playback_queue_seconds':max(0.0,start_at-ready_at),
                'capture_to_playback_seconds':max(0.0,start_at-capture_started),
                'speech_end_to_playback_seconds':(
                    max(0.0,start_at-speech_end) if isinstance(speech_end,(int,float)) else None),
                'pending_speech_audio_seconds':pending_speech,
                'pending_presentation_audio_seconds':pending_presentation})
        playback_tasks.append(asyncio.create_task(finish_playout()))

    async def emit(event):
        events.append({key:value for key,value in event.items() if key != 'pcm'})
        if event.get('type') == 'phrase':
            phrase_metadata[event.get('phrase_id')] = event
        if event.get('type') == 'phrase_ready':
            queue_playout(event)
        if event.get('type') == 'source_chunk_admission':
            print(json.dumps({key:event.get(key) for key in ('type','source_revision','admitted',
                'reason','urgent','source_uncertainty_seconds','requests_per_source_minute')
                if event.get(key) is not None}), flush=True)
        elif event.get('type') in ('error','committed','phrase_ready','played'):
            metrics = event.get('metrics') or {}
            print(json.dumps({'type':event.get('type'),'phrase_id':event.get('phrase_id'),
                'source_start':event.get('source_start'), 'source_end':event.get('source_end'),
                'presentation_mode':event.get('presentation_mode'),
                'presentation_audio_seconds':metrics.get('presentation_audio_seconds'),
                'generated_audio_seconds':metrics.get('generated_audio_seconds'),
                'error_type':event.get('error_type')}, default=str), flush=True)
        elif event.get('type') == 'warning':
            print(json.dumps({key:event.get(key) for key in
                ('type','code','source_start','source_end','reason') if event.get(key) is not None},
                default=str), flush=True)
    started = time.perf_counter()
    session = None
    try:
        if not hasattr(builtins, '_adaptive_live_models'):
            builtins._adaptive_live_models = await asyncio.to_thread(LocalModels,
                ROOT/'temp/live-model-session', device='cuda', source_language='en',
                target_language='tr', whisper_model=os.environ.get('LIVE_WHISPER_MODEL', '/content/faster-whisper-small'),
                voxcpm_model=saved_vox, asr_model=saved_asr)
        models = builtins._adaptive_live_models
        if not hasattr(models, 'inference_scheduler'):
            from live_dubbing.inference_scheduler import PriorityInferenceScheduler
            models.inference_scheduler = PriorityInferenceScheduler(enabled=True)
        models.inference_scheduler.enabled = os.environ.get(
            'LIVE_TEST_GPU_SCHEDULER', '1') == '1'
        if not hasattr(models,'_recorded_gate_original_close'):
            models._recorded_gate_original_close = models.close
        # Gate A/B/C use the same already-warmed model instances. EOS still
        # drains the session; release the GPU only after the final C run.
        models.close = lambda: None
        # Start every latency comparison with the same empty speaker/context
        # state, while retaining only warm model weights and safe source caches.
        from live_dubbing.speakers import SpeakerTracker
        from engine.context import ContextMemory
        models.memory.close()
        models.memory = ContextMemory(output/'context.sqlite3')
        models.speakers = SpeakerTracker(output/'references')
        models.directory = output
        session_kwargs = {}
        if 'audio_backpressure' in inspect.signature(LiveSession).parameters:
            session_kwargs['audio_backpressure'] = os.environ.get(
                'LIVE_TEST_AUDIO_BACKPRESSURE','1') == '1'
        session = LiveSession(models, emit, output, mode=mode, **session_kwargs)
        # Native models are warm between controlled attempts. Each attempt has
        # fresh VAD/hypothesis/queue state and a distinct trace directory.
        from live_dubbing.speech import SileroStream
        models.vad = SileroStream()
        models.closed = False
        seconds_value = os.environ.get('LIVE_TEST_SECONDS')
        fixture_start = float(os.environ.get('LIVE_TEST_START_SECONDS', '0'))
        if audio_path:
            with wave.open(audio_path, 'rb') as audio:
                if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate()) != (1,2,16000):
                    raise ValueError('Recorded gate requires mono 16 kHz PCM16 source audio')
                start_frame = int(fixture_start*audio.getframerate())
                audio.setpos(min(start_frame,audio.getnframes()))
                available = (audio.getnframes()-audio.tell())/audio.getframerate()
                seconds = min(available,float(seconds_value)) if seconds_value else available
                pcm = audio.readframes(int(seconds*audio.getframerate()))
        else:
            seconds = float(seconds_value or '7')
            pcm = subprocess.check_output(['ffmpeg','-v','error','-ss',str(fixture_start),
                '-t',str(seconds),'-i',str(ROOT/'test_multispeaker.mp4'),
                '-f','s16le','-ac','1','-ar','16000','pipe:1'])
        await session.start()
        capture_started = time.perf_counter()
        session.set_capture_origin(capture_started)
        chunk_size = 640
        arrival = []
        for sequence, offset in enumerate(range(0, len(pcm), chunk_size)):
            media_time = offset/32000
            # Feed only audio that has arrived by this real wall-clock instant.
            target = capture_started + media_time + len(pcm[offset:offset+chunk_size])/32000
            await asyncio.sleep(max(0, target-time.perf_counter()))
            arrival.append({'media_seconds':media_time,'wall_seconds':time.perf_counter()-capture_started})
            # Mirror the browser client: a bounded-drain warning ends capture
            # before any later source packets are accepted. Continue below
            # with finish() so confirmed work drains and an uncertain tail is
            # reported explicitly instead of overflowing the PCM buffer.
            if session.failure or session.overload_drain_requested:
                break
            await session.push(AudioPacket(sequence, media_time, pcm[offset:offset+chunk_size]).encode())
        await session.finish()
        deadline = time.perf_counter()+max(180.0, len(pcm)/32000*3)
        while not session.failure and not all(task.done() for task in session.tasks):
            if time.perf_counter()>deadline:
                raise TimeoutError('Bounded Gate A processing deadline exceeded')
            await asyncio.sleep(.1)
        if playback_tasks:
            await asyncio.wait_for(asyncio.gather(*playback_tasks),
                timeout=max(120.0,len(pcm)/32000*2))
        ready = [event for event in events if event['type']=='phrase_ready']
        committed = [event for event in events if event['type']=='committed']
        played_events = [event for event in events if event['type']=='played']
        ready_ids = [event.get('phrase_id') for event in ready]
        played_ids = [event.get('phrase_id') for event in played_events]
        committed_intervals = [(event.get('source_start'), event.get('source_end'))
                               for event in committed]
        committed_duration = sum(max(0.0, end-start) for start,end in committed_intervals
                                 if isinstance(start,(int,float)) and isinstance(end,(int,float)))
        intervals = sorted((start,end) for start,end in committed_intervals
            if isinstance(start,(int,float)) and isinstance(end,(int,float)) and end > start)
        unique_coverage = overlap_seconds = 0.0
        frontier = 0.0
        for start,end in intervals:
            unique_coverage += max(0.0,end-max(start,frontier))
            overlap_seconds += max(0.0,frontier-start)
            frontier = max(frontier,end)
        last_committed_end = max((end for _,end in committed_intervals
                                  if isinstance(end,(int,float))), default=None)
        final_tail = session.untranslated_final_tail
        final_tail_summary = ({key:final_tail.get(key) for key in
            ('source_start','source_end','reason') if final_tail.get(key) is not None}
            if isinstance(final_tail,dict) else None)
        capture_seconds = len(pcm)/32000
        fed_source_seconds = max(0.0, session.clock.end or 0.0)
        from live_dubbing.adaptive import source_text
        final_tail_text = final_tail.get('source_text','') if isinstance(final_tail,dict) else ''
        final_hypothesis = source_text(session.agreement.hypothesis)
        final_confirmed = source_text(session.agreement.confirmed)
        terminal_punctuation = re.compile(r'[.!?。！？…]$')
        eos_debug = {
            'tail_word_count': len(final_tail_text.split()),
            'final_hypothesis_word_count': len(final_hypothesis.split()),
            'final_confirmed_word_count': len(final_confirmed.split()),
            'final_hypothesis_ends_sentence': bool(terminal_punctuation.search(final_hypothesis.strip())),
            'final_confirmed_ends_sentence': bool(terminal_punctuation.search(final_confirmed.strip())),
            'agreement_observation_count': session.agreement.observations,
            'last_speech_end_seconds': session.last_speech,
            'trailing_nonspeech_seconds': max(0.0, fed_source_seconds-session.last_speech),
            'final_hypothesis_last_word_end_seconds': (
                session.agreement.hypothesis[-1].end if session.agreement.hypothesis else None),
            'final_confirmed_last_word_end_seconds': (
                session.agreement.confirmed[-1].end if session.agreement.confirmed else None),
        }
        result = {'gate':'A-small-real-model-smoke',
            'passed':bool(ready) and not session.failure and not session.overload_drain_requested,
            'natural_eos_passed':bool(ready) and not session.failure and
                not session.overload_drain_requested and final_tail_summary is None,
            'mode':mode, 'label':label, 'input_audio_path':audio_path,
            'fixture_start_seconds':fixture_start,
            'fixture_seconds':capture_seconds, 'fed_packets':session.clock.next_sequence,
            'fed_source_seconds':fed_source_seconds,
            'unfed_source_seconds':max(0.0, capture_seconds-fed_source_seconds),
            'stopped_for_overload':session.overload_drain_requested,
            'committed_source_interval_seconds':committed_duration,
            'unique_committed_source_coverage_seconds':unique_coverage,
            'unique_committed_coverage_ratio_of_captured_clock':unique_coverage/max(capture_seconds,1.0),
            'overlapping_committed_source_seconds':overlap_seconds,
            'source_interval_ordered':all(
                committed_intervals[index][0] <= committed_intervals[index+1][0]
                for index in range(len(committed_intervals)-1)
                if all(isinstance(value,(int,float)) for value in
                       (*committed_intervals[index],*committed_intervals[index+1]))),
            'last_committed_source_end_seconds':last_committed_end,
            'untranslated_final_tail':final_tail_summary,
            'eos_debug':eos_debug,
            'arrival':arrival,'phrases':len(ready),'errors':[e for e in events if e['type']=='error'],
            'wall_seconds':time.perf_counter()-started,'model_constructor_seconds':models.load_seconds,
            'gpu':torch.cuda.get_device_name(0),'cuda':torch.version.cuda,'python':sys.version,
            'versions':{name:importlib.metadata.version(name) for name in
                ('torch','faster-whisper','voxcpm','transformers','pyannote.audio')},
            'simulated_playout':True,'actual_playback':False,'youtube':False,
            'gpu_inference_scheduler_enabled':models.inference_scheduler.enabled,
            'read_wait_write_enabled':session.read_wait_write_enabled,
            'read_wait_write_queries':session.read_wait_write_queries,
            'read_wait_write_rejections':session.read_wait_write_rejections,
            'played_phrase_count':session.played_count,
            'played_speech_phrase_count':session.played_speech_count,
            'played_caption_phrase_count':session.played_caption_count,
            'ready_phrase_count':len(ready),'played_phrase_count':len(played_events),
            'ready_not_played_phrase_ids':[identifier for identifier in ready_ids
                if identifier not in played_ids],
            'duplicate_playback_acknowledgements':len(played_ids)-len(set(played_ids)),
            'boundary_selector_calls':getattr(session,'boundary_selector_calls',None),
            'boundary_guard_calls':getattr(session,'boundary_guard_calls',None),
            'qwen_boundary_call_count':(getattr(session,'boundary_selector_calls',0)+
                                        getattr(session,'boundary_guard_calls',0)),
            'stale_boundary_result_count':sum(1 for event in events
                if event.get('type')=='source_chunk_result_discarded'),
            'boundary_admission_counts':{
                'admitted':sum(bool(event.get('admitted')) for event in events
                               if event.get('type')=='source_chunk_admission'),
                'deferred_reasons':{reason:sum(1 for event in events
                    if event.get('type')=='source_chunk_admission' and not event.get('admitted')
                    and event.get('reason')==reason)
                    for reason in sorted({event.get('reason') for event in events
                        if event.get('type')=='source_chunk_admission' and not event.get('admitted')})}},
            'audio_duration_totals':{
                'generated_speech_seconds':sum((event.get('generated_audio_seconds') or 0.0)
                    for event in events if event.get('type')=='played'),
                'committed_source_vad_speech_seconds':sum((event.get('source_vad_speech_seconds') or 0.0)
                    for event in committed),
                'presentation_seconds':sum((event.get('presentation_audio_seconds') or 0.0)
                    for event in events if event.get('type')=='played'),
                'speech_to_committed_source_ratio':(
                    sum((event.get('generated_audio_seconds') or 0.0)
                        for event in events if event.get('type')=='played')/max(committed_duration,1.0)),
                'generated_audio_per_source_vad_speech_second':(
                    sum((event.get('generated_audio_seconds') or 0.0)
                        for event in events if event.get('type')=='played')/
                    max(sum((event.get('source_vad_speech_seconds') or 0.0)
                            for event in committed),.001)),
            },
            'source_vad_coverage':{
                'captured_speech_seconds':round(session.vad_speech_seconds_total,3),
                'committed_speech_seconds':round(session.committed_vad_speech_seconds,3),
                'unresolved_speech_seconds':round(max(0.0,session.vad_speech_seconds_total-
                    session.committed_vad_speech_seconds),3),
                'committed_timeline_seconds':round(session.committed_source_timeline_seconds,3),
            },
            'latency_seconds':{
                'speech_end_to_simulated_playback':percentile_summary(
                    event.get('speech_end_to_playback_seconds') for event in events
                    if event.get('type')=='played' and isinstance(
                        event.get('speech_end_to_playback_seconds'),(int,float))),
                'simulated_playback_queue_wait':percentile_summary(
                    event.get('playback_queue_seconds') for event in events
                    if event.get('type')=='played' and isinstance(
                        event.get('playback_queue_seconds'),(int,float))),
                'ready_speech_queue_seconds':percentile_summary(
                    (event.get('metrics') or {}).get('pending_speech_audio_seconds')
                    for event in events if event.get('type')=='phrase_ready' and isinstance(
                        (event.get('metrics') or {}).get('pending_speech_audio_seconds'),(int,float))),
                'inference_scheduler_wait_seconds':percentile_summary(
                    (event.get('metrics') or {}).get('inference_scheduler_total_wait_seconds')
                    for event in events if event.get('type')=='phrase_ready' and isinstance(
                        (event.get('metrics') or {}).get('inference_scheduler_total_wait_seconds'),
                        (int,float))),
                'qwen_selector_worker_seconds':percentile_summary(
                    (event.get('metrics') or {}).get('wall_seconds') for event in events
                    if event.get('type')=='source_chunk_proposal' and isinstance(
                        (event.get('metrics') or {}).get('wall_seconds'),(int,float))),
                'qwen_guard_worker_seconds':percentile_summary(
                    (event.get('metrics') or {}).get('wall_seconds') for event in events
                    if event.get('type')=='source_chunk_guard' and isinstance(
                        (event.get('metrics') or {}).get('wall_seconds'),(int,float))),
            },
            'qwen_admission_is_not_semantic_certification':True,
            'semantic_human_review':'pending; ASR recovery alone is not semantic correctness'}
    except Exception as exc:
        result = {'gate':'A-small-real-model-smoke','passed':False,
                  'error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc(),
                  'simulated_playout':True,'actual_playback':False,'youtube':False}
    finally:
        if session is not None:
            # Reuse warm models during this bounded development session. Stop
            # tasks without unloading until the final explicit cleanup script.
            session.closed = True
            for task in session.tasks:
                task.cancel()
            await asyncio.gather(*session.tasks, return_exceptions=True)
        if os.environ.get('LIVE_TEST_RELEASE_MODELS','0') == '1' and hasattr(
                builtins,'_adaptive_live_models'):
            models = builtins._adaptive_live_models
            original_close = getattr(models,'_recorded_gate_original_close',None)
            if original_close is not None:
                models.close = original_close
                models.close()
            del builtins._adaptive_live_models
    with (output/'events.jsonl').open('w') as stream:
        for event in events:
            stream.write(json.dumps(event,ensure_ascii=False,default=str)+'\n')
    (output/'gate-result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    Path('/content/live-gate-latest.txt').write_text(str(output))
    print(json.dumps({'output':str(output),'result':{key:value for key,value in result.items() if key!='arrival'}},ensure_ascii=False),flush=True)


with ThreadPoolExecutor(max_workers=1) as executor:
    executor.submit(lambda: asyncio.run(main())).result(timeout=240)
