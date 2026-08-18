import React, { useEffect, useMemo, useState } from 'react';
import { apiFetch, apiUrl, uploadMedia } from './api';

const languages = { Spanish: 'es', French: 'fr', German: 'de', Portuguese: 'pt', Japanese: 'ja', Turkish: 'tr' };
const terminalStates = ['completed', 'failed', 'cancelled'];
const progressByState = { queued: 8, provisioning: 16, downloading: 25, separating_audio: 38, transcribing: 52, translating: 65, synthesizing: 78, mixing: 88, lip_syncing: 92, uploading: 97, completed: 100 };

function Glyph({ type }) {
  const symbols = { video: '▣', voice: '◉', stems: '∿', noise: '⌁', transcript: '▤', folder: '□', card: '▭', settings: '⚙' };
  return <span className="glyph" aria-hidden="true">{symbols[type] || '·'}</span>;
}

function AuthView({ onBack, onAuthenticated }) {
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const submit = async (event) => {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      if (resetMode) {
        const result = await apiFetch('/api/auth/password-reset/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
        setResetToken(result.dev_token || ''); setError(result.dev_token ? 'Development reset token issued below.' : 'If the account exists, reset instructions were sent.');
      } else {
        const payload = mode === 'login' ? { email, password } : { email, password, display_name: displayName };
        onAuthenticated(await apiFetch(`/api/auth/${mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
      }
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <div className="auth-page"><div className="auth-card"><button className="brand-button" onClick={onBack}>Lingo<span>Wave</span></button><h1>{resetMode ? 'Reset access.' : mode === 'login' ? 'Welcome back.' : 'Start creating.'}</h1><p>{resetMode ? 'Request a secure password reset.' : mode === 'login' ? 'Sign in to continue your workspace.' : 'Create an account with 30 starter credits.'}</p><form onSubmit={submit}>{!resetMode && mode === 'signup' && <input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Your name" /> }<input required type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Email address" />{!resetMode && <input required minLength="12" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password · 12 characters minimum" />}{error && <div className="auth-error">{error}</div>}{resetToken && <code className="reset-token">{resetToken}</code>}<button className="button primary" disabled={busy}>{busy ? 'Working…' : resetMode ? 'Request reset' : mode === 'login' ? 'Log in' : 'Create account'} <span>→</span></button></form>{!resetMode && mode === 'login' && <button className="auth-switch" onClick={() => setResetMode(true)}>Forgot your password?</button>}<button className="auth-switch" onClick={() => { setResetMode(false); setMode(mode === 'login' ? 'signup' : 'login'); }}>{resetMode ? 'Back to sign in' : mode === 'login' ? 'Need an account? Create one' : 'Already have an account? Log in'}</button></div></div>;
}

function Toast({ message }) { return message ? <div className="toast">✓ {message}</div> : null; }

function JobStatus({ job, onDownload, onPreview }) {
  if (!job) return null;
  return <div className="job-status"><div className="job-status-heading"><span>{job.state.replaceAll('_', ' ')}</span><strong>{progressByState[job.state] || 12}%</strong></div><div className="progress-bar"><i style={{ width: `${progressByState[job.state] || 12}%` }} /></div>{job.error_message && <p className="job-error">{job.error_message}</p>}{job.artifacts?.length > 0 && <div className="artifact-list">{job.artifacts.map(artifact => <div className="artifact-row" key={artifact.id}><span>{artifact.filename}</span>{artifact.content_type.startsWith('audio/') && <audio controls preload="none" src={`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`} />}{artifact.content_type.startsWith('video/') && <video controls preload="metadata" src={`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`} /> }<div><button onClick={() => artifact.content_type.startsWith('text/') || artifact.content_type.includes('subrip') ? onPreview(artifact) : onDownload(artifact)}> {artifact.content_type.startsWith('text/') || artifact.content_type.includes('subrip') ? 'Preview' : 'Download'} </button><button onClick={() => onDownload(artifact)}>↓</button></div></div>)}</div>}</div>;
}

function UploadCard({ file, busy, onFile, onRemove, title = 'Drop your media here' }) {
  return <section className={`upload-card ${file ? 'has-file' : ''}`}><label className="dropzone"><input type="file" accept="video/*,audio/*" onChange={e => onFile(e.target.files)} disabled={busy} />{file ? <><div className="media-preview" style={{ backgroundImage: `url(${file.local})` }}><div className="video-scene"><div className="person" /><div className="mic" /></div></div><div className="file-meta"><span className="file-icon">▤</span><div><strong>{file.name}</strong><small>{Math.round(file.duration_seconds)}s · FFprobe inspected</small></div><button className="remove-file" onClick={e => { e.preventDefault(); onRemove(); }}>×</button></div><p className="browse-copy">Drop another file or <span>click to browse</span></p></> : <><div className="upload-icon">↑</div><h3>{title}</h3><p>or <span>click to browse</span></p><small>MP4, MOV, WEBM, WAV · up to 4GB</small></>}</label>{file && <audio className="inline-audio" controls src={file.local} />}</section>;
}

function DownloadableResult({ job, onDownload, onPreview }) {
  return <JobStatus job={job} onDownload={onDownload} onPreview={onPreview} />;
}

export function RealAppShell({ onExit, initialUser }) {
  const [activeTool, setActiveTool] = useState('Video Translator');
  const [file, setFile] = useState(null);
  const [target, setTarget] = useState('Spanish');
  const [sourceLanguage, setSourceLanguage] = useState('');
  const [preserveVoice, setPreserveVoice] = useState(true);
  const [keepBackground, setKeepBackground] = useState(true);
  const [lipSync, setLipSync] = useState(false);
  const [quality, setQuality] = useState('balanced');
  const [stemCount, setStemCount] = useState(4);
  const [translateSubtitles, setTranslateSubtitles] = useState(false);
  const [voiceId, setVoiceId] = useState('');
  const [voices, setVoices] = useState([]);
  const [voiceName, setVoiceName] = useState('');
  const [voiceDeclaration, setVoiceDeclaration] = useState('');
  const [voiceAuthorized, setVoiceAuthorized] = useState(false);
  const [voiceReference, setVoiceReference] = useState(null);
  const [voiceText, setVoiceText] = useState('');
  const [estimate, setEstimate] = useState(null);
  const [job, setJob] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const [previewText, setPreviewText] = useState('');
  const [credits, setCredits] = useState(0);
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);
  const userInitials = (initialUser?.display_name || initialUser?.email || 'LW').slice(0, 2).toUpperCase();
  const showToast = (message) => { setToast(message); window.setTimeout(() => setToast(''), 3200); };
  const refreshWorkspace = () => Promise.all([apiFetch('/api/credits'), apiFetch('/api/voices'), apiFetch('/api/jobs')]).then(([creditData, voiceData, jobs]) => { setCredits(creditData.balance); setVoices(voiceData); setRecentJobs(jobs); if (!voiceId && voiceData[0]) setVoiceId(voiceData[0].id); }).catch(error => showToast(error.message));
  useEffect(() => { refreshWorkspace(); }, []);
  useEffect(() => {
    if (!job || terminalStates.includes(job.state)) return undefined;
    const timer = window.setInterval(() => apiFetch(`/api/jobs/${job.id}`).then(next => { setJob(next); if (terminalStates.includes(next.state)) { refreshWorkspace(); showToast(next.state === 'completed' ? 'Processing complete — your files are ready.' : next.error_message || 'The worker could not complete this job.'); } }).catch(error => showToast(error.message)), 1200);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state]);
  const cost = estimate?.credits || '—';
  const operation = useMemo(() => activeTool === 'Video Translator' ? 'dubbing' : activeTool === 'Transcription' ? (translateSubtitles ? 'subtitle_translation' : 'transcription') : activeTool === 'Stem Splitter' ? 'stems' : activeTool === 'Noise Remover' ? 'noise' : 'tts', [activeTool, translateSubtitles]);
  const handleFile = async (next) => { const picked = next?.[0]; if (!picked) return; setBusy(true); try { const asset = await uploadMedia(picked); setFile({ ...asset, name: asset.filename, local: URL.createObjectURL(picked) }); setEstimate(null); setJob(null); showToast('Media inspected by FFprobe — ready to configure.'); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const requestJob = async (jobOptions = {}) => {
    const estimatePayload = { media_asset_id: file?.id || null, operation, lip_sync: lipSync, quality, text: jobOptions.text, target_language: languages[target], source_language: sourceLanguage || null };
    const nextEstimate = await apiFetch('/api/jobs/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(estimatePayload) });
    setEstimate(nextEstimate);
    if (credits < nextEstimate.credits) throw new Error(`You need ${nextEstimate.credits} credits; your balance is ${credits}.`);
    const created = await apiFetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ ...estimatePayload, media_asset_id: file?.id || null, preserve_voice: preserveVoice, keep_background: keepBackground, lip_sync: lipSync, voice_profile_id: jobOptions.voice_profile_id || (activeTool === 'Video Translator' ? voiceId : null), text: jobOptions.text, stems: stemCount }) });
    setJob(created); showToast('Job queued — the worker will report each real processing stage.');
  };
  const start = async () => { if (!file && activeTool !== 'Voice Studio') return showToast('Choose media first.'); if (activeTool === 'Video Translator' && !voiceId) return showToast('Create or choose a consented voice profile first.'); if (activeTool === 'Voice Studio' && (!voiceId || !voiceText.trim())) return showToast('Choose a voice and enter text first.'); setBusy(true); try { await requestJob(activeTool === 'Voice Studio' ? { text: voiceText, voice_profile_id: voiceId } : {}); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const createVoice = async (event) => { event.preventDefault(); if (!voiceReference) return showToast('Choose a 3–90 second reference sample.'); setBusy(true); try { const form = new FormData(); form.append('name', voiceName); form.append('declaration', voiceDeclaration); form.append('authorized', String(voiceAuthorized)); form.append('upload', voiceReference); const created = await apiFetch('/api/voices', { method: 'POST', body: form }); setVoices(current => [created, ...current]); setVoiceId(created.id); setVoiceName(''); setVoiceDeclaration(''); setVoiceReference(null); showToast('Consent recorded and voice profile secured.'); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const downloadArtifact = async (artifact) => { window.open(`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`, '_blank', 'noopener'); };
  const previewArtifact = async (artifact) => { try { const result = await apiFetch(`/api/jobs/${job.id}/artifacts/${artifact.id}/preview`); setPreviewText(result.text); } catch (error) { showToast(error.message); } };
  const revokeVoice = async (id) => { try { await apiFetch(`/api/voices/${id}`, { method: 'DELETE' }); setVoices(current => current.filter(voice => voice.id !== id)); if (voiceId === id) setVoiceId(''); showToast('Voice reference deleted.'); } catch (error) { showToast(error.message); } };
  const selectTool = (tool) => { setActiveTool(tool); setFile(null); setEstimate(null); setJob(null); setPreviewText(''); };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="app-brand" onClick={onExit}>Lingo<span>Wave</span></button>
        <div className="side-nav">
          {[["video", "Video Translator"], ["voice", "Voice Studio"], ["stems", "Stem Splitter"], ["noise", "Noise Remover"], ["transcript", "Transcription"]].map(([icon, label]) => (
            <button key={label} className={activeTool === label ? "active" : ""} onClick={() => selectTool(label)}><Glyph type={icon} />{label}</button>
          ))}
        </div>
        <div className="side-bottom">
          <button onClick={() => { setActiveTool("Projects"); setJob(null); }}><Glyph type="folder" />Projects</button>
          <button onClick={() => showToast("Internal billing abstraction is active; payment providers are intentionally disabled.")}><Glyph type="card" />Billing</button>
          <button onClick={() => apiFetch("/api/account").then(account => showToast(account.user.email + " · " + account.user.plan)).catch(error => showToast(error.message))}><Glyph type="settings" />Settings</button>
        </div>
      </aside>
      <div className="app-content">
        <header className="app-header">
          <div className="mobile-brand"><b>LingoWave</b></div>
          <div className="credit-pill"><span>◉</span>{credits} credits</div>
          <button className="avatar" aria-label="Log out" onClick={() => apiFetch("/api/auth/logout", { method: "POST" }).finally(onExit)}>{userInitials}</button>
        </header>
        <main className="workspace">
          <div className="workspace-title">
            <div><h1>{activeTool}</h1><p>{activeTool === "Video Translator" ? "Translate and dub your next story without losing its character." : activeTool === "Projects" ? "Your persisted jobs and downloadable artifacts." : "A focused workspace for your next media project."}</p></div>
            <button className="help-button">?</button>
          </div>
          {activeTool === "Projects" ? (
            <ProjectHistory jobs={recentJobs} />
          ) : activeTool === "Voice Studio" ? (
            <VoiceWorkspace voices={voices} voiceId={voiceId} setVoiceId={setVoiceId} voiceName={voiceName} setVoiceName={setVoiceName} voiceDeclaration={voiceDeclaration} setVoiceDeclaration={setVoiceDeclaration} voiceAuthorized={voiceAuthorized} setVoiceAuthorized={setVoiceAuthorized} voiceReference={voiceReference} setVoiceReference={setVoiceReference} voiceText={voiceText} setVoiceText={setVoiceText} createVoice={createVoice} revokeVoice={revokeVoice} busy={busy} job={job} start={start} estimate={estimate} cost={cost} downloadArtifact={downloadArtifact} previewArtifact={previewArtifact} previewText={previewText} />
          ) : (
            <>
              <div className="workflow-steps"><span className="current"><b>1</b>Upload</span><i /><span className={file ? "current" : ""}><b>2</b>Configure</span><i /><span className={job?.state === "completed" ? "current" : ""}><b>3</b>Export</span></div>
              <div className="workspace-grid">
                <div>
                  <UploadCard file={file} busy={busy} onFile={handleFile} onRemove={() => { setFile(null); setJob(null); setEstimate(null); }} title={activeTool === "Transcription" ? "Drop audio or video here" : activeTool === "Stem Splitter" ? "Drop music or video here" : "Drop your video here"} />
                  {job && <DownloadableResult job={job} onDownload={downloadArtifact} onPreview={previewArtifact} />}
                  {previewText && <pre className="transcript-preview">{previewText}</pre>}
                </div>
                <ConfigRail activeTool={activeTool} operation={operation} target={target} setTarget={setTarget} sourceLanguage={sourceLanguage} setSourceLanguage={setSourceLanguage} preserveVoice={preserveVoice} setPreserveVoice={setPreserveVoice} keepBackground={keepBackground} setKeepBackground={setKeepBackground} lipSync={lipSync} setLipSync={setLipSync} quality={quality} setQuality={setQuality} stemCount={stemCount} setStemCount={setStemCount} translateSubtitles={translateSubtitles} setTranslateSubtitles={setTranslateSubtitles} voices={voices} voiceId={voiceId} setVoiceId={setVoiceId} cost={cost} estimate={estimate} busy={busy} job={job} onStart={start} />
              </div>
            </>
          )}
        </main>
        <Toast message={toast} />
      </div>
    </div>
  );
}

function ConfigRail({ activeTool, operation, target, setTarget, sourceLanguage, setSourceLanguage, preserveVoice, setPreserveVoice, keepBackground, setKeepBackground, lipSync, setLipSync, quality, setQuality, stemCount, setStemCount, translateSubtitles, setTranslateSubtitles, voices, voiceId, setVoiceId, cost, estimate, busy, job, onStart }) {
  const sourceOptions = [['Auto-detect', ''], ['English', 'en'], ['Turkish', 'tr'], ['German', 'de']];
  return <aside className="config-rail"><div className="config-group"><label>Source language</label><select value={sourceLanguage || ''} onChange={e => setSourceLanguage(e.target.value)}>{sourceOptions.map(([label, value]) => <option key={label} value={value}>{label}</option>)}</select></div>{activeTool !== 'Noise Remover' && <div className="config-group"><label>Target language</label><select value={target} onChange={e => setTarget(e.target.value)}>{Object.keys(languages).map(lang => <option key={lang}>{lang}</option>)}</select></div>}{activeTool === 'Video Translator' && <><div className="config-group"><label>Voice profile</label><select value={voiceId} onChange={e => setVoiceId(e.target.value)}><option value="">Choose a consented voice</option>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select></div><div className="toggle-group"><Toggle label="Preserve voice" checked={preserveVoice} onChange={setPreserveVoice} /><Toggle label="Keep background" checked={keepBackground} onChange={setKeepBackground} /><Toggle label="Lip sync" premium checked={lipSync} onChange={setLipSync} /></div></>}{activeTool === 'Stem Splitter' && <div className="config-group"><label>Output</label><select value={stemCount} onChange={e => setStemCount(Number(e.target.value))}><option value="2">Vocals + instrumental</option><option value="4">4 stems</option></select></div>}{activeTool === 'Transcription' && <Toggle label="Translate subtitles" checked={translateSubtitles} onChange={setTranslateSubtitles} />}{activeTool !== 'Noise Remover' && <div className="config-group"><label>Quality</label><select value={quality} onChange={e => setQuality(e.target.value)}><option value="balanced">Balanced</option><option value="studio">Studio</option><option value="draft">Draft</option></select></div>}<div className="cost-box"><span>Estimated cost</span><strong>{cost}<small>credits</small></strong><p>{estimate ? `${estimate.duration_seconds.toFixed(1)}s · server-side estimate` : 'Upload media to calculate from the configured profile.'}</p></div><button className="button primary start-button" disabled={busy || Boolean(job && !terminalStates.includes(job.state))} onClick={onStart}>{busy ? 'Working…' : job && !terminalStates.includes(job.state) ? `${job.state.replaceAll('_', ' ')} · ${progressByState[job.state] || 12}%` : operation === 'transcription' ? 'Transcribe' : operation === 'stems' ? 'Split stems' : operation === 'noise' ? 'Remove noise' : 'Start translation'} {!busy && <span>→</span>}</button></aside>;
}

function Toggle({ checked, onChange, label, premium = false }) { return <label className="toggle-row"><span>{label}{premium && <em>Premium</em>}</span><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} /><i /></label>; }

function VoiceWorkspace({ voices, voiceId, setVoiceId, voiceName, setVoiceName, voiceDeclaration, setVoiceDeclaration, voiceAuthorized, setVoiceAuthorized, voiceReference, setVoiceReference, voiceText, setVoiceText, createVoice, revokeVoice, busy, job, start, estimate, cost, downloadArtifact, previewArtifact, previewText }) {
  return <div className="voice-workspace"><div className="voice-columns"><form className="voice-card" onSubmit={createVoice}><h2>Create a consented voice</h2><p>Reference samples are private, ownership-recorded, and deletable.</p><input required value={voiceName} onChange={e => setVoiceName(e.target.value)} placeholder="Voice name" /><textarea required value={voiceDeclaration} onChange={e => setVoiceDeclaration(e.target.value)} placeholder="I own or am authorized to use this voice." /><label className="consent-check"><input type="checkbox" checked={voiceAuthorized} onChange={e => setVoiceAuthorized(e.target.checked)} /> I confirm authorization to use this voice.</label><input required type="file" accept="audio/*" onChange={e => setVoiceReference(e.target.files?.[0] || null)} /><button className="button primary" disabled={busy}>Store reference securely <span>→</span></button></form><div className="voice-card"><h2>Generate speech</h2><select value={voiceId} onChange={e => setVoiceId(e.target.value)}><option value="">Choose a voice</option>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select><textarea value={voiceText} onChange={e => setVoiceText(e.target.value)} placeholder="Enter text to synthesize" /><button className="button primary" disabled={busy || !voiceId || !voiceText} onClick={start}>Generate with Chatterbox <span>→</span></button><div className="cost-box"><span>Estimated cost</span><strong>{cost}<small>credits</small></strong><p>Calculated from text length and the server-side TTS profile.</p></div>{job && <JobStatus job={job} onDownload={downloadArtifact} onPreview={previewArtifact} />}{previewText && <pre className="transcript-preview">{previewText}</pre>}</div></div><div className="voice-list"><h2>Stored profiles</h2>{voices.length === 0 ? <p>No voice profiles yet.</p> : voices.map(voice => <div className="voice-row" key={voice.id}><span>{voice.name}<small>Consent {voice.consent_id.slice(0, 8)}…</small></span><button onClick={() => revokeVoice(voice.id)}>Delete reference</button></div>)}</div></div>;
}

function ProjectHistory({ jobs }) { return <div className="project-history">{jobs.length === 0 ? <div className="empty-projects">No jobs yet. Start with a media tool.</div> : jobs.map(job => <div className="history-row" key={job.id}><div><strong>{job.operation}</strong><span>{new Date(job.created_at).toLocaleString()}</span></div><em className={job.state === 'completed' ? 'green' : job.state === 'failed' ? 'red' : 'blue'}>{job.state}</em></div>)}</div>; }

export { AuthView };
