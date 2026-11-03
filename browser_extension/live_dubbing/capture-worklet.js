// AudioContext resamples the explicitly selected source to 16 kHz; no microphone.
class CapturePCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = []; this.sent = 0; this.sequence = 0; this.finished = false;
    this.lastContextEnd = currentTime;
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        this.finished = true;
        this.emit(this.lastContextEnd);
        this.port.postMessage({flushed: true, samples: this.sent});
      }
    };
  }
  emit(contextTimeEnd) {
    if (!this.pending.length) return;
    const pcm = new Int16Array(this.pending);
    this.port.postMessage({sequence: this.sequence++, start: this.sent / 16000,
      context_time_start: contextTimeEnd-this.pending.length/sampleRate,
      context_time_end: contextTimeEnd,
      pcm: pcm.buffer}, [pcm.buffer]);
    this.sent += this.pending.length;
    this.pending = [];
  }
  process(inputs, outputs) {
    if (this.finished) return true;
    this.lastContextEnd = currentTime;
    const channels = inputs[0] || [];
    // A paused/silent tab can expose zero input channels. Preserve the same
    // continuous AudioContext clock as the video recorder by sending silence
    // for those real render quanta, rather than deleting elapsed source time.
    const length = channels[0]?.length || outputs[0]?.[0]?.length || 128;
    for (let i = 0; i < length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] / channels.length;
      this.pending.push(Math.round(Math.max(-1, Math.min(1, value)) * 32767));
      this.lastContextEnd = currentTime + (i+1)/sampleRate;
      if (this.pending.length === 320) this.emit(this.lastContextEnd);
    }
    return true;
  }
}
registerProcessor('capture-pcm', CapturePCM);
