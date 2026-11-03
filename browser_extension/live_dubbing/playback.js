// Encoded source A/V stays in this browser. The local service receives PCM only.
export function estimateServerClockOffset(clientSentMs, serverReceivedMs, serverSentMs, clientReceivedMs) {
  const values = [clientSentMs, serverReceivedMs, serverSentMs, clientReceivedMs];
  if (!values.every(Number.isFinite) || clientReceivedMs < clientSentMs || serverSentMs < serverReceivedMs)
    return null;
  const roundTripMs = Math.max(0,
    (clientReceivedMs-clientSentMs)-(serverSentMs-serverReceivedMs));
  return {client_perf_to_server_monotonic_offset_ms:
      ((serverReceivedMs-clientSentMs)+(serverSentMs-clientReceivedMs))/2,
    round_trip_ms:roundTripMs, uncertainty_ms:roundTripMs/2};
}

export function phraseVideoRate(sourceDuration, presentationDuration) {
  if (!Number.isFinite(sourceDuration) || sourceDuration <= 0 ||
      !Number.isFinite(presentationDuration) || presentationDuration <= 0)
    throw Error('Invalid phrase presentation duration');
  const requested = sourceDuration/presentationDuration;
  const applied = Math.max(.25, Math.min(1.5, requested));
  return {requested, applied, clamped:Math.abs(applied-requested) > 1e-9};
}

export function hasReachedFinalDrainFrontier({videoEnded=false, currentTime, bufferEnd,
  captureTime}) {
  if (videoEnded) return true;
  if (![currentTime, bufferEnd].every(Number.isFinite) || bufferEnd <= 0) return false;
  // Ordinary playback stops 40 ms before the analyzed source frontier. A final
  // encoded WebM segment may then leave a short container tail after that
  // frontier. Treat that bounded tail as drained once the source frontier has
  // been reached; otherwise the 80 ms end test can require 12 ms more video
  // than ordinary playback is allowed to present (observed at real EOS).
  if (currentTime >= bufferEnd - .08) return true;
  if (!Number.isFinite(captureTime)) return false;
  const encodedTail = bufferEnd - captureTime;
  const frontierGap = captureTime - currentTime;
  const bufferedTail = bufferEnd - currentTime;
  return encodedTail >= 0 && encodedTail <= .12 &&
    frontierGap >= 0 && frontierGap <= .045 && bufferedTail <= .15;
}

export class DelayedPlayer {
  constructor(video, send, status) {
    Object.assign(this, {video, send, status, captureTime: 0, analyzedUntil: 0,
      minimumDelay: 3, maximumAhead: 20, paused: false, stopped: false, active: null, bytes: 0,
      frameCount: 0, lastTelemetry: 0, original: false, sourceEnded: false,
      frameSamples: [], captureClock: null, overloadFinishRequested: false});
    this.phrases = new Map();
    this.holds = new Set();
    this.pendingSources = new Map();
    this.pendingBlobs = [];
    this.objects = [];
    this.audio = new AudioContext({sampleRate: 48000});
    this.gain = this.audio.createGain();
    this.gain.connect(this.audio.destination);
  }

