#!/usr/bin/env python3
"""Create transcript-free per-phrase latency and queue reports from live events."""
from __future__ import annotations

import argparse
from collections import defaultdict
from html import escape
import json
import math
from pathlib import Path


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _percentile(values, quantile):
    values = sorted(value for value in values if _number(value) is not None)
    if not values:
        return None
    index = (len(values)-1)*quantile
    lower = int(index)
    upper = min(len(values)-1, lower+1)
    return values[lower] + (values[upper]-values[lower])*(index-lower)


def _stage_values(phrase):
    metrics = phrase.get('metrics') or {}
    draft = ((metrics.get('translation_commitment') or {}).get('draft_context') or {})
    decode = phrase.get('decode_metrics') or {}
    played = phrase.get('played') or {}
    values = {
        'speech_end_to_asr_seconds': metrics.get('speech_end_to_asr_seconds'),
        'speech_end_to_first_audio_seconds': (metrics.get('speech_end_to_usable_audio_seconds')
            if phrase.get('presentation_mode') == 'speech' else None),
        'speech_end_to_usable_output_seconds': (metrics.get('speech_end_to_usable_audio_seconds')
            if phrase.get('presentation_mode') in ('speech', 'caption_only') else None),
        'speech_end_to_playback_seconds': played.get('speech_end_to_playback_seconds'),
        'capture_to_playback_seconds': played.get('capture_to_playback_seconds'),
        'asr_inference_seconds': metrics.get('source_asr_inference_seconds'),
        'asr_lock_wait_seconds': metrics.get('source_asr_lock_wait_seconds'),
        'asr_worker_queue_wait_seconds': metrics.get('source_worker_pool_wait_seconds'),
        'asr_rpc_roundtrip_seconds': metrics.get('source_worker_rpc_roundtrip_seconds'),
        'decode_coalescing_wait_seconds': decode.get('decode_coalescing_wait_seconds'),
        'local_agreement_seconds': decode.get('local_agreement_seconds'),
        'boundary_model_seconds': decode.get('boundary_model_seconds'),
        'endpoint_decision_seconds': decode.get('endpoint_decision_seconds'),
        'phrase_queue_wait_seconds': metrics.get('phrase_queue_wait_seconds'),
        'render_executor_queue_wait_seconds': metrics.get('render_executor_queue_wait_seconds'),
        'model_render_roundtrip_seconds': metrics.get('render_roundtrip_seconds'),
        'model_worker_queue_wait_seconds': metrics.get('model_worker_pool_wait_seconds'),
        'speaker_selection_seconds': metrics.get('speaker_seconds'),
        'context_retrieval_seconds': metrics.get('context_retrieval_seconds', draft.get('context_retrieval_seconds')),
        'render_to_translation_start_seconds': metrics.get('translation_pre_inference_wait_seconds'),
        'asr_to_translation_start_seconds': metrics.get('asr_to_translation_start_seconds'),
        'translation_inference_seconds': draft.get('translator_inference_seconds',
            draft.get('translation_seconds', metrics.get('translation_seconds'))),
        'translation_validation_seconds': draft.get('translation_validation_seconds'),
        'tts_first_chunk_seconds': metrics.get('tts_first_chunk_seconds'),
        'tts_generation_after_first_chunk_seconds': metrics.get('tts_generation_after_first_chunk_seconds'),
        'tts_total_seconds': metrics.get('tts_seconds', metrics.get('total_tts_seconds')),
        'qa_seconds': metrics.get('qa_seconds', metrics.get('total_qa_seconds')),
        'source_interval_seconds': metrics.get('source_seconds'),
        'generated_audio_seconds': metrics.get('audio_seconds'),
        'presentation_audio_seconds': metrics.get('presentation_audio_seconds'),
        'tts_to_source_duration_ratio': metrics.get('tts_to_source_duration_ratio'),
        'presentation_to_source_duration_ratio': metrics.get('presentation_to_source_duration_ratio'),
        'tts_retry_count': metrics.get('retry_count'),
        'qa_asr_lock_wait_seconds': metrics.get('qa_asr_lock_wait_seconds'),
        'qa_asr_inference_seconds': metrics.get('qa_asr_inference_seconds'),
        'playback_queue_seconds': played.get('playback_queue_seconds'),
        'audio_video_offset_seconds': played.get('audio_video_offset_seconds', played.get('observed_av_offset_seconds')),
        'live_edge_to_displayed_video_seconds': played.get('live_edge_to_displayed_video_seconds'),
        'video_playback_rate': played.get('video_playback_rate'),
        'requested_video_playback_rate': played.get('requested_video_playback_rate'),
        'video_playback_rate_clamped': (int(played['video_playback_rate_clamped'])
            if isinstance(played.get('video_playback_rate_clamped'), bool) else None),
    }
    return {key: value for key, value in values.items() if _number(value) is not None}


