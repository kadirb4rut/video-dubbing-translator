"""Optional authenticated localhost service; offline GUI imports no AI models."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import time
import threading
import uuid

from aiohttp import web, WSMsgType

from .security import Credentials, source_policy, validated_glossary
from .session import LiveSession, RUNTIME_IMPLEMENTATION

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / 'browser_extension/live_dubbing'
COOKIE = 'live_dubbing_local_session'


def readiness():
    from translation.model_manager import is_installed, runtime_python
    from diarization.model_manager import status
    from voxcpm_runtime import model_ready
    from .segmentation import installed as segmentation_installed
    from .qwen_translation import (installed as qwen_installed, selected_backend,
                                   selected_boundary_backend)
    diarization = status()
    vox_path = os.environ.get('LIVE_VOXCPM_PATH')
    vox = (Path(vox_path) / 'model.safetensors').is_file() if vox_path else model_ready()[0]
    whisper = os.environ.get('LIVE_WHISPER_MODEL', 'small')
    if Path(whisper).is_dir():
        asr = (Path(whisper) / 'model.bin').is_file()
    else:
        try:
            from huggingface_hub import try_to_load_from_cache
            repo = whisper if '/' in whisper else f'Systran/faster-whisper-{whisper}'
            asr = isinstance(try_to_load_from_cache(repo, 'model.bin'), str)
        except ImportError:
            asr = False
    hymt_ready = is_installed('hymt2-1.8b') and runtime_python().is_file()
    chosen = selected_backend()
    boundary = selected_boundary_backend(chosen)
    speaker_ready = diarization.installed and diarization.runtime_ready
    speaker_fallback_allowed = os.environ.get('LIVE_REQUIRE_SPEAKER_MODELS', '0') == '0'
    return {'hymt2': hymt_ready,
            'live_translation_backend':chosen,
            'live_translation':bool(qwen_installed()) if chosen == 'qwen3.5-27b' else hymt_ready,
            'live_boundary_backend':boundary,
            'live_boundary_model_files_installed':bool(qwen_installed()) if boundary == 'qwen3.5-27b'
                else bool(segmentation_installed()),
            'speaker_models': speaker_ready,
            'speaker_policy': ('community1_embeddings_and_overlap' if speaker_ready else
                'unconfirmed_current_segment_reference_fallback' if speaker_fallback_allowed else
                'community1_required'),
            'voxcpm': vox, 'asr': asr, 'multilingual_segmentation':bool(segmentation_installed()),
            'note': 'Explicit local model setup required. This endpoint downloads/loads no models.'}


def create_app(*, port=8766, runtime_factory=None, evidence_root=None,
               capture_start_timeout=120., dashboard_origin='http://127.0.0.1:8765'):
    if not 0 < capture_start_timeout <= 600:
        raise ValueError('Capture-start timeout must be positive and bounded')
    origin = f'http://127.0.0.1:{port}'
    credentials = Credentials()
    runtime_factory = runtime_factory or _runtime
    evidence_root = Path(evidence_root or ROOT / 'temp/live-dubbing')
    holder = {'session': None, 'initializing': False}

    @web.middleware
    async def boundary(request, handler):
        if request.host != f'127.0.0.1:{port}':
            raise web.HTTPForbidden(text='Exact loopback Host required')
        supplied = request.headers.get('Origin')
        dashboard_pair_route = (
            supplied == dashboard_origin and request.path in
            ('/api/pairing-session', '/api/pair')
        )
        if supplied and supplied != origin and supplied not in credentials.extension_origins and not dashboard_pair_route:
            if request.path != '/api/redeem':
                raise web.HTTPForbidden(text='Origin not paired')
        response = await handler(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; connect-src 'self' ws://127.0.0.1:" + str(port) + "; worker-src 'self'; frame-ancestors 'none'; object-src 'none'"})
        if supplied == dashboard_origin and request.path in ('/api/pairing-session', '/api/pair'):
            response.headers['Access-Control-Allow-Origin'] = dashboard_origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Vary'] = 'Origin'
        return response

    def local_authorized(request):
        return (request.headers.get('Origin') == origin and
                credentials.valid_cookie(request.cookies.get(COOKIE)))

    def dashboard_authorized(request):
        return (request.headers.get('Origin') == dashboard_origin and
                credentials.valid_cookie(request.cookies.get(COOKIE)))

    async def index(request):
        response = web.FileResponse(WEB_ROOT / 'player.html')
        response.set_cookie(COOKIE, credentials.cookie, httponly=True, samesite='Strict', path='/')
        return response

    async def status(request):
        return web.json_response({'app': 'video-dubbing-translator-live-v1',
                                  'pairing_api_version': 1,
                                  'active': holder['session'] is not None, 'initializing': holder['initializing'],
                                  'models': readiness(), 'youtube': 'experimental_user_authorized_tab_capture',
                                  'runtime_implementation': RUNTIME_IMPLEMENTATION})

    async def ticket(request):
        if not local_authorized(request):
            raise web.HTTPForbidden(text='Same-origin browser session required')
        return web.json_response({'ticket': credentials.issue_ticket(origin)})

    async def pairing_session(request):
        if request.headers.get('Origin') != dashboard_origin:
            raise web.HTTPForbidden(text='Local dashboard origin required')
        response = web.json_response({'ok': True})
        response.set_cookie(COOKIE, credentials.cookie, httponly=True,
                            samesite='Strict', path='/')
        return response

    async def pairing(request):
        if not (local_authorized(request) or dashboard_authorized(request)):
            raise web.HTTPForbidden(text='Explicit local pairing action required')
        return web.json_response({'code': credentials.issue_pairing(), 'expires_seconds': 120})

    async def pairing_options(request):
        if request.headers.get('Origin') != dashboard_origin:
            raise web.HTTPForbidden(text='Local dashboard origin required')
        return web.Response(headers={
            'Access-Control-Allow-Origin': dashboard_origin,
            'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Vary': 'Origin',
        })

    async def redeem(request):
        try:
            data = await request.json()
            extension_origin = request.headers.get('Origin', '')
            token = credentials.redeem(data.get('code', ''), extension_origin)
        except (ValueError, TypeError) as exc:
            raise web.HTTPForbidden(text=str(exc))
        response = web.json_response({'ticket': token})
        response.headers['Access-Control-Allow-Origin'] = extension_origin
        return response

    async def redeem_options(request):
        import re
        supplied = request.headers.get('Origin', '')
        if not re.fullmatch(r'chrome-extension://[a-p]{32}', supplied):
            raise web.HTTPForbidden(text='Extension origin required')
        return web.Response(headers={'Access-Control-Allow-Origin': supplied,
            'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type'})

    async def ws_handler(request):
        supplied_origin = request.headers.get('Origin', '')
        if supplied_origin not in {origin, *credentials.extension_origins}:
            raise web.HTTPForbidden(text='WebSocket origin denied')
        ws = web.WebSocketResponse(max_msg_size=64*1024, heartbeat=20)
        await ws.prepare(request)
        session = None
        capture_deadline = None
        owns_startup = False
        send_lock = asyncio.Lock()

        async def emit(value):
            async with send_lock:
                if not ws.closed:
                    await ws.send_json(value, dumps=lambda item: json.dumps(item, default=str, ensure_ascii=False))

        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=10)
            if not credentials.consume_ticket(first.get('ticket', ''), supplied_origin):
                raise ValueError('Missing, expired, replayed or wrong-origin session ticket')
            if holder['session'] is not None or holder['initializing']:
                raise ValueError('Another live session is active; stop it before starting another')
            settings = first.get('settings', {})
            settings['glossary'] = validated_glossary(settings.get('glossary'))
            if settings.get('source_authorized') is not True:
                raise ValueError('Explicit source capture authorization is required')
            allowed, reason = source_policy(settings.get('source_url'), settings.get('source_kind'),
                                            user_authorized=settings['source_authorized'])
            if not allowed:
                raise ValueError(reason)
            from translation.backends import VOXCPM2_LANGUAGES
            if settings.get('target_language', 'tr') not in VOXCPM2_LANGUAGES:
                raise ValueError('Unsupported target language')
            if settings.get('mode', 'balanced') not in ('quality', 'balanced', 'low_latency'):
                raise ValueError('Unsupported latency mode')
            if settings.get('source_language', 'auto') not in ('auto', *VOXCPM2_LANGUAGES):
                raise ValueError('Unsupported source language')
            directory = evidence_root / uuid.uuid4().hex
            directory.mkdir(parents=True, exist_ok=False)
            holder['initializing'] = True
            owns_startup = True
            await emit({'type': 'loading', 'message': 'Loading local models. Capture has not started.'})
            cancel_startup = threading.Event()
            settings['_startup_cancel'] = cancel_startup
            loading = asyncio.create_task(asyncio.to_thread(runtime_factory, directory, settings))
            control = asyncio.create_task(ws.receive())
            try:
                completed, _ = await asyncio.wait((loading, control), return_when=asyncio.FIRST_COMPLETED)
                if control in completed:
                    cancel_startup.set()
                    # Constructor observes cancellation and disposes its own
                    # process group. If startup won the race, dispose that
                    # successfully constructed runtime too.
                    try:
                        abandoned = await loading
                    except (RuntimeError, OSError):
                        pass
                    else:
                        await asyncio.to_thread(abandoned.close)
                    raise ValueError('Startup cancelled before capture')
                models = await loading
            finally:
                control.cancel()
                await asyncio.gather(control, return_exceptions=True)
                if not loading.done():
                    cancel_startup.set()
            holder['initializing'] = False
            session = LiveSession(models, emit, directory, mode=settings.get('mode', 'balanced'))
            holder['session'] = session
            await session.start()
            async def expire_unstarted_capture():
                await asyncio.sleep(capture_start_timeout)
                if session.clock.end is None:
                    await session.event('error', error='capture_start_timeout: no source capture started; models released')
                    await session.stop()
                    await ws.close()
            capture_deadline = asyncio.create_task(expire_unstarted_capture())
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    await session.push(message.data)
                elif message.type == WSMsgType.TEXT:
                    data = json.loads(message.data)
                    command = data.get('type')
                    if command == 'stop':
                        break
                    if command == 'client_error':
                        error = data.get('error')
                        if not isinstance(error, str) or len(error) > 2000:
                            raise ValueError('Invalid client error report')
                        await session.event('client_error', error=error)
                        break
                    if command == 'eos':
                        await session.finish()
                    elif command == 'played':
                        await session.acknowledge(data)
                    elif command in ('pause', 'resume'):
                        session.paused = command == 'pause'
                        await session.event(command, semantics='presentation paused; capture/inference remain bounded')
                    elif command in ('playback_metrics', 'capture_metrics', 'source_state'):
                        if command == 'capture_metrics':
                            session.update_capture_clock(data.get('capture_media_time'),
                                data.get('capture_sample_end_performance_ms'),
                                data.get('capture_clock_uncertainty_ms'))
                        elif command == 'playback_metrics':
                            session.observe_playback_audio_demand(
                                data.get('pending_speech_audio_seconds'),
                                capture_media_time=data.get('capture_media_time'),
                                video_media_time=data.get('video_media_time'))
                        await session.event(command, **{key: data.get(key) for key in
                            ('video_media_time', 'capture_media_time', 'buffer_seconds', 'buffer_start_seconds', 'audio_context_time',
                             'video_current_time', 'video_ready_state', 'video_paused', 'video_seeking', 'frame_callback_age_seconds',
                             'original_audio_enabled', 'source_video_muted', 'translated_gain',
                             'presented_frames', 'av_offset_seconds', 'queue_depth', 'buffer_bytes', 'video_playback_rate',
                             'pending_speech_audio_seconds', 'pending_presentation_audio_seconds',
                             'active_speech_audio_seconds', 'queued_speech_audio_seconds',
                             'capture_elapsed_seconds', 'audio_callback_delay_seconds', 'capture_context_time',
                             'capture_sample_end_performance_ms', 'capture_clock_uncertainty_ms', 'state', 'reason')})
                    elif command == 'ping':
                        server_received = time.perf_counter()
                        server_sent = time.perf_counter()
                        await emit({'type': 'pong',
                            'client_sent_perf_ms': data.get('client_sent_perf_ms'),
                            'server_received_monotonic': server_received,
                            'server_sent_monotonic': server_sent})
                    elif command == 'clock_sync':
                        session.update_browser_clock(data.get('client_perf_to_server_monotonic_offset_ms'),
                                                     data.get('uncertainty_ms'))
                        await session.event('clock_sync', **{key:data.get(key) for key in
                            ('client_perf_to_server_monotonic_offset_ms', 'round_trip_ms',
                             'uncertainty_ms', 'sample_client_sent_perf_ms',
                             'sample_client_received_perf_ms')})
                    elif command not in ('eos',):
                        raise ValueError('Unknown control message')
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except (ValueError, RuntimeError, asyncio.TimeoutError, OSError) as exc:
            await emit({'type': 'error', 'error': f'{type(exc).__name__}: {exc}'})
        finally:
            if capture_deadline is not None:
                capture_deadline.cancel()
                await asyncio.gather(capture_deadline, return_exceptions=True)
            if session is not None:
                await session.stop()
                if holder['session'] is session:
                    holder['session'] = None
            if owns_startup:
                holder['initializing'] = False
            await ws.close()
        return ws

    async def cleanup(app):
        if holder['session'] is not None:
            await holder['session'].stop()

    app = web.Application(middlewares=[boundary], client_max_size=64*1024)
    app.router.add_get('/', index)
    app.router.add_get('/api/status', status)
    app.router.add_get('/api/pairing-session', pairing_session)
    app.router.add_post('/api/ticket', ticket)
    app.router.add_post('/api/pair', pairing)
    app.router.add_options('/api/pair', pairing_options)
    app.router.add_post('/api/redeem', redeem)
    app.router.add_options('/api/redeem', redeem_options)
    app.router.add_get('/ws', ws_handler)
    for filename in ('client.js', 'playback.js', 'capture-worklet.js', 'style.css'):
        async def asset(request, name=filename):
            return web.FileResponse(WEB_ROOT / name)
        app.router.add_get('/' + filename, asset)
    app.on_cleanup.append(cleanup)
    return app


def _runtime(directory, settings):
    checks = readiness()
    missing = [key for key, value in checks.items() if isinstance(value, bool) and not value
               and not (key == 'speaker_models' and
                        os.environ.get('LIVE_REQUIRE_SPEAKER_MODELS', '0') == '0')]
    if missing:
        raise RuntimeError('Live model setup required: ' + ', '.join(missing) + '. No model was downloaded or loaded.')
    from .worker import ModelProcess
    return ModelProcess(directory, device='auto', source_language=settings.get('source_language'),
                       target_language=settings.get('target_language', 'tr'),
                       whisper_model=os.environ.get('LIVE_WHISPER_MODEL', 'small'),
                       startup_cancel=settings.get('_startup_cancel'), glossary=settings.get('glossary'))


def main():
    parser = argparse.ArgumentParser(description='Optional local adaptive Live Dubbing service (no cloud dependency)')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Choose an unprivileged localhost port')
    web.run_app(create_app(port=args.port), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
