// Regression for Chrome's real play/pause AbortError; no media/GPU simulation claim.
import assert from 'node:assert/strict';
import fs from 'node:fs';
globalThis.AudioContext = class {createGain() {return {connect() {}};}};
const source = fs.readFileSync(new URL('../browser_extension/live_dubbing/playback.js', import.meta.url), 'utf8');
const {DelayedPlayer,estimateServerClockOffset,phraseVideoRate,hasReachedFinalDrainFrontier} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const errors = [];
let calls = 0;
const video = {paused: true, play() {calls++; return Promise.reject(Object.assign(Error('held'), {name:'AbortError'}));}};
const player = new DelayedPlayer(video, () => {}, (value, kind) => {if (kind === 'error') errors.push(value);});
const first = player.playVideo(); player.playVideo();
await first;
assert.equal(calls, 1);
assert.equal(errors.length, 0);
video.play = () => Promise.reject(Object.assign(Error('denied'), {name:'NotAllowedError'}));
await player.playVideo();
assert.deepEqual(errors, ['denied']);
player.paused = true; assert.equal(player.playVideo(), undefined);
player.paused = false; player.sourceEnded = true; video.ended = true;
assert.equal(player.playVideo(), undefined);
video.ended = false; player.drained = true; assert.equal(player.playVideo(), undefined);
player.drained = false; player.sourceEnded = false;
const clocked = Object.assign(Object.create(DelayedPlayer.prototype), {
  audio:{currentTime:5, getOutputTimestamp:()=>({contextTime:5,performanceTime:2000})},
});
assert.deepEqual(estimateServerClockOffset(1000,1350,1354,1204), {
  client_perf_to_server_monotonic_offset_ms:250, round_trip_ms:200, uncertainty_ms:100,
});
assert.equal(estimateServerClockOffset(10,9,8,7),null);
assert.equal(clocked.outputClock(5.125).performanceTime, 2125);
clocked.setCaptureClock(2, 8, {contextTime:8,performanceTime:4000});
assert.equal(clocked.capturePerformanceTime(1.25), 3250);
const longPhraseRate=phraseVideoRate(1,2.546);
assert.ok(Math.abs(longPhraseRate.applied-(1/2.546))<1e-9);
assert.equal(longPhraseRate.clamped,false);
assert.equal(phraseVideoRate(1,8).applied,.25);
assert.equal(phraseVideoRate(1,8).clamped,true);
assert.throws(()=>phraseVideoRate(1,0),/Invalid phrase presentation duration/);
assert.equal(hasReachedFinalDrainFrontier({videoEnded:false,currentTime:602,bufferEnd:602.092,captureTime:602.04}),true,
  'allow the observed 92 ms encoded tail after playback reached the 40 ms source frontier');
assert.equal(hasReachedFinalDrainFrontier({videoEnded:false,currentTime:602,bufferEnd:602.25,captureTime:602.04}),false,
  'do not call a materially larger buffered tail drained');
assert.equal(hasReachedFinalDrainFrontier({videoEnded:false,currentTime:602,bufferEnd:602.092,captureTime:602.06}),false,
  'do not finish while playback is more than 45 ms behind the captured frontier');
assert.equal(hasReachedFinalDrainFrontier({videoEnded:true,currentTime:0,bufferEnd:0,captureTime:0}),true);
const drainSignals=[];
const finalTailVideo={currentTime:602,paused:true,ended:false,playbackRate:1,
  buffered:{length:1,start:()=>599.293,end:()=>602.092},pause(){this.paused=true;}};
const finalTailPlayer=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:finalTailVideo,captureTime:602.04,analyzedUntil:602.04,
  sourceEnded:true,inferenceEnded:true,recorderEnded:true,mediaSource:{readyState:'ended'},
  active:null,phrases:new Map(),holds:new Set(),pendingSources:new Map(),
  lastTelemetry:performance.now(),send:value=>drainSignals.push(value),status() {},
});
finalTailPlayer.tick();
assert.equal(drainSignals[0]?.state,'playback_drained',
  'Finish must complete after the last acknowledged phrase and bounded final WebM tail');
const previousAnimationFrame = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame=()=>{};
let tailPlayCalls=0;
const longerTailVideo={currentTime:341.523611,paused:true,ended:false,playbackRate:1,
  buffered:{length:1,start:()=>338,end:()=>341.848},
  pause(){this.paused=true;}, play(){tailPlayCalls++;this.paused=false;return Promise.resolve();}};