  async attach(stream) {
    const mime = 'video/webm;codecs=vp8,opus';
    if (!MediaRecorder.isTypeSupported(mime) || !MediaSource.isTypeSupported(mime))
      throw Error('This browser cannot buffer the captured A/V format.');
    this.mediaSource = new MediaSource();
    const url = URL.createObjectURL(this.mediaSource);
    this.objects.push(url);
    this.video.src = url;
    await new Promise(resolve => this.mediaSource.addEventListener('sourceopen', resolve, {once: true}));
    this.sourceBuffer = this.mediaSource.addSourceBuffer(mime);
    this.sourceBuffer.addEventListener('updateend', () => this.appendNext());
    // A paused/silent captured track can omit initial encoded time. A real
    // paired Chrome test lost ~1 s without this continuous audio clock. Keep
    // source audio at unity gain and add digital zero, so MediaRecorder sees
    // time even before source speech arrives. This stream stays in-browser;
    // ASR continues to receive the original capture Worklet PCM.
    this.recordingAudio = new AudioContext({sampleRate: 48000});
    const destination = this.recordingAudio.createMediaStreamDestination();
    this.recordingAudio.createMediaStreamSource(stream).connect(destination);
    this.recordingSilence = this.recordingAudio.createConstantSource();
    this.recordingSilence.offset.value = 0;
    this.recordingSilence.connect(destination);
    this.recordingSilence.start();
    // tabCapture can stop emitting frames during a static logo while audio
    // keeps arriving. Record the latest permitted frame at a continuous rate;
    // otherwise MSE can stall behind its apparently buffered audio frontier.
    this.recordingVideo = document.createElement('video');
    this.recordingVideo.muted = true; this.recordingVideo.playsInline = true;
    this.recordingVideo.srcObject = new MediaStream(stream.getVideoTracks());
    this.recordingVideo.play().catch(error => {
      if (error.name !== 'AbortError' && !this.stopped) this.status(error.message, 'error');
    });
    const settings = stream.getVideoTracks()[0].getSettings();
    const canvas = document.createElement('canvas');
    canvas.width = settings.width || 1280; canvas.height = settings.height || 720;
    const context = canvas.getContext('2d', {alpha: false});
    // Declare the actual 25 fps cadence. captureStream(0) caused a 1-second
    // WebM DefaultDuration in the real tab recording, despite requested frames.
    const canvasStream = canvas.captureStream(25);
    const frameTrack = canvasStream.getVideoTracks()[0];
    if (!frameTrack.requestFrame) throw Error('Continuous captured-video frames are unsupported in this browser.');
    const paint = () => {
      if (this.recordingVideo?.readyState >= 2)
        context.drawImage(this.recordingVideo, 0, 0, canvas.width, canvas.height);
      else context.fillRect(0, 0, canvas.width, canvas.height);
      frameTrack.requestFrame();
    };
    this.paintRecordingFrame = paint;
    paint();
    this.recordingStream = new MediaStream([frameTrack, ...destination.stream.getAudioTracks()]);
    await this.recordingAudio.resume();
    // MSE removal extends to the next random-access frame. Unconstrained
    // recorder GOPs can delete frames ahead of the playhead when pruning a
    // three-second history (observed in real Chrome tab capture).
    this.recorder = new MediaRecorder(this.recordingStream, {mimeType: mime, videoBitsPerSecond: 1800000,
      videoKeyFrameIntervalDuration: 1000});
    this.recorder.ondataavailable = event => {
      if (!event.data.size || this.stopped) return;
      this.bytes += event.data.size;
      if (this.bytes > 32 * 1024 * 1024)
        return this.status('Encoded buffer exceeded 32 MB. Stop and restart at a clean boundary.', 'error');
      this.pendingBlobs.push(event.data);
      this.appendNext();
    };
    this.recorder.onstop = () => {
      this.recorderEnded = true; this.appendNext();
      this.closeRecordingMedia().catch(error => this.status(error.message, 'error'));
    };
    this.recorder.onerror = event => this.status(event.error?.message || 'Media recorder error', 'error');
    this.captureWall = performance.now();
    this.recorder.start(250);
    paint(); // The initial requested frame must also reach the started recorder.
    this.video.requestVideoFrameCallback((now, metadata) => this.frame(now, metadata));
    this.tick();
  }

  async appendNext() {
    if (this.appending || this.stopped || !this.sourceBuffer || this.sourceBuffer.updating) return;
    if (!this.pendingBlobs.length) {
      if (this.recorderEnded && this.mediaSource.readyState === 'open') this.mediaSource.endOfStream();
      return;
    }
    this.appending = true;
    try { this.sourceBuffer.appendBuffer(await this.pendingBlobs.shift().arrayBuffer()); }
    catch (error) { this.status(error.message, 'error'); }
    finally { this.appending = false; }
  }

  async closeRecordingMedia() {
    const clock = this.recordingAudio;
    this.recordingAudio = null;
    this.paintRecordingFrame = null;
    if (this.recordingVideo) {
      this.recordingVideo.pause(); this.recordingVideo.srcObject = null; this.recordingVideo = null;
    }
    this.recordingSilence?.stop(); this.recordingSilence = null;
    this.recordingStream?.getTracks().forEach(track => track.stop());
    this.recordingStream = null;
    await clock?.close();
  }

  captureFrame(sequence) {
    // The existing Worklet emits continuous 20 ms packets, including silence.
    // Every second packet supplies a frame; background tab timer throttling
    // must not replace this common media clock with 1 fps setInterval callbacks.
    if (sequence % 2 === 1) this.paintRecordingFrame?.();
  }