def _candidate_values(metrics):
    values = defaultdict(list)
    candidates = metrics.get('tts_candidate_timings') or []
    for candidate in candidates:
        for source, target in (
            ('conditioning_cache_seconds', 'conditioning_cache_seconds'),
            ('tts_first_chunk_seconds', 'tts_candidate_ttfa_seconds'),
            ('tts_rtf', 'tts_candidate_rtf'),
            ('qa_seconds', 'qa_candidate_seconds'),
            ('speaker_similarity_seconds', 'speaker_similarity_seconds'),
        ):
            value = _number(candidate.get(source))
            if value is not None:
                values[target].append(value)
    return dict(values)


def _duration(start, end):
    start, end = _number(start), _number(end)
    if start is None or end is None or end < start:
        return None
    return end-start


def _critical_path(metrics, played, clock_mapping):
    stamps = metrics.get('stage_timestamps_monotonic') or {}
    stages = []

    def add(name, start_key, end_key, start=None, end=None):
        start = stamps.get(start_key) if start is None else start
        end = stamps.get(end_key) if end is None else end
        seconds = _duration(start, end)
        if seconds is not None:
            stages.append({'stage':name, 'started_at':start, 'completed_at':end,
                           'duration_seconds':seconds})

    add('speech_end_to_asr_dispatch', 'speech_end_source_monotonic_at', 'asr_dispatched_at')
    add('asr_dispatch_to_worker_start', 'asr_dispatched_at', 'asr_worker_started_at')
    add('asr_worker_execution', 'asr_worker_started_at', 'asr_worker_completed_at')
    add('asr_worker_to_parent_return', 'asr_worker_completed_at', 'asr_completed_at')
    add('asr_completed_to_stable_prefix', 'asr_completed_at', 'stable_prefix_confirmed_at')
    add('stable_prefix_to_endpoint_decision', 'stable_prefix_confirmed_at', 'endpoint_decision_completed_at')
    add('endpoint_to_commit', 'endpoint_decision_completed_at', 'source_committed_at')
    add('phrase_queue_wait', 'phrase_queue_enqueued_at', 'render_dispatched_at')
    add('render_executor_queue_wait', 'render_dispatched_at', 'render_executor_started_at')
    add('executor_to_model_worker_start', 'render_executor_started_at', 'model_worker_started_at')
    add('model_worker_execution', 'model_worker_started_at', 'model_worker_completed_at')
    add('model_worker_to_parent_return', 'model_worker_completed_at', 'render_executor_completed_at')
    add('speaker_analysis', 'speaker_analysis_started_at', 'speaker_analysis_completed_at')
    add('context_retrieval', 'context_retrieval_started_at', 'context_retrieval_completed_at')
    add('translation_pre_inference_wait', 'render_dispatched_at', 'translation_started_at')
    add('translation_inference', 'translation_started_at', 'translation_completed_at')
    add('translation_validation', 'translation_validation_started_at', 'translation_validation_completed_at')

    for attempt, candidate in enumerate(metrics.get('tts_candidate_timings') or []):
        prefix = f'tts_attempt_{attempt}'
        for name, start_key, end_key in (
            ('conditioning_cache', 'conditioning_started_at', 'conditioning_completed_at'),
            ('ttfa', 'tts_started_at', 'tts_first_chunk_at'),
            ('tts_generation_after_first_chunk', 'tts_first_chunk_at', 'tts_completed_at'),
            ('qa', 'qa_started_at', 'qa_completed_at'),
            ('qa_signal', 'qa_signal_started_at', 'qa_signal_completed_at'),
            ('presentation_timing', 'presentation_timing_started_at', 'presentation_timing_completed_at'),
            ('qa_asr_lock_wait', 'qa_asr_lock_requested_at', 'qa_asr_lock_acquired_at'),
            ('qa_asr_inference', 'qa_asr_started_at', 'qa_asr_completed_at'),
            ('qa_text_evaluation', 'qa_text_evaluation_started_at', 'qa_text_evaluation_completed_at'),
            ('speaker_similarity', 'speaker_similarity_started_at', 'speaker_similarity_completed_at'),
        ):
            add(f'{prefix}_{name}', start_key, end_key,
                candidate.get(start_key), candidate.get(end_key))

    if clock_mapping:
        for label in ('playback_admission_to_audio_output', 'actual_audio_presentation'):
            if label == 'playback_admission_to_audio_output':
                start = clock_mapping.get('playback_admission_server_elapsed_seconds')
                end = clock_mapping.get('audio_output_start_server_elapsed_seconds')
            elif label == 'actual_audio_presentation':
                start = clock_mapping.get('audio_output_start_server_elapsed_seconds')
                end = clock_mapping.get('audio_output_end_server_elapsed_seconds')
            else:
                start = clock_mapping.get('audio_output_start_server_elapsed_seconds')
                end = clock_mapping.get('audio_output_end_server_elapsed_seconds')
            seconds = _duration(start, end)
            if seconds is not None:
                stages.append({'stage':label, 'started_at_server_elapsed':start,
                    'completed_at_server_elapsed':end, 'duration_seconds':seconds})
    return stages


