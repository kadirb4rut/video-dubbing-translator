// State ownership regression; real capture/playback acceptance is separate.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const listeners = {};
const store = {};
const tab = {id:1,url:'https://www.youtube.com/watch?v=test',windowId:9,active:true,
  title:'authorized test source',mutedInfo:{muted:false}};
const sent=[];
let openedTab=null;
const chrome = {
  action:{onClicked:{addListener(fn){listeners.action=fn;}}},
  runtime:{id:'unit-extension',getURL:p=>'chrome-extension://unit-extension/'+p,
    onMessage:{addListener(fn){listeners.message=fn;}},sendMessage:async message=>{sent.push(message);}},
  storage:{session:{get:async key=>key===null?{...store}:{[key]:store[key]},
    set:async data=>Object.assign(store,structuredClone(data)),remove:async key=>{delete store[key];}}},
  tabs:{get:async()=>structuredClone(tab),query:async()=>[structuredClone(tab)],update:async(id,change)=>{
    assert.equal(id,1);tab.mutedInfo={muted:change.muted,reason:'extension',extensionId:'unit-extension'};
    return structuredClone(tab);
  },onRemoved:{addListener(fn){listeners.removed=fn;}},onUpdated:{addListener(fn){listeners.updated=fn;}},
  onActivated:{addListener(fn){listeners.activated=fn;}}},
  sidePanel:{open:async({tabId})=>{openedTab=tabId;}},
  tabCapture:{getMediaStreamId:async()=> 'unit-stream'}
};
vm.runInNewContext(fs.readFileSync(new URL('../browser_extension/live_dubbing/service-worker.js',import.meta.url),'utf8'),
  {chrome,URL,setTimeout});
await listeners.action({id:1,url:tab.url,windowId:9,title:tab.title});
assert.equal(openedTab,1,'the source tab remains active while the side panel opens beside it');
assert.equal(store['1'].tabId,1);
const send=type=>new Promise(resolve=>listeners.message({type},
  {url:chrome.runtime.getURL('player.html?sidepanel=1'),tab:{id:1,windowId:9}},resolve));
assert.ok((await send('hold_source_audio')).error);
const captureResult=await send('capture');
assert.equal(captureResult.streamId,'unit-stream',JSON.stringify(captureResult));
assert.equal(store['1'].captureActive,true);
const focusResult=await send('focus_player');
assert.equal(focusResult.focused,false);assert.equal(focusResult.sourceActive,true);
tab.active=false;
await listeners.activated({tabId:2,windowId:9});
assert.ok(sent.some(message=>message.type==='source_visibility_lost'&&message.sourceTabId===1));
assert.equal(store['1'].captureActive,false);
tab.active=true;
tab.mutedInfo={muted:true,reason:'capture'};
assert.equal((await send('hold_source_audio')).muted,true);
// Releasing the capture must leave the owned drain mute in place.
if(tab.mutedInfo.reason==='capture')tab.mutedInfo={muted:false};
assert.equal(tab.mutedInfo.muted,true);
await send('release_source_audio');assert.equal(tab.mutedInfo.muted,false);
tab.mutedInfo={muted:true,reason:'user'};
await send('capture');await send('hold_source_audio');await send('release_source_audio');
assert.equal(tab.mutedInfo.muted,true);
tab.mutedInfo={muted:false,reason:'user'};
await send('capture');await send('hold_source_audio');
tab.mutedInfo={muted:false,reason:'user'};
await send('release_source_audio');assert.equal(tab.mutedInfo.reason,'user');
await send('capture');await send('hold_source_audio');
await listeners.removed(1);
assert.equal(tab.mutedInfo.muted,false);
assert.equal(store['1'],undefined);
console.log('PASS: side panel keeps source tab active, drain mute/restoration, tab-switch graceful finish, and source-close cleanup');