  frame(now, metadata) {
    if (this.stopped) return;
    this.frameCount = metadata.presentedFrames;
    this.presentedMediaTime = metadata.mediaTime;
    this.lastPresentedWall = now;
    const expectedDisplayTime = Number.isFinite(metadata.expectedDisplayTime) ? metadata.expectedDisplayTime : now;
    const sample = {expectedDisplayTime, callbackTime:now, mediaTime:metadata.mediaTime,
      presentedFrames:metadata.presentedFrames};
    this.frameSamples.push(sample);
    if (this.frameSamples.length > 300) this.frameSamples.shift();
    const active = this.active;
    if (active?.videoPlayRequestedAt != null && active.videoFirstFrame == null &&
        !this.video.paused && metadata.expectedDisplayTime >= active.videoPlayRequestedAt - 1 &&
        metadata.mediaTime >= active.source_start - .06) active.videoFirstFrame = sample;
    this.video.requestVideoFrameCallback((n, m) => this.frame(n, m));
  }
  setCaptureClock(mediaEnd, contextTimeEnd, outputTimestamp) {
    if (!Number.isFinite(mediaEnd) || !Number.isFinite(contextTimeEnd) ||
        !Number.isFinite(outputTimestamp?.contextTime) || !Number.isFinite(outputTimestamp?.performanceTime)) return;
    this.captureClock = {mediaEnd,
      performanceTime:outputTimestamp.performanceTime + (contextTimeEnd-outputTimestamp.contextTime)*1000};
  }
  capturePerformanceTime(mediaTime) {
    if (!this.captureClock || !Number.isFinite(mediaTime)) return null;
    return this.captureClock.performanceTime - (this.captureClock.mediaEnd-mediaTime)*1000;
  }
  outputClock(contextTime) {
    const callStarted = performance.now();
    const timestamp = this.audio.getOutputTimestamp?.();
    const callCompleted = performance.now();
    if (Number.isFinite(timestamp?.contextTime) && Number.isFinite(timestamp?.performanceTime)) {
      return {performanceTime:timestamp.performanceTime + (contextTime-timestamp.contextTime)*1000,
        uncertaintyMs:(callCompleted-callStarted)/2, source:'audio_output_timestamp'};
    }
    return {performanceTime:callCompleted+(contextTime-(this.audio.currentTime ?? contextTime))*1000,
      uncertaintyMs:null, source:'context_clock_estimate'};
  }
  sourceHold(start) { this.holds.add(start); }
  committed(message) {
    this.pendingSources.set(message.phrase_id, message.source_start);
  }
  coverage(message) {
    this.analyzedUntil = message.analyzed_until;
    if (message.uncommitted_start !== null) this.sourceHold(message.uncommitted_start);
  }
  phrase(metadata) {
    if (this.phrases.has(metadata.phrase_id)) throw Error('Duplicate phrase ID');
    if (this.phrases.size >= 12) throw Error('Bounded playback queue is full');
    this.phrases.set(metadata.phrase_id, {...metadata, parts: [], next: 0,
      ready: false, received: performance.now()});
  }
  chunk(message) {
    const item = this.phrases.get(message.phrase_id);
    if (!item || message.sequence !== item.next++) throw Error('PCM request/sequence mismatch');
    const raw = atob(message.pcm);
    if (raw.length % 2) throw Error('Invalid PCM16 byte alignment');
    const bytes = Uint8Array.from(raw, character => character.charCodeAt(0));
    item.parts.push(new Int16Array(bytes.buffer));
  }
  ready(id) {
    const item = this.phrases.get(id);
    if (!item || item.next !== item.chunks) throw Error('Incomplete PCM stream');
    const generatedCount = item.parts.reduce((n, part) => n + part.length, 0);
    const captionOnly = item.presentation_mode === 'caption_only';
    if ((!generatedCount && !captionOnly) || generatedCount > 48000 * 30 ||
        (captionOnly && generatedCount)) throw Error('Invalid bounded phrase duration');
    // Keep all source video when native speech is shorter than the maximum
    // 1.5x video rate can cover. Preserve natural speech and append silence;
    // otherwise audio.onended seeks past still-unpresented source frames.
    const minimumCount = Math.ceil((item.source_end - item.source_start) / 1.5 * 48000);
    // This silent buffer is a presentation clock, never synthesized speech.
    const count = captionOnly ? Math.max(1, Math.ceil((item.source_end-item.source_start)*48000)) :
      Math.max(generatedCount, minimumCount);
    if (count > 48000 * 30) throw Error('Invalid bounded presentation duration');
    item.generatedAudioSeconds = generatedCount / 48000;
    item.silencePaddingSeconds = (count-generatedCount) / 48000;
    item.buffer = this.audio.createBuffer(1, count, 48000);
    const values = item.buffer.getChannelData(0);
    let offset = 0;
    for (const part of item.parts) for (const sample of part) values[offset++] = sample / 32768;
    item.parts = [];
    item.ready = true;
    item.readyAt = performance.now();
    item.playbackQueueDepthAtAdmission = this.phrases.size;
    const demand = this.audioDemand();
    item.pendingSpeechAudioSecondsAtReady = demand.pendingSpeechSeconds;
    item.pendingPresentationAudioSecondsAtReady = demand.pendingPresentationSeconds;
    this.pendingSources?.delete(id);
    for (const start of this.holds) if (start <= item.source_end + .04) this.holds.delete(start);
  }
  audioDemand(excludePhraseId = null) {
    let pendingSpeechSeconds = 0;
    let pendingPresentationSeconds = 0;
    let activeSpeechSeconds = 0;
    let queuedSpeechSeconds = 0;
    for (const [id, item] of this.phrases) {
      if (id === excludePhraseId || !item.ready) continue;
      const active = item === this.active;
      const elapsed = active && Number.isFinite(item.audioStart)
        ? Math.max(0, this.audio.currentTime-item.audioStart) : 0;
      const speech = Math.max(0, item.generatedAudioSeconds-elapsed);
      const presentation = Math.max(0, item.buffer.duration-elapsed);
      pendingSpeechSeconds += speech;
      pendingPresentationSeconds += presentation;
      if (active) activeSpeechSeconds += speech;
      else queuedSpeechSeconds += speech;
    }
    return {pendingSpeechSeconds, pendingPresentationSeconds,
      activeSpeechSeconds, queuedSpeechSeconds};
  }
  setOriginal(value) {
    this.original = value;
    this.video.muted = !value;
    this.gain.gain.value = value ? 0 : 1;
  }
  pause() { this.paused = true; this.video.pause(); this.audio.suspend(); }
  async resume() { this.paused = false; await this.audio.resume(); }
  playVideo() {
    if (this.stopped || this.paused || this.drained ||
        (this.sourceEnded && this.video.ended) || !this.video.paused || this.playRequest) return;
    this.playRequest = Promise.resolve().then(() => {
      if (!this.stopped && !this.paused && !this.drained &&
          !(this.sourceEnded && this.video.ended)) return this.video.play();
    }).catch(error => {
      // A new hold/pause can legitimately cancel an asynchronous play().
      // Browser permission/decoder errors still fail visibly.
      if (error.name !== 'AbortError' && !this.stopped) this.status(error.message, 'error');
    }).finally(() => { this.playRequest = null; });
    return this.playRequest;
  }