const longerTailSignals=[];
const longerTailPlayer=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,paused:false,video:longerTailVideo,captureTime:341.536,analyzedUntil:341.536,
  sourceEnded:true,inferenceEnded:true,recorderEnded:true,mediaSource:{readyState:'ended'},
  active:null,phrases:new Map(),holds:new Set(),pendingSources:new Map(),
  lastTelemetry:performance.now(),send:value=>longerTailSignals.push(value),status() {},
});
longerTailPlayer.tick();
await longerTailPlayer.playRequest;
assert.equal(tailPlayCalls,1,'play the remaining encoded frames instead of getting stuck at the PCM frontier');
assert.equal(longerTailSignals.length,0,'do not announce drain before the encoded tail plays');
longerTailVideo.currentTime=341.8;
longerTailPlayer.tick();
assert.equal(longerTailSignals[0]?.state,'playback_drained','finish after the encoded tail reaches its end');
longerTailPlayer.drained=false;
longerTailPlayer.pendingSources.set('unresolved',341.4);
longerTailVideo.currentTime=341.523611;
longerTailVideo.paused=true;
longerTailPlayer.tick();
assert.equal(tailPlayCalls,1,'do not advance the final tail across a pending source phrase');
globalThis.requestAnimationFrame=previousAnimationFrame;
const playedEvents=[];
const endVideo={currentTime:11,pause(){}};
const endItem={phrase_id:'phrase-clock-test',source_start:10,source_end:11,
  videoRate:1,playbackStartWall:2000,audioStart:1,audioScheduleWallTime:1999,
  readyAt:1900,buffer:{duration:1},generatedAudioSeconds:1,silencePaddingSeconds:0};
const ending=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:endVideo,presentedMediaTime:10.9,frameCount:4,
  audio:{currentTime:2,getOutputTimestamp:()=>({contextTime:2,performanceTime:3000})},
  frameSamples:[{expectedDisplayTime:2990,mediaTime:10.9}],captureClock:{mediaEnd:11,performanceTime:1900},
  send:value=>playedEvents.push(value),phrases:new Map([[endItem.phrase_id,endItem]]),active:endItem,
});
ending.end(endItem);
assert.equal(playedEvents[0].speech_end_to_playback_seconds,.1);
assert.equal(playedEvents[0].capture_to_playback_seconds,1.1);
assert.ok(Math.abs(playedEvents[0].audio_video_offset_seconds+.1)<1e-9);
assert.equal(playedEvents[0].audio_output_end_performance_ms,3000);
assert.equal(ending.active,null);
const lagEvents=[];
const lagVideo={currentTime:10.4,pause(){}};
const lagPlayer=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:lagVideo,presentedMediaTime:10.4,frameCount:1,
  audio:{currentTime:2,getOutputTimestamp:()=>({contextTime:2,performanceTime:3000})},
  frameSamples:[{expectedDisplayTime:2990,mediaTime:10.4}],captureClock:null,captureWall:1000,
  send:value=>lagEvents.push(value),phrases:new Map([[endItem.phrase_id,endItem]]),active:endItem,
});
lagPlayer.end(endItem);
assert.equal(lagVideo.currentTime,10.4, 'do not seek forward past unpresented source video');
assert.ok(Math.abs(lagEvents[0].video_source_end_catchup_seconds-.6)<1e-9);
assert.equal(lagPlayer.active,null);
const slowPhraseRate=phraseVideoRate(1,2.546);
const slowEvents=[];
const slowItem={...endItem,phrase_id:'phrase-slow-video-sync',source_start:10,source_end:11,
  videoRate:slowPhraseRate.applied,playbackStartWall:2000,buffer:{duration:2.546}};
const slowPlayer=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:{currentTime:11,pause(){}},presentedMediaTime:11,frameCount:1,
  audio:{currentTime:3.546,getOutputTimestamp:()=>({contextTime:3.546,performanceTime:4546})},
  frameSamples:[{expectedDisplayTime:4546,mediaTime:11}],captureClock:null,captureWall:1000,
  send:value=>slowEvents.push(value),phrases:new Map([[slowItem.phrase_id,slowItem]]),active:slowItem,
});
slowPlayer.end(slowItem);
assert.ok(Math.abs(slowEvents[0].audio_video_offset_seconds)<1e-9);
const uncalibratedEvents=[];
const uncalibratedItem={...endItem,phrase_id:'phrase-fallback-clock',source_start:.5,source_end:1,
  playbackStartWall:2500};