def _slope(points, key):
    values = [(_number(point.get('server_elapsed')), _number(point.get(key))) for point in points]
    values = [(x,y) for x,y in values if x is not None and y is not None]
    if len(values) < 2:
        return None
    mean_x = sum(x for x,_ in values)/len(values)
    mean_y = sum(y for _,y in values)/len(values)
    variance = sum((x-mean_x)**2 for x,_ in values)
    return None if variance == 0 else sum((x-mean_x)*(y-mean_y) for x,y in values)/variance


def summarize(events):
    phrases = {}
    timeline = []
    current_decode = None
    clock_sync = []
    server_monotonic_started_at = None
    ready_metadata = {}
    capture_seconds = None
    analyzed_until = 0.0
    latest_asr_source_end = None
    latest_committed_source_end = None
    latest_ready_source_end = None
    phrase_source_ends = {}
    played_order = []
    warnings = []
    capture_windows = []
    for event in events:
        kind = event.get('type')
        elapsed = _number(event.get('server_elapsed'))
        if kind == 'ready':
            server_monotonic_started_at = _number(event.get('server_monotonic_started_at'))
            ready_metadata = {key:event.get(key) for key in (
                'mode', 'model_load_seconds', 'child_model_load_seconds', 'asr_model', 'sample_rate',
                'output_sample_rate', 'initial_video_delay', 'maximum_video_buffer_seconds',
                'endpoint_policy', 'boundary_model', 'boundary_device',
                'contextual_chunking', 'runtime_implementation')}
        elif kind == 'hypothesis':
            source_end = _number(event.get('source_end'))
            if source_end is not None:
                latest_asr_source_end = max(latest_asr_source_end or 0.0, source_end)
            current_decode = {key:event.get(key) for key in (
                'decode_coalescing_wait_seconds', 'decode_coalesced_request_count',
                'asr_seconds', 'decode_cycle_seconds', 'local_agreement_seconds',
                'boundary_model_seconds', 'endpoint_decision_seconds', 'ingest_queue',
                'phrase_queue', 'input_queue_seconds', 'asr_dispatched_at',
                'asr_completed_at', 'local_agreement_started_at',
                'local_agreement_completed_at', 'boundary_model_started_at',
                'boundary_model_completed_at', 'endpoint_decision_started_at',
                'endpoint_decision_completed_at', 'asr_worker_timing')}
            if elapsed is not None:
                timeline.append({'server_elapsed':elapsed, 'ingest_queue_depth':event.get('ingest_queue'),
                    'phrase_queue_depth':event.get('phrase_queue'), 'input_queue_seconds':event.get('input_queue_seconds')})
        elif kind == 'committed':
            phrase = phrases.setdefault(event.get('phrase_id'), {})
            phrase.update({'phrase_id':event.get('phrase_id'), 'source_start':event.get('source_start'),
                'source_end':event.get('source_end'), 'decode_metrics':current_decode})
            start, end = _number(event.get('source_start')), _number(event.get('source_end'))
            if start is not None and end is not None and end >= start:
                capture_windows.append((start,end))
                phrase_source_ends[event.get('phrase_id')] = end
                latest_committed_source_end = max(latest_committed_source_end or 0.0, end)
        elif kind == 'phrase_ready':
            phrase = phrases.setdefault(event.get('phrase_id'), {})
            phrase.update({'phrase_id':event.get('phrase_id'), 'presentation_mode':event.get('presentation_mode'),
                'metrics':event.get('metrics') or {}, 'ready_server_elapsed':elapsed})
            source_end = phrase_source_ends.get(event.get('phrase_id'))
            if source_end is not None:
                latest_ready_source_end = max(latest_ready_source_end or 0.0, source_end)
        elif kind == 'played':
            phrase = phrases.setdefault(event.get('phrase_id'), {})
            phrase['played'] = {key:event.get(key) for key in (
                'speech_end_to_playback_seconds', 'playback_queue_seconds', 'audio_video_offset_seconds',
                'observed_av_offset_seconds', 'audio_output_start_performance_ms',
                'audio_output_end_performance_ms', 'audio_output_timestamp_uncertainty_ms',
                'audio_output_schedule_performance_ms', 'audio_output_clock_source',
                'playback_admission_performance_ms', 'playback_admission_queue_depth',
                'audio_output_started_after_schedule_ms', 'video_frame_expected_display_time_ms',
                'video_frame_media_time', 'video_playback_rate', 'capture_to_playback_seconds',
                'requested_video_playback_rate', 'video_playback_rate_clamped',
                'generated_audio_seconds', 'presentation_audio_seconds', 'silence_padding_seconds',
                'presentation_mode', 'live_edge_to_displayed_video_seconds')}
            phrase['played_server_elapsed'] = elapsed
            played_order.append(event.get('phrase_id'))
        elif kind == 'playback_metrics' and elapsed is not None:
            capture_media = _number(event.get('capture_media_time'))
            video_media = _number(event.get('video_media_time'))
            timeline.append({'server_elapsed':elapsed, 'browser_phrase_queue_depth':event.get('queue_depth'),
                'buffer_seconds':event.get('buffer_seconds'), 'video_media_time':event.get('video_media_time'),
                'capture_media_time':event.get('capture_media_time'),
                'live_edge_to_displayed_video_seconds': (capture_media-video_media)
                    if capture_media is not None and video_media is not None else None})
        elif kind == 'capture_metrics' and elapsed is not None:
            capture_time = _number(event.get('capture_media_time'))
            if capture_time is not None:
                capture_seconds = max(capture_seconds or 0.0, capture_time)
            point = {'server_elapsed':elapsed, 'capture_callback_delay_seconds':event.get('audio_callback_delay_seconds'),
                'capture_media_time':capture_time,
                'latest_asr_source_end':latest_asr_source_end,
                'latest_committed_source_end':latest_committed_source_end,
                'latest_ready_source_end':latest_ready_source_end}
            for key, frontier in (
                ('capture_minus_latest_asr_window_seconds', latest_asr_source_end),
                ('capture_minus_latest_committed_end_seconds', latest_committed_source_end),
                ('capture_minus_latest_ready_end_seconds', latest_ready_source_end),
            ):
                point[key] = max(0.0, capture_time-frontier) if frontier is not None else None
            timeline.append(point)
        elif kind == 'coverage':
            value = _number(event.get('analyzed_until'))
            if value is not None:
                analyzed_until = max(analyzed_until, value)
        elif kind == 'warning':
            warnings.append({key:event.get(key) for key in ('code','reason','source_start','source_end')
                if event.get(key) is not None})
        elif kind == 'untranslated_final_tail':
            warnings.append({key:event.get(key) for key in ('code','source_start','source_end','reason')
                if event.get(key) is not None})
        elif kind == 'clock_sync':
            clock_sync.append({key:event.get(key) for key in ('server_elapsed',
                'client_perf_to_server_monotonic_offset_ms', 'round_trip_ms', 'uncertainty_ms')})

    rows = []
    for phrase in sorted(phrases.values(), key=lambda value: (_number(value.get('source_start')) or 0)):
        row = {'phrase_id':phrase.get('phrase_id'), 'source_start':phrase.get('source_start'),
            'source_end':phrase.get('source_end'), 'presentation_mode':phrase.get('presentation_mode'),
            'stages_seconds':_stage_values(phrase),
            'tts_candidate_stages_seconds':_candidate_values(phrase.get('metrics') or {})}
        played = phrase.get('played') or {}
        played_elapsed = _number(phrase.get('played_server_elapsed'))
        if server_monotonic_started_at is not None and clock_sync and played_elapsed is not None:
            nearby = [sample for sample in clock_sync
                if abs((_number(sample.get('server_elapsed')) or 0)-played_elapsed) <= 15]
            sync = min(nearby or clock_sync, key=lambda sample:(_number(sample.get('uncertainty_ms')) or float('inf')))
            offset = _number(sync.get('client_perf_to_server_monotonic_offset_ms'))
            if offset is not None:
                mapping = {'clock_uncertainty_ms':sync.get('uncertainty_ms')}
                for key,out_key in (('playback_admission_performance_ms','playback_admission_server_elapsed_seconds'),
                                    ('audio_output_schedule_performance_ms','audio_output_schedule_server_elapsed_seconds'),
                                    ('audio_output_start_performance_ms','audio_output_start_server_elapsed_seconds'),
                                    ('audio_output_end_performance_ms','audio_output_end_server_elapsed_seconds')):
                    perf_ms = _number(played.get(key))
                    if perf_ms is not None:
                        mapping[out_key] = (perf_ms+offset)/1000-server_monotonic_started_at
                if len(mapping)>1:
                    row['browser_clock_mapping'] = mapping
        row['critical_path'] = _critical_path(phrase.get('metrics') or {}, played,
            row.get('browser_clock_mapping'))
        rows.append(row)
    stage_names = sorted({name for row in rows for name in row['stages_seconds']})
    distributions = {}
    for name in stage_names:
        values = [row['stages_seconds'][name] for row in rows if name in row['stages_seconds']]
        distributions[name] = {'n':len(values), 'p50':_percentile(values,.5), 'p95':_percentile(values,.95),
            'max':max(values) if values else None}
    candidate_stage_names = sorted({name for row in rows for name in row['tts_candidate_stages_seconds']})
    candidate_distributions = {}
    for name in candidate_stage_names:
        values = [value for row in rows for value in row['tts_candidate_stages_seconds'].get(name, [])]
        candidate_distributions[name] = {'n':len(values), 'p50':_percentile(values,.5),
            'p95':_percentile(values,.95), 'max':max(values) if values else None}
    critical_path_values = defaultdict(list)
    for row in rows:
        for stage in row['critical_path']:
            value = _number(stage.get('duration_seconds'))
            if value is not None:
                critical_path_values[stage['stage']].append(value)
    critical_path_distributions = {
        name: {'n':len(values), 'p50':_percentile(values,.5), 'p95':_percentile(values,.95),
            'max':max(values) if values else None}
        for name, values in sorted(critical_path_values.items())
    }
    backlog_values = defaultdict(list)
    for point in timeline:
        for key in ('capture_minus_latest_asr_window_seconds',
                    'capture_minus_latest_committed_end_seconds',
                    'capture_minus_latest_ready_end_seconds'):
            value = _number(point.get(key))
            if value is not None:
                backlog_values[key].append(value)
    backlog_distributions = {
        name: {'n':len(values), 'p50':_percentile(values,.5), 'p95':_percentile(values,.95),
            'max':max(values) if values else None}
        for name, values in sorted(backlog_values.items())
    }
    merged = []
    for start,end in sorted(capture_windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1],end)
        else:
            merged.append([start,end])
    committed_source_seconds = sum(end-start for start,end in merged)
    unique_played = set(played_order)
    queue_growth = {key:_slope(timeline,key) for key in (
        'ingest_queue_depth','phrase_queue_depth','browser_phrase_queue_depth',
        'input_queue_seconds','buffer_seconds','live_edge_to_displayed_video_seconds',
        'capture_minus_latest_asr_window_seconds','capture_minus_latest_committed_end_seconds',
        'capture_minus_latest_ready_end_seconds')}
    return {'schema_version':2, 'phrase_count':len(rows), 'phrases':rows,
        'stage_distributions_seconds':distributions,
        'critical_path_stage_distributions_seconds':critical_path_distributions,
        'tts_candidate_stage_distributions_seconds':candidate_distributions,
        'source_backlog_distributions_seconds':backlog_distributions,
        'queue_timeline':timeline, 'queue_growth_per_second':queue_growth,
        'coverage':{'captured_source_seconds':capture_seconds,
            'capture_measurement_available':capture_seconds is not None,
            'analyzed_until_seconds':analyzed_until,
            'committed_source_interval_seconds':committed_source_seconds,
            'committed_interval_over_capture_ratio':committed_source_seconds/capture_seconds
                if capture_seconds is not None and capture_seconds > 0 else None,
            'untranslated_tail_seconds':max([_duration(item.get('source_start'),item.get('source_end')) or 0
                for item in warnings if item.get('code') == 'untranslated_final_tail']+[0])},
        'ready_outputs':{'count':sum(item.get('presentation_mode') in ('speech','caption_only')
                for item in phrases.values()),
            'speech_count':sum(item.get('presentation_mode') == 'speech' for item in phrases.values()),
            'caption_only_count':sum(item.get('presentation_mode') == 'caption_only' for item in phrases.values())},
        'playback':{'acknowledgement_count':len(played_order),
            'unique_acknowledgement_count':len(unique_played),
            'duplicate_acknowledgement_count':len(played_order)-len(unique_played),
            'video_playback_rate_clamp_count':sum(item.get('played',{}).get('video_playback_rate_clamped') is True
                for item in phrases.values()),
            'minimum_video_playback_rate':min((value for item in phrases.values()
                if (value := _number(item.get('played',{}).get('video_playback_rate'))) is not None), default=None),
            'speech_count':sum(item.get('played',{}).get('presentation_mode') == 'speech'
                for item in phrases.values()),
            'caption_only_count':sum(item.get('played',{}).get('presentation_mode') == 'caption_only'
                for item in phrases.values())},
        'runtime':ready_metadata, 'warnings':warnings,
        'clock_sync_samples':clock_sync,
        'privacy_note':'Source and target transcripts, URLs, tokens and raw event payloads are omitted. Stage durations overlap where explicitly shown and must not be summed into a total.'}


