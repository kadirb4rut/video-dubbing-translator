import {DelayedPlayer, estimateServerClockOffset} from './playback.js';
const $ = id => document.getElementById(id);
const extension = location.protocol === 'chrome-extension:';
if (extension && new URLSearchParams(location.search).has('sidepanel'))
  document.body.classList.add('sidepanel');
const endpoint = extension ? 'http://127.0.0.1:8766' : location.origin;
let ws = null, stream = null, inputAudio = null, worklet = null, player = null;
let sourceInfo = null, stopping = false, finishing = false, flushDone = null;
let clockPingTimer = null;
const clockPings = new Map();
let maximumVideoBufferSeconds = 20, overloadWarning = '';
const history = [];
const languages = {ar:'Arabic',my:'Burmese',zh:'Chinese',da:'Danish',nl:'Dutch',en:'English',fi:'Finnish',fr:'French',de:'German',el:'Greek',he:'Hebrew',hi:'Hindi',id:'Indonesian',it:'Italian',ja:'Japanese',km:'Khmer',ko:'Korean',lo:'Lao',ms:'Malay',no:'Norwegian',pl:'Polish',pt:'Portuguese',ru:'Russian',es:'Spanish',sw:'Swahili',sv:'Swedish',tl:'Filipino',th:'Thai',tr:'Turkish',vi:'Vietnamese'};
for (const [id, automatic, selected] of [['sourceLanguage', true, 'auto'], ['targetLanguage', false, 'tr']]) {
  const control = $(id);
  control.replaceChildren();
  if (automatic) control.add(new Option('Automatic', 'auto'));
  for (const [code, name] of Object.entries(languages)) control.add(new Option(name, code));
  control.value = selected;
}
function record(text) {
  history.push(text);
  if (history.length > 80) history.shift();
  $('events').textContent = history.join('\n');
}
function send(message) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}
function pingServerClock() {
  if (ws?.readyState !== WebSocket.OPEN) return;
  const clientSent = performance.now();
  clockPings.set(clientSent, clientSent);
  for (const key of clockPings.keys()) if (clientSent-key > 30000) clockPings.delete(key);
  send({type:'ping', client_sent_perf_ms:clientSent});
}
function buttons(state) {
  $('state').textContent = state;
  $('start').disabled = state !== 'Inactive';
  $('capture').disabled = state !== 'Ready to capture';
  $('stop').disabled = state === 'Inactive';
  $('pause').disabled = state !== 'Capturing';
  $('resume').disabled = state !== 'Paused';
  $('finish').disabled = !['Capturing', 'Paused'].includes(state);
  for (const id of ['sourceUrl', 'sourceLanguage', 'targetLanguage', 'mode', 'consent', 'glossary'])
    $(id).disabled = state !== 'Inactive';
}
function status(value, kind, item) {
  if (kind === 'error') {
    send({type:'client_error', error:String(value).slice(0,2000)});
    $('warning').textContent = String(value);
    record('ERROR ' + value);
    stop();
  } else if (kind === 'playing') {
    $('sourceText').textContent = item.source_text;
    $('targetText').textContent = item.target_text;
    const identified = item.speaker.speaker_id.match(/LIVE_SPEAKER_(\d+)/);
    const voice = identified ? 'Speaker ' + (Number(identified[1]) + 1) : 'Temporary neutral voice';
    const captionOnly = item.presentation_mode === 'caption_only';
    const sourceCaptionOnly = captionOnly && item.metrics?.source_caption_only;
    $('speaker').textContent = sourceCaptionOnly ? 'Source caption only · no synthesized voice' : voice + (captionOnly ? ' · text fallback' :
      item.speaker.reason === 'embedding_match' ? ' · matched voice' : '');
    $('translationState').textContent = sourceCaptionOnly ?
      'Translation failed integrity checks; showing source caption only' : captionOnly ?
      'Speech unavailable for this phrase; showing translation' : 'Playing validated speech';
  } else if (kind === 'overload') {
    $('warning').textContent = String(value);
    record(String(value));
    // Use the normal bounded flush/EOS path so already prepared phrases play
    // once and the final unresolved source span is reported by the server.
    finishCapture();
  } else if (kind === 'metrics') {
    $('delay').textContent = value.delay.toFixed(2) + ' s';
    $('buffer').textContent = value.buffer.toFixed(2) + ' s';
  } else if (kind === 'drained') {
    $('translationState').textContent = value;
    buttons('Finished');
    record(value);
  }
}
async function request(path, body) {
  const response = await fetch(endpoint + path, {method: 'POST',
    credentials: extension ? 'omit' : 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  if (!response.ok) throw Error(await response.text());
  return response.json();
}
async function onMessage(event) {
  try {
    const message = JSON.parse(event.data);
    switch (message.type) {
      case 'pong': {
        const clientSent = Number(message.client_sent_perf_ms);
        const clientReceived = performance.now();
        if (!clockPings.has(clientSent) || !Number.isFinite(message.server_received_monotonic) ||
            !Number.isFinite(message.server_sent_monotonic)) break;
        clockPings.delete(clientSent);
        const estimate = estimateServerClockOffset(clientSent,
          message.server_received_monotonic*1000, message.server_sent_monotonic*1000, clientReceived);
        if (!estimate) break;
        send({type:'clock_sync', ...estimate,
          sample_client_sent_perf_ms:clientSent, sample_client_received_perf_ms:clientReceived});
        break;
      }
      case 'loading': $('translationState').textContent = message.message; break;
      case 'ready':
        maximumVideoBufferSeconds = Math.max(20, Math.min(40, message.maximum_video_buffer_seconds || 20));
        pingServerClock();
        clearInterval(clockPingTimer);
        clockPingTimer = setInterval(pingServerClock, 5000);
        buttons('Ready to capture');
        $('translationState').textContent = 'Local models ready. Choose the source to begin capture.';
        record('Models loaded in ' + message.model_load_seconds.toFixed(2) + ' s'); break;
      case 'speech_started': player?.sourceHold(message.source_start); break;
      case 'coverage': player?.coverage(message); break;
      case 'hypothesis':
        $('hypothesisText').textContent = message.stable || message.provisional;
        $('hypothesisState').textContent = message.stable ?
          'Confirmed incoming speech — not yet played' : 'Provisional incoming speech — not yet played';
        if (!player?.active) $('translationState').textContent = message.decision.action === 'wait' ?
          'Waiting for a complete phrase' : message.decision.action;
        record(message.decision.action + ': ' + message.decision.reasons.join(', ')); break;
      case 'committed':
        player?.committed(message);
        if (!player?.active) $('translationState').textContent = 'Preparing and checking translated speech';
        record('Committed ' + message.phrase_id + ' ' + message.source_text); break;
      case 'phrase':
        if (message.metrics.speaker_warning) {
          $('warning').textContent = message.metrics.speaker_warning;
          record(message.metrics.speaker_warning);
        }
        player.phrase(message); break;
      case 'pcm': player.chunk(message); break;
      case 'phrase_ready': player.ready(message.phrase_id); break;
      case 'warning':
        if (message.code === 'processing_overload_drain') overloadWarning = message.warning;
        $('warning').textContent = message.code === 'untranslated_final_tail' && overloadWarning ?
          overloadWarning + ' ' + message.warning : message.warning;
        record(message.warning + (message.source_text ? ' Source: ' + message.source_text : ''));
        if (message.code === 'processing_overload_drain') await finishCapture();
        break;
      case 'error': status(message.error, 'error'); break;
      case 'end_of_stream':
        player?.finishInference();
        record('Source ended; draining ' + message.pending_playback + ' phrase(s).'); break;
    }
  } catch (error) { status(error.message, 'error'); }
}
async function start() {
  if (!$('consent').checked) {
    $('warning').textContent = 'Explicit source authorization is required before starting.';
    return;
  }
  try {
    const glossary = JSON.parse($('glossary').value.trim() || '{}');
    if (!glossary || typeof glossary !== 'object' || Array.isArray(glossary) ||
        Object.keys(glossary).length > 50 || Object.entries(glossary).some(([key, value]) =>
          !key.trim() || typeof value !== 'string' || !value.trim() ||
          key.length > 160 || value.length > 160 || /[\u0000-\u001f]/.test(key + value)))
      throw Error('Glossary must contain at most 50 nonempty single-line source/target terms.');
    stopping = finishing = false;
    overloadWarning = '';
    $('warning').textContent = '';
    $('sourceText').textContent = '';
    $('targetText').textContent = '';
    $('hypothesisText').textContent = '';
    $('hypothesisState').textContent = 'Incoming speech — not yet played';
    $('speaker').textContent = 'No speaker';
    $('delay').textContent = '–';
    $('buffer').textContent = '–';
    buttons('Starting');
    const auth = extension ? await request('/api/redeem', {code: $('pairCode').value.trim()}) :
      await request('/api/ticket');
    $('pairCode').value = '';
    if (stopping) return;
    ws = new WebSocket(endpoint.replace('http:', 'ws:') + '/ws');
    ws.onopen = () => {
      send({ticket: auth.ticket, settings: {
        source_url: sourceInfo?.url || $('sourceUrl').value,
        source_kind: extension ? 'browser_tab' : 'display', source_authorized: true,
        source_language: $('sourceLanguage').value, target_language: $('targetLanguage').value,
        mode: $('mode').value, glossary}});
    };
    ws.onmessage = onMessage;
    ws.onerror = () => status('Local live service connection failed.', 'error');
    ws.onclose = () => { clearInterval(clockPingTimer); clockPingTimer = null;
      clockPings.clear(); if (!stopping) status('Connection closed. Capture has stopped.', 'error'); };
  } catch (error) { status(error.message, 'error'); }
}
async function capture() {
  try {
    buttons('Capturing');
    if (extension) {
      const info = await chrome.runtime.sendMessage({type: 'capture'});
      if (info.error) throw Error(info.error);
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {mandatory: {chromeMediaSource: 'tab', chromeMediaSourceId: info.streamId}},
        video: {mandatory: {chromeMediaSource: 'tab', chromeMediaSourceId: info.streamId,
          maxWidth: 1280, maxHeight: 720, maxFrameRate: 24}}});
      // Chrome may activate the captured YouTube tab when getUserMedia starts.
      // The extension player is a side panel on that same source tab, so keep
      // the source active instead of foregrounding a separate player tab.
      const focus = await chrome.runtime.sendMessage({type: 'focus_player'});
      if (focus?.error) throw Error(focus.error);
      if (document.visibilityState !== 'visible')
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(Error('Live player did not return to the foreground.')), 2000);
          document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') { clearTimeout(timeout); resolve(); }
          }, {once: true});
        });
    } else {
      stream = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 24, width: 1280, height: 720}, audio: true});
      $('warning').textContent = 'Display capture cannot mute its source automatically. Use the tab extension to suppress original audio reliably.';
    }
    if (!stream.getAudioTracks().length) throw Error('No shared tab audio. Stop and select a source with audio sharing enabled.');
    inputAudio = new AudioContext({sampleRate: 16000});
    await inputAudio.suspend();
    await inputAudio.audioWorklet.addModule(new URL('./capture-worklet.js', import.meta.url));
    worklet = new AudioWorkletNode(inputAudio, 'capture-pcm');
    inputAudio.createMediaStreamSource(stream).connect(worklet);
    worklet.connect(inputAudio.destination); // processor emits silence on this output
    player = new DelayedPlayer($('video'), send, status);
    player.minimumDelay = {quality: 4, balanced: 3, low_latency: 2}[$('mode').value];
    player.maximumAhead = maximumVideoBufferSeconds;
    player.setOriginal($('audioMode').value === 'original');
    let lastCaptureTelemetry = 0;
    worklet.port.onmessage = event => {
      if (event.data.flushed) { flushDone?.(); return; }
      if (ws?.readyState !== WebSocket.OPEN) return;
      const {sequence, start, pcm, context_time_end:contextTimeEnd} = event.data;
      const mediaEnd = start + pcm.byteLength/32000;
      const clockBefore = performance.now();
      const outputTimestamp = inputAudio.getOutputTimestamp?.();
      const clockAfter = performance.now();
      player.setCaptureClock(mediaEnd, contextTimeEnd, outputTimestamp);
      const packet = new ArrayBuffer(13 + pcm.byteLength);
      const view = new DataView(packet);
      view.setUint8(0, 1); view.setUint32(1, sequence, true); view.setFloat64(5, start, true);
      new Uint8Array(packet, 13).set(new Uint8Array(pcm));
      if (ws.bufferedAmount > 64000)
        return status('Input transport is behind; stopped without silently dropping chunks.', 'error');
      ws.send(packet);
      player.captureTime = mediaEnd;
      player.captureFrame(sequence);
      const elapsed = (performance.now() - player.captureWall) / 1000;
      if (elapsed - lastCaptureTelemetry > 1) {
        const samplePerformance = Number.isFinite(outputTimestamp?.performanceTime) &&
          Number.isFinite(contextTimeEnd) && Number.isFinite(outputTimestamp?.contextTime) ?
          outputTimestamp.performanceTime+(contextTimeEnd-outputTimestamp.contextTime)*1000 : null;
        send({type: 'capture_metrics', capture_elapsed_seconds: elapsed,
          capture_media_time: player.captureTime,
          audio_callback_delay_seconds: samplePerformance == null ? null :
            Math.max(0, (clockAfter-samplePerformance)/1000),
          capture_context_time:contextTimeEnd,
          capture_sample_end_performance_ms:samplePerformance,
          capture_clock_uncertainty_ms:(clockAfter-clockBefore)/2});
        lastCaptureTelemetry = elapsed;
      }
    };
    await player.attach(stream);
    await inputAudio.resume();
    for (const track of stream.getTracks()) track.addEventListener('ended', finishCapture, {once: true});
    $('translationState').textContent = 'Listening';
  } catch (error) { status(error.message, 'error'); }
}
async function finishCapture() {
  if (finishing || stopping || !worklet) return;
  finishing = true;
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error('Capture flush timed out')), 2000);
      flushDone = () => { clearTimeout(timeout); resolve(); };
      worklet.port.postMessage('flush');
    });
    if (extension) {
      const held = await chrome.runtime.sendMessage({type: 'hold_source_audio'});
      if (held.error) throw Error(held.error);
      if (!held.muted) throw Error('Could not suppress source audio while draining.');
      send({type: 'source_state', state: 'draining_source_muted', reason: held.reason});
    }
    player.finishCapture();
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    await inputAudio?.close(); inputAudio = null;
    send({type: 'eos'});
    buttons('Draining');
    record('Capture finished. Final PCM flushed before end of stream; playback is draining.');
  } catch (error) { status(error.message, 'error'); }
}
async function stop() {
  if (stopping) return;
  stopping = true;
  clearInterval(clockPingTimer); clockPingTimer = null; clockPings.clear();
  send({type: 'stop'});
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  stream?.getTracks().forEach(track => track.stop()); stream = null;
  if (inputAudio) { await inputAudio.close(); inputAudio = null; }
  if (player) { await player.stop(); player = null; }
  if (extension) await chrome.runtime.sendMessage({type: 'release_source_audio'}).catch(() => {});
  buttons('Inactive');
  $('delay').textContent = '—';
  $('buffer').textContent = '—';
  $('speaker').textContent = 'No speaker';
  $('translationState').textContent = 'Stopped; capture released';
}
$('start').addEventListener('click', start);
$('capture').addEventListener('click', capture);
$('finish').addEventListener('click', finishCapture);
$('stop').addEventListener('click', stop);
$('pause').addEventListener('click', () => {
  player?.pause(); send({type: 'pause'}); buttons('Paused');
  record('Presentation paused; a bounded capture buffer remains active.');
});
$('resume').addEventListener('click', async () => {
  await player?.resume(); send({type: 'resume'}); buttons('Capturing');
});
$('audioMode').addEventListener('change', () => player?.setOriginal($('audioMode').value === 'original'));
$('pair').addEventListener('click', async () => {
  try {
    $('pairOutput').textContent = (await request('/api/pair')).code;
    $('copyPair').disabled = false;
    $('copyPair').textContent = 'Copy pairing code';
  }
  catch (error) { $('pairOutput').textContent = error.message; }
});
$('copyPair').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('pairOutput').textContent);
    $('copyPair').textContent = 'Copied — expires after 120 seconds';
  } catch (error) { $('warning').textContent = 'Select and copy the pairing code: ' + error.message; }
});
window.addEventListener('pagehide', stop);
if (extension) {
  $('extensionPair').hidden = false;
  $('pairingDetails').hidden = true;
  chrome.runtime.onMessage.addListener(message => {
    if (message.type === 'source_invalidated' && message.sourceTabId === sourceInfo?.tabId)
      status(message.reason, 'error');
    if (message.type === 'source_visibility_lost' && message.sourceTabId === sourceInfo?.tabId && stream) {
      $('warning').textContent = message.reason;
      record(message.reason);
      finishCapture();
    }
  });
  try {
    sourceInfo = await chrome.runtime.sendMessage({type: 'source'});
    if (sourceInfo.error) throw Error(sourceInfo.error);
    $('sourceUrl').value = sourceInfo.url;
    $('sourceUrl').readOnly = true;
    $('sourceUrl').title = 'Selected with the extension action. To change source, use the extension on that tab.';
  } catch (error) { $('warning').textContent = error.message; }
} else {
  try {
    const data = await (await fetch('/api/status')).json();
    $('readiness').textContent = Object.entries(data.models)
      .filter(([, value]) => typeof value === 'boolean')
      .map(([key, value]) => key + ': ' + (value ? 'available' : 'setup required')).join(' · ');
  } catch (error) { $('readiness').textContent = error.message; }
}
document.addEventListener('visibilitychange', () => {
  if (extension && document.visibilityState === 'hidden' && stream && !finishing && !stopping) {
    const reason = 'The Live Dubbing panel was hidden. Capture is finishing to preserve source timing.';
    $('warning').textContent = reason;
    record(reason);
    finishCapture();
  }
});
