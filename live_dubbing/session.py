"""Timestamped ingest, incremental decoding, bounded phrase synthesis and audit."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import json
import re
from pathlib import Path
import time
import traceback
import uuid
import os
import wave
import hashlib
import math
from types import SimpleNamespace
from collections import deque

from .adaptive import Decision, LocalAgreement, incomplete_ending, normalized, quantity_continues, source_text, RUNTIME_SOURCE_SHA256 as ADAPTIVE_SOURCE_SHA256
from .universal import UniversalLatencyController
from .simultaneous import AdaptiveSimultaneousTranslationController, SimultaneousCommit
from .read_wait_write import AdaptiveReadWaitWriteController, ReadWaitWriteCommit
from .commitment import InvalidProposal, Proposal
from .protocol import AudioPacket, CaptureClock, ProtocolError
from .scheduling import QwenBoundaryAdmission

RUNTIME_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RUNTIME_IMPLEMENTATION = {'session': RUNTIME_SOURCE_SHA256, 'adaptive': ADAPTIVE_SOURCE_SHA256,
    'speech':hashlib.sha256(Path(__file__).with_name('speech.py').read_bytes()).hexdigest(),
    'models':hashlib.sha256(Path(__file__).with_name('models.py').read_bytes()).hexdigest(),
    'scheduling':hashlib.sha256(Path(__file__).with_name('scheduling.py').read_bytes()).hexdigest(),
    'timing':hashlib.sha256(Path(__file__).with_name('timing.py').read_bytes()).hexdigest(),
    'qa':hashlib.sha256(Path(__file__).with_name('qa.py').read_bytes()).hexdigest(),
    'universal':hashlib.sha256(Path(__file__).with_name('universal.py').read_bytes()).hexdigest(),
    'simultaneous':hashlib.sha256(Path(__file__).with_name('simultaneous.py').read_bytes()).hexdigest(),
    'read_wait_write':hashlib.sha256(Path(__file__).with_name('read_wait_write.py').read_bytes()).hexdigest()}

PLAYBACK_METRIC_MAX_AGE_SECONDS = 2.5
PRESENTATION_MAX_TEMPO_BY_MODE = {'quality':1.20, 'balanced':1.30, 'low_latency':1.30}
# Pitch-preserving catch-up is applied only after the measured live-edge lag
# exceeds each mode's target. It changes the next voiced presentation, not the
# source boundary or translation, and every retimed candidate still passes the
# normal audio QA gates before it can be sent to the browser.
PRESENTATION_CATCHUP_BY_MODE = {
    'quality': (28.0, 1.04),
    'balanced': (23.0, 1.08),
    'low_latency': (12.0, 1.40),
}
PRESENTATION_CATCHUP_GAIN_BY_MODE = {
    'quality': .008,
    'balanced': .008,
    'low_latency': .08,
}
# The browser's bounded player currently accepts twelve outstanding phrases.
# Keep two slots in reserve for protocol/event ordering, and let playback ACKs
# provide backpressure before the client reaches that hard safety limit.
BROWSER_PLAYBACK_HIGH_WATERMARK = 10
CAPTURE_DIAGNOSTIC_MAX_SECONDS = 300


def adaptive_presentation_speed_factor(mode, video_delay_seconds):
    """Boundedly accelerate pitch-preserved speech when live lag grows.

    The per-mode target keeps Quality least aggressive and Low Latency most
    responsive. Invalid or absent browser timing telemetry never changes audio.
    """
    target, maximum = PRESENTATION_CATCHUP_BY_MODE[mode]
    if (not isinstance(video_delay_seconds, (int, float))
            or isinstance(video_delay_seconds, bool)
            or not math.isfinite(video_delay_seconds)):
        return 1.0
    excess = max(0.0, float(video_delay_seconds)-target)
    gain = PRESENTATION_CATCHUP_GAIN_BY_MODE[mode]
    return round(min(maximum, 1.0+excess*gain), 3)


def _same_source_endpoint(candidate_end, confirmed_end):
    """Only let a pending READ/WAIT/WRITE check hold its exact source cut."""
    if not isinstance(candidate_end, (int, float)) or not isinstance(confirmed_end, (int, float)):
        return False
    return math.isfinite(candidate_end) and math.isfinite(confirmed_end) and abs(
        candidate_end-confirmed_end) <= .35


def _source_caption_fallback(item, error):
    """Keep a phrase with failed translation preservation visible and continue.

    Translation integrity checks are safety gates. A rejection must not be
    converted into speech, and it must not terminate synthesis for later
    phrases. Preserve only the source caption for this interval.
    """
    message = str(error)
    if not any(marker in message for marker in (
            'translation_preservation_failed', 'explicit_glossary_not_preserved',
            'translation_semantic_support_failed', 'translation_semantic_support_unavailable')):
        return None
    import re
    semantic_support = ('translation_semantic_support_' in message)
    issue_kind = ('semantic_support' if semantic_support else
                  'number' if re.search(r'["\']kind["\']\s*:\s*["\']number["\']', message)
                  else 'glossary' if 'explicit_glossary_not_preserved' in message or
                  re.search(r'["\']kind["\']\s*:\s*["\']glossary["\']', message)
                  else 'translation')
    support_check = None
    if semantic_support:
        try:
            support_check = json.loads(message.partition(': ')[2])
        except (ValueError, TypeError):
            support_check = {'action':'semantic_support_rejected',
                'error_type':'unparsed_diagnostics'}
    fallback_reason = ('translation_semantic_support_rejected' if semantic_support else
        f'translation_{issue_kind}_preservation_failed')
    return SimpleNamespace(
        identifier=item['identifier'], source_start=item['source_start'],
        source_end=item['source_end'], source_text=item['source_text'], target_text='',
        speaker={'speaker_id':'SOURCE_CAPTION_ONLY','reason':'translation_validation_failed'},
        pcm=b'', chunk_sizes=[], candidates=[],
        metrics={'presentation_mode':'caption_only', 'speech_generated':False,
            'fallback_reason':fallback_reason,
            'source_caption_only':True, 'translation_validation_failed':True,
            'translation_validation_issue_kind':issue_kind, 'tts_backend':'unavailable',
            'translation_outlier_guard':support_check,
            'attempted_tts_backend':'not_started', 'tts_seconds':0.0, 'qa_seconds':0.0})


class LiveSession:
    def __init__(self, models, emit, directory: Path, *, mode='balanced', boundary=None,
                 audio_backpressure=True):
        if type(audio_backpressure) is not bool:
            raise ValueError('audio_backpressure must be a boolean')
        self.models, self.emit, self.directory = models, emit, Path(directory)
        self.controller = UniversalLatencyController(mode)
        self.simultaneous = AdaptiveSimultaneousTranslationController(mode)
        self.read_wait_write = AdaptiveReadWaitWriteController(mode)
        self.read_wait_write_enabled = (
            getattr(models, 'live_translation_backend', None) == 'hymt2-1.8b'
            and getattr(models, 'live_boundary_backend', None) == 'qwen3.5-27b'
            and callable(getattr(models, 'propose_simultaneous', None))
            and callable(getattr(models, 'assess_simultaneous_candidate', None)))
        self.read_wait_write_task = None
        self.read_wait_write_task_approved_end = None
        self.read_wait_write_task_cut_signature = None
        self.read_wait_write_context_cut_key = None
        self.read_wait_write_context_text = None
        self.read_wait_write_context_source_text = None
        self.read_wait_write_context_previous_source = None
        self.read_wait_write_context_previous_translation = None
        self.read_wait_write_context_duration_seconds = None
        self.read_wait_write_context_origin_observation_id = None
        self.read_wait_write_endpoint_hold_cut = None
        self.read_wait_write_endpoint_hold_count = 0
        self.read_wait_write_generation = 0
        self.pending_read_wait_write_commit = None
        self.pending_read_wait_write_metrics = None
        self.read_wait_write_queries = 0
        self.read_wait_write_rejections = 0
        self.target_translation_task = None
        self.target_translation_words = ()
        self.target_translation_language = None
        self.target_translation_failures = 0
        self.boundary = boundary
        self.boundary_admission = QwenBoundaryAdmission(mode)
        self.boundary_audio_backpressure = audio_backpressure
        self.boundary_source_signature = None
        self.guarded_boundary_source_signature = None
        self.boundary_source_revision = 0
        self.boundary_selector_calls = 0
        self.boundary_guard_calls = 0
        self.pending_playback_audio_seconds = {}
        self.latest_playback_audio_demand_seconds = None
        self.latest_playback_metrics_at = None
        self.latest_playback_video_delay_seconds = None
        self.latest_playback_video_delay_at = None
        self.audio_ready_since_playback_metrics_seconds = 0.0
        self.vad_speech_intervals = deque()
        self.vad_speech_seconds_total = 0.0
        self.committed_vad_speech_seconds = 0.0
        self.committed_source_timeline_seconds = 0.0
        self.source_history = deque(maxlen=8)
        self.target_translation_history = deque(maxlen=8)
        self.pending_playback_targets = {}
        self.agreement = LocalAgreement()
        self.clock = CaptureClock()
        self.packet_receive_times = deque(maxlen=5000)
        self.capture_first_packet_received_at = None
        self.capture_last_packet_received_at = None
        self.browser_clock_offset_ms = None
        self.browser_clock_uncertainty_ms = None
        self.capture_clock_origin_monotonic = None
        self.capture_clock_uncertainty_ms = None
        self.latest_capture_clock_sample = None
        self.audio_queue = asyncio.Queue(maxsize=100)  # 2 s at the browser's 20 ms contract
        self.phrase_queue = asyncio.Queue(maxsize=3)
        self.buffer = bytearray()
        self.overflow_audio = bytearray()
        self.overflow_start = None
        self.unprocessed_input_packet_count = 0
        self.unprocessed_input_start = None
        self.unprocessed_input_end = None
        self.processed_source_end = None
        self.buffer_start = 0.0
        self.origin = None
        self.phrase_start = None
        self.last_speech = 0.0
        self.last_decode = -1.0
        self.stable_since = 0.0
        self.stable_text = ''
        self.source_language = models.source_language
        self.observed_language = None
        self.tasks = []
        self.decode_task = None
        self.decode_requested = asyncio.Event()
        self.decode_media = 0.0
        self.decode_request_at = None
        self.decode_request_media = None
        self.decode_coalesced_requests = 0
        self.decoder_finishing = False
        self.closed = False
        self.paused = False
        self.sent, self.played = set(), set()
        self.playback_capacity_changed = asyncio.Event()
        self.played_order = deque(maxlen=256)
        self.sent_count = self.played_count = 0
        self.caption_count = self.played_caption_count = self.played_speech_count = 0
        self.sent_kinds = {}
        self.finishing = False
        self.overload_drain_requested = False
        self.pending = {}
        self.processing_pending = {}
        self.events = []
        self.started = time.perf_counter()
        self.total_events = 0
        self.queued_seconds = 0.0
        self.last_coverage = -1.0
        self.failure = None
        self.untranslated_final_tail = None
        self.asr_interval = {'quality': .8, 'balanced': .6, 'low_latency': .4}[mode]
        self.asr_work_seconds = 0.0
        self.capture_origin = None
        self.chunk_proposal = None
        self.chunk_proposal_observations = 0
        self.rejected_chunk_proposals = {}
        self.pauses = deque(maxlen=128)
        self.capture_diagnostic = None
        self.capture_diagnostic_samples = 0
        if os.environ.get('LIVE_CAPTURE_DIAGNOSTICS') == '1':
            # Explicit development diagnostic, bounded to one five-minute
            # validation run. Disabled by default; captured source audio stays
            # on this same computer.
            self.capture_diagnostic = wave.open(str(self.directory / 'captured-source.wav'), 'wb')
            self.capture_diagnostic.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))

    def set_capture_origin(self, monotonic_seconds):
        if self.capture_origin is not None:
            raise ProtocolError('Capture origin may only be set once')
        self.capture_origin = float(monotonic_seconds)

    def update_browser_clock(self, offset_ms, uncertainty_ms):
        if (not isinstance(offset_ms, (int, float)) or isinstance(offset_ms, bool) or
                not isinstance(uncertainty_ms, (int, float)) or isinstance(uncertainty_ms, bool) or
                not (-1e9 < offset_ms < 1e9) or not (0 <= uncertainty_ms <= 5000)):
            return False
        self.browser_clock_offset_ms = float(offset_ms)
        self.browser_clock_uncertainty_ms = float(uncertainty_ms)
        if self.latest_capture_clock_sample:
            self._calibrate_capture_clock(*self.latest_capture_clock_sample)
        return True

    def update_capture_clock(self, media_end, performance_ms, uncertainty_ms):
        if (not isinstance(media_end, (int, float)) or isinstance(media_end, bool) or media_end < 0 or
                not isinstance(performance_ms, (int, float)) or isinstance(performance_ms, bool) or
                not isinstance(uncertainty_ms, (int, float)) or isinstance(uncertainty_ms, bool) or
                not (0 <= uncertainty_ms <= 5000)):
            return False
        self.latest_capture_clock_sample = (float(media_end), float(performance_ms), float(uncertainty_ms))
        if self.browser_clock_offset_ms is not None:
            self._calibrate_capture_clock(*self.latest_capture_clock_sample)
        return True

    def _calibrate_capture_clock(self, media_end, performance_ms, uncertainty_ms):
        mapped_server_time = (performance_ms + self.browser_clock_offset_ms) / 1000
        self.capture_clock_origin_monotonic = mapped_server_time - media_end
        self.capture_clock_uncertainty_ms = (self.browser_clock_uncertainty_ms**2 + uncertainty_ms**2)**.5

    def _capture_monotonic_time(self, media_time):
        origin = self.capture_clock_origin_monotonic
        uncertainty = self.capture_clock_uncertainty_ms
        if origin is None:
            origin = self.capture_origin
        return (origin+media_time if origin is not None else None), uncertainty

    def _record_vad_speech(self, start, end):
        """Accumulate speech-only source coverage and retain only uncommitted spans."""
        if end <= start:
            return
        if self.vad_speech_intervals and start <= self.vad_speech_intervals[-1][1]:
            previous_start, previous_end = self.vad_speech_intervals[-1]
            self.vad_speech_seconds_total += max(0.0, end-max(start, previous_end))
            self.vad_speech_intervals[-1] = (previous_start, max(previous_end, end))
        else:
            self.vad_speech_seconds_total += end-start
            self.vad_speech_intervals.append((start, end))

    def _uncommitted_vad_coverage(self, start=None, end=None):
        """Return exact residual VAD spans, optionally clipped to a source range."""
        intervals = []
        for speech_start, speech_end in self.vad_speech_intervals:
            left = speech_start if start is None else max(speech_start, float(start))
            right = speech_end if end is None else min(speech_end, float(end))
            if right > left:
                intervals.append({
                    'source_start': round(left, 6),
                    'source_end': round(right, 6),
                    'speech_seconds': round(right-left, 6),
                })
        return intervals

    def _commit_vad_speech(self, start, end):
        if end <= start:
            return 0.0
        voiced = 0.0
        remaining = deque()
        for speech_start, speech_end in self.vad_speech_intervals:
            left = max(start, speech_start)
            right = min(end, speech_end)
            if right > left:
                voiced += right-left
            # Subtract only the committed source range. VAD activity outside
            # either edge remains visible for the end-of-session coverage audit.
            if speech_start < start:
                remaining.append((speech_start, min(speech_end, start)))
            if speech_end > end:
                remaining.append((max(speech_start, end), speech_end))
        self.vad_speech_intervals = remaining
        self.committed_vad_speech_seconds += voiced
        self.committed_source_timeline_seconds += max(0.0, end-start)
        return voiced

    def observe_playback_audio_demand(self, seconds, *, capture_media_time=None,
                                      video_media_time=None, now=None):
        """Record the browser's remaining generated-speech audio demand."""
        if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds < 0):
            return False
        self.latest_playback_audio_demand_seconds = float(seconds)
        self.latest_playback_metrics_at = time.perf_counter() if now is None else float(now)
        if (isinstance(capture_media_time, (int, float)) and not isinstance(capture_media_time, bool)
                and math.isfinite(capture_media_time)
                and isinstance(video_media_time, (int, float)) and not isinstance(video_media_time, bool)
                and math.isfinite(video_media_time)):
            self.latest_playback_video_delay_seconds = max(
                0.0, float(capture_media_time)-float(video_media_time))
            self.latest_playback_video_delay_at = self.latest_playback_metrics_at
        self.audio_ready_since_playback_metrics_seconds = 0.0
        return True

    def live_video_delay_seconds(self, *, now=None):
        if self.latest_playback_video_delay_seconds is None or self.latest_playback_video_delay_at is None:
            return None
        timestamp = time.perf_counter() if now is None else float(now)
        age = max(0.0, timestamp-self.latest_playback_video_delay_at)
        if age > PLAYBACK_METRIC_MAX_AGE_SECONDS:
            return None
        return self.latest_playback_video_delay_seconds

    def presentation_speed_factor(self, *, now=None):
        return adaptive_presentation_speed_factor(self.controller.mode,
            self.live_video_delay_seconds(now=now))

    def note_playback_audio_ready(self, identifier, seconds):
        """Track a newly sent phrase until browser telemetry includes it."""
        if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError('generated audio duration must be finite and non-negative')
        if seconds == 0:
            return
        self.pending_playback_audio_seconds[identifier] = float(seconds)
        self.audio_ready_since_playback_metrics_seconds += float(seconds)

    def ready_speech_audio_demand(self, *, now=None):
        """Prefer recent browser remaining audio plus phrases sent since it.

        The server's unacknowledged-audio map stores each phrase's full duration
        until the playback acknowledgement arrives. That overstates load while
        an active phrase is playing. Recent browser telemetry reports the true
        remaining demand; newly emitted phrases are added until the next sample.
        """
        timestamp = time.perf_counter() if now is None else float(now)
        if self.latest_playback_metrics_at is not None:
            age = max(0.0, timestamp-self.latest_playback_metrics_at)
            if age <= PLAYBACK_METRIC_MAX_AGE_SECONDS:
                return (self.latest_playback_audio_demand_seconds or 0.0) + \
                    self.audio_ready_since_playback_metrics_seconds
        return sum(self.pending_playback_audio_seconds.values())

    def playback_audio_metrics_age(self, *, now=None):
        if self.latest_playback_metrics_at is None:
            return None
        timestamp = time.perf_counter() if now is None else float(now)
        return max(0.0, timestamp-self.latest_playback_metrics_at)

    def _packet_received_at(self, media_time):
        for start, end, received_at in reversed(self.packet_receive_times):
            if start-.001 <= media_time <= end+.021:
                return received_at
            if end < media_time:
                break
        return None

    async def event(self, kind, **values):
        if kind == 'error':
            self.failure = values.get('error', 'unknown live error')
        event = {'type': kind, 'server_elapsed': time.perf_counter() - self.started, **values}
        self.total_events += 1
        self.events.append(event)
        if len(self.events) > 2000:
            del self.events[:1000]
        # Persist incrementally so hours of telemetry do not accumulate in RAM.
        with (self.directory / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event, default=str, ensure_ascii=False) + '\n')
        await self.emit(event)

    async def _wait_for_playback_capacity(self):
        """Hold synthesis while the browser has its bounded phrase window full.

        Captured source remains governed by the existing finite PCM/video
        limits. Each played ACK opens one phrase slot; this prevents the
        browser's client-side queue guard from turning ordinary playback lag
        into a fatal WebSocket stop that discards already accepted phrases.
        """
        wait_started = None
        while not self.closed:
            if len(self.sent) < BROWSER_PLAYBACK_HIGH_WATERMARK:
                if wait_started is not None:
                    await self.event('playback_backpressure', action='released',
                        outstanding_phrase_count=len(self.sent),
                        wait_seconds=round(time.perf_counter()-wait_started, 3),
                        phrase_queue_depth=self.phrase_queue.qsize())
                return True
            if wait_started is None:
                wait_started = time.perf_counter()
                await self.event('playback_backpressure', action='waiting_for_played_ack',
                    outstanding_phrase_count=len(self.sent),
                    high_watermark=BROWSER_PLAYBACK_HIGH_WATERMARK,
                    phrase_queue_depth=self.phrase_queue.qsize())
            # Clear before rechecking so an ACK between the count check and
            # wait cannot be lost.
            self.playback_capacity_changed.clear()
            if len(self.sent) < BROWSER_PLAYBACK_HIGH_WATERMARK:
                continue
            await self.playback_capacity_changed.wait()
        return False

    async def start(self):
        if self.boundary is None:
            from .segmentation import BoundaryClient
            self.boundary = await asyncio.to_thread(BoundaryClient)
        self.decode_task = asyncio.create_task(self._decode_loop())
        self.tasks = [asyncio.create_task(self._ingest()), self.decode_task,
                      asyncio.create_task(self._synthesize())]
        source_budget = self.controller.source_budget
        recovery_budget = self.controller.source_recovery_budget
        await self.event('ready', mode=self.controller.mode,
            server_monotonic_started_at=self.started,
            runtime_implementation=RUNTIME_IMPLEMENTATION,
            endpoint_policy='confirmed_source_contextual_mt_v2_with_source_boundary_prefetch',
            boundary_model='segment-any-text/sat-3l-sm',
            contextual_boundary_model=getattr(self.models, 'live_boundary_backend', 'sat'),
            boundary_device='cpu', boundary_final_input_score_ignored=True,
            source_agreement='incremental_local_agreement',
            translation_selection='exact_source_chunk_prefetch_with_endpoint_commit',
            translation_equality_required=False,
            simultaneous_policy=self.simultaneous.policy,
            simultaneous_controller=('incremental_target_stability_read_wait_write'
                if self.read_wait_write_enabled else 'confirmed_source_boundary_prefetch'),
            simultaneous_semantic_correctness_certified=False,
            read_wait_write_enabled=self.read_wait_write_enabled,
            read_wait_write_mode=self.controller.mode,
            read_wait_write_translation_backend=getattr(self.models, 'live_translation_backend', None),
            read_wait_write_semantic_checker=(getattr(self.models, 'live_boundary_backend', None)
                if self.read_wait_write_enabled else None),
            read_wait_write_requires_two_growing_source_observations=True,
            contextual_chunking=getattr(self.models, 'contextual_chunking', False) is True,
            initial_video_delay=self.controller.policy.initial_video_delay,
            maximum_video_buffer_seconds=min(34.0, max(20.0,
                recovery_budget + 12.0 + self.controller.policy.initial_video_delay)),
            maximum_audio_buffer_seconds=self._maximum_pcm_buffer_seconds(),
            maximum_queued_input_audio_seconds=2.01,
            maximum_total_retained_source_seconds=self._maximum_pcm_buffer_seconds()+2.01,
            source_recovery_budget_seconds=recovery_budget,
            model_load_seconds=self.models.load_seconds,
            child_model_load_seconds=getattr(self.models, 'child_load_seconds', None), protocol=1,
            asr_model=getattr(self.models, 'whisper_model', None),
            sample_rate=16000, output_sample_rate=48000)

    async def push(self, binary):
        if self.closed or self.failure:
            raise ProtocolError('Session has stopped')
        packet = AudioPacket.decode(binary)
        received_at = time.perf_counter()
        if self.finishing:
            raise ProtocolError('Audio arrived after end of stream')
        if self.capture_origin is None:
            self.capture_origin = time.perf_counter() - packet.end
        self.clock.accept(packet)
        queued_after = self.queued_seconds + len(packet.pcm) / 32000
        retained_after = queued_after + len(self.overflow_audio) / 32000
        if self.audio_queue.full() or retained_after > 2.01:
            raise ProtocolError('capture_queue_overrun: inference cannot keep up; no audio was silently dropped')
        self.queued_seconds = queued_after
        if self.capture_first_packet_received_at is None:
            self.capture_first_packet_received_at = received_at
        self.capture_last_packet_received_at = received_at
        self.packet_receive_times.append((packet.start, packet.end, received_at))
        self.audio_queue.put_nowait(packet)
        if self.overflow_audio and retained_after >= 1.5:
            await self._request_overload_drain('bounded_source_uncertainty')
        if self.capture_diagnostic is not None:
            if (self.capture_diagnostic_samples + len(packet.pcm)//2 <=
                    CAPTURE_DIAGNOSTIC_MAX_SECONDS*16000):
                self.capture_diagnostic.writeframes(packet.pcm)
                self.capture_diagnostic_samples += len(packet.pcm)//2
            else:
                self.capture_diagnostic.close()
                self.capture_diagnostic = None

    def _slice(self, start, end):
        audio = (self.buffer + self.overflow_audio) if self.overflow_audio else self.buffer
        a = max(0, round((start - self.buffer_start) * 16000)) * 2
        b = max(0, round((end - self.buffer_start) * 16000)) * 2
        return bytes(audio[a:b])

    def _trim_pcm_buffer(self, media_now, *, merge_overflow=False):
        if merge_overflow and self.overflow_audio:
            self.buffer.extend(self.overflow_audio)
            self.overflow_audio.clear()
            self.overflow_start = None
        keep_from = (self._asr_context_start() if self.phrase_start is not None
                     else max(self.buffer_start, media_now - 1.5))
        if keep_from > self.buffer_start:
            count = min(len(self.buffer), round((keep_from - self.buffer_start) * 16000) * 2)
            del self.buffer[:count]
            self.buffer_start += count / 32000

    def _request_decode(self, media_now):
        self.last_decode = media_now
        if self.decode_task is None:
            return False
        self.decode_media = media_now
        if self.decode_request_at is None:
            self.decode_request_at = time.perf_counter()
            self.decode_request_media = media_now
        else:
            self.decode_coalesced_requests += 1
            self.decode_request_media = media_now
        self.decode_requested.set()
        return True

    def _record_unprocessed_packet(self, packet):
        if self.unprocessed_input_start is None:
            self.unprocessed_input_start = packet.start
        self.unprocessed_input_end = packet.end
        self.unprocessed_input_packet_count += 1

    async def _wait_for_pcm_capacity(self, packet):
        limit_bytes = int(self._maximum_pcm_buffer_seconds() * 32000)
        # Once audio is staged, every later packet must follow it.  The
        # adaptive limit can grow while decoding, but newly available space
        # in the main buffer does not precede the already staged samples.
        if not self.overflow_audio and len(self.buffer) + len(packet.pcm) <= limit_bytes:
            return 'pcm'
        packet_seconds = len(packet.pcm) / 32000
        staged_seconds = len(self.overflow_audio) / 32000
        queued_after_stage = max(0.0, self.queued_seconds - packet_seconds)
        if staged_seconds + packet_seconds + queued_after_stage > 2.01:
            self._record_unprocessed_packet(packet)
            await self._request_overload_drain('bounded_source_uncertainty')
            return False
        if self.overflow_start is None:
            self.overflow_start = self.buffer_start + len(self.buffer) / 32000
        expected_start = self.overflow_start + staged_seconds
        if abs(packet.start - expected_start) > .004:
            raise ProtocolError('Staged PCM is not contiguous with the bounded source buffer')
        self.overflow_audio.extend(packet.pcm)
        return 'staged'

    def _asr_context_start(self):
        start = self.phrase_start - .6
        committed = self.agreement.committed_end
        if committed >= 0 and 0 <= self.phrase_start - committed <= 1.5:
            # Preserve the preceding word rather than cutting its tail into
            # an apparent new word (real "think tank / arrears" became Bank).
            # Extra context stays within 1.5 s; TTS reference slices are exact.
            start = min(start, max(committed - .6, self.phrase_start - 1.5))
        return max(self.buffer_start, start)

    def _maximum_pcm_buffer_seconds(self):
        policy = self.controller.policy
        source_budget = self.controller.source_recovery_budget
        # Physical PCM includes 1.5 s of already-committed acoustic context.
        # A sentence can first agree on the decode just after its source budget
        # and then require the configured hold and one more decode. In a real
        # VOD the old 18-s cap killed that already-complete sentence mid-hold.
        return max(18.0, source_budget + 1.5 + 2*self._effective_asr_interval() +
                   max(policy.silence, policy.complex_hold) + .04)

    def _maximum_admitted_pcm_seconds(self):
        # The admission budget adapts to measured ASR work.  A later faster
        # decode may lower that budget after samples have already been safely
        # admitted.  Validate retained samples against the maximum possible
        # admission budget; new packets still use the current, smaller limit.
        policy = self.controller.policy
        return max(18.0, self.controller.source_recovery_budget + 1.5 + 2*2.0 +
                   max(policy.silence, policy.complex_hold) + .04)

    def _effective_asr_interval(self):
        # Real rolling windows reached .97 s of work on a .6-s schedule,
        # starving the shared source/QA decoder and growing the input queue.
        # Leave measured compute headroom; retain each mode's minimum cadence.
        return min(2.0, max(self.asr_interval, 1.25*self.asr_work_seconds))

    async def _contextual_candidate(self, agreed, hypothesis, media_now, candidate, *, batching_seconds=0.):
        """Hold a model-proposed source boundary across distinct ASR windows.

        This selects source ownership only. Exact-source contextual translation
        and audio QA remain separate; the proposal certifies no semantic score.
        """
        if getattr(self.models, 'contextual_chunking', False) is not True:
            return candidate
        if self.controller.clause_confirms(candidate, agreed) and not self.chunk_proposal:
            return candidate
        preferred_proposal = None
        if self.chunk_proposal:
            signature, end = self.chunk_proposal
            prefix = agreed[:len(signature)]
            if (prefix and tuple(normalized(w.text) for w in prefix) == signature
                    and abs(prefix[-1].end-end) <= .35 and len(prefix) < len(hypothesis)
                    and hypothesis[len(prefix)].end > prefix[-1].end+.025):
                # Reconsider the proposed end with the newly arrived right
                # context. A stable prefix alone cannot reveal a rate/unit
                # qualifier which only became clear in the next window.
                preferred_proposal = len(prefix)
            else:
                self.chunk_proposal = None
                self.guarded_boundary_source_signature = None
                self.chunk_proposal_observations = 0
        target = {'quality':6., 'balanced':4., 'low_latency':3.}[self.controller.mode]
        if not agreed or len(hypothesis) > 256 or media_now-agreed[0].start < target:
            return candidate
        eligible = [i+1 for i,w in enumerate(agreed)
                    if w.end-agreed[0].start >= 1. and media_now-w.end >= self.controller.lookahead
                    and i+1 < len(hypothesis) and hypothesis[i+1].end > w.end+.025]
        if not eligible:
            return candidate
        # A repeatedly rejected cut must not monopolize every later decode.
        # Exclusion only changes proposals: every alternative is still checked
        # by the existing deterministic and model guards before selection.
        def proposal_key(count):
            return tuple(normalized(w.text) for w in agreed[:count])
        def remember_rejected_boundary(count, *, definitive=False):
            key = proposal_key(count)
            if len(self.rejected_chunk_proposals) >= 64 and key not in self.rejected_chunk_proposals:
                self.rejected_chunk_proposals.clear()
            previous = self.rejected_chunk_proposals.get(key, 0)
            # These source-level failures cannot become safe by replaying the
            # same prefix. Model-guard failures retain one context-driven retry.
            self.rejected_chunk_proposals[key] = max(2, previous + 1) if definitive else previous + 1
        rejected_ends = [agreed[count-1].end for count in eligible
            if self.rejected_chunk_proposals.get(proposal_key(count), 0) >= 2]
        if rejected_ends:
            frontier = max(rejected_ends)
            minimum_advance = QwenBoundaryAdmission.POLICIES[
                self.controller.mode]['minimum_context_advance']
            advanced = [count for count in eligible
                if agreed[count-1].end >= frontier + minimum_advance]
            await self.event('source_chunk_frontier',
                rejected_frontier_seconds=round(frontier-agreed[0].start, 3),
                minimum_context_advance_seconds=minimum_advance,
                eligible_before=len(eligible), eligible_after=len(advanced),
                advanced=bool(advanced))
            if not advanced:
                return candidate
            eligible = advanced
        alternatives = [count for count in eligible
                        if self.rejected_chunk_proposals.get(proposal_key(count), 0) < 2]
        if alternatives:
            eligible = alternatives
        preferred = (preferred_proposal if preferred_proposal in eligible else
            min(eligible, key=lambda count:abs(agreed[count-1].end-agreed[0].start-target)))
        source_signature = tuple((normalized(w.text), round(w.start/.35), round(w.end/.35))
                                  for w in hypothesis)
        # A source revision describes new ASR words/timestamps. Eligibility can
        # change as media time advances even when no source evidence changed.
        source_revision_signature = source_signature
        source_revision_changed = source_revision_signature != self.boundary_source_signature
        if source_revision_changed:
            self.boundary_source_signature = source_revision_signature
            self.boundary_source_revision += 1
        # Admission is measured against local-agreement ASR evidence. A
        # changing speculative suffix can invalidate stale results, but cannot
        # by itself buy another contextual model request.
        source_units = tuple(normalized(w.text) for w in agreed)
        candidate_text = source_text(agreed[:preferred]) if preferred else ''
        candidate_end = agreed[preferred-1].end if preferred else 0.0
        candidate_id = hashlib.sha256(
            f'{candidate_text.casefold()}|{candidate_end:.2f}'.encode('utf-8')).hexdigest()[:16]
        language = (self.observed_language or self.source_language or '').lower().split('-', 1)[0]
        if (not source_revision_changed and preferred_proposal is not None
                and self.chunk_proposal
                and self.guarded_boundary_source_signature == source_signature):
            proposed_units, proposed_end = self.chunk_proposal
            proposed_prefix = agreed[:len(proposed_units)]
            if (proposed_prefix
                    and tuple(normalized(w.text) for w in proposed_prefix) == proposed_units
                    and abs(proposed_prefix[-1].end-proposed_end) <= .35
                    and self._stable_prefix_matches(proposed_prefix, agreed)):
                await self.event('source_chunk_admission',
                    source_revision=self.boundary_source_revision,
                    candidate_id=candidate_id, admitted=False,
                    reason='reuse_guarded_boundary_same_source_revision',
                    source_uncertainty_seconds=round(max(0., hypothesis[-1].end-
                        (self.phrase_start if self.phrase_start is not None else agreed[0].start)), 3))
                return self.controller.candidate(agreed,
                    proposed_end=len(proposed_prefix), preferred_seconds=batching_seconds,
                    source_language=language)
        uncommitted_start = (self.phrase_start if self.phrase_start is not None
                             else agreed[0].start)
        # A stable transcript can stop growing while VAD confirms continued
        # speech. Use that speech edge for urgency and the rate window; the
        # boundary model still sees only the stable source words below.
        speech_frontier = max(agreed[-1].end, min(media_now, self.last_speech))
        source_age = max(0.0, speech_frontier-uncommitted_start)
        provisional_source_age = max(0.0, hypothesis[-1].end-uncommitted_start)
        ready_speech_seconds = self.ready_speech_audio_demand()
        admission = self.boundary_admission.admit(
            signature=source_signature,
            source_units=source_units, candidate_id=candidate_id,
            source_time=agreed[-1].end, source_age_seconds=source_age,
            source_budget_seconds=self.controller.source_budget,
            asr_queue_depth=self.audio_queue.qsize(),
            asr_queue_capacity=self.audio_queue.maxsize,
            tts_inflight=bool(self.processing_pending),
            phrase_queue_depth=self.phrase_queue.qsize(),
            ready_speech_seconds=(ready_speech_seconds
                if self.boundary_audio_backpressure else 0.0),
            previous_pair_seconds=self.boundary_admission.last_pair_seconds,
            rate_window_time_seconds=speech_frontier)
        request_id = uuid.uuid4().hex
        admission_metrics = {
            'request_id':request_id, 'source_revision':self.boundary_source_revision,
            'candidate_id':candidate_id, 'admitted':admission.admitted,
            'reason':admission.reason, 'urgent':admission.urgent,
            'source_uncertainty_seconds':round(source_age, 3),
            'provisional_source_age_seconds':round(provisional_source_age, 3),
            'vad_confirmed_speech_frontier_seconds':round(speech_frontier, 3),
            'source_budget_seconds':self.controller.source_budget,
            'source_advance_seconds':round(admission.source_advance_seconds, 3),
            'changed_source_units':admission.changed_units,
            'minimum_source_gap_seconds':round(admission.minimum_source_gap_seconds, 3),
            'urgent_source_gap_seconds':round(admission.urgent_source_gap_seconds, 3),
            'requests_per_source_minute':admission.requests_per_source_minute,
            'asr_queue_depth':self.audio_queue.qsize(),
            'phrase_queue_depth':self.phrase_queue.qsize(),
            'tts_inflight_count':len(self.processing_pending),
            'ready_speech_audio_seconds':round(ready_speech_seconds, 3),
        }
        if not admission.admitted:
            await self.event('source_chunk_admission', **admission_metrics)
            # An exact revision was already selected and passed the source
            # dependency guard. Reuse that checked cut while the surrounding
            # source evidence is unchanged; do not ask Qwen to repeat itself.
            if admission.reason == 'unchanged_source_revision' and self.chunk_proposal:
                proposed_units, proposed_end = self.chunk_proposal
                proposed_prefix = agreed[:len(proposed_units)]
                if (proposed_prefix
                        and tuple(normalized(w.text) for w in proposed_prefix) == proposed_units
                        and abs(proposed_prefix[-1].end-proposed_end) <= .35
                        and self._stable_prefix_matches(proposed_prefix, agreed)):
                    return self.controller.candidate(agreed,
                        proposed_end=len(proposed_prefix), preferred_seconds=batching_seconds,
                        source_language=language)
            return candidate

        await self.event('source_chunk_admission', **admission_metrics)
        selector_started = time.perf_counter()
        request_source_revision = self.boundary_source_revision
        self.boundary_selector_calls += 1
        try:
            proposal = await asyncio.to_thread(self.models.select_boundary,
                units=[w.surface if w.surface is not None else (' ' if i else '')+w.text
                       for i,w in enumerate(hypothesis)], eligible_ends=eligible,
                preferred_end_unit=preferred)
        except (RuntimeError, ValueError, TypeError, KeyError, TimeoutError) as exc:
            elapsed = max(0.0, time.perf_counter()-selector_started)
            self.boundary_admission.record_pair_seconds(elapsed)
            await self.event('source_chunk_request_failed', request_id=request_id,
                source_revision=request_source_revision, candidate_id=candidate_id,
                stage='selector', error_type=type(exc).__name__,
                model_wait_seconds=elapsed, source_uncertainty_seconds=round(source_age, 3))
            return candidate
        if request_source_revision != self.boundary_source_revision:
            await self.event('source_chunk_result_discarded', request_id=request_id,
                source_revision=request_source_revision,
                current_source_revision=self.boundary_source_revision,
                candidate_id=candidate_id, reason='source_revision_superseded')
            return candidate
        end = proposal['end_unit']
        if type(end) is not int or end not in [0, *eligible]:
            raise ProtocolError('Invalid contextual source boundary; no source was consumed')
        await self.event('source_chunk_proposal', end_unit=end,
            source_text=source_text(agreed[:end]) if end else '',
            following_text=source_text(hypothesis[end:]) if end else '',
            metrics=proposal['metrics'], semantic_correctness_certified=False,
            request_id=request_id, source_revision=request_source_revision,
            candidate_id=candidate_id,
            requests_per_source_minute=admission.requests_per_source_minute)
        if not end:
            self.boundary_admission.record_pair_seconds(
                max(0.0, time.perf_counter()-selector_started))
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            return candidate
        prefix = agreed[:end]
        # This deterministic observation takes precedence over a model's
        # boundary opinion: the following unit belongs to the number. Avoid
        # spending a second GPU request on a cut that cannot be committed.
        if quantity_continues(prefix, hypothesis):
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            await self.event('source_chunk_guard', source_end=prefix[-1].end,
                end_unit=end, crosses_dependency=True, accepted=False,
                error_type=None, reason='quantity_unit_continuation', metrics={},
                semantic_correctness_certified=False, request_id=request_id,
                source_revision=request_source_revision, candidate_id=candidate_id)
            remember_rejected_boundary(end, definitive=True)
            self.boundary_admission.record_pair_seconds(
                max(0.0, time.perf_counter()-selector_started))
            return candidate
        if language == 'en' and incomplete_ending(source_text(prefix)):
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            await self.event('source_chunk_guard', source_end=prefix[-1].end,
                end_unit=end, crosses_dependency=True, accepted=False,
                error_type=None, reason='incomplete_english_internal_boundary', metrics={},
                semantic_correctness_certified=False, request_id=request_id,
                source_revision=request_source_revision, candidate_id=candidate_id)
            remember_rejected_boundary(end, definitive=True)
            self.boundary_admission.record_pair_seconds(
                max(0.0, time.perf_counter()-selector_started))
            return candidate
        guard_method = getattr(self.models, 'assess_source_boundary', None)
        guard = None
        error_type = None
        if callable(guard_method):
            self.boundary_guard_calls += 1
            guard_started = time.perf_counter()
            try:
                guard = await asyncio.to_thread(guard_method, prefix=source_text(prefix),
                                                continuation=source_text(hypothesis[end:]))
            except (RuntimeError, ValueError, TypeError, KeyError, TimeoutError) as exc:
                error_type = type(exc).__name__
            guard_seconds = max(0.0, time.perf_counter()-guard_started)
        else:
            guard_seconds = 0.0
        self.boundary_admission.record_pair_seconds(
            max(0.0, time.perf_counter()-selector_started))
        accepted = isinstance(guard, dict) and guard.get('crosses_dependency') is False
        await self.event('source_chunk_guard', source_end=prefix[-1].end,
            end_unit=end, crosses_dependency=(guard.get('crosses_dependency')
                                            if isinstance(guard, dict) else None),
            accepted=accepted, error_type=error_type,
            metrics=(guard.get('metrics', {}) if isinstance(guard, dict) else {}),
            semantic_correctness_certified=False, request_id=request_id,
            source_revision=request_source_revision, candidate_id=candidate_id,
            selector_and_guard_seconds=round(max(0.0, time.perf_counter()-selector_started), 3),
            guard_seconds=round(guard_seconds, 3),
            requests_per_source_minute=admission.requests_per_source_minute)
        if request_source_revision != self.boundary_source_revision:
            await self.event('source_chunk_result_discarded', request_id=request_id,
                source_revision=request_source_revision,
                current_source_revision=self.boundary_source_revision,
                candidate_id=candidate_id, stage='guard',
                reason='source_revision_superseded')
            return candidate
        if not accepted:
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            remember_rejected_boundary(end)
            return candidate
        self.rejected_chunk_proposals.pop(proposal_key(end), None)
        previous_proposal = self.chunk_proposal
        previous_guarded_signature = self.guarded_boundary_source_signature
        selected_units = tuple(normalized(w.text) for w in prefix)
        same_guarded_cut = (previous_proposal is not None
            and previous_proposal[0] == selected_units
            and abs(previous_proposal[1]-prefix[-1].end) <= .35)
        if same_guarded_cut and previous_guarded_signature != source_signature:
            self.chunk_proposal_observations += 1
        else:
            self.chunk_proposal_observations = 1
        self.chunk_proposal = (selected_units, prefix[-1].end)
        self.guarded_boundary_source_signature = source_signature
        return self.controller.candidate(agreed, proposed_end=end, preferred_seconds=batching_seconds,
                                         source_language=language)

    @staticmethod
    def _stable_prefix_matches(prefix, stable):
        return (bool(prefix) and len(prefix) <= len(stable)
            and source_text(prefix) == source_text(stable[:len(prefix)]) and all(
            normalized(a.text) == normalized(b.text)
            and abs(a.end-b.end) <= .35 and abs(a.start-b.start) <= .5
            for a, b in zip(prefix, stable)))

    @classmethod
    def _approved_read_wait_write_end(cls, words, eligible_ends, proposal):
        """Map a guarded contextual cut into the current unowned RWW snapshot."""
        if not proposal:
            return None
        signature, source_end = proposal
        prefix = tuple(words[:len(signature)])
        if (not prefix or tuple(normalized(word.text) for word in prefix) != signature
                or not cls._stable_prefix_matches(prefix, words)
                or abs(prefix[-1].end-source_end) > .35):
            return None
        end_unit = len(prefix)
        return end_unit if end_unit in eligible_ends else None

    def _read_wait_write_context_for_cut(self, cut_key, words, end_unit,
                                         observation_id):
        """Freeze every time-varying Hy-MT2 input for one guarded source cut.

        ASR surfaces and timestamps can be reformatted slightly while the
        lexical owner stays stable. If those values, or playback history,
        change between growing snapshots, deterministic translation may still
        produce different targets and never satisfy READ/WAIT/WRITE agreement.
        """
        if self.read_wait_write_context_cut_key != cut_key:
            prefix = tuple(words[:end_unit])
            if not prefix:
                raise ValueError('A guarded READ/WAIT/WRITE cut requires source words')
            self.read_wait_write_context_cut_key = cut_key
            self.read_wait_write_context_source_text = source_text(prefix)
            self.read_wait_write_context_duration_seconds = max(
                .2, prefix[-1].end-prefix[0].start)
            self.read_wait_write_context_previous_source = (
                ' '.join(self.source_history)[-800:])
            self.read_wait_write_context_previous_translation = (
                ' '.join(self.target_translation_history)[-800:])
            self.read_wait_write_context_text = source_text(words[end_unit:])
            self.read_wait_write_context_origin_observation_id = observation_id
        return {
            'source_text':self.read_wait_write_context_source_text,
            'duration_seconds':self.read_wait_write_context_duration_seconds,
            'previous_source':self.read_wait_write_context_previous_source or '',
            'previous_translation':self.read_wait_write_context_previous_translation or '',
            'following_text':self.read_wait_write_context_text or '',
            'origin_observation_id':self.read_wait_write_context_origin_observation_id,
        }

    def _clear_read_wait_write_context(self):
        self.read_wait_write_context_cut_key = None
        self.read_wait_write_context_text = None
        self.read_wait_write_context_source_text = None
        self.read_wait_write_context_previous_source = None
        self.read_wait_write_context_previous_translation = None
        self.read_wait_write_context_duration_seconds = None
        self.read_wait_write_context_origin_observation_id = None

    async def _translate_stable_prefix(self, words, language, previous_text,
                                      source_asr_completed_at, following_text=''):
        started = time.perf_counter()
        try:
            draft = await asyncio.to_thread(self.models.translate_draft,
                source_text=source_text(words), source_language=language,
                following_text=following_text, previous_text=previous_text,
                duration=max(.2, words[-1].end-words[0].start),
                include_following_context=True)
            return {'words': words, 'draft': draft, 'started_at': started,
                    'completed_at': time.perf_counter(), 'error_type': None,
                    'source_asr_completed_at': source_asr_completed_at,
                    'source_language': language, 'following_text': following_text}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {'words': words, 'draft': None, 'started_at': started,
                    'completed_at': time.perf_counter(), 'error_type': type(exc).__name__,
                    'source_asr_completed_at': source_asr_completed_at,
                    'source_language': language}

    async def _run_read_wait_write_query(self, words, language, eligible_ends,
                                        preferred_end_unit, previous_source,
                                        previous_translation, observation_id,
                                        source_generation, asr_completed_at,
                                        approved_end_unit=None,
                                        approved_following_text=None,
                                        approved_source_text=None,
                                        approved_duration_seconds=None,
                                        approved_context_origin_observation_id=None):
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(self.models.propose_simultaneous,
                words=words, source_language=language,
                eligible_ends=eligible_ends,
                preferred_end_unit=preferred_end_unit,
                previous_source=previous_source,
                previous_translation=previous_translation,
                approved_end_unit=approved_end_unit,
                approved_following_text=approved_following_text,
                approved_source_text=approved_source_text,
                approved_duration_seconds=approved_duration_seconds)
            return {'words':words, 'language':language, 'result':result,
                'eligible_ends':tuple(eligible_ends),
                'approved_end_unit':approved_end_unit,
                'approved_following_text':approved_following_text,
                'approved_source_text':approved_source_text,
                'approved_duration_seconds':approved_duration_seconds,
                'approved_context_origin_observation_id':
                    approved_context_origin_observation_id,
                'observation_id':observation_id, 'source_generation':source_generation,
                'asr_completed_at':asr_completed_at,
                'started_at':started, 'completed_at':time.perf_counter(),
                'error_type':None}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {'words':words, 'language':language,
                'eligible_ends':tuple(eligible_ends),
                'approved_end_unit':approved_end_unit,
                'approved_context_origin_observation_id':
                    approved_context_origin_observation_id,
                'observation_id':observation_id, 'source_generation':source_generation,
                'asr_completed_at':asr_completed_at,
                'started_at':started, 'completed_at':time.perf_counter(),
                'error_type':type(exc).__name__}

    def _read_wait_write_commit_adapter(self, commit, metrics, asr_completed_at):
        source_words = tuple(commit.source_words)
        target_text = commit.verified.translation
        # A guarded RWW candidate carries the underlying Hy-MT2 draft timing;
        # older/test providers may only supply the enclosing proposal timing.
        # Normalize both into the same draft contract used by endpoint MT.
        draft_metrics = dict(metrics.get('translation_draft_metrics') or {})
        draft_metrics.setdefault('translation_seconds', metrics.get(
            'proposal_seconds', max(0.0, metrics.get('proposal_completed_at', 0.0)
                                    - metrics.get('proposal_started_at', 0.0))))
        draft_metrics.setdefault('translation_started_at',
            metrics.get('proposal_started_at'))
        draft_metrics.setdefault('translation_completed_at',
            metrics.get('proposal_completed_at'))
        draft_metrics.update({
            'translation_backend':'hymt2-1.8b',
            'simultaneous_semantic_check_started_at':metrics['candidate_check_started_at'],
            'simultaneous_semantic_check_completed_at':metrics['candidate_check_completed_at'],
            'read_wait_write':metrics,
            'source_boundary_evidence':commit.evidence,
            'agreement_is_not_semantic_correctness':True,
        })
        draft = {'text':target_text, 'metrics':draft_metrics}
        return SimultaneousCommit(source_words,
            source_text(source_words), target_text, draft,
            source_words[0].start, source_words[-1].end,
            commit.evidence, asr_completed_at)

    @staticmethod
    def _source_commit_policy(target_commit):
        if target_commit is None:
            return 'source_endpoint_policy'
        evidence = getattr(target_commit, 'evidence', None)
        if isinstance(evidence, dict) and evidence.get('policy'):
            return evidence['policy']
        return 'confirmed_source_boundary_prefetch'

    async def _advance_read_wait_write(self, stable_words, language, media_now, *,
                                       allow_query, asr_completed_at,
                                       allow_approved_query=False):
        """Advance one bounded learned early-translation proposal at a time."""
        started = time.perf_counter()
        if not self.read_wait_write_enabled:
            return None, None, 0.0
        stable_words = tuple(stable_words)
        task = self.read_wait_write_task
        if task is not None and task.done():
            self.read_wait_write_task = None
            self.read_wait_write_task_approved_end = None
            self.read_wait_write_task_cut_signature = None
            try:
                outcome = task.result()
            except asyncio.CancelledError:
                outcome = None
            except Exception as exc:
                outcome = {'error_type':type(exc).__name__,
                           'started_at':time.perf_counter(),
                           'completed_at':time.perf_counter()}
            if outcome is not None:
                stale = (outcome.get('source_generation') != self.read_wait_write_generation
                    or outcome.get('language') != language
                    or not self.read_wait_write._same_prefix(
                        outcome.get('words', ()),
                        self.read_wait_write.unowned_words(stable_words)))
                if outcome.get('error_type'):
                    self.read_wait_write_rejections += 1
                    await self.event('read_wait_write_query_error',
                        observation_id=outcome.get('observation_id'),
                        error_type=outcome['error_type'],
                        rejection_count=self.read_wait_write_rejections)
                    self.read_wait_write.reset_candidate()
                elif stale:
                    self.read_wait_write_rejections += 1
                    self.read_wait_write.reset_candidate()
                    await self.event('read_wait_write_result_discarded',
                        observation_id=outcome.get('observation_id'),
                        reason='source_revision_or_ownership_changed',
                        rejection_count=self.read_wait_write_rejections)
                else:
                    try:
                        result = outcome['result']
                        raw = result.get('raw') if isinstance(result, dict) else None
                        proposal = Proposal.parse(raw, len(outcome['words']))
                        verified = self.read_wait_write.observe_proposal(raw,
                            outcome['words'], media_now=media_now,
                            observation_id=outcome['observation_id'],
                            eligible_ends=outcome['eligible_ends'])
                        proposal_metrics = result.get('metrics', {})
                        await self.event('read_wait_write_proposal',
                            observation_id=outcome['observation_id'],
                            source_word_count=len(outcome['words']),
                            status=proposal.status, proposed_end_unit=proposal.end_unit,
                            approved_end_unit=outcome.get('approved_end_unit'),
                            proposal_seconds=proposal_metrics.get('proposal_seconds'),
                            proposal_strategy=proposal_metrics.get('proposal_strategy',
                                'hymt2_read_wait_write'),
                            inference_scheduler_wait_seconds=proposal_metrics.get(
                                'inference_scheduler_wait_seconds'),
                            source_start=(outcome['words'][0].start if outcome['words'] else None),
                            source_end=(outcome['words'][-1].end if outcome['words'] else None),
                            repeated_target_verified=verified is not None,
                            right_context_policy=proposal_metrics.get('right_context_policy'),
                            translation_prompt_context_policy=(
                                'frozen_full_approved_cut_snapshot'
                                if outcome.get('approved_source_text') is not None
                                   and outcome.get('approved_duration_seconds') is not None
                                   and outcome.get('approved_following_text') is not None
                                else proposal_metrics.get(
                                    'translation_prompt_context_policy',
                                    'current_committed_context')),
                            approved_context_origin_observation_id=outcome.get(
                                'approved_context_origin_observation_id'),
                            approved_duration_seconds=outcome.get(
                                'approved_duration_seconds'),
                            approved_source_text_char_count=(len(outcome['approved_source_text'])
                                if outcome.get('approved_source_text') is not None else None),
                            approved_right_context_char_count=(len(outcome['approved_following_text'])
                                if outcome.get('approved_following_text') is not None else None),
                            target_text_emitted=False)
                        if verified is not None:
                            if self.phrase_queue.full() or self.overload_drain_requested:
                                self.read_wait_write.reset_candidate()
                                self.read_wait_write_rejections += 1
                            else:
                                current_words = tuple(self.read_wait_write.unowned_words(stable_words))
                                owner_words = current_words[:len(verified.source)]
                                continuation = current_words[len(verified.source):]
                                candidate_started = time.perf_counter()
                                checked = await asyncio.to_thread(
                                    self.models.assess_simultaneous_candidate,
                                    prefix=source_text(owner_words),
                                    continuation=source_text(continuation),
                                    target_text=verified.translation)
                                candidate_completed = time.perf_counter()
                                check_result = checked.get('result') if isinstance(checked, dict) else None
                                provider = ((checked.get('metrics') or {}).get('provider')
                                    if isinstance(checked, dict) else None)
                                owner_text = source_text(owner_words)
                                owner_digest = hashlib.sha256(owner_text.encode('utf-8')).hexdigest()
                                target_digest = hashlib.sha256(
                                    verified.translation.encode('utf-8')).hexdigest()
                                boundary_check = {
                                    'observation_id':outcome['observation_id'],
                                    'end_unit':len(verified.source),
                                    'crosses_dependency':(check_result.get('crosses_dependency')
                                        if isinstance(check_result, dict) else None),
                                    'provider':provider or ''}
                                translation_check = {
                                    'observation_id':outcome['observation_id'],
                                    'end_unit':len(verified.source),
                                    'source_sha256':owner_digest,
                                    'target_sha256':target_digest,
                                    'supported':(check_result.get('supported')
                                        if isinstance(check_result, dict) else None),
                                    'provider':provider or ''}
                                commit = self.read_wait_write.verify_candidate(verified,
                                    stable_words,
                                    observation_id=outcome['observation_id'],
                                    boundary_check=boundary_check,
                                    translation_check=translation_check)
                                if commit is not None:
                                    metrics = {
                                        'proposal_started_at':outcome['started_at'],
                                        'proposal_completed_at':outcome['completed_at'],
                                        'proposal_seconds':max(0.0,
                                            outcome['completed_at']-outcome['started_at']),
                                        'candidate_check_started_at':candidate_started,
                                        'candidate_check_completed_at':candidate_completed,
                                        'candidate_check_seconds':max(0.0,
                                            candidate_completed-candidate_started),
                                        'candidate_check_metrics':(checked.get('metrics', {})
                                            if isinstance(checked, dict) else {}),
                                        'translation_backend':'hymt2-1.8b',
                                        'semantic_checker':provider,
                                    }
                                    self.pending_read_wait_write_commit = commit
                                    self.pending_read_wait_write_metrics = metrics
                                else:
                                    self.read_wait_write_rejections += 1
                                await self.event('read_wait_write_candidate_check',
                                    observation_id=outcome['observation_id'],
                                    source_end=owner_words[-1].end if owner_words else None,
                                    crosses_dependency=boundary_check['crosses_dependency'],
                                    translation_supported=translation_check['supported'],
                                    accepted=commit is not None,
                                    checker_seconds=max(0.0,candidate_completed-candidate_started),
                                    rejection_count=self.read_wait_write_rejections,
                                    semantic_correctness_certified=False)
                    except (InvalidProposal, KeyError, TypeError, ValueError, RuntimeError) as exc:
                        self.read_wait_write_rejections += 1
                        self.read_wait_write.reset_candidate()
                        await self.event('read_wait_write_proposal_rejected',
                            observation_id=outcome.get('observation_id'),
                            error_type=type(exc).__name__,
                            rejection_count=self.read_wait_write_rejections)

        pending = self.pending_read_wait_write_commit
        if pending is not None:
            owner = tuple(pending.source_words)
            if (self.read_wait_write._same_prefix(owner,
                    self.read_wait_write.unowned_words(stable_words))
                    and not self.phrase_queue.full()
                    and not self.overload_drain_requested):
                metrics = self.pending_read_wait_write_metrics or {}
                self.pending_read_wait_write_commit = None
                self.pending_read_wait_write_metrics = None
                return self._read_wait_write_commit_adapter(pending, metrics,
                    asr_completed_at), pending, max(0.0, time.perf_counter()-started)
            if not self.read_wait_write._same_prefix(owner,
                    self.read_wait_write.unowned_words(stable_words)):
                self.read_wait_write.reset_candidate()
                self.pending_read_wait_write_commit = None
                self.pending_read_wait_write_metrics = None

        if ((allow_query or allow_approved_query) and self.read_wait_write_task is None
                and self.pending_read_wait_write_commit is None
                and not self.phrase_queue.full() and not self.overload_drain_requested
                and self.audio_queue.qsize() < 50):
            now = time.perf_counter()
            if self.read_wait_write.should_query(stable_words, now, media_now=media_now):
                eligible = self.read_wait_write.eligible_ends(stable_words, media_now)
                if eligible:
                    query_words = tuple(self.read_wait_write.unowned_words(stable_words))
                    preferred = self.read_wait_write.preferred_end(query_words, eligible)
                    approved_end = self._approved_read_wait_write_end(
                        query_words, eligible, self.chunk_proposal)
                    if allow_query or approved_end is not None:
                        observation_id = self.read_wait_write.mark_query(query_words, now)
                        self.read_wait_write_queries += 1
                        previous_source = ' '.join(self.source_history)[-800:]
                        previous_translation = ' '.join(self.target_translation_history)[-800:]
                        approved_following_text = None
                        approved_source_text = None
                        approved_duration_seconds = None
                        approved_context_origin_observation_id = None
                        if approved_end is not None and self.chunk_proposal:
                            cut_key = (tuple(self.chunk_proposal[0]),
                                       round(self.chunk_proposal[1] / .35))
                            frozen = self._read_wait_write_context_for_cut(
                                cut_key, query_words, approved_end, observation_id)
                            previous_source = frozen['previous_source']
                            previous_translation = frozen['previous_translation']
                            approved_following_text = frozen['following_text']
                            approved_source_text = frozen['source_text']
                            approved_duration_seconds = frozen['duration_seconds']
                            approved_context_origin_observation_id = frozen[
                                'origin_observation_id']
                        self.read_wait_write_task_approved_end = approved_end
                        self.read_wait_write_task_cut_signature = (
                            self.chunk_proposal[0] if approved_end is not None
                            and self.chunk_proposal else None)
                        self.read_wait_write_task = asyncio.create_task(
                            self._run_read_wait_write_query(query_words, language,
                                eligible, preferred, previous_source, previous_translation,
                                observation_id, self.read_wait_write_generation,
                                asr_completed_at, approved_end, approved_following_text,
                                approved_source_text, approved_duration_seconds,
                                approved_context_origin_observation_id))
        return None, None, max(0.0, time.perf_counter()-started)

    async def _advance_simultaneous(self, stable, language, media_now, *, allow_query,
                                   asr_completed_at, drain_pending=False, drain_all_pending=False,
                                   boundary_kind=None, right_context_seconds=0.,
                                   following_text=''):
        """Read one exact source chunk ahead of its ordinary endpoint commit."""
        started = time.perf_counter()
        self.simultaneous.refresh_source(stable)
        task = self.target_translation_task
        task_matches_source = (self.target_translation_language == language
            and len(self.target_translation_words) == len(stable)
            and self._stable_prefix_matches(self.target_translation_words, stable))
        if ((drain_all_pending or (drain_pending and task_matches_source))
                and task is not None and not task.done()):
            # Finish must account for an already-dispatched source-chunk
            # translation before the endpoint can be finalized.
            await asyncio.shield(task)
        if task is not None and task.done():
            self.target_translation_task = None
            self.target_translation_words = ()
            self.target_translation_language = None
            try:
                outcome = task.result()
            except asyncio.CancelledError:
                outcome = None
            except Exception as exc:
                outcome = {'words': (), 'draft': None, 'started_at': time.perf_counter(),
                           'completed_at': time.perf_counter(), 'error_type': type(exc).__name__}
            if outcome is not None:
                words = outcome['words']
                if outcome['error_type']:
                    self.target_translation_failures += 1
                    await self.event('target_hypothesis_error',
                        source_word_count=len(words),
                        translation_seconds=max(0.0, outcome['completed_at']-outcome['started_at']),
                        failure_count=self.target_translation_failures,
                        error_type=outcome['error_type'])
                elif (outcome['source_language'] == language and
                      len(words) == len(stable) and self._stable_prefix_matches(words, stable) and
                      words[0].start > self.agreement.committed_end + .025):
                    draft = outcome['draft']
                    self.simultaneous.observe(words, draft, outcome['completed_at'],
                        source_asr_completed_at=outcome['source_asr_completed_at'])
                    metrics = draft.get('metrics', {})
                    await self.event('target_draft_prefetch',
                        source_start=words[0].start, source_end=words[-1].end,
                        source_word_count=len(words), target_character_count=len(draft['text']),
                        translation_seconds=max(0.0, outcome['completed_at']-outcome['started_at']),
                        translation_inference_seconds=metrics.get('translator_inference_seconds'),
                        translation_queue_wait_seconds=metrics.get('model_worker_pool_wait_seconds'),
                        translation_worker_seconds=metrics.get('model_worker_execution_seconds'),
                        source_asr_completed_at=outcome['source_asr_completed_at'],
                        observed_at=outcome['completed_at'],
                        source_boundary_kind=boundary_kind,
                        right_context_seconds=round(max(0.0, right_context_seconds), 3),
                        hypothesis_count=len(self.simultaneous.hypotheses),
                        target_text_emitted=False)
                else:
                    self.simultaneous.rejected_revision_count += 1
                    await self.event('target_hypothesis_invalidated',
                        source_word_count=len(words),
                        invalidated_count=self.simultaneous.rejected_revision_count)

        proposal = self.simultaneous.source_boundary_proposal(stable,
            boundary_kind=boundary_kind, right_context_seconds=right_context_seconds)
        model_supports_translation = callable(getattr(type(self.models), 'translate_draft', None))
        context_ready = boundary_kind in {'clause', 'sentence', 'pause', 'eos'}
        if (allow_query and context_ready and proposal is None and self.target_translation_task is None
                and model_supports_translation and not self.phrase_queue.full()
                and not self.overload_drain_requested
                and self.audio_queue.qsize() < 50
                and self.simultaneous.should_query(stable, media_now)):
            words = tuple(stable)
            self.simultaneous.mark_query(words, media_now)
            previous = ' '.join(self.source_history)[-800:]
            self.target_translation_task = asyncio.create_task(
                self._translate_stable_prefix(words, language, previous, asr_completed_at,
                                              following_text=following_text))
            self.target_translation_words = words
            self.target_translation_language = language
        return proposal, max(0.0, time.perf_counter()-started)

    async def _request_overload_drain(self, reason):
        if not self.overload_drain_requested:
            self.overload_drain_requested = True
            cutoff = self.clock.end
            unconfirmed_start = self.phrase_start
            details = {'capture_cutoff_source_seconds': cutoff}
            cutoff_notice = ('Capture stop requested at source {:.2f}s; already accepted audio, including the bounded recorder flush, will be analyzed.'
                             .format(cutoff) if cutoff is not None else
                             'Capture stop requested; already accepted audio, including the bounded recorder flush, will be analyzed.')
            if (unconfirmed_start is not None and cutoff is not None and
                    cutoff >= unconfirmed_start):
                duration = cutoff - unconfirmed_start
                details.update(unconfirmed_source_start_seconds=unconfirmed_start,
                    unconfirmed_source_end_seconds=cutoff,
                    unconfirmed_source_duration_seconds=duration)
                cutoff_notice += (' Unconfirmed captured source span {:.2f}–{:.2f}s may remain untranslated.'
                                  .format(unconfirmed_start, cutoff))
            if self.unprocessed_input_packet_count:
                details.update(unprocessed_input_start_seconds=self.unprocessed_input_start,
                    unprocessed_input_end_seconds=self.unprocessed_input_end,
                    unprocessed_input_packet_count=self.unprocessed_input_packet_count,
                    unprocessed_input_duration_seconds=max(0.0,
                        self.unprocessed_input_end - self.unprocessed_input_start))
            await self.event('warning', code='processing_overload_drain', reason=reason,
                warning='Speech processing reached a bounded safety limit. ' + cutoff_notice +
                        ' Confirmed phrases will drain; any final unconfirmed speech remains explicitly unresolved.',
                **details)

    async def _decode(self, media_now, *, end_of_stream=False, request_metrics=None):
        if self.overload_drain_requested and not end_of_stream:
            return
        if self.phrase_start is None:
            return
        # Word alignments are estimates. Retain the same short acoustic
        # context after a commit as before first speech; cutting exactly at
        # an ASR word boundary can remove the next word's consonant onset.
        asr_start = self._asr_context_start()
        audio = self._slice(asr_start, media_now)
        # Ingestion continues during inference. Only acoustic evidence already
        # present in this immutable audio snapshot can confirm its endpoint.
        last_speech = min(self.last_speech, media_now)
        pauses = tuple((start, min(end, media_now)) for start, end in self.pauses
                       if start <= media_now)
        if len(audio) < 16000:
            return
        decode_started = time.perf_counter()
        started = decode_started
        result = await asyncio.to_thread(self.models.recognize, audio, asr_start, self.source_language)
        asr_completed_at = time.perf_counter()
        actual_asr_seconds = asr_completed_at-started
        model_runtime = getattr(result, 'runtime_metrics', {}) or {}
        worker_timing = getattr(result, 'worker_timing', {}) or {}
        if self.source_language is None and self.observed_language not in (None, result.language):
            # The first subsecond auto-detection can be wrong. Do not force
            # that language on every future window or retain its confirmed
            # words after the acoustic language evidence changes.
            self.agreement.reset_uncommitted()
            self.simultaneous.reset_uncommitted()
            self.read_wait_write_generation += 1
            self.read_wait_write.advance_committed_frontier(self.agreement.committed_end)
            self.pending_read_wait_write_commit = None
            self.pending_read_wait_write_metrics = None
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            self.read_wait_write_endpoint_hold_cut = None
            self.read_wait_write_endpoint_hold_count = 0
            self._clear_read_wait_write_context()
            self.rejected_chunk_proposals.clear()
            await self.event('source_language_changed', previous=self.observed_language,
                             current=result.language, source_end=media_now)
        self.observed_language = result.language
        agreement_started = time.perf_counter()
        agreed = self.agreement.update(result.words)
        agreement_completed_at = time.perf_counter()
        hypothesis = self.agreement.hypothesis
        boundary_started = time.perf_counter()
        scores = await asyncio.to_thread(self.boundary.predict, source_text(hypothesis))
        boundary_completed_at = time.perf_counter()
        self.controller.update(hypothesis, scores)
        # Ending capture can leave a complete confirmed sentence followed by
        # an unfinished one. Keep normal endpoint selection at EOS too; the
        # incomplete tail must not veto the preceding complete sentence.
        # A busy synthesizer should receive coherent source batches rather
        # than a growing queue of tiny sentences, each paying MT/QA overhead.
        # Only actual learned/contextual boundaries can end a batch. A quiet
        # confirmed turn still flushes immediately, including short replies.
        endpoint_started = time.perf_counter()
        batching_seconds = ({'quality':6., 'balanced':4., 'low_latency':3.}[self.controller.mode]
                            if self.processing_pending and not end_of_stream else 0.)
        stable = self.controller.candidate(agreed, pauses, preferred_seconds=batching_seconds,
                                           source_language=result.language)
        stable = await self._contextual_candidate(agreed, hypothesis, media_now, stable,
                                                   batching_seconds=batching_seconds)
        pause_boundary = self.controller.pause_confirms(stable, self.agreement.hypothesis, pauses)
        clause_boundary = self.controller.clause_confirms(stable, agreed)
        text = source_text(stable)
        signature = ' '.join(normalized(word.text) for word in stable)
        if signature != self.stable_text:
            self.stable_text, self.stable_since = signature, media_now
        # Processing age starts at commit, not at the utterance's source
        # position. A complex 10-second phrase may only have been dispatched
        # 100 ms ago; treating it as 10 seconds of compute causes false stops.
        pending_seconds = max((time.perf_counter() - start for start in self.processing_pending.values()), default=0.0)
        input_queue_seconds = max(0.0, self.queued_seconds)
        ready_speech_seconds = self.ready_speech_audio_demand()
        decision = self.controller.decide(stable=stable, hypothesis=self.agreement.hypothesis,
            media_now=media_now, last_speech=last_speech, phrase_start=self.phrase_start,
            stable_since=self.stable_since, pending_seconds=pending_seconds, end_of_stream=end_of_stream,
            input_queue_seconds=input_queue_seconds,
            ready_speech_seconds=ready_speech_seconds,
            confirmed_pause_boundary=pause_boundary, confirmed_clause_boundary=clause_boundary,
            source_language=result.language,
            confirmed_boundary_observations=(self.chunk_proposal_observations
                if self.chunk_proposal and self.guarded_boundary_source_signature ==
                    self.boundary_source_signature else 0),
            source_budget_seconds=self.controller.source_recovery_budget)
        decision_completed_at = time.perf_counter()

        sentence_boundary = bool(stable and re.search(r'[.!?。！？]$', text)
            and not re.search(r'(?:\.{2,}|…)$', text))
        if pause_boundary:
            boundary_kind = 'pause'
        elif sentence_boundary:
            boundary_kind = 'sentence'
        elif clause_boundary:
            boundary_kind = 'clause'
        elif end_of_stream and decision.action == 'commit':
            boundary_kind = 'eos'
        else:
            boundary_kind = None
        right_context_seconds = (max(0.0, agreed[-1].end-stable[-1].end)
                                 if stable and len(agreed) > len(stable) else 0.0)
        following_text = source_text(agreed[len(stable):]) if len(agreed) > len(stable) else ''

        # READ the exact source candidate, WAIT for its independently confirmed
        # source boundary and mode-sized right context, then WRITE only the
        # translation of that whole owned chunk. Endpointing still decides
        # when ownership transfers; speculative translations never consume it.
        allow_translation_query = (not end_of_stream and decision.action != 'commit'
            and pending_seconds < 4.0 and self.audio_queue.qsize() < 50)
        read_wait_write_commit = None
        if self.read_wait_write_enabled:
            allow_approved_query = (decision.action == 'commit' and not end_of_stream
                and pending_seconds < 4.0 and self.audio_queue.qsize() < 50)
            target_commit, read_wait_write_commit, simultaneous_controller_seconds = (
                await self._advance_read_wait_write(agreed, result.language, media_now,
                    allow_query=allow_translation_query,
                    asr_completed_at=asr_completed_at,
                    allow_approved_query=allow_approved_query))
            if (read_wait_write_commit is None and decision.action == 'commit'
                    and not end_of_stream and self.read_wait_write_task is not None
                    and self.read_wait_write_task_approved_end is not None
                    and self.chunk_proposal and self.read_wait_write_task_cut_signature ==
                        self.chunk_proposal[0]
                    and stable
                    and _same_source_endpoint(self.chunk_proposal[1], stable[-1].end)):
                cut_key = (tuple(self.chunk_proposal[0]),
                           round(self.chunk_proposal[1] / .35))
                if self.read_wait_write_endpoint_hold_cut != cut_key:
                    self.read_wait_write_endpoint_hold_cut = cut_key
                    self.read_wait_write_endpoint_hold_count = 0
                if self.read_wait_write_endpoint_hold_count < 2:
                    self.read_wait_write_endpoint_hold_count += 1
                    decision = Decision('wait', tuple(dict.fromkeys((*decision.reasons,
                        'awaiting_verified_incremental_target'))), decision.text,
                        decision.complexity, decision.waited_seconds)
                    await self.event('read_wait_write_endpoint_hold',
                        approved_end_unit=self.read_wait_write_task_approved_end,
                        source_end=self.chunk_proposal[1],
                        hold_count=self.read_wait_write_endpoint_hold_count,
                        maximum_hold_count=2,
                        reason='bounded_asr_turn_for_growing_target_observation',
                        observation_id=self.read_wait_write.next_observation_id)
            elif (read_wait_write_commit is None and decision.action == 'commit'
                    and not end_of_stream and self.read_wait_write_task is not None
                    and self.read_wait_write_task_approved_end is not None
                    and self.chunk_proposal and self.read_wait_write_task_cut_signature ==
                        self.chunk_proposal[0]
                    and stable
                    and not _same_source_endpoint(self.chunk_proposal[1], stable[-1].end)
                    and self.chunk_proposal[1] < stable[-1].end - .35):
                # A speculative proper-prefix translation may still be waiting
                # for its independent boundary/meaning checks. Once the normal
                # endpoint policy has confirmed the complete, later source
                # phrase, that prefix must not hold the whole phrase hostage:
                # the speculative result is invalidated by the commit below.
                # This avoids adding checker latency to a phrase whose safe
                # source ownership is already complete, without weakening the
                # checks required to commit the earlier prefix itself.
                await self.event('read_wait_write_prefix_not_blocking_endpoint',
                    speculative_source_end=self.chunk_proposal[1],
                    confirmed_source_end=stable[-1].end,
                    reason='complete_endpoint_ready_while_proper_prefix_check_pending',
                    observation_id=self.read_wait_write.next_observation_id)
            if read_wait_write_commit is not None:
                stable = tuple(read_wait_write_commit.source_words)
                text = source_text(stable)
                decision = Decision('commit', ('verified_incremental_translation',),
                    text, (), max(0.0, media_now-stable[-1].end))
                boundary_kind = 'incremental_candidate'
                right_context_seconds = max(0.0, agreed[-1].end-stable[-1].end)
                following_text = source_text(agreed[len(stable):]) if len(agreed) > len(stable) else ''
            else:
                target_commit, _ordinary_simultaneous_seconds = await self._advance_simultaneous(
                    # READ/WAIT/WRITE is allowed to speculate on an earlier
                    # target boundary, but it can legitimately return WAIT
                    # for most growing snapshots. Keep the ordinary exact-
                    # boundary translation prefetch active as a fallback so
                    # enabling RWW never forces every phrase to start MT only
                    # after source ownership commits. _advance_simultaneous
                    # still requires a confirmed contextual boundary and only
                    # reuses a draft for the exact committed source interval.
                    stable, result.language, media_now,
                    allow_query=allow_translation_query,
                    asr_completed_at=asr_completed_at,
                    drain_pending=(decision.action == 'commit'), drain_all_pending=end_of_stream,
                    boundary_kind=boundary_kind, right_context_seconds=right_context_seconds,
                    following_text=following_text)
                simultaneous_controller_seconds += _ordinary_simultaneous_seconds
        else:
            target_commit, simultaneous_controller_seconds = await self._advance_simultaneous(
                stable, result.language, media_now,
                allow_query=allow_translation_query,
                asr_completed_at=asr_completed_at,
                drain_pending=(decision.action == 'commit'), drain_all_pending=end_of_stream,
                boundary_kind=boundary_kind, right_context_seconds=right_context_seconds,
                following_text=following_text)
        committed_frontier = self.agreement.committed_end
        source_interval_overlap = (max(0.0, committed_frontier-stable[0].start)
            if stable and committed_frontier >= 0 else 0.0)
        source_interval_frontier_clip = self.agreement.frontier_clip_seconds
        if source_interval_overlap > 0.025:
            reasons = tuple(dict.fromkeys((*decision.reasons, 'source_interval_regression')))
            action = 'wait' if decision.action == 'commit' else decision.action
            decision = Decision(action, reasons, decision.text, decision.complexity,
                                decision.waited_seconds)
        # A transient full queue is not itself evidence of sustained overload.
        # Keep the source uncommitted and continue collecting it while the
        # existing finite queue drains. Measured input/ready-audio pressure
        # and the unchanged finite PCM and transport caps govern graceful stop.
        if decision.action == 'commit' and self.phrase_queue.full() and not end_of_stream:
            decision = Decision('wait', ('processing_queue_full_holding_source',),
                                decision.text, decision.complexity, decision.waited_seconds)
        if (decision.action == 'commit' and target_commit is not None
                and target_commit.source_text != decision.text):
            # The normal endpoint may normalize source spacing around a
            # quantity. Reuse a prefetch only if it translated the exact text
            # that downstream ownership will record.
            target_commit = None
            if read_wait_write_commit is not None:
                self.read_wait_write.reset_candidate()
                read_wait_write_commit = None
        if (decision.action == 'fallback' and not end_of_stream and
                'bounded_uncertainty_exhausted' in decision.reasons):
            # A phrase budget is a soft semantic deadline. Keep using the
            # already bounded PCM and input-queue capacity for final ASR /
            # boundary confirmation attempts. _ingest applies backpressure at
            # the unchanged PCM cap and requests a graceful drain if that
            # finite reserve cannot confirm a boundary.
            reason = ('rechecking_boundary_with_staged_source_pcm' if self.overflow_audio
                      else 'using_existing_pcm_context_reserve')
            decision = Decision('wait', tuple(dict.fromkeys((*decision.reasons, reason))),
                                decision.text, decision.complexity, decision.waited_seconds)
        self.asr_work_seconds = time.perf_counter() - started
        speech_end = min(last_speech, stable[-1].end) if stable else last_speech
        speech_end_monotonic, capture_clock_uncertainty_ms = self._capture_monotonic_time(speech_end)
        source_start_received_at = self._packet_received_at(stable[0].start) if stable else None
        source_end_received_at = self._packet_received_at(stable[-1].end) if stable else None
        await self.event('hypothesis', provisional=result.text, stable=text,
            boundary_evidence={'internal_score':scores[len(text)-1]
                if stable and clause_boundary else None,
                'final_input_score_ignored':True,
                'policy':'confirmed_source_contextual_mt_v2_with_source_boundary_prefetch'},
            confirmed_revision_count=self.agreement.revisions,
            observed_words=[asdict(word) for word in result.words],
            asr_window_start=asr_start, asr_window_seconds=len(audio)/32000,
            source_language=result.language, language_probability=result.language_probability,
            language_selection='manual' if self.source_language else 'rolling_auto',
            mean_asr_log_probability=result.mean_log_probability, source_end=media_now,
            asr_seconds=actual_asr_seconds, decode_cycle_seconds=self.asr_work_seconds,
            decode_coalescing_wait_seconds=(max(0.0, decode_started-request_metrics['requested_at'])
                if request_metrics and request_metrics.get('requested_at') is not None else None),
            decode_coalesced_request_count=(request_metrics.get('coalesced_count', 0) if request_metrics else 0),
            asr_runtime_metrics=model_runtime, asr_worker_timing=worker_timing,
            local_agreement_seconds=max(0.0, agreement_completed_at-agreement_started),
            boundary_model_seconds=max(0.0, boundary_completed_at-boundary_started),
            endpoint_decision_seconds=max(0.0, decision_completed_at-endpoint_started),
            local_agreement_started_at=agreement_started,
            local_agreement_completed_at=agreement_completed_at,
            boundary_model_started_at=boundary_started,
            boundary_model_completed_at=boundary_completed_at,
            endpoint_decision_started_at=endpoint_started,
            endpoint_decision_completed_at=decision_completed_at,
            simultaneous_controller_seconds=simultaneous_controller_seconds,
            prefetched_source_drafts_retained=len(self.simultaneous.hypotheses),
            source_draft_prefetch_failures=self.target_translation_failures,
            decode_started_at=decode_started, asr_completed_at=asr_completed_at,
            capture_clock_uncertainty_ms=capture_clock_uncertainty_ms,
            effective_asr_interval_seconds=self._effective_asr_interval(),
            synthesis_batch_target_seconds=batching_seconds,
            source_interval_overlap_seconds=source_interval_overlap,
            source_interval_frontier_clip_seconds=source_interval_frontier_clip,
            source_pcm_buffer_seconds=(len(self.buffer)+len(self.overflow_audio))/32000,
            staged_source_pcm_seconds=len(self.overflow_audio)/32000,
            input_queue_seconds=input_queue_seconds,
            ready_speech_audio_seconds=round(ready_speech_seconds, 3),
            server_unacknowledged_speech_audio_seconds=round(
                sum(self.pending_playback_audio_seconds.values()), 3),
            playback_audio_metrics_age_seconds=(
                round(self.playback_audio_metrics_age(), 3)
                if self.playback_audio_metrics_age() is not None else None),
            pending_render_age_seconds=round(pending_seconds, 3),
            pending_render_count=len(self.processing_pending),
            decision=asdict(decision),
            target_commit_evidence=(target_commit.evidence if target_commit is not None
                                    and decision.action == 'commit' else None),
            ingest_queue=self.audio_queue.qsize(), phrase_queue=self.phrase_queue.qsize())
        if decision.action == 'commit':
            identifier = uuid.uuid4().hex
            if self.phrase_queue.full() and not end_of_stream:
                # Preserve this source span and retry it on the next stable
                # ASR update after synthesis releases a queue slot. Queue
                # fullness alone is transient; the bounded source-PCM and
                # video-delay guards remain responsible for sustained overload.
                await self.event('phrase_queue_admission', action='holding_source',
                    reason='phrase_queue_full', phrase_queue_depth=self.phrase_queue.qsize(),
                    source_start=stable[0].start, source_end=stable[-1].end)
                return
            start, end = stable[0].start, stable[-1].end
            committed_vad_speech_seconds = self._commit_vad_speech(start, end)
            source_start_received_at = self._packet_received_at(start)
            source_end_received_at = self._packet_received_at(end)
            item = {'identifier': identifier, 'source_text': decision.text,
                'source_start': start, 'source_end': end, 'source_language': result.language,
                'source_vad_speech_seconds': committed_vad_speech_seconds,
                'source_interval_frontier_clip_seconds': source_interval_frontier_clip,
                'pcm': self._slice(start, end), 'committed_at': time.perf_counter(),
                'translation_draft': ({'text':target_commit.draft['text'],
                    'metrics':{**target_commit.draft.get('metrics', {}),
                        'source_boundary_evidence':target_commit.evidence}}
                    if target_commit is not None and decision.action == 'commit' else None),
                'target_commit_evidence': (target_commit.evidence if target_commit is not None
                                           and decision.action == 'commit' else None),
                'translation_request': (None if target_commit is not None
                                        and decision.action == 'commit' else {
                    'source_text': decision.text, 'source_language': result.language,
                    'following_text': source_text(hypothesis[len(stable):]),
                    'previous_text': ' '.join(self.source_history)[-800:],
                    'duration': max(.2, end-start)}),
                'source_observation': self.agreement.observations,
                'asr_dispatched_at':started,
                'asr_completed_at':(target_commit.source_asr_completed_at
                    if target_commit is not None and decision.action == 'commit'
                    and target_commit.source_asr_completed_at is not None else asr_completed_at),
                'target_translation_prefetch_started_at':(
                    target_commit.draft.get('metrics', {}).get('translation_started_at')
                    if target_commit is not None and decision.action == 'commit'
                    else None),
                'decode_started_at':decode_started,
                'local_agreement_started_at':agreement_started,
                'local_agreement_completed_at':agreement_completed_at,
                'boundary_model_started_at':boundary_started,
                'boundary_model_completed_at':boundary_completed_at,
                'endpoint_decision_started_at':endpoint_started,
                'endpoint_decision_completed_at':decision_completed_at,
                'speech_end': speech_end, 'speech_end_server_monotonic':speech_end_monotonic,
                'capture_clock_uncertainty_ms':capture_clock_uncertainty_ms,
                'source_start_packet_received_at':source_start_received_at,
                'source_end_packet_received_at':source_end_received_at,
                'endpoint_media': media_now,
                'speech_end_method': 'asr_word_end_bounded_by_vad',
                'speech_end_to_asr_seconds': (asr_completed_at-speech_end_monotonic)
                    if speech_end_monotonic is not None else None,
                'asr_seconds': actual_asr_seconds,
                'asr_runtime_metrics':model_runtime,
                'asr_worker_timing':worker_timing,
                'audio_buffer_seconds':(len(self.buffer)+len(self.overflow_audio))/32000,
                'input_queue_depth':self.audio_queue.qsize(),
                'phrase_queue_depth_at_commit':self.phrase_queue.qsize()}
            if end_of_stream:
                admission_started = time.perf_counter()
                item['phrase_queue_enqueued_at'] = admission_started
                await asyncio.wait_for(self.phrase_queue.put(item), timeout=30.)
                item['phrase_queue_admission_wait_seconds'] = max(
                    0.0, time.perf_counter()-admission_started)
            else:
                item['phrase_queue_enqueued_at'] = time.perf_counter()
                item['phrase_queue_admission_wait_seconds'] = 0.0
                self.phrase_queue.put_nowait(item)
            self.agreement.commit(stable)
            if target_commit is not None:
                self.simultaneous.accept(target_commit)
            else:
                self.simultaneous.advance_source_boundary(end)
            if read_wait_write_commit is not None:
                self.read_wait_write.accept(read_wait_write_commit)
            else:
                self.read_wait_write.advance_committed_frontier(end)
            self.read_wait_write_generation += 1
            self.pending_read_wait_write_commit = None
            self.pending_read_wait_write_metrics = None
            self.chunk_proposal = None
            self.guarded_boundary_source_signature = None
            self.chunk_proposal_observations = 0
            self.read_wait_write_endpoint_hold_cut = None
            self.read_wait_write_endpoint_hold_count = 0
            self._clear_read_wait_write_context()
            self.rejected_chunk_proposals.clear()
            self.source_history.append(decision.text)
            self.pending[identifier] = start
            self.processing_pending[identifier] = item['committed_at']
            remaining = self.agreement.hypothesis
            self.phrase_start = (remaining[0].start if remaining else
                max(end + .025, media_now - .6) if self.last_speech > media_now else None)
            self.stable_text = ''
            if self.overflow_audio:
                self._trim_pcm_buffer(media_now, merge_overflow=True)
            await self.event('committed', phrase_id=identifier, source_start=start, source_end=end,
                             source_vad_speech_seconds=round(committed_vad_speech_seconds, 3),
                             source_timeline_seconds=round(max(0.0, end-start), 3),
                             source_text=decision.text, decision=asdict(decision),
                             source_commit_policy=self._source_commit_policy(target_commit),
                             target_commit_evidence=(target_commit.evidence
                                if target_commit is not None
                                else None),
                             source_interval_frontier_clip_seconds=item['source_interval_frontier_clip_seconds'],
                             source_first_sample_received_at=item['source_start_packet_received_at'],
                             source_last_sample_received_at=item['source_end_packet_received_at'],
                             source_endpoint_decision_completed_at=item['endpoint_decision_completed_at'],
                             source_audio_buffer_seconds=item['audio_buffer_seconds'],
                             input_queue_depth=item['input_queue_depth'],
                             phrase_queue_depth=item['phrase_queue_depth_at_commit'])
        elif decision.action == 'fallback':
            if not end_of_stream:
                # The already configured physical PCM reserve is exhausted.
                # Stop capture cleanly, drain confirmed phrases, and expose
                # any final uncertain tail to the user.
                await self._request_overload_drain('bounded_source_uncertainty')
                return
            # Capture has already ended; _drain_decoder records this tail.
            return
        elif decision.action == 'backpressure':
            if not end_of_stream:
                await self._request_overload_drain('processing_cannot_keep_up')
                return
            raise ProtocolError(f'{decision.action}: {", ".join(decision.reasons)}; Original audio remains an explicit user choice')

    async def _drain_decoder(self):
        now = (self.processed_source_end if self.processed_source_end is not None
               else self.clock.end or 0.0)
        unconfirmed = 0
        while self.phrase_start is not None and unconfirmed < 4:
            previous_end = self.agreement.committed_end
            await self._decode(now, end_of_stream=True)
            unconfirmed = (0 if self.agreement.committed_end > previous_end
                           else unconfirmed + 1)
        if self.phrase_start is not None:
            self.untranslated_final_tail = {
                'source_start': self.phrase_start, 'source_end': now,
                'source_text': source_text(self.agreement.hypothesis),
                'reason': 'unconfirmed_end_of_stream'}
            uncommitted_vad_speech = max(0.0,
                self.vad_speech_seconds_total-self.committed_vad_speech_seconds)
            unresolved_vad_intervals = self._uncommitted_vad_coverage()
            final_tail_vad_intervals = self._uncommitted_vad_coverage(
                self.phrase_start, now)
            await self.event('warning', code='untranslated_final_tail',
                warning='Capture ended before the final source phrase could be confirmed. '
                        'That tail remains untranslated; verified speech will still play.',
                uncommitted_vad_speech_seconds=round(uncommitted_vad_speech, 3),
                unresolved_vad_interval_seconds=round(sum(
                    span['speech_seconds'] for span in unresolved_vad_intervals), 3),
                unresolved_vad_intervals=unresolved_vad_intervals,
                final_tail_vad_speech_seconds=round(sum(
                    span['speech_seconds'] for span in final_tail_vad_intervals), 3),
                final_tail_vad_intervals=final_tail_vad_intervals,
                **self.untranslated_final_tail)
            self.phrase_start = None
        else:
            uncommitted_vad_speech = max(0.0,
                self.vad_speech_seconds_total-self.committed_vad_speech_seconds)
            if uncommitted_vad_speech >= .5:
                unresolved_vad_intervals = self._uncommitted_vad_coverage()
                await self.event('warning', code='uncommitted_vad_speech_activity',
                    warning=(f'Coverage check: {uncommitted_vad_speech:.2f}s of VAD-marked '
                        'speech activity falls outside committed source intervals. This may '
                        'be an ASR/VAD timing mismatch; transcript omission is unconfirmed.'),
                    source_vad_speech_seconds=round(self.vad_speech_seconds_total, 3),
                    committed_vad_speech_seconds=round(self.committed_vad_speech_seconds, 3),
                    uncommitted_vad_speech_seconds=round(uncommitted_vad_speech, 3),
                    unresolved_vad_interval_seconds=round(sum(
                        span['speech_seconds'] for span in unresolved_vad_intervals), 3),
                    unresolved_vad_intervals=unresolved_vad_intervals,
                    transcript_omission_confirmed=False)
        await self.phrase_queue.put(None)

    async def _decode_loop(self):
        """One decoder, with at most one coalesced request for the latest PCM.

        Slow ASR/translation cannot block capture/VAD or create a queue of old
        inference windows. The existing unresolved-source PCM limit still
        stops a session explicitly when models cannot make progress.
        """
        try:
            while not self.closed:
                await self.decode_requested.wait()
                request_metrics = {'requested_at': self.decode_request_at,
                    'requested_media': self.decode_request_media,
                    'coalesced_count': self.decode_coalesced_requests}
                self.decode_requested.clear()
                self.decode_request_at = self.decode_request_media = None
                self.decode_coalesced_requests = 0
                if self.decoder_finishing:
                    await self._drain_decoder()
                    return
                await self._decode(self.decode_media, request_metrics=request_metrics)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.event('error', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())

    async def _ingest(self):
        try:
            while not self.closed:
                packet = await self.audio_queue.get()
                try:
                    if packet is None:
                        if self.unprocessed_input_packet_count:
                            await self.event('source_input_unprocessed',
                                source_start=self.unprocessed_input_start,
                                source_end=self.unprocessed_input_end,
                                source_duration_seconds=max(0.0,
                                    self.unprocessed_input_end-self.unprocessed_input_start),
                                packet_count=self.unprocessed_input_packet_count,
                                reason='bounded_pcm_capacity')
                        if self.decode_task is not None:
                            self.decoder_finishing = True
                            self.decode_requested.set()
                        else:
                            # Direct diagnostic callers may run only ingest.
                            await self._drain_decoder()
                        return
                    if self.origin is None:
                        self.origin = self.buffer_start = packet.start
                    capacity = await self._wait_for_pcm_capacity(packet)
                    if not capacity:
                        self.queued_seconds = max(0.0,
                            self.queued_seconds-len(packet.pcm)/32000)
                        continue
                    self.queued_seconds = max(0.0,
                        self.queued_seconds-len(packet.pcm)/32000)
                    self.processed_source_end = packet.end
                    if capacity == 'pcm':
                        self.buffer.extend(packet.pcm)
                    for relative_end, speech, probability in self.models.vad.push(packet.pcm):
                        end = self.origin + relative_end
                        if speech:
                            self._record_vad_speech(max(self.origin, end-512/16000), end)
                            self.last_speech = end
                            if self.phrase_start is None:
                                self.phrase_start = max(self.buffer_start, end - .6, self.agreement.committed_end + .025)
                                await self.event('speech_started', source_start=self.phrase_start,
                                                 vad_probability=probability)
                        if not speech and self.last_speech:
                            if self.pauses and self.pauses[-1][0] == self.last_speech:
                                self.pauses[-1] = (self.last_speech, end)
                            else:
                                self.pauses.append((self.last_speech, end))
                    # Drain queued PCM/VAD first, then decode the latest window.
                    # This coalesces redundant provisional ASR work; every input
                    # sample is retained and no committed text is skipped.
                    if (self.phrase_start is not None and self.queued_seconds <= .10 and
                            packet.end - self.last_decode >= self._effective_asr_interval()):
                        if not self._request_decode(packet.end):
                            await self._decode(packet.end)
                    if packet.end - self.last_coverage >= .1:
                        await self.event('coverage', analyzed_until=packet.end,
                            uncommitted_start=self.phrase_start, capture_latency_seconds=None)
                        self.last_coverage = packet.end
                    if capacity == 'pcm':
                        self._trim_pcm_buffer(packet.end)
                    if len(self.buffer) > 32000 * self._maximum_admitted_pcm_seconds():
                        raise ProtocolError('uncommitted_audio_buffer_limit: source remained uncertain')
                finally:
                    self.audio_queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.event('error', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())

    async def _synthesize(self):
        try:
            while not self.closed:
                item = await self.phrase_queue.get()
                try:
                    if item is None:
                        if self.target_translation_task is not None:
                            await asyncio.shield(self.target_translation_task)
                            self.target_translation_task = None
                        await self.event('end_of_stream', pending_playback=len(self.sent - self.played),
                            pending_speech_audio_seconds=sum(self.pending_playback_audio_seconds.values()),
                            source_vad_speech_seconds=round(self.vad_speech_seconds_total, 3),
                            committed_vad_speech_seconds=round(self.committed_vad_speech_seconds, 3),
                            unresolved_vad_speech_seconds=round(max(0.0,
                                self.vad_speech_seconds_total-self.committed_vad_speech_seconds), 3),
                            unresolved_vad_interval_seconds=round(sum(
                                span['speech_seconds'] for span in
                                self._uncommitted_vad_coverage()), 3),
                            unresolved_vad_intervals=self._uncommitted_vad_coverage(),
                            committed_source_timeline_seconds=round(
                                self.committed_source_timeline_seconds, 3))
                        # All PCM already belongs to the browser now. Retaining
                        # CUDA models until the user presses Stop wastes memory
                        # while an already-buffered presentation drains.
                        await asyncio.to_thread(self.models.close)
                        return
                    capacity_wait_started = time.perf_counter()
                    if not await self._wait_for_playback_capacity():
                        return
                    playback_capacity_wait_seconds = max(
                        0.0, time.perf_counter()-capacity_wait_started)
                    payload = {key: value for key, value in item.items() if key in
                        ('identifier', 'pcm', 'source_text', 'source_start', 'source_end', 'source_language',
                         'translation_draft', 'translation_request')}
                    payload['maximum_presentation_tempo'] = PRESENTATION_MAX_TEMPO_BY_MODE[
                        self.controller.mode]
                    payload['presentation_speed_factor'] = self.presentation_speed_factor()
                    render_dispatched_at = time.perf_counter()
                    def render_with_timing():
                        started_at = time.perf_counter()
                        try:
                            value = self.models.render(**payload)
                        except Exception as exc:
                            return None, started_at, time.perf_counter(), exc
                        return value, started_at, time.perf_counter(), None
                    rendered, render_worker_started_at, render_worker_completed_at, render_error = await asyncio.to_thread(render_with_timing)
                    if render_error is not None:
                        rendered = _source_caption_fallback(item, render_error)
                        if rendered is None:
                            raise render_error
                    if self.closed:
                        return
                    ready = time.perf_counter()
                    presentation_mode = rendered.metrics.get('presentation_mode', 'speech')
                    generated_audio_seconds = (len(rendered.pcm) / 96000.0
                                               if presentation_mode != 'caption_only' else 0.0)
                    if generated_audio_seconds > 0:
                        self.note_playback_audio_ready(rendered.identifier, generated_audio_seconds)
                    pending_speech_audio_seconds = sum(self.pending_playback_audio_seconds.values())
                    rendered.metrics.update({
                        'source_interval_frontier_clip_seconds': item.get(
                            'source_interval_frontier_clip_seconds', 0.0),
                        'phrase_queue_wait_seconds': max(0.0, render_dispatched_at-item.get('phrase_queue_enqueued_at', render_dispatched_at)),
                        'phrase_queue_admission_wait_seconds': item.get('phrase_queue_admission_wait_seconds', 0.0),
                        'playback_capacity_wait_seconds':playback_capacity_wait_seconds,
                        'render_executor_queue_wait_seconds': max(0.0, render_worker_started_at-render_dispatched_at),
                        'model_render_call_seconds': max(0.0, render_worker_completed_at-render_worker_started_at),
                        'render_roundtrip_seconds': max(0.0, ready-render_dispatched_at),
                        'queue_depth_at_render_start': self.phrase_queue.qsize(),
                        'input_queue_depth_at_render_start': self.audio_queue.qsize(),
            'source_commit_policy': (item.get('target_commit_evidence') or {}).get(
                            'policy', 'source_endpoint_policy'),
                        'target_commit_evidence': item.get('target_commit_evidence'),
                        'source_audio_buffer_seconds_at_render_start': (
                            len(self.buffer)+len(self.overflow_audio))/32000,
                        'staged_source_pcm_seconds_at_render_start': len(self.overflow_audio)/32000,
                        'generated_audio_seconds':generated_audio_seconds,
                        'source_vad_speech_seconds':round(float(
                            item.get('source_vad_speech_seconds', 0.0)), 3),
                        'audio_to_source_speech_ratio':(generated_audio_seconds /
                            max(float(item.get('source_vad_speech_seconds', 0.0)), .001)),
                        'pending_speech_audio_seconds':pending_speech_audio_seconds,
                        'pending_speech_phrase_count':len(self.pending_playback_audio_seconds),
                    })
                    commitment = rendered.metrics.get('translation_commitment', {})
                    draft_metrics = commitment.get('draft_context', {})
                    if draft_metrics:
                        translation_request = item.get('translation_request') or {}
                        await self.event('translation_draft', phrase_id=rendered.identifier,
                            source_start=item['source_start'], source_end=item['source_end'],
                            source_text=item['source_text'],
                            following_text=translation_request.get('following_text', ''),
                            target_text=rendered.target_text, observation=item['source_observation'],
                            eligible_for_synthesis=True, source_support='confirmed_source_boundary_prefetch',
                            translation_selection=(item.get('target_commit_evidence') or {}).get(
                                'policy', 'latest_complete_contextual_candidate'),
                            semantic_correctness_certified=False, metrics=draft_metrics,
                            event_observed_after_audio_qa=True)
                    caption_only = presentation_mode == 'caption_only'
                    metadata = {'type': 'phrase', 'phrase_id': rendered.identifier,
                        'presentation_mode':presentation_mode,
                        'source_start': rendered.source_start, 'source_end': rendered.source_end,
                        'source_text': rendered.source_text, 'target_text': rendered.target_text,
                        'speaker': rendered.speaker, 'sample_rate': 48000, 'chunks': len(rendered.chunk_sizes),
                        'metrics': {**rendered.metrics,
                            'asr_to_translation_start_seconds': (
                                max(0.0, draft_metrics['translation_started_at'] - item['asr_completed_at'])
                                if 'translation_started_at' in draft_metrics else
                                render_dispatched_at - item['asr_completed_at'] + rendered.metrics.get('render_to_translation_start_seconds', 0.)),
                            'translation_prefetch_lead_seconds': max(0.0,
                                item['committed_at']-item['target_translation_prefetch_started_at'])
                                if item.get('target_translation_prefetch_started_at') is not None else 0.0,
                            'translation_started_before_source_commit': bool(
                                item.get('target_translation_prefetch_started_at') is not None and
                                item['target_translation_prefetch_started_at'] < item['committed_at']),
                            'translation_pre_inference_wait_seconds': max(0.0,
                                draft_metrics.get('translation_started_at', render_dispatched_at) - render_dispatched_at),
                            'asr_to_phrase_queue_seconds': max(0.0,
                                item.get('phrase_queue_enqueued_at', render_dispatched_at)-item['asr_completed_at']),
                            'speech_end_to_usable_audio_seconds': (
                                ready-item['speech_end_server_monotonic']
                                if not caption_only and item.get('speech_end_server_monotonic') is not None else None),
                            'speech_end_to_caption_seconds': (
                                ready-item['speech_end_server_monotonic']
                                if caption_only and item.get('speech_end_server_monotonic') is not None else None),
                            'capture_clock_uncertainty_ms':item.get('capture_clock_uncertainty_ms'),
                            'commit_to_usable_audio_seconds': None if caption_only else ready - item['committed_at'],
                            'commit_to_caption_seconds': ready - item['committed_at'] if caption_only else None,
                            'speech_end_to_asr_seconds': item['speech_end_to_asr_seconds'],
                            'speech_end_source_seconds': item['speech_end'],
                            'speech_end_method': item['speech_end_method'],
                            'intentional_endpoint_delay_seconds': item['endpoint_media'] - item['speech_end'],
                            **{'source_'+key: value for key,value in item.get('asr_runtime_metrics', {}).items()},
                            **{'source_worker_'+key: value for key,value in item.get('asr_worker_timing', {}).items()},
                            'stage_timestamps_monotonic':{
                                'source_first_sample_received_at':item.get('source_start_packet_received_at'),
                                'source_last_sample_received_at':item.get('source_end_packet_received_at'),
                                'speech_end_source_monotonic_at':item.get('speech_end_server_monotonic'),
                                'asr_dispatched_at':item.get('asr_dispatched_at'),
                                'asr_worker_started_at':item.get('asr_worker_timing', {}).get('worker_started_at'),
                                'asr_worker_completed_at':item.get('asr_worker_timing', {}).get('worker_completed_at'),
                                'asr_completed_at':item.get('asr_completed_at'),
                                'stable_prefix_confirmed_at':item.get('local_agreement_completed_at'),
                                'boundary_model_completed_at':item.get('boundary_model_completed_at'),
                                'endpoint_decision_completed_at':item.get('endpoint_decision_completed_at'),
                                'target_translation_started_at':draft_metrics.get('translation_started_at'),
                                'target_translation_completed_at':draft_metrics.get('translation_completed_at'),
                                'source_committed_at':item.get('committed_at'),
                                'phrase_queue_enqueued_at':item.get('phrase_queue_enqueued_at'),
                                'render_dispatched_at':render_dispatched_at,
                                'render_executor_started_at':render_worker_started_at,
                                'render_executor_completed_at':render_worker_completed_at,
                                'model_worker_started_at':rendered.metrics.get('model_worker_worker_started_at'),
                                'model_worker_completed_at':rendered.metrics.get('model_worker_worker_completed_at'),
                                'speaker_analysis_started_at':rendered.metrics.get('speaker_started_at'),
                                'speaker_analysis_completed_at':rendered.metrics.get('speaker_completed_at'),
                                'context_retrieval_started_at':rendered.metrics.get('context_retrieval_started_at'),
                                'context_retrieval_completed_at':rendered.metrics.get('context_retrieval_completed_at'),
                                'translation_started_at':draft_metrics.get('translation_started_at'),
                                'translation_completed_at':draft_metrics.get('translation_completed_at'),
                                'translation_validation_started_at':draft_metrics.get('translation_validation_started_at'),
                                'translation_validation_completed_at':draft_metrics.get('translation_validation_completed_at'),
                                'audio_ready_at':ready,
                            }}}
                    self.pending_playback_targets[rendered.identifier] = (
                        rendered.target_text if presentation_mode != 'caption_only' else '')
                    await self.emit(metadata)
                    offset = 0
                    for sequence, size in enumerate(rendered.chunk_sizes):
                        await self.emit({'type': 'pcm', 'phrase_id': rendered.identifier,
                            'sequence': sequence, 'pcm': base64.b64encode(rendered.pcm[offset:offset+size]).decode()})
                        offset += size
                    self.sent.add(rendered.identifier)
                    self.sent_kinds[rendered.identifier] = presentation_mode
                    if presentation_mode == 'caption_only':
                        self.caption_count += 1
                        await self.event('caption_fallback', phrase_id=rendered.identifier,
                            source_start=rendered.source_start, source_end=rendered.source_end,
                            target_text=rendered.target_text,
                            reason=rendered.metrics.get('fallback_reason', 'bounded_tts_qa_failed'),
                            source_caption_only=bool(rendered.metrics.get('source_caption_only')),
                            speech_generated=False)
                    else:
                        self.sent_count += 1
                    self.processing_pending.pop(rendered.identifier, None)
                    await self.event('phrase_ready', phrase_id=rendered.identifier,
                        presentation_mode=presentation_mode, **{'metrics': metadata['metrics']})
                finally:
                    self.phrase_queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.event('error', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())

    async def acknowledge(self, message):
        identifier = message.get('phrase_id')
        if identifier not in self.sent or identifier in self.played:
            raise ProtocolError('Unknown or duplicate playback acknowledgement')
        self.played.add(identifier)
        self.played_count += 1
        presentation_mode = self.sent_kinds.pop(identifier, 'speech')
        if presentation_mode == 'caption_only':
            self.played_caption_count += 1
        else:
            self.played_speech_count += 1
            spoken_target = self.pending_playback_targets.get(identifier, '')
            if spoken_target:
                self.target_translation_history.append(spoken_target)
        self.pending_playback_targets.pop(identifier, None)
        self.sent.remove(identifier)
        self.playback_capacity_changed.set()
        if len(self.played_order) == self.played_order.maxlen:
            self.played.discard(self.played_order[0])
        self.played_order.append(identifier)
        self.pending.pop(identifier, None)
        played_audio_seconds = self.pending_playback_audio_seconds.pop(identifier, 0.0)
        self.observe_playback_audio_demand(message.get('pending_speech_audio_seconds'))
        pending_speech_audio_seconds = sum(self.pending_playback_audio_seconds.values())
        await self.event('played', presentation_mode=presentation_mode,
            played_audio_seconds=played_audio_seconds,
            server_pending_speech_audio_seconds=pending_speech_audio_seconds,
            **{key: message.get(key) for key in
            ('phrase_id', 'video_start', 'video_end', 'audio_context_start', 'audio_context_end',
             'observed_av_offset_seconds', 'playback_queue_seconds', 'capture_to_playback_seconds',
             'speech_end_to_playback_seconds', 'video_playback_rate', 'presented_frames',
             'requested_video_playback_rate', 'video_playback_rate_clamped',
             'video_source_end_catchup_seconds',
             'generated_audio_seconds', 'presentation_audio_seconds', 'silence_padding_seconds',
             'audio_output_start_performance_ms', 'audio_output_end_performance_ms',
              'audio_output_started_after_schedule_ms', 'audio_output_schedule_performance_ms',
              'audio_output_clock_source', 'playback_admission_performance_ms',
              'playback_admission_queue_depth', 'audio_video_offset_seconds',
             'video_frame_expected_display_time_ms', 'video_frame_media_time',
              'live_edge_to_displayed_video_seconds',
             'audio_output_timestamp_uncertainty_ms', 'video_first_frame_delay_ms',
             'video_play_request_to_first_frame_ms',
             'video_play_resolution_to_audio_schedule_ms', 'video_play_succeeded',
             'pending_speech_audio_seconds', 'pending_presentation_audio_seconds')})

    async def finish(self):
        if self.finishing:
            return
        self.finishing = True
        await self.audio_queue.put(None)

    async def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.target_translation_task is not None:
            self.target_translation_task.cancel()
            await asyncio.gather(self.target_translation_task, return_exceptions=True)
            self.target_translation_task = None
        if self.read_wait_write_task is not None:
            self.read_wait_write_task.cancel()
            await asyncio.gather(self.read_wait_write_task, return_exceptions=True)
            self.read_wait_write_task = None
            self.read_wait_write_task_approved_end = None
            self.read_wait_write_task_cut_signature = None
        if self.capture_diagnostic is not None:
            self.capture_diagnostic.close()
            self.capture_diagnostic = None
        self.models.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.boundary is not None:
            await asyncio.to_thread(self.boundary.close)
            self.boundary = None
        await asyncio.to_thread(self.models.close)
        summary = {'captured_packets': self.clock.next_sequence, 'synthesized_phrases': self.sent_count,
                   'captured_source_seconds': self.clock.end,
                   'processed_source_seconds': self.processed_source_end,
                   'staged_source_pcm_seconds':len(self.overflow_audio)/32000,
                   'unprocessed_input_packets': self.unprocessed_input_packet_count,
                   'unprocessed_input_start_seconds': self.unprocessed_input_start,
                   'unprocessed_input_end_seconds': self.unprocessed_input_end,
                   'caption_fallbacks':self.caption_count,
                   'spoken_phrases_played':self.played_speech_count,
                   'caption_phrases_played':self.played_caption_count,
                   'played_phrases': self.played_count, 'unplayed_phrases': sorted(self.sent),
                   'untranslated_final_tail': self.untranslated_final_tail,
                   'unresolved_vad_speech_seconds':round(max(0.0,
                       self.vad_speech_seconds_total-self.committed_vad_speech_seconds), 3),
                   'unresolved_vad_interval_seconds':round(sum(span['speech_seconds']
                       for span in self._uncommitted_vad_coverage()), 3),
                   'unresolved_vad_intervals':self._uncommitted_vad_coverage(),
                   'wall_seconds': time.perf_counter()-self.started, 'events': self.total_events}
        (self.directory / 'session-summary.json').write_text(json.dumps(summary, indent=2))