def _queue_svg(points):
    width, height, left, top = 780, 230, 46, 20
    bottom, right = height-32, width-18
    series = [('ingest_queue_depth','Input queue'), ('phrase_queue_depth','Model phrase queue'),
        ('browser_phrase_queue_depth','Browser playback queue')]
    usable = [(point, _number(point.get('server_elapsed'))) for point in points]
    usable = [(point,x) for point,x in usable if x is not None]
    if len(usable) < 2:
        return '<p>Queue timeline unavailable: this event file has fewer than two queue samples.</p>'
    xs = [x for _,x in usable]
    max_y = max([_number(point.get(key)) or 0 for point,_ in usable for key,_ in series] + [1])
    def xy(point, x, key):
        value = _number(point.get(key))
        if value is None:
            return None
        xx = left+(x-min(xs))/max(1e-9,max(xs)-min(xs))*(right-left)
        yy = bottom-value/max_y*(bottom-top)
        return f'{xx:.1f},{yy:.1f}'
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Queue depths over time">',
        f'<path d="M{left} {top}V{bottom}H{right}" fill="none" stroke="#777"/>']
    colors = ['#167d9a','#d18422','#7555a2']
    for (key,label),color in zip(series,colors):
        coords = [xy(point,x,key) for point,x in usable]
        coords = [coord for coord in coords if coord is not None]
        if coords:
            parts.append(f'<polyline points="{" ".join(coords)}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<text x="{left+8}" y="{top+15+18*len([p for p in parts if p.startswith("<text")])}" fill="{color}">{escape(label)}</text>')
    parts.append(f'<text x="{left}" y="{height-7}" font-size="11">server monotonic elapsed (s)</text></svg>')
    return ''.join(parts)


