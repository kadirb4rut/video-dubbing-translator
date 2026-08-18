import React, { useEffect, useMemo, useState } from 'react';
import { apiFetch, uploadMedia } from './api';

const languages = { Spanish: 'es', French: 'fr', German: 'de', Portuguese: 'pt', Japanese: 'ja', Turkish: 'tr' };

function Glyph({ type }) {
  const symbols = { home: '⌂', video: '▣', voice: '◉', stems: '∿', noise: '⌁', transcript: '▤', folder: '□', card: '▭', settings: '⚙' };
  return <span className="glyph" aria-hidden="true">{symbols[type] || '·'}</span>;
}

function AuthView({ onBack, onAuthenticated }) {
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      const payload = mode === 'login' ? { email, password } : { email, password, display_name: displayName };
      const result = await apiFetch(`/api/auth/${mode === 'login' ? 'login' : 'signup'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      onAuthenticated(result);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <div className="auth-page"><div className="auth-card"><button className="brand-button" onClick={onBack}>Lingo<span>Wave</span></button><h1>{mode === 'login' ? 'Welcome back.' : 'Start creating.'}</h1><p>{mode === 'login' ? 'Sign in to continue your workspace.' : 'Create an account with 30 starter credits.'}</p><form onSubmit={submit}>{mode === 'signup' && <input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Your name" /> }<input required type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Email address" /><input required minLength="12" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password · 12 characters minimum" />{error && <div className="auth-error">{error}</div>}<button className="button primary" disabled={busy}>{busy ? 'Working…' : mode === 'login' ? 'Log in' : 'Create account'} <span>→</span></button></form><button className="auth-switch" onClick={() => setMode(mode === 'login' ? 'signup' : 'login')}>{mode === 'login' ? 'Need an account? Create one' : 'Already have an account? Log in'}</button></div></div>;
}

export function RealAppShell({ onExit, initialUser }) {
  const [activeTool, setActiveTool] = useState('Video Translator');
  const [file, setFile] = useState(null);
  const [target, setTarget] = useState('Spanish');
  const [preserveVoice, setPreserveVoice] = useState(true);
  const [keepBackground, setKeepBackground] = useState(true);
  const [lipSync, setLipSync] = useState(false);
  const [quality, setQuality] = useState('balanced');
  const [estimate, setEstimate] = useState(null);
  const [job, setJob] = useState(null);
  const [credits, setCredits] = useState(0);
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);
  const userInitials = (initialUser?.display_name || initialUser?.email || 'LW').slice(0, 2).toUpperCase();
  const showToast = (message) => { setToast(message); window.setTimeout(() => setToast(''), 3200); };
  useEffect(() => { apiFetch('/api/credits').then(result => setCredits(result.balance)).catch(error => showToast(error.message)); }, []);
  useEffect(() => {
    if (!job || ['completed', 'failed', 'cancelled'].includes(job.state)) return undefined;
    const timer = window.setInterval(() => apiFetch(`/api/jobs/${job.id}`).then(next => { setJob(next); if (next.state === 'completed') { setCredits(value => Math.max(0, value - (next.actual_credits || next.reserved_credits))); showToast('Translation complete — your export is ready.'); } if (next.state === 'failed') showToast(next.error_message || 'The worker could not complete this job.'); }).catch(error => showToast(error.message)), 1200);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state]);
  const cost = estimate?.credits || '—';
  const progress = job ? ({ queued: 8, provisioning: 16, downloading: 25, separating_audio: 38, transcribing: 52, translating: 65, synthesizing: 78, mixing: 88, lip_syncing: 92, uploading: 97, completed: 100 }[job.state] || 12) : 0;
  const handleFile = async (next) => { const picked = next?.[0]; if (!picked) return; setBusy(true); try { const asset = await uploadMedia(picked); setFile({ ...asset, name: asset.filename, local: URL.createObjectURL(picked) }); setEstimate(null); showToast('Media inspected by FFprobe — ready to configure.'); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const start = async () => {
    if (!file) return showToast('Choose a video first so we can estimate the cost.');
    setBusy(true);
    try {
      const operation = activeTool === 'Video Translator' ? 'dubbing' : activeTool === 'Transcription' ? 'transcription' : activeTool === 'Stem Splitter' ? 'stems' : activeTool === 'Noise Remover' ? 'noise' : 'tts';
      const result = await apiFetch('/api/jobs/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ media_asset_id: file.id, operation, lip_sync: lipSync, quality }) });
      setEstimate(result);
      if (credits < result.credits) return showToast(`You need ${result.credits} credits; your balance is ${credits}.`);
      const created = await apiFetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ media_asset_id: file.id, operation, target_language: languages[target], preserve_voice: preserveVoice, keep_background: keepBackground, lip_sync: lipSync, quality }) });
      setJob(created); showToast('Job queued — a worker will update this timeline.');
    } catch (error) { showToast(error.message); } finally { setBusy(false); }
  };
  return <div className="app-shell"><aside className="sidebar"><button className="app-brand" onClick={onExit}>Lingo<span>Wave</span></button><div className="side-nav">{[['video', 'Video Translator'], ['voice', 'Voice Studio'], ['stems', 'Stem Splitter'], ['noise', 'Noise Remover'], ['transcript', 'Transcription']].map(([icon, label]) => <button key={label} className={activeTool === label ? 'active' : ''} onClick={() => setActiveTool(label)}><Glyph type={icon} />{label}</button>)}</div><div className="side-bottom"><button><Glyph type="folder" />Projects</button><button><Glyph type="card" />Billing</button><button><Glyph type="settings" />Settings</button></div></aside><div className="app-content"><header className="app-header"><div className="mobile-brand"><b>LingoWave</b></div><div className="credit-pill"><span>◉</span>{credits} credits</div><button className="avatar" aria-label="Account" onClick={() => apiFetch('/api/auth/logout', { method: 'POST' }).finally(onExit)}>{userInitials}</button></header><main className="workspace"><div className="workspace-title"><div><h1>{activeTool}</h1><p>{activeTool === 'Video Translator' ? 'Translate and dub your next story without losing its character.' : 'A focused workspace for your next audio project.'}</p></div><button className="help-button">?</button></div><div className="workflow-steps"><span className="current"><b>1</b>Upload</span><i /><span className={file ? 'current' : ''}><b>2</b>Configure</span><i /><span className={job?.state === 'completed' ? 'current' : ''}><b>3</b>Export</span></div><div className="workspace-grid"><section className={`upload-card ${file ? 'has-file' : ''}`}><label className="dropzone"><input type="file" accept="video/*,audio/*" onChange={e => handleFile(e.target.files)} />{file ? <><div className="media-preview" style={{ backgroundImage: `url(${file.local})` }}><div className="video-scene"><div className="person" /><div className="mic" /></div></div><div className="file-meta"><span className="file-icon"><span>▤</span></span><div><strong>{file.name}</strong><small>{Math.round(file.duration_seconds)}s · FFprobe inspected</small></div><button className="remove-file" onClick={e => { e.preventDefault(); setFile(null); setJob(null); setEstimate(null); }}><span>×</span></button></div><p className="browse-copy">Drop another video or <span>click to browse</span></p></> : <><div className="upload-icon">↑</div><h3>Drop your video here</h3><p>or <span>click to browse</span></p><small>MP4, MOV, WEBM · up to 4GB</small></>}</label><div className="recent-projects"><div className="section-heading"><h2>Recent projects</h2><button onClick={() => apiFetch('/api/jobs').then(rows => showToast(`${rows.length} jobs in your workspace.`))}>View all →</button></div>{job ? <div className="project-row"><div className="thumb"><div className="person small" /></div><div className="project-name"><strong>{file?.name}</strong><span>Current job <b>·</b> {job.operation}</span></div><span>{job.state}</span><em className={job.state === 'completed' ? 'green' : job.state === 'failed' ? 'red' : 'blue'}>{job.state}</em><span>{new Date(job.created_at).toLocaleDateString()}</span></div> : <p className="empty-projects">Your completed jobs will appear here.</p>}</div></section><aside className="config-rail"><div className="config-group"><label>Source language</label><select defaultValue="Auto-detect"><option>Auto-detect</option><option>English</option><option>Turkish</option><option>German</option></select></div><div className="config-group"><label>Target language</label><select value={target} onChange={e => setTarget(e.target.value)}>{Object.keys(languages).map(lang => <option key={lang}>{lang}</option>)}</select></div><div className="toggle-group"><label className="toggle-row"><span>Preserve voice</span><input type="checkbox" checked={preserveVoice} onChange={e => setPreserveVoice(e.target.checked)} /><i /></label><label className="toggle-row"><span>Keep background</span><input type="checkbox" checked={keepBackground} onChange={e => setKeepBackground(e.target.checked)} /><i /></label><label className="toggle-row"><span>Lip sync <em>Premium</em></span><input type="checkbox" checked={lipSync} onChange={e => setLipSync(e.target.checked)} /><i /></label></div><div className="config-group"><label>Quality</label><select value={quality} onChange={e => setQuality(e.target.value)}><option value="balanced">Balanced</option><option value="studio">Studio</option><option value="draft">Draft</option></select></div><div className="cost-box"><span>Estimated cost</span><strong>{cost}<small>credits</small></strong><p>{estimate ? `${estimate.duration_seconds.toFixed(1)}s · server-side estimate` : 'Upload media to calculate from the configured profile.'}</p></div><button className="button primary start-button" disabled={busy || Boolean(job && !['completed', 'failed', 'cancelled'].includes(job.state))} onClick={start}>{busy ? 'Working…' : job && !['completed', 'failed', 'cancelled'].includes(job.state) ? `${job.state.replaceAll('_', ' ')} · ${progress}%` : 'Start translation'} {!busy && <span>→</span>}</button>{job && !['completed', 'failed', 'cancelled'].includes(job.state) && <div className="progress-bar"><i style={{ width: `${progress}%` }} /></div>}</aside></div></main>{toast && <div className="toast">✓ {toast}</div>}</div></div>;
}

export { AuthView };