const uncalibrated=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,captureWall:1000,video:{currentTime:1,pause(){}},presentedMediaTime:1,
  frameCount:1,audio:{currentTime:2,getOutputTimestamp:()=>({contextTime:2,performanceTime:3000})},
  frameSamples:[],captureClock:null,send:value=>uncalibratedEvents.push(value),
  phrases:new Map([[uncalibratedItem.phrase_id,uncalibratedItem]]),active:uncalibratedItem,
});
uncalibrated.end(uncalibratedItem);
assert.equal(uncalibratedEvents[0].capture_to_playback_seconds,1);
assert.equal(uncalibratedEvents[0].speech_end_to_playback_seconds,.5);
const beforeDrain = calls;
const queued = player.playVideo(); player.drained = true;
await queued; assert.equal(calls, beforeDrain);
player.drained = false;
player.paused = false; player.stopped = true; assert.equal(player.playVideo(), undefined);
const beginOrder=[];
const waitingVideo = Object.assign(new EventTarget(), {readyState:1, currentTime:.02,
  paused:true, playbackRate:1, pause() {this.paused=true;}, play() {
    beginOrder.push('video.play'); this.paused=false; return Promise.resolve();
  }});
let created = 0, started = 0;
const guarded = Object.assign(Object.create(DelayedPlayer.prototype), {
  video:waitingVideo, audio:{currentTime:0, resume:async()=>{beginOrder.push('audio.resume');}, createBufferSource() {
    created++; return {connect() {}, start() {started++;beginOrder.push('audio.start');}};
  }}, gain:{}, status() {}, stopped:false,
});
const longPhrase={source_start:.02,source_end:1.02,buffer:{duration:2.546}};
const pending = guarded.begin(longPhrase);
await Promise.resolve(); assert.equal(created,0);
waitingVideo.readyState=2; waitingVideo.dispatchEvent(new Event('canplay'));
await pending; assert.equal(created,1); assert.equal(started,1);
assert.deepEqual(beginOrder,['audio.resume','video.play','audio.start'],
  'start delayed video before scheduling translated audio to avoid a throttled-frame A/V gap');
assert.ok(longPhrase.videoPlayResolvedAt >= longPhrase.videoPlayRequestedAt);
assert.ok(Math.abs(longPhrase.videoRate-(1/2.546))<1e-9);
assert.ok(Math.abs(waitingVideo.playbackRate-(1/2.546))<1e-9);
assert.equal(longPhrase.videoRateClamped,false);
let padded;
const shortSpeech = {source_start:0,source_end:3,parts:[new Int16Array(48000).fill(8192)],next:1,chunks:1};
const fitter = Object.assign(Object.create(DelayedPlayer.prototype), {
  phrases:new Map([['short',shortSpeech]]),holds:new Set([0]),pendingSources:new Map(),audio:{createBuffer(channels,count,rate) {
    padded=new Float32Array(count);return {duration:count/rate,getChannelData:()=>padded};
  }},
});
fitter.ready('short');
assert.equal(shortSpeech.generatedAudioSeconds,1);assert.equal(shortSpeech.buffer.duration,2);
assert.equal(shortSpeech.silencePaddingSeconds,1);
assert.equal(padded[47999],.25);assert.equal(padded[48000],0);assert.equal(padded[95999],0);
const caption = {source_start:0,source_end:2,presentation_mode:'caption_only',parts:[],next:0,chunks:0};
fitter.committed({phrase_id:'caption',source_start:0});
fitter.committed({phrase_id:'overlapping-next',source_start:1.9});
fitter.phrases.set('caption',caption);fitter.ready('caption');
assert.equal(fitter.pendingSources.has('caption'),false);
assert.equal(fitter.pendingSources.get('overlapping-next'),1.9);
assert.equal(caption.generatedAudioSeconds,0);assert.equal(caption.buffer.duration,2);
assert.equal(caption.silencePaddingSeconds,2);assert.ok(padded.every(value=>value===0));
fitter.phrases.set('empty-speech',{...caption,presentation_mode:'speech',parts:[]});
assert.throws(()=>fitter.ready('empty-speech'),/Invalid bounded phrase duration/);
// Replay the actual failed LIVE positions: committed speech started at 10.22,
// but the old player ran to 13.35 and pruned its required keyframes.
globalThis.requestAnimationFrame=()=>{};
let catchupPlays=0;
const catchupVideo={currentTime:10.4,paused:true,playbackRate:1,ended:false,seeking:false,
  buffered:{length:1,start:()=>0,end:()=>12},pause(){this.paused=true;},
  play(){catchupPlays++;this.paused=false;return Promise.resolve();}};
