// activeTab authorizes only the source selected through the extension action.
// Session storage retains that selection across MV3 worker suspension. It stores
// no credentials, media, unrelated tabs, history or player internals.
async function selection(sourceId) {
  return (await chrome.storage.session.get(String(sourceId)))[String(sourceId)];
}
async function restoreSourceAudio(source) {
  if (!source?.ownsMute) return;
  try {
    const tab = await chrome.tabs.get(source.tabId);
    // Preserve a newer explicit user/other-extension mute decision.
    if (tab.mutedInfo?.reason === 'extension' && tab.mutedInfo.extensionId === chrome.runtime.id)
      await chrome.tabs.update(source.tabId, {muted: source.preCaptureMuted});
  } catch (_) { /* A closed source has no audio state to restore. */ }
}
chrome.action.onClicked.addListener(async tab => {
  if (!tab.id || !/^https?:/.test(tab.url || '')) return;
  // Keep the authorized source tab active and put the delayed player beside it.
  // A background YouTube tab can pause while the old separate player keeps
  // recording repeated frames and digital silence as if the source clock ran.
  const opened = chrome.sidePanel.open({tabId: tab.id});
  const existing = await selection(tab.id);
  if (!existing?.captureActive) {
    await chrome.storage.session.set({[String(tab.id)]: {
      tabId: tab.id, windowId: tab.windowId, url: tab.url, title: tab.title,
      captureActive: false,
    }});
  }
  await opened;
});
async function invalidate(sourceId, reason) {
  const pairs = await chrome.storage.session.get(null);
  for (const [playerId, source] of Object.entries(pairs)) {
    if (source.tabId !== sourceId) continue;
    await restoreSourceAudio(source);
    await chrome.storage.session.remove(playerId);
    chrome.runtime.sendMessage({type: 'source_invalidated', sourceTabId: sourceId, reason}).catch(() => {});
  }
}
chrome.tabs.onRemoved.addListener(async id => {
  await restoreSourceAudio(await selection(id));
  await chrome.storage.session.remove(String(id));
  await invalidate(id, 'Selected source tab closed. Capture stopped.');
});
chrome.tabs.onUpdated.addListener((id, change) => {
  if (change.status === 'loading' || change.url)
    invalidate(id, 'Selected source navigated or reloaded. Select it again explicitly.');
});
chrome.tabs.onActivated.addListener(async ({tabId, windowId}) => {
  const pairs = await chrome.storage.session.get(null);
  for (const source of Object.values(pairs)) {
    if (source.captureActive && source.windowId === windowId && source.tabId !== tabId) {
      source.captureActive = false;
      await chrome.storage.session.set({[String(source.tabId)]: source});
      chrome.runtime.sendMessage({type: 'source_visibility_lost', sourceTabId: source.tabId,
        reason: 'The selected YouTube tab is no longer active. Capture is finishing to preserve source timing.'}).catch(() => {});
    }
  }
});
async function selectedSource(sender) {
  const page = new URL(sender.url);
  if (page.pathname !== '/player.html') throw Error('Only the Live Dubbing player can request capture.');
  const explicit = Number(page.searchParams.get('sourceTabId'));
  const explicitId = Number.isInteger(explicit) && explicit > 0 ? explicit : null;
  const candidates = explicitId ? [explicitId] : [sender.tab?.id].filter(Number.isInteger);
  if (!explicitId) {
    const [active] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
    if (Number.isInteger(active?.id) && !candidates.includes(active.id)) candidates.push(active.id);
  }
  for (const sourceId of candidates) {
    const source = await selection(sourceId);
    if (source) return source;
  }
  throw Error(explicitId ? 'The selected source is no longer available.' :
    'Use the extension action on the permitted source tab, then keep it active beside Live Dubbing.');
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!sender.url?.startsWith(chrome.runtime.getURL('player.html'))) {
    reply({error: 'Only the visible extension player can request capture.'}); return false;
  }
  (async () => {
    let source;
    // The side panel can finish opening just before session storage commits.
    // Retry briefly so startup does not surface a false "select source" error.
    for (let attempt = 0; attempt < 10; attempt++) {
      try { source = await selectedSource(sender); break; }
      catch (error) {
        if (attempt === 9 || !/Use the extension action/.test(error.message)) throw error;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    }
    const sourceId = source.tabId;
    if (message.type === 'source') return source;
    if (message.type === 'capture') {
      const tab = await chrome.tabs.get(sourceId);
      if (tab.url !== source.url) throw Error('Source changed; select it again explicitly.');
      source.preCaptureMuted = Boolean(tab.mutedInfo?.muted);
      source.captureActive = true;
      await chrome.storage.session.set({[String(sourceId)]: source});
      const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: sourceId});
      return {...source, streamId};
    }
    if (message.type === 'focus_player') {
      const tab = await chrome.tabs.get(sourceId);
      if (tab.url !== source.url || !tab.active)
        throw Error('Keep the selected YouTube tab active beside the Live Dubbing panel.');
      return {focused: false, sourceActive: true};
    }
    if (message.type === 'hold_source_audio') {
      if (typeof source.preCaptureMuted !== 'boolean') throw Error('No selected-tab capture to drain.');
      const tab = await chrome.tabs.get(sourceId);
      if (tab.url !== source.url) throw Error('Selected source changed before draining.');
      source.ownsMute = true;
      source.captureActive = false;
      await chrome.storage.session.set({[String(sourceId)]: source});
      // Transfer capture's temporary mute to an owned tab mute before closing
      // tracks, so original speech cannot leak into delayed translated tails.
      await chrome.tabs.update(source.tabId, {muted: true});
      const current = await chrome.tabs.get(sourceId);
      if (current.url !== source.url || !(await selection(sourceId))?.ownsMute) {
        await restoreSourceAudio(source);
        throw Error('Selected source ended or changed while draining.');
      }
      return {held: true, muted: current.mutedInfo.muted, reason: current.mutedInfo.reason};
    }
    if (message.type === 'release_source_audio') {
      await restoreSourceAudio(source);
      source.ownsMute = false;
      source.captureActive = false;
      await chrome.storage.session.set({[String(sourceId)]: source});
      return {released: true};
    }
    throw Error('Unknown extension action');
  })().then(reply).catch(error => reply({error: error.message}));
  return true;
});