def _backlog_svg(points):
    width, height, left, top = 780, 230, 46, 20
    bottom, right = height-32, width-18
    series = [('capture_minus_latest_asr_window_seconds','Capture → latest ASR window'),
        ('capture_minus_latest_committed_end_seconds','Capture → committed source'),
        ('capture_minus_latest_ready_end_seconds','Capture → ready output')]
    usable = [(point, _number(point.get('server_elapsed'))) for point in points]
    usable = [(point,x) for point,x in usable if x is not None]
    if len(usable) < 2:
        return '<p>Source backlog timeline unavailable: fewer than two time samples.</p>'
    xs = [x for _,x in usable]
    max_y = max([_number(point.get(key)) or 0 for point,_ in usable for key,_ in series] + [1])
    def xy(point, x, key):
        value = _number(point.get(key))
        if value is None:
            return None
        xx = left+(x-min(xs))/max(1e-9,max(xs)-min(xs))*(right-left)
        yy = bottom-value/max_y*(bottom-top)
        return f'{xx:.1f},{yy:.1f}'
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Source media backlog over time">',
        f'<path d="M{left} {top}V{bottom}H{right}" fill="none" stroke="#777"/>']
    colors = ['#167d9a','#d18422','#7555a2']
    for (key,label),color in zip(series,colors):
        coords = [xy(point,x,key) for point,x in usable]
        coords = [coord for coord in coords if coord is not None]
        if coords:
            parts.append(f'<polyline points="{" ".join(coords)}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<text x="{left+8}" y="{top+15+18*len([p for p in parts if p.startswith("<text")])}" fill="{color}">{escape(label)}</text>')
    parts.append(f'<text x="{left}" y="{height-7}" font-size="11">server monotonic elapsed (s); vertical axis is source-media seconds</text></svg>')
    return ''.join(parts)