const catchup=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:catchupVideo,captureTime:12,analyzedUntil:12,minimumDelay:3,maximumAhead:20,
  paused:false,sourceEnded:true,inferenceEnded:false,recorderEnded:false,active:null,
  phrases:new Map([['next',{source_start:11,ready:true}]]),holds:new Set(),pendingSources:new Map(),
  lastTelemetry:performance.now(),send(){},status(){},
});
catchup.tick(); await catchup.playRequest;
assert.equal(catchupPlays,1,'play the remaining video before starting the following phrase');
assert.equal(catchupVideo.currentTime,10.4,'catch-up must not seek past the remaining video');
let removes=[];let gapPlays=0;
const ranges={length:1,start:()=>0,end:()=>29.05};
const heldVideo={currentTime:13.35,buffered:ranges,readyState:4,seeking:false,
  paused:true,muted:true,pause(){this.paused=true;},play(){gapPlays++;return Promise.resolve();}};
const heldPlayer=new DelayedPlayer(heldVideo,()=>{},()=>{});
Object.assign(heldPlayer,{captureTime:30,analyzedUntil:30,presentedMediaTime:13.375,
  lastTelemetry:-Infinity,maximumAhead:40,gain:{gain:{value:1}},
  sourceBuffer:{updating:false,buffered:ranges,remove:(a,b)=>removes.push([a,b])}});
heldPlayer.committed({phrase_id:'pending-real-source',source_start:10.22});
heldPlayer.tick();
assert.equal(gapPlays,0);assert.equal(removes.length,1);assert.ok(removes[0][1]<=7.22+1e-9);
removes=[];heldPlayer.appending=true;heldPlayer.lastTelemetry=-Infinity;heldPlayer.tick();
assert.equal(removes.length,0);
heldPlayer.appending=false;heldVideo.seeking=true;heldPlayer.lastTelemetry=-Infinity;heldPlayer.tick();
assert.equal(removes.length,0);

// A live playback-buffer cap requests one graceful capture finish. The fixed
// buffered interval remains playable after sourceEnded, without a fatal stop.
const oldAnimationFrame=globalThis.requestAnimationFrame;
let scheduledFrames=0;
globalThis.requestAnimationFrame=()=>{scheduledFrames++;};
const overloadSignals=[];
const overflowVideo={currentTime:0,paused:true,playbackRate:1,ended:false,seeking:false,
  buffered:{length:1,start:()=>0,end:()=>40},pause(){this.paused=true;},play(){return Promise.resolve();}};
const overflowPlayer=Object.assign(Object.create(DelayedPlayer.prototype),{
  stopped:false,video:overflowVideo,captureTime:40,analyzedUntil:40,minimumDelay:4,maximumAhead:34,
  paused:true,sourceEnded:false,inferenceEnded:false,recorderEnded:false,active:null,
  phrases:new Map(),holds:new Set(),pendingSources:new Map(),frameSamples:[],lastTelemetry:performance.now(),
  send:value=>overloadSignals.push({type:'send',value}),
  status:(value,kind)=>overloadSignals.push({type:'status',value,kind}),
});
overflowPlayer.tick(); overflowPlayer.tick();
assert.equal(overloadSignals.filter(e=>e.type==='status').length,1);
assert.equal(overloadSignals.find(e=>e.type==='status').kind,'overload');
assert.equal(overloadSignals.filter(e=>e.type==='send'&&e.value.state==='buffer_limit_finish').length,1);
const scheduledBeforeDrain=scheduledFrames;
overflowPlayer.sourceEnded=true;
overflowPlayer.tick();
assert.equal(overloadSignals.filter(e=>e.type==='status').length,1);
assert.ok(scheduledFrames>scheduledBeforeDrain);
globalThis.requestAnimationFrame=oldAnimationFrame;
console.log('PASS: one pending play request, expected pause cancellation, visible errors, bounded overload finish, finite drain, pause/stop/end guards');
