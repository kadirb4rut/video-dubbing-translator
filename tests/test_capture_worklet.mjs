// Regression for a real browser failure: silence must not delete media time.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const packets = [];
let Processor;
let renderTime = 0;
const workletGlobal = {
  AudioWorkletProcessor: class {constructor() {this.port = {postMessage: value => packets.push(value)};}},
  registerProcessor: (_name, implementation) => {Processor = implementation;}, Int16Array, Math,
  sampleRate:16000,
};
Object.defineProperty(workletGlobal, 'currentTime', {get:() => renderTime});
vm.runInNewContext(fs.readFileSync(new URL('../browser_extension/live_dubbing/capture-worklet.js', import.meta.url), 'utf8'), workletGlobal);
const processor = new Processor();
const output = [[new Float32Array(128)]];
for (let i = 0; i < 80; i++) { renderTime=i*128/16000; processor.process([[]], output); }
assert.equal(packets.length, 32);
assert.equal(packets[31].start, .62);
assert.ok(packets.every(packet => [...new Int16Array(packet.pcm)].every(sample => sample === 0)));
for (let i = 80; i < 161; i++) { renderTime=i*128/16000; processor.process([[new Float32Array(128).fill(.25)]], output); }
processor.port.onmessage({data: 'flush'});
const ack = packets.pop();
assert.equal(ack.flushed, true);
assert.equal(ack.samples, 161 * 128);
let samples = 0;
for (const [sequence, packet] of packets.entries()) {
  assert.equal(packet.sequence, sequence);
  assert.equal(packet.start, samples / 16000);
  assert.ok(Math.abs(packet.context_time_start-samples/16000)<1e-9);
  assert.ok(Math.abs(packet.context_time_end-(samples+packet.pcm.byteLength/2)/16000)<1e-9);
  samples += packet.pcm.byteLength / 2;
}
assert.equal(samples, 161 * 128);
const count = packets.length;
processor.process([[new Float32Array(128)]], output);
assert.equal(packets.length, count);
console.log('PASS: empty-input silence, speech, clock continuity, final partial packet, one EOS flush');