def write_report(summary, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir/'live_latency_analysis.json'
    html_path = output_dir/'live_latency_analysis.html'
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    dist_rows = ''.join('<tr><td>'+escape(name)+'</td><td>'+str(item['n'])+'</td><td>'+_fmt(item['p50'])+
        '</td><td>'+_fmt(item['p95'])+'</td><td>'+_fmt(item['max'])+'</td></tr>'
        for name,item in summary['stage_distributions_seconds'].items())
    candidate_dist_rows = ''.join('<tr><td>'+escape(name)+'</td><td>'+str(item['n'])+'</td><td>'+_fmt(item['p50'])+
        '</td><td>'+_fmt(item['p95'])+'</td><td>'+_fmt(item['max'])+'</td></tr>'
        for name,item in summary['tts_candidate_stage_distributions_seconds'].items())
    critical_rows = ''.join('<tr><td>'+escape(name)+'</td><td>'+str(item['n'])+'</td><td>'+_fmt(item['p50'])+
        '</td><td>'+_fmt(item['p95'])+'</td><td>'+_fmt(item['max'])+'</td></tr>'
        for name,item in summary.get('critical_path_stage_distributions_seconds', {}).items())
    backlog_rows = ''.join('<tr><td>'+escape(name)+'</td><td>'+str(item['n'])+'</td><td>'+_fmt(item['p50'])+
        '</td><td>'+_fmt(item['p95'])+'</td><td>'+_fmt(item['max'])+'</td></tr>'
        for name,item in summary.get('source_backlog_distributions_seconds', {}).items())
    phrase_rows = ''.join('<tr><td>'+escape(str(row['phrase_id']))+'</td><td>'+_fmt(row['source_start'])+
        '–'+_fmt(row['source_end'])+'</td><td>'+escape(str(row.get('presentation_mode') or 'pending'))+
        '</td><td>'+escape(', '.join(f'{name}={value:.3f}s' for name,value in row['stages_seconds'].items()))+
        '</td><td>'+escape(', '.join(f'{item["stage"]}={item["duration_seconds"]:.3f}s'
            for item in row['critical_path']))+'</td></tr>'
        for row in summary['phrases'])
    coverage = summary['coverage']
    outputs = summary['ready_outputs']
    playback = summary['playback']
    runtime = escape(json.dumps(summary.get('runtime') or {}, ensure_ascii=False, sort_keys=True))
    html_path.write_text('<!doctype html><meta charset="utf-8"><title>Live latency analysis</title>'
        '<style>body{font:14px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#20242b}'
        'table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #ccd2d8;padding:.45rem;text-align:left}'
        'td:last-child{font-variant-numeric:tabular-nums}svg{width:100%;border:1px solid #ddd}</style>'
        f'<h1>Live latency analysis</h1><p>{summary["phrase_count"]} phrase records. Direct speech-end latency is reported separately from stage distributions; percentiles are not added together.</p>'
        f'<p>Captured source measured: {_fmt(coverage["captured_source_seconds"])} s; committed source intervals: {_fmt(coverage["committed_source_interval_seconds"])} s '
        f'({_fmt(coverage["committed_interval_over_capture_ratio"])} of captured media time); analyzed through {_fmt(coverage["analyzed_until_seconds"])} s; '
        f'untranslated tail: {_fmt(coverage["untranslated_tail_seconds"])} s. Ready outputs: {outputs["count"]} (speech {outputs["speech_count"]}, caption-only {outputs["caption_only_count"]}). '
        f'Playback acknowledgements: {playback["unique_acknowledgement_count"]} unique, '
        f'{playback["duplicate_acknowledgement_count"]} duplicates; speech {playback["speech_count"]}, caption-only {playback["caption_only_count"]}.</p>'
        f'<p>Runtime: <code>{runtime}</code></p>'
        '<h2>Queue depth over time</h2>'+_queue_svg(summary['queue_timeline'])+
        '<h2>Queue growth per second</h2><pre>'+escape(json.dumps(summary['queue_growth_per_second'], indent=2))+'</pre>'+
        '<h2>Stage distributions</h2><table><thead><tr><th>Stage</th><th>n</th><th>p50 (s)</th><th>p95 (s)</th><th>max (s)</th></tr></thead><tbody>'+dist_rows+'</tbody></table>'
        '<h2>Ordered critical-path stage distributions</h2><p>These are observed intervals and may overlap; do not sum their percentiles.</p><table><thead><tr><th>Stage</th><th>n</th><th>p50 (s)</th><th>p95 (s)</th><th>max (s)</th></tr></thead><tbody>'+critical_rows+'</tbody></table>'
        '<h2>Source-media backlog</h2><p>Gaps are measured in source-media seconds from the latest captured position to each processing frontier.</p>'+_backlog_svg(summary['queue_timeline'])+
        '<table><thead><tr><th>Frontier</th><th>n</th><th>p50 (s)</th><th>p95 (s)</th><th>max (s)</th></tr></thead><tbody>'+backlog_rows+'</tbody></table>'+
        '<h2>TTS candidate and retry distributions</h2><table><thead><tr><th>Stage</th><th>n</th><th>p50 (s)</th><th>p95 (s)</th><th>max (s)</th></tr></thead><tbody>'+candidate_dist_rows+'</tbody></table>'
        '<h2>Per-phrase critical path (no transcript text)</h2><table><thead><tr><th>ID</th><th>Source interval (s)</th><th>Output</th><th>Observed stage metrics</th><th>Measured ordered intervals</th></tr></thead><tbody>'+phrase_rows+'</tbody></table>'
        '<p>'+escape(summary['privacy_note'])+'</p>')
    return json_path, html_path


def _fmt(value):
    return '—' if _number(value) is None else f'{value:.3f}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path, help='events.jsonl from a live session')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    events = []
    with args.events.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    json_path, html_path = write_report(summarize(events), args.output_dir)
    print(f'Wrote {json_path} and {html_path}')


if __name__ == '__main__':
    main()