  async begin(item) {
    this.active = item; // Set before awaiting; tick must never schedule twice.
    this.video.pause();
    if (Math.abs(this.video.currentTime - item.source_start) > .025) {
      const seek = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(Error('Delayed video seek timed out')), 3000);
        this.video.addEventListener('seeked', () => { clearTimeout(timeout); resolve(); }, {once: true});
      });
      this.video.currentTime = item.source_start;
      await seek;
    }
    if (this.video.readyState < 2) {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          this.video.removeEventListener('loadeddata', available);
          this.video.removeEventListener('canplay', available);
          reject(Error('Captured video was not ready; translated audio was not played.'));
        }, 3000);
        const available = () => {
          if (this.video.readyState < 2) return;
          clearTimeout(timeout);
          this.video.removeEventListener('loadeddata', available);
          this.video.removeEventListener('canplay', available);
          resolve();
        };
        this.video.addEventListener('loadeddata', available);
        this.video.addEventListener('canplay', available);
      });
    }
    await this.audio.resume();
    if (this.stopped || this.paused) return;
    const rate = phraseVideoRate(item.source_end-item.source_start, item.buffer.duration);
    item.videoRate = rate.applied;
    item.requestedVideoRate = rate.requested;
    item.videoRateClamped = rate.clamped;
    this.video.playbackRate = item.videoRate;
    // Start the delayed video before scheduling its translated audio. The
    // previous order waited for the next animation-frame tick after audio had
    // started; throttled extension panels could leave video paused for almost
    // a second on every phrase, causing measurable A/V error and lost playback
    // throughput. Await Chrome's play promise so the media element has actually
    // accepted playback before the common audio-output clock is scheduled.
    item.videoPlayRequestedAt = performance.now();
    try {
      await this.video.play();
      item.videoPlayResolvedAt = performance.now();
      item.videoPlaySucceeded = true;
    } catch (error) {
      item.videoPlayResolvedAt = performance.now();
      item.videoPlaySucceeded = false;
      if (error.name !== 'AbortError' && !this.stopped) this.status(error.message, 'error');
    }
    if (this.stopped || this.paused) {
      this.video.pause();
      return;
    }
    const node = this.audio.createBufferSource();
    node.buffer = item.buffer;
    node.connect(this.gain);
    item.audioStart = this.audio.currentTime + .10;
    item.videoStart = this.video.currentTime;
    item.audioScheduleRequestedAt = performance.now();
    item.audioScheduleWallTime = item.audioScheduleRequestedAt + (item.audioStart-this.audio.currentTime)*1000;
    const outputStart = this.outputClock(item.audioStart);
    item.audioOutputStartPerformanceTime = outputStart.performanceTime;
    item.audioOutputTimestampUncertaintyMs = outputStart.uncertaintyMs;
    item.audioOutputClockSource = outputStart.source;
    item.playbackStartWall = outputStart.performanceTime;
    item.node = node;
    node.onended = () => this.end(item);
    node.start(item.audioStart);
    this.status(item.target_text, 'playing', item);
  }
  end(item) {
    if (this.stopped) return;
    this.video.pause();
    // Audio can end while the displayed video is still behind the source
    // phrase boundary, for example when its requested rate was clamped or
    // the browser temporarily stalled. Seeking forwards here silently drops
    // the remaining source frames. Let the ordinary analyzed-gap playback
    // reach the boundary instead, and expose the residual to telemetry.
    const videoCatchupSeconds = Math.max(0, item.source_end-this.video.currentTime);
    const videoEnd = this.presentedMediaTime ?? this.video.currentTime;
    const outputEnd = this.outputClock(this.audio.currentTime);
    const audioOutputEnd = outputEnd.performanceTime;
    const sourceStartWall = this.capturePerformanceTime(item.source_start);
    const sourceEndWall = this.capturePerformanceTime(item.source_end);
    const captureToStart = sourceStartWall == null ? (item.playbackStartWall-this.captureWall)/1000 :
      (item.playbackStartWall-sourceStartWall)/1000;
    const captureToPlayback = sourceStartWall == null ? captureToStart-item.source_start : captureToStart;
    const lastFrameAtOutput = [...this.frameSamples].reverse().find(frame =>
      frame.expectedDisplayTime <= audioOutputEnd) || null;
    let audioVideoOffset = null;
    if (Number.isFinite(item.playbackStartWall) && lastFrameAtOutput) {
      const expectedMediaTime = item.source_start +
        ((audioOutputEnd-item.playbackStartWall)/1000)*item.videoRate;
      audioVideoOffset = lastFrameAtOutput.mediaTime-expectedMediaTime;
    }
    const pendingDemand = this.audioDemand(item.phrase_id);
    this.send({type: 'played', phrase_id: item.phrase_id, video_start: item.videoStart,
      video_end: videoEnd, audio_context_start: item.audioStart,
      audio_context_end: this.audio.currentTime,
      observed_av_offset_seconds: audioVideoOffset,
      audio_video_offset_seconds:audioVideoOffset,
      audio_output_start_performance_ms:item.playbackStartWall,
      audio_output_end_performance_ms:audioOutputEnd,
      audio_output_started_after_schedule_ms:item.playbackStartWall-item.audioScheduleWallTime,
      audio_output_schedule_performance_ms:item.audioScheduleWallTime,
      audio_output_clock_source:item.audioOutputClockSource,
      audio_output_timestamp_uncertainty_ms:item.audioOutputTimestampUncertaintyMs,
      playback_admission_performance_ms:item.readyAt,
      playback_admission_queue_depth:item.playbackQueueDepthAtAdmission,
      video_play_request_to_first_frame_ms:item.videoFirstFrame ?
        item.videoFirstFrame.expectedDisplayTime-item.videoPlayRequestedAt : null,
      video_play_resolution_to_audio_schedule_ms:item.videoPlayResolvedAt != null ?
        item.audioScheduleWallTime-item.videoPlayResolvedAt : null,
      video_play_succeeded:item.videoPlaySucceeded ?? null,
      video_first_frame_delay_ms:item.videoFirstFrame ?
        item.videoFirstFrame.expectedDisplayTime-item.playbackStartWall : null,
      video_frame_expected_display_time_ms:lastFrameAtOutput?.expectedDisplayTime ?? null,
      video_frame_media_time:lastFrameAtOutput?.mediaTime ?? null,
      live_edge_to_displayed_video_seconds:lastFrameAtOutput ?
        this.captureTime-lastFrameAtOutput.mediaTime : null,
      playback_queue_seconds: (item.playbackStartWall - item.readyAt) / 1000,
      capture_to_playback_seconds: captureToPlayback,
      speech_end_to_playback_seconds: sourceEndWall == null ? captureToStart - item.source_end :
        (item.playbackStartWall-sourceEndWall)/1000,
      generated_audio_seconds: item.generatedAudioSeconds,
      presentation_audio_seconds: item.buffer.duration,
      silence_padding_seconds: item.silencePaddingSeconds,
      pending_speech_audio_seconds: pendingDemand.pendingSpeechSeconds,
      pending_presentation_audio_seconds: pendingDemand.pendingPresentationSeconds,
      video_playback_rate: item.videoRate,
      requested_video_playback_rate: item.requestedVideoRate ?? item.videoRate,
      video_playback_rate_clamped: item.videoRateClamped ?? false,
      video_source_end_catchup_seconds: videoCatchupSeconds,
      presented_frames: this.frameCount});
    this.phrases.delete(item.phrase_id);
    this.active = null;
    // Retain the existing correction for an already overshot boundary, but
    // never seek forward past unseen video when translated audio finishes.
    if (this.video.currentTime >= item.source_end) this.video.currentTime = item.source_end;
    this.video.playbackRate = 1;
  }
  finishCapture() {
    this.sourceEnded = true;
    if (this.recorder?.state !== 'inactive') this.recorder.stop();
  }
  finishInference() {
    this.inferenceEnded = true;
    this.holds.clear();
    this.analyzedUntil = this.captureTime;
  }
  tick() {
    if (this.stopped) return;
    const now = performance.now();
    const ranges = this.video.buffered;
    const end = ranges.length ? ranges.end(ranges.length - 1) : 0;
    const held = Math.min(Infinity, ...this.holds, ...this.pendingSources.values());
    const next = [...this.phrases.values()].sort((a, b) => a.source_start - b.source_start)[0];
    const current = this.video.currentTime;
    // The PCM capture clock can extend a few samples beyond the last encoded
    // video frame. Calling play() at a native ended element rewinds it to zero.
    // Finish before scheduling another play promise, including already queued
    // promises, so a normal drain cannot become replay/backlog failure.
    if (this.inferenceEnded && this.recorderEnded && this.mediaSource?.readyState === 'ended' &&
        !this.active && !next && hasReachedFinalDrainFrontier({videoEnded:this.video.ended,
          currentTime:current, bufferEnd:end, captureTime:this.captureTime})) {
      this.drained = true;
      this.video.pause();
      this.send({type: 'source_state', state: 'playback_drained',
        reason: 'All validated phrases played and buffered source reached its end.'});
      this.status('Source and validated translations finished. Stop to release models.', 'drained');
      return;
    }
    // Once every confirmed phrase has played, a recorder may still contain
    // encoded frames beyond the PCM capture frontier. Present that remaining
    // tail instead of pausing at the PCM frontier forever. Do not advance
    // across pending source phrases or change the normal live buffer bounds.
    const finalEncodedTail = this.sourceEnded && this.inferenceEnded && this.recorderEnded &&
      this.mediaSource?.readyState === 'ended' && !this.active && !next &&
      this.pendingSources.size === 0 && Number.isFinite(this.captureTime) &&
      current >= this.captureTime - .045 && current < end - .08;
    const frontier = Math.min(this.analyzedUntil, held, next ? next.source_start : Infinity);
    if (this.active) {
      if (!this.paused && this.active.node && this.audio.currentTime >= this.active.audioStart) {
        if (current < this.active.source_end - .015)
          this.playVideo();
        else this.video.pause();
      }
    } else if (!this.paused) {
      // Catch up only through analyzed gaps. Active translated speech keeps
      // its natural audio clock and the phrase-specific video rate.
      this.video.playbackRate = this.captureTime-current > this.minimumDelay+2 ? 1.5 : 1;
      if (finalEncodedTail) {
        this.video.playbackRate = 1;
        this.playVideo();
      } else if (next?.ready && current >= next.source_start - .05)
        this.begin(next).catch(error => this.status(error.message, 'error'));
      else if (current < Math.min(frontier, end) - .04 && (this.sourceEnded || end - current > this.minimumDelay))
        this.playVideo();
      else this.video.pause();
    }
    if (!this.sourceEnded && end - current > this.maximumAhead) {
      if (!this.overloadFinishRequested) {
        this.overloadFinishRequested = true;
        this.send({type:'source_state', state:'buffer_limit_finish',
          reason:'bounded_video_buffer_exceeded'});
        this.status('Live delay reached the bounded ' + this.maximumAhead +
          '-second limit. Capture is stopping and confirmed phrases will drain; source after the cutoff will not be dubbed.',
          'overload');
      }
      // Keep the playback clock alive while the async PCM flush and EOS
      // handshake finish. Once sourceEnded is set, this finite buffer drains.
      requestAnimationFrame(() => this.tick());
      return;
    }
    if (now - this.lastTelemetry > 1000) {
      let offset = null;
      if (this.active?.node) {
        const outputNow = this.outputClock(this.audio.currentTime);
        const progress = Math.max(0, Math.min(1,
          (outputNow.performanceTime-this.active.audioOutputStartPerformanceTime)/
            (1000*this.active.buffer.duration)));
        offset = (this.presentedMediaTime ?? current) - (this.active.source_start +
          progress * (this.active.source_end - this.active.source_start));
      }
      const demand = this.audioDemand();
      this.send({type: 'playback_metrics', video_media_time: this.presentedMediaTime ?? current,
        video_current_time: current, video_ready_state: this.video.readyState,
        video_paused: this.video.paused, video_seeking: this.video.seeking,
        original_audio_enabled: this.original, source_video_muted: this.video.muted,
        translated_gain: this.gain.gain.value,
        frame_callback_age_seconds: this.lastPresentedWall == null ? null : (now-this.lastPresentedWall)/1000,
        capture_media_time: this.captureTime, buffer_seconds: end - current,
        buffer_start_seconds: ranges.length ? ranges.start(0) : null,
        audio_context_time: this.audio.currentTime, presented_frames: this.frameCount,
        av_offset_seconds: offset, queue_depth: this.phrases.size, buffer_bytes: this.bytes,
        pending_speech_audio_seconds:demand.pendingSpeechSeconds,
        pending_presentation_audio_seconds:demand.pendingPresentationSeconds,
        active_speech_audio_seconds:demand.activeSpeechSeconds,
        queued_speech_audio_seconds:demand.queuedSpeechSeconds,
        video_playback_rate: this.video.playbackRate});
      this.status({delay: this.captureTime - current, buffer: end - current}, 'metrics');
      this.lastTelemetry = now;
      const retainedPosition = Math.min(current, this.presentedMediaTime ?? current,
        ...this.pendingSources.values());
      if (!this.video.seeking && !this.appending && !this.sourceBuffer?.updating && retainedPosition > 4 && this.sourceBuffer?.buffered.length &&
          this.sourceBuffer.buffered.start(0) < retainedPosition - 3) {
        try {
          const start = this.sourceBuffer.buffered.start(0);
          this.sourceBuffer.remove(start, retainedPosition - 3);
          this.bytes = Math.round(this.bytes * Math.max(0, (end - retainedPosition + 3) / (end - start)));
        } catch (error) { this.status(error.message, 'error'); }
      }
    }
    requestAnimationFrame(() => this.tick());
  }
  async stop() {
    this.stopped = true;
    this.video.pause();
    if (this.recorder?.state !== 'inactive') this.recorder?.stop();
    if (this.active?.node) { this.active.node.onended = null; this.active.node.stop(); }
    this.phrases.clear(); this.pendingBlobs = []; this.holds.clear(); this.pendingSources.clear();
    await this.audio.close();
    await this.closeRecordingMedia();
    this.video.removeAttribute('src'); this.video.load();
    for (const url of this.objects) URL.revokeObjectURL(url);
  }
}
