import React, { useEffect, useMemo, useState } from 'react';
import { apiFetch, apiUrl, uploadMedia } from './api';

const languages = { English: 'en', Spanish: 'es', French: 'fr', German: 'de', Portuguese: 'pt', Japanese: 'ja', Turkish: 'tr' };
const terminalStates = ['completed', 'failed', 'cancelled'];
const jobStages = ['created', 'validating', 'queued', 'provisioning', 'downloading', 'separating_audio', 'transcribing', 'translating', 'synthesizing', 'mixing', 'lip_syncing', 'uploading'];

function stageProgress(state) {
  const index = jobStages.indexOf(state);
  return { position: index < 0 ? 1 : index + 1, total: jobStages.length };
}

function stageLabel(state) {
  const progress = stageProgress(state);
  return `Stage ${progress.position} of ${progress.total}`;
}

function Glyph({ type }) {
  const symbols = { video: '▣', voice: '◉', stems: '∿', noise: '⌁', transcript: '▤', folder: '□', card: '▭', settings: '⚙' };
  return <span className="glyph" aria-hidden="true">{symbols[type] || '·'}</span>;
}

function GoogleIcon() {
  return <svg className="google-mark" viewBox="0 0 18 18" aria-hidden="true"><path fill="#4285F4" d="M17.64 9.205c0-.638-.057-1.252-.164-1.841H9v3.482h4.844a4.14 4.14 0 0 1-1.796 2.715v2.258h2.909c1.703-1.568 2.683-3.88 2.683-6.614Z" /><path fill="#34A853" d="M9 18c2.43 0 4.468-.805 5.957-2.181l-2.909-2.258c-.806.54-1.835.86-3.048.86-2.344 0-4.33-1.584-5.04-3.714H.953v2.331A9 9 0 0 0 9 18Z" /><path fill="#FBBC05" d="M3.96 10.707A5.41 5.41 0 0 1 3.675 9c0-.593.102-1.17.285-1.707V4.962H.953A9 9 0 0 0 0 9c0 1.453.348 2.827.953 4.038l3.007-2.331Z" /><path fill="#EA4335" d="M9 3.579c1.322 0 2.508.454 3.442 1.345l2.582-2.582C13.464.89 11.426 0 9 0A9 9 0 0 0 .953 4.962L3.96 7.293C4.67 5.163 6.656 3.579 9 3.579Z" /></svg>;
}

function AuthView({ onBack, onAuthenticated }) {
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [resetRequested, setResetRequested] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [googleEnabled, setGoogleEnabled] = useState(null);
  const [googleBusy, setGoogleBusy] = useState(false);
  useEffect(() => {
    const authError = new URLSearchParams(window.location.search).get('auth_error');
    if (authError) {
      const messages = { google_cancelled: 'Google sign-in was cancelled.', google_invalid_callback: 'Google sign-in could not be completed.', google_invalid_state: 'Google sign-in expired. Please try again.', google_identity_invalid: 'Google identity verification failed. Please try again.', google_identity_conflict: 'This Google account could not be linked safely.' };
      setError(messages[authError] || 'Google sign-in could not be completed.');
      window.history.replaceState({}, '', `${window.location.pathname}${window.location.hash}`);
    }
    apiFetch('/api/auth/google/config').then(result => setGoogleEnabled(Boolean(result.enabled))).catch(() => setGoogleEnabled(false));
  }, []);
  const submit = async (event) => {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      if (resetMode && !resetRequested) {
        const result = await apiFetch('/api/auth/password-reset/request', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) });
        setResetToken(result.dev_token || ''); setResetRequested(true); setError(result.dev_token ? 'Development reset token issued below.' : 'If the account exists, reset instructions were sent. Enter the token from the email to continue.');
      } else if (resetMode) {
        await apiFetch('/api/auth/password-reset/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: resetToken.trim(), password: newPassword }) });
        setResetMode(false); setResetRequested(false); setResetToken(''); setNewPassword(''); setMode('login'); setError('Password reset. You can sign in with the new password.');
      } else {
        const payload = mode === 'login' ? { email, password } : { email, password, display_name: displayName };
        onAuthenticated(await apiFetch(`/api/auth/${mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
      }
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const continueWithGoogle = () => {
    if (!googleEnabled) {
      setError(googleEnabled === false ? 'Google sign-in is not configured on this deployment yet.' : 'Checking Google sign-in availability…');
      return;
    }
    setError(''); setGoogleBusy(true); window.location.assign(`${apiUrl}/api/auth/google/login`);
  };
  return <div className="auth-page"><div className="auth-card"><button className="brand-button" onClick={onBack}>Lingo<span>Wave</span></button><h1>{resetMode ? 'Reset access.' : mode === 'login' ? 'Welcome back.' : 'Start creating.'}</h1><p>{resetMode ? resetRequested ? 'Enter the one-time token from your email.' : 'Request a secure password reset.' : mode === 'login' ? 'Sign in to continue your workspace.' : 'Create an account with 30 starter credits.'}</p><form onSubmit={submit}>{!resetMode && mode === 'signup' && <input value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Your name" /> }{(!resetMode || !resetRequested) && <input required type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Email address" />}{resetMode && resetRequested && <><input required value={resetToken} onChange={e => setResetToken(e.target.value)} placeholder="Password reset token" /><input required minLength="12" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="New password · 12 characters minimum" /></>}{!resetMode && <input required minLength="12" type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password · 12 characters minimum" />}{error && <div className="auth-error" role="alert">{error}</div>}{resetToken && resetRequested && <code className="reset-token">{resetToken}</code>}<button className="button primary" disabled={busy}>{busy ? 'Working…' : resetMode ? resetRequested ? 'Set new password' : 'Request reset' : mode === 'login' ? 'Log in' : 'Create account'} <span>→</span></button></form>{!resetMode && <><div className="auth-divider" role="separator"><span>or</span></div><button type="button" className="google-button" onClick={continueWithGoogle} disabled={busy || googleBusy}>{googleBusy ? 'Connecting…' : <><GoogleIcon />Continue with Google</>}</button></>}{!resetMode && mode === 'login' && <button className="auth-switch" onClick={() => { setResetMode(true); setResetRequested(false); setResetToken(''); }}>Forgot your password?</button>}{resetMode && resetRequested && <button className="auth-switch" onClick={() => { setResetRequested(false); setResetToken(''); setNewPassword(''); }}>Request another token</button>}<button className="auth-switch" onClick={() => { setResetMode(false); setResetRequested(false); setResetToken(''); setNewPassword(''); setMode(mode === 'login' ? 'signup' : 'login'); }}>{resetMode ? 'Back to sign in' : mode === 'login' ? 'Need an account? Create one' : 'Already have an account? Log in'}</button></div></div>;
}

function Toast({ toast }) {
  if (!toast) return null;
  const icon = toast.tone === 'success' ? '✓' : toast.tone === 'warning' ? '!' : '×';
  return <div className={`toast ${toast.tone}`} role="status" aria-live="polite" aria-atomic="true"><span className="toast-icon" aria-hidden="true">{icon}</span>{toast.message}</div>;
}

function JobStatus({ job, onDownload, onPreview, onCancel, sourceFile }) {
  if (!job) return null;
  const enhanced = job.artifacts?.find(artifact => artifact.name === 'enhanced_audio');
  const progress = stageProgress(job.state);
  const terminalLabel = job.state === 'completed' ? 'Complete' : terminalStates.includes(job.state) ? 'Stopped' : stageLabel(job.state);
  const width = job.state === 'completed' ? 100 : `${(progress.position / progress.total) * 100}%`;
  return <div className="job-status"><div className="job-status-heading"><span>{job.state.replaceAll('_', ' ')}</span><strong>{terminalLabel}</strong></div><div className="progress-bar" aria-label={`Processing ${terminalLabel}`}><i style={{ width }} /></div>{job.error_message && <p className="job-error">{job.error_message}</p>}{job.operation === 'noise' && sourceFile?.local && enhanced && <div className="noise-comparison"><div><span>Original</span><audio controls preload="metadata" src={sourceFile.local} /></div><div><span>Enhanced</span><audio controls preload="metadata" src={`${apiUrl}/api/jobs/${job.id}/artifacts/${enhanced.id}/download`} /></div></div>}{job.artifacts?.length > 0 && <div className="artifact-list">{job.artifacts.map(artifact => <div className="artifact-row" key={artifact.id}><span>{artifact.filename}</span>{artifact.content_type.startsWith('audio/') && <audio controls preload="none" src={`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`} />}{artifact.content_type.startsWith('video/') && <video controls preload="metadata" src={`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`} /> }<div><button onClick={() => artifact.content_type.startsWith('text/') || artifact.content_type.includes('subrip') ? onPreview(artifact) : onDownload(artifact)}> {artifact.content_type.startsWith('text/') || artifact.content_type.includes('subrip') ? 'Preview' : 'Download'} </button><button onClick={() => onDownload(artifact)}>↓</button></div></div>)}</div>}{onCancel && !terminalStates.includes(job.state) && <button className="cancel-job" onClick={() => onCancel(job.id)}>Cancel job</button>}</div>;
}

function UploadCard({ file, busy, onFile, onRemove, title = 'Drop your media here' }) {
  return <section className={`upload-card ${file ? 'has-file' : ''}`}><label className="dropzone"><input type="file" accept="video/*,audio/*" onChange={e => onFile(e.target.files)} disabled={busy} />{file ? <><div className="media-preview" style={file.media_kind === 'audio' ? { backgroundImage: `url(${file.local})` } : undefined}>{file.media_kind === 'video' ? <video controls preload="metadata" src={file.local} aria-label="Selected source video" /> : <div className="video-scene"><div className="person" /><div className="mic" /></div>}</div><div className="file-meta"><span className="file-icon">▤</span><div><strong>{file.name}</strong><small>{Math.round(file.duration_seconds)}s · FFprobe inspected</small></div><button className="remove-file" onClick={e => { e.preventDefault(); onRemove(); }}>×</button></div><p className="browse-copy">Drop another file or <span>click to browse</span></p></> : <><div className="upload-icon">↑</div><h3>{title}</h3><p>or <span>click to browse</span></p><small>MP4, MOV, WEBM, WAV · up to 4GB</small></>}</label>{file?.media_kind === 'audio' && <audio className="inline-audio" controls src={file.local} aria-label="Selected source audio" />}</section>;
}

function DownloadableResult({ job, onDownload, onPreview, onCancel, sourceFile }) {
  return <JobStatus job={job} onDownload={onDownload} onPreview={onPreview} onCancel={onCancel} sourceFile={sourceFile} />;
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
  const [estimateError, setEstimateError] = useState('');
  const [job, setJob] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [previewText, setPreviewText] = useState('');
  const [previewArtifactId, setPreviewArtifactId] = useState(null);
  const [savingPreview, setSavingPreview] = useState(false);
  const [credits, setCredits] = useState(0);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const userInitials = (initialUser?.display_name || initialUser?.email || 'LW').slice(0, 2).toUpperCase();
  const showToast = (message) => {
    const text = String(message);
    const tone = /cancelled|stopped/i.test(text) ? 'warning' : /error|failed|unable|need |choose |create or|could not|must |not configured|expired|invalid|requires|unavailable|no mail/i.test(text) ? 'error' : 'success';
    setToast({ message: text, tone });
    window.setTimeout(() => setToast(null), 3200);
  };
  const refreshWorkspace = () => Promise.all([apiFetch('/api/credits'), apiFetch('/api/voices'), apiFetch('/api/jobs'), apiFetch('/api/projects')]).then(([creditData, voiceData, jobs, projectData]) => { setCredits(creditData.balance); setVoices(voiceData); setRecentJobs(jobs); setProjects(projectData); if (!voiceId && voiceData[0]) setVoiceId(voiceData[0].id); }).catch(error => showToast(error.message));
  useEffect(() => { refreshWorkspace(); }, []);
  useEffect(() => () => { if (file?.local) URL.revokeObjectURL(file.local); }, [file?.local]);
  useEffect(() => {
    if (!job || terminalStates.includes(job.state)) return undefined;
    const timer = window.setInterval(() => apiFetch(`/api/jobs/${job.id}`).then(next => { setJob(next); if (terminalStates.includes(next.state)) { refreshWorkspace(); showToast(next.state === 'completed' ? 'Processing complete — your files are ready.' : next.error_message || 'The worker could not complete this job.'); } }).catch(error => showToast(error.message)), 1200);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state]);
  const cost = estimate?.credits || '—';
  const operation = useMemo(() => activeTool === 'Video Translator' ? 'dubbing' : activeTool === 'Transcription' ? (translateSubtitles ? 'subtitle_translation' : 'transcription') : activeTool === 'Stem Splitter' ? 'stems' : activeTool === 'Noise Remover' ? 'noise' : 'tts', [activeTool, translateSubtitles]);
  const readableEstimateError = error => error.message.includes('Measured credit profile') ? 'Processing is temporarily unavailable until measured cost profiles are configured.' : error.message;
  useEffect(() => {
    if (!file || activeTool === 'Voice Studio') return undefined;
    let cancelled = false;
    setEstimate(null);
    setEstimateError('');
    const payload = { media_asset_id: file.id, operation, lip_sync: lipSync, quality, target_language: languages[target], source_language: sourceLanguage || null };
    apiFetch('/api/jobs/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(nextEstimate => { if (!cancelled) setEstimate(nextEstimate); })
      .catch(error => { if (!cancelled) setEstimateError(readableEstimateError(error)); });
    return () => { cancelled = true; };
  }, [file?.id, activeTool, operation, target, sourceLanguage, lipSync, quality]);
  const handleFile = async (next) => { const picked = next?.[0]; if (!picked) return; setBusy(true); try { const asset = await uploadMedia(picked); setFile({ ...asset, name: asset.filename, local: URL.createObjectURL(picked) }); setEstimate(null); setEstimateError(''); setJob(null); setPreviewText(''); setPreviewArtifactId(null); showToast('Media inspected by FFprobe — ready to configure.'); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const requestJob = async (jobOptions = {}) => {
    const estimatePayload = { media_asset_id: file?.id || null, operation, lip_sync: lipSync, quality, text: jobOptions.text, target_language: languages[target], source_language: sourceLanguage || null };
    const nextEstimate = await apiFetch('/api/jobs/estimate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(estimatePayload) });
    setEstimate(nextEstimate); setEstimateError('');
    if (credits < nextEstimate.credits) throw new Error(`You need ${nextEstimate.credits} credits; your balance is ${credits}.`);
    const created = await apiFetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ ...estimatePayload, project_id: selectedProjectId || null, media_asset_id: file?.id || null, preserve_voice: preserveVoice, keep_background: keepBackground, lip_sync: lipSync, voice_profile_id: jobOptions.voice_profile_id || (activeTool === 'Video Translator' ? voiceId : null), text: jobOptions.text, stems: stemCount }) });
    setJob(created); setPreviewText(''); setPreviewArtifactId(null); showToast('Job queued — the worker will report each real processing stage.');
  };
  const start = async () => { if (!file && activeTool !== 'Voice Studio') return showToast('Choose media first.'); if (activeTool === 'Video Translator' && !voiceId) return showToast('Create or choose a consented voice profile before starting.'); if (activeTool === 'Voice Studio' && (!voiceId || !voiceText.trim())) return showToast('Choose a voice and enter text first.'); setBusy(true); try { await requestJob(activeTool === 'Voice Studio' ? { text: voiceText, voice_profile_id: voiceId } : {}); } catch (error) { const message = readableEstimateError(error); setEstimateError(message); showToast(message); } finally { setBusy(false); } };
  const createVoice = async (event) => { event.preventDefault(); if (!voiceReference) return showToast('Choose a 3–90 second reference sample.'); setBusy(true); try { const form = new FormData(); form.append('name', voiceName); form.append('declaration', voiceDeclaration); form.append('authorized', String(voiceAuthorized)); form.append('upload', voiceReference); const created = await apiFetch('/api/voices', { method: 'POST', body: form }); setVoices(current => [created, ...current]); setVoiceId(created.id); setVoiceName(''); setVoiceDeclaration(''); setVoiceReference(null); showToast('Consent recorded and voice profile secured.'); } catch (error) { showToast(error.message); } finally { setBusy(false); } };
  const downloadArtifact = async (artifact) => { window.open(`${apiUrl}/api/jobs/${job.id}/artifacts/${artifact.id}/download`, '_blank', 'noopener'); };
  const previewArtifact = async (artifact) => { try { const result = await apiFetch(`/api/jobs/${job.id}/artifacts/${artifact.id}/preview`); setPreviewText(result.text); setPreviewArtifactId(artifact.id); } catch (error) { showToast(error.message); } };
  const savePreview = async () => { if (!job || !previewArtifactId) return; setSavingPreview(true); try { await apiFetch(`/api/jobs/${job.id}/artifacts/${previewArtifactId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: previewText }) }); showToast('Text artifact saved.'); } catch (error) { showToast(error.message); } finally { setSavingPreview(false); } };
  const cancelJob = async id => { try { const next = await apiFetch(`/api/jobs/${id}/cancel`, { method: 'POST' }); setJob(next); await refreshWorkspace(); showToast('Job cancelled and reserved credits accounted for.'); } catch (error) { showToast(error.message); } };
  const revokeVoice = async (id) => { try { await apiFetch(`/api/voices/${id}`, { method: 'DELETE' }); setVoices(current => current.filter(voice => voice.id !== id)); if (voiceId === id) setVoiceId(''); showToast('Voice reference deleted.'); } catch (error) { showToast(error.message); } };
  const createProject = async name => { try { const project = await apiFetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }); setProjects(current => [project, ...current]); setSelectedProjectId(project.id); showToast('Project created.'); } catch (error) { showToast(error.message); } };
  const selectTool = (tool) => { setActiveTool(tool); setFile(null); setEstimate(null); setEstimateError(''); setJob(null); setPreviewText(''); setPreviewArtifactId(null); };
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
          <button onClick={() => { setActiveTool("Billing"); setJob(null); }}><Glyph type="card" />Billing</button>
          <button onClick={() => selectTool("Settings")}><Glyph type="settings" />Settings</button>
          {(initialUser?.role === 'admin' || initialUser?.role === 'operator') && <button onClick={() => selectTool("Operations")}><Glyph type="settings" />Operations</button>}
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
            <div><h1>{activeTool}</h1><p>{activeTool === "Video Translator" ? "Translate and dub your next story without losing its character." : activeTool === "Projects" ? "Your persisted jobs and downloadable artifacts." : activeTool === "Billing" ? "Manage your plan, credits, and secure Stripe checkout." : "A focused workspace for your next media project."}</p></div>
            <button className="help-button">?</button>
          </div>
          {activeTool === "Projects" ? (
            <ProjectHistory jobs={recentJobs} projects={projects} selectedProjectId={selectedProjectId} onSelectProject={setSelectedProjectId} onCreateProject={createProject} />
          ) : activeTool === "Billing" ? (
            <BillingWorkspace onToast={showToast} />
          ) : activeTool === "Settings" ? (
            <AccountSettings onDeleted={onExit} onToast={showToast} />
          ) : activeTool === "Operations" ? (
            <OperatorView onToast={showToast} />
          ) : activeTool === "Voice Studio" ? (
            <VoiceWorkspace voices={voices} voiceId={voiceId} setVoiceId={setVoiceId} target={target} setTarget={setTarget} voiceName={voiceName} setVoiceName={setVoiceName} voiceDeclaration={voiceDeclaration} setVoiceDeclaration={setVoiceDeclaration} voiceAuthorized={voiceAuthorized} setVoiceAuthorized={setVoiceAuthorized} voiceReference={voiceReference} setVoiceReference={setVoiceReference} voiceText={voiceText} setVoiceText={setVoiceText} createVoice={createVoice} revokeVoice={revokeVoice} busy={busy} job={job} start={start} cancelJob={cancelJob} estimate={estimate} cost={cost} downloadArtifact={downloadArtifact} previewArtifact={previewArtifact} previewText={previewText} />
          ) : (
            <>
              <div className="workflow-steps"><span className="current"><b>1</b>Upload</span><i /><span className={file ? "current" : ""}><b>2</b>Configure</span><i /><span className={job?.state === "completed" ? "current" : ""}><b>3</b>Export</span></div>
              <div className="workspace-grid">
                <div>
                  <UploadCard file={file} busy={busy} onFile={handleFile} onRemove={() => { setFile(null); setJob(null); setEstimate(null); setPreviewText(''); setPreviewArtifactId(null); }} title={activeTool === "Transcription" || activeTool === "Noise Remover" ? "Drop audio or video here" : activeTool === "Stem Splitter" ? "Drop music or video here" : "Drop your video here"} />
                  {job && <DownloadableResult job={job} sourceFile={file} onDownload={downloadArtifact} onPreview={previewArtifact} onCancel={cancelJob} />}
                  {previewArtifactId !== null && <EditableTextArtifact value={previewText} onChange={setPreviewText} onSave={savePreview} saving={savingPreview} />}
                </div>
                <ConfigRail activeTool={activeTool} operation={operation} target={target} setTarget={setTarget} sourceLanguage={sourceLanguage} setSourceLanguage={setSourceLanguage} preserveVoice={preserveVoice} setPreserveVoice={setPreserveVoice} keepBackground={keepBackground} setKeepBackground={setKeepBackground} lipSync={lipSync} setLipSync={setLipSync} quality={quality} setQuality={setQuality} stemCount={stemCount} setStemCount={setStemCount} translateSubtitles={translateSubtitles} setTranslateSubtitles={setTranslateSubtitles} voices={voices} voiceId={voiceId} setVoiceId={setVoiceId} cost={cost} estimate={estimate} estimateError={estimateError} hasFile={Boolean(file)} busy={busy} job={job} onStart={start} />
              </div>
            </>
          )}
        </main>
        <Toast toast={toast} />
      </div>
    </div>
  );
}

function ConfigRail({ activeTool, operation, target, setTarget, sourceLanguage, setSourceLanguage, preserveVoice, setPreserveVoice, keepBackground, setKeepBackground, lipSync, setLipSync, quality, setQuality, stemCount, setStemCount, translateSubtitles, setTranslateSubtitles, voices, voiceId, setVoiceId, cost, estimate, estimateError, hasFile, busy, job, onStart }) {
  const voiceRequired = activeTool === 'Video Translator' && !voiceId;
  const mediaRequired = activeTool !== 'Voice Studio' && !hasFile;
  const estimatePending = hasFile && !estimate && !estimateError;
  const unavailable = Boolean(estimateError);
  const sourceOptions = [['Auto-detect', ''], ...Object.entries(languages).map(([label, value]) => [label, value])];
  const activeJob = job && !terminalStates.includes(job.state);
  return <aside className="config-rail"><div className="config-group"><label>Source language</label><select value={sourceLanguage || ''} onChange={e => setSourceLanguage(e.target.value)}>{sourceOptions.map(([label, value]) => <option key={label} value={value}>{label}</option>)}</select></div>{activeTool !== 'Noise Remover' && <div className="config-group"><label>Target language</label><select value={target} onChange={e => setTarget(e.target.value)}>{Object.keys(languages).map(lang => <option key={lang}>{lang}</option>)}</select></div>}{activeTool === 'Video Translator' && <><div className="config-group"><label>Voice profile</label><select value={voiceId} onChange={e => setVoiceId(e.target.value)}><option value="">Choose a consented voice</option>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select></div><div className="toggle-group"><Toggle label="Preserve voice" checked={preserveVoice} onChange={setPreserveVoice} /><Toggle label="Keep background" checked={keepBackground} onChange={setKeepBackground} /><Toggle label="Lip sync" premium checked={lipSync} onChange={setLipSync} disabled /></div></>}{activeTool === 'Stem Splitter' && <div className="config-group"><label>Output</label><select value={stemCount} onChange={e => setStemCount(Number(e.target.value))}><option value="2">Vocals + instrumental</option><option value="4">4 stems</option></select></div>}{activeTool === 'Transcription' && <Toggle label="Translate subtitles" checked={translateSubtitles} onChange={setTranslateSubtitles} />}{activeTool !== 'Noise Remover' && <div className="config-group"><label>Quality</label><select value={quality} onChange={e => setQuality(e.target.value)}><option value="balanced">Balanced</option><option value="studio">Studio</option><option value="draft">Draft</option></select></div>}<div className="cost-box"><span>Estimated cost</span><strong>{cost}<small>credits</small></strong><p className={estimateError ? 'cost-error' : undefined}>{mediaRequired ? 'Upload media to calculate from the configured profile.' : voiceRequired ? 'Choose a consented voice profile before processing.' : estimateError || (estimate ? `${estimate.duration_seconds.toFixed(1)}s · server-side estimate` : 'Upload media to calculate from the configured profile.')}</p></div><button className="button primary start-button" disabled={busy || mediaRequired || voiceRequired || estimatePending || unavailable || Boolean(activeJob)} onClick={onStart}>{busy ? 'Working…' : activeJob ? `${job.state.replaceAll('_', ' ')} · ${stageLabel(job.state)}` : unavailable ? 'Unavailable' : estimatePending ? 'Calculating…' : operation === 'transcription' ? 'Transcribe' : operation === 'stems' ? 'Split stems' : operation === 'noise' ? 'Remove noise' : 'Start translation'} {!busy && !unavailable && !estimatePending && <span>→</span>}</button></aside>;
}

function Toggle({ checked, onChange, label, premium = false, disabled = false }) { return <label className={`toggle-row ${disabled ? 'disabled' : ''}`}><span>{label}{premium && <em>{disabled ? 'Unavailable' : 'Premium'}</em>}</span><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled} /><i /></label>; }

function EditableTextArtifact({ value, onChange, onSave, saving }) {
  return <div className="editable-artifact"><textarea className="transcript-preview" value={value} onChange={event => onChange(event.target.value)} aria-label="Editable text artifact" /><button className="button secondary" onClick={onSave} disabled={saving}>{saving ? 'Saving…' : 'Save edits'}</button></div>;
}

function VoiceWorkspace({ voices, voiceId, setVoiceId, target, setTarget, voiceName, setVoiceName, voiceDeclaration, setVoiceDeclaration, voiceAuthorized, setVoiceAuthorized, voiceReference, setVoiceReference, voiceText, setVoiceText, createVoice, revokeVoice, busy, job, start, cancelJob, estimate, cost, downloadArtifact, previewArtifact, previewText }) {
  return <div className="voice-workspace"><div className="voice-columns"><form className="voice-card" onSubmit={createVoice}><h2>Create a consented voice</h2><p>Reference samples are private, ownership-recorded, and deletable.</p><input required value={voiceName} onChange={e => setVoiceName(e.target.value)} placeholder="Voice name" /><textarea required value={voiceDeclaration} onChange={e => setVoiceDeclaration(e.target.value)} placeholder="I own or am authorized to use this voice." /><label className="consent-check"><input type="checkbox" checked={voiceAuthorized} onChange={e => setVoiceAuthorized(e.target.checked)} /> I confirm authorization to use this voice.</label><input required type="file" accept="audio/*" onChange={e => setVoiceReference(e.target.files?.[0] || null)} /><button className="button primary" disabled={busy}>Store reference securely <span>→</span></button></form><div className="voice-card"><h2>Generate speech</h2><select value={voiceId} onChange={e => setVoiceId(e.target.value)}><option value="">Choose a voice</option>{voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}</select><div className="config-group"><label>Language</label><select value={target} onChange={e => setTarget(e.target.value)}>{Object.keys(languages).map(language => <option key={language}>{language}</option>)}</select></div><textarea value={voiceText} onChange={e => setVoiceText(e.target.value)} placeholder="Enter text to synthesize" /><button className="button primary" disabled={busy || !voiceId || !voiceText} onClick={start}>Generate with VoxCPM2 <span>→</span></button><div className="cost-box"><span>Estimated cost</span><strong>{cost}<small>credits</small></strong><p>Calculated from text length and the server-side TTS profile.</p></div>{job && <JobStatus job={job} onDownload={downloadArtifact} onPreview={previewArtifact} onCancel={cancelJob} />}{previewText && <pre className="transcript-preview">{previewText}</pre>}</div></div><div className="voice-list"><h2>Stored profiles</h2>{voices.length === 0 ? <p>No voice profiles yet.</p> : voices.map(voice => <div className="voice-row" key={voice.id}><span>{voice.name}<small>Consent {voice.consent_id.slice(0, 8)}…</small></span><button onClick={() => revokeVoice(voice.id)}>Delete reference</button></div>)}</div></div>;
}

function BillingWorkspace({ onToast }) {
  const [billing, setBilling] = useState(null);
  const [plans, setPlans] = useState([]);
  const [busy, setBusy] = useState(false);
  const reload = async () => { try { const [summary, availablePlans] = await Promise.all([apiFetch('/api/billing'), apiFetch('/api/plans')]); setBilling(summary); setPlans(availablePlans); } catch (error) { onToast(error.message); } };
  useEffect(() => { reload(); }, []);
  const checkout = async (kind, key) => { setBusy(true); try { const result = await apiFetch('/api/billing/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, key }) }); window.location.assign(result.url); } catch (error) { onToast(error.message); setBusy(false); } };
  const portal = async () => { setBusy(true); try { const result = await apiFetch('/api/billing/portal', { method: 'POST' }); window.location.assign(result.url); } catch (error) { onToast(error.message); setBusy(false); } };
  if (!billing) return <div className="billing-workspace"><section className="billing-card"><p>Loading billing…</p></section></div>;
  return <div className="billing-workspace"><section className="billing-card billing-summary"><div><span className="eyebrow">Current plan</span><h2>{billing.plan.name}</h2><p>{billing.credits} credits available</p></div><div className="billing-summary-actions">{billing.checkout_enabled && billing.subscriptions?.length > 0 && <button className="button secondary" onClick={portal} disabled={busy}>Manage subscription</button>}<button className="button secondary" onClick={reload} disabled={busy}>Refresh</button></div></section><section className="billing-card"><div className="billing-heading"><div><h2>Plans</h2><p>Subscriptions renew through Stripe Billing; credits are granted only from verified webhooks.</p></div></div><div className="billing-plan-grid">{plans.filter(item => item.key !== 'free').map(item => <article className={`billing-plan ${item.key === 'pro' ? 'featured' : ''}`} key={item.key}><span className="eyebrow">{item.name}</span><strong>{item.monthly_credits.toLocaleString()} <small>credits / month</small></strong><p>{item.max_concurrent_jobs} concurrent jobs · {item.max_voice_profiles} saved voices</p><button className="button primary" onClick={() => checkout('subscription', item.key)} disabled={busy || !billing.checkout_enabled}>{billing.checkout_enabled ? `Choose ${item.name}` : 'Checkout unavailable'}</button></article>)}</div>{!billing.checkout_enabled && <p className="billing-note">Stripe is not configured on this deployment yet. Add the secret, webhook, and price IDs to enable checkout; no simulated payment is shown.</p>}</section><section className="billing-card"><div className="billing-heading"><div><h2>Credit packs</h2><p>Buy additional processing credits without changing your plan.</p></div></div><div className="credit-pack-grid">{[['starter', '100'], ['growth', '500'], ['scale', '1,500']].map(([key, label]) => <button className="credit-pack" key={key} onClick={() => checkout('credits', key)} disabled={busy || !billing.checkout_enabled}><strong>{label}</strong><span>{key} pack</span></button>)}</div></section>{billing.purchases?.length > 0 && <section className="billing-card"><h2>Recent purchases</h2>{billing.purchases.map(purchase => <div className="billing-row" key={`${purchase.pack_key}-${purchase.created_at}`}><span>{purchase.pack_key} · {purchase.credits} credits</span><em>{purchase.status}</em></div>)}</section>}</div>;
}

function AccountSettings({ onDeleted, onToast }) {
  const [account, setAccount] = useState(null);
  const [usage, setUsage] = useState([]);
  const [displayName, setDisplayName] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { Promise.all([apiFetch('/api/account'), apiFetch('/api/usage')]).then(([result, recentUsage]) => { setAccount(result); setDisplayName(result.user.display_name || ''); setUsage(recentUsage); }).catch(error => onToast(error.message)); }, []);
  const save = async event => { event.preventDefault(); setBusy(true); try { const user = await apiFetch('/api/account', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ display_name: displayName }) }); setAccount(current => ({ ...current, user })); onToast('Account settings saved.'); } catch (error) { onToast(error.message); } finally { setBusy(false); } };
  const remove = async () => { if (!window.confirm('Delete your account, media, artifacts, and voice references?')) return; setBusy(true); try { await apiFetch('/api/account', { method: 'DELETE' }); onDeleted(); } catch (error) { onToast(error.message); setBusy(false); } };
  if (!account) return <div className="settings-card"><p>Loading account settings…</p></div>;
  return <div className="settings-workspace"><form className="settings-card" onSubmit={save}><h2>Account settings</h2><p>Update the profile attached to your persisted workspace.</p><label>Display name<input value={displayName} onChange={event => setDisplayName(event.target.value)} maxLength={120} /></label><label>Email<input value={account.user.email} readOnly /></label><div className="settings-summary"><span>Plan<strong>{account.user.plan}</strong></span><span>Credits<strong>{account.credits}</strong></span></div><button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save settings'} <span>→</span></button></form><section className="settings-card"><h2>Usage history</h2><p>Persisted processing activity and actual charges when measured.</p>{usage.length === 0 ? <p>No completed processing records yet.</p> : usage.slice(0, 10).map(entry => <div className="history-row" key={entry.job_id}><div><strong>{entry.operation}</strong><span>{entry.state} · {entry.input_duration_seconds ? `${Number(entry.input_duration_seconds / 60).toFixed(1)} min` : 'text input'}</span></div><em className={entry.state === 'completed' ? 'green' : entry.state === 'failed' ? 'red' : 'blue'}>{entry.actual_credits == null ? '—' : `${entry.actual_credits} credits`}</em></div>)}</section><div className="settings-card danger-card"><h2>Delete account</h2><p>This permanently removes stored media, generated artifacts, and consented voice references.</p><button className="button danger" disabled={busy} onClick={remove}>Delete my account</button></div></div>;
}

function OperatorView({ onToast }) {
  const [metrics, setMetrics] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [abuse, setAbuse] = useState([]);
  const [queue, setQueue] = useState(null);
  const [versions, setVersions] = useState([]);
  const [gpuProfiles, setGpuProfiles] = useState([]);
  const [users, setUsers] = useState([]);
  const [voices, setVoices] = useState([]);
  const [search, setSearch] = useState('');
  const [selectedUserId, setSelectedUserId] = useState('');
  const [creditAmount, setCreditAmount] = useState('10');
  const reload = async () => { try { const [metricData, jobData, abuseData, queueData, versionData, gpuData, userData, voiceData] = await Promise.all([apiFetch('/api/admin/metrics'), apiFetch('/api/admin/jobs'), apiFetch('/api/admin/abuse'), apiFetch('/api/admin/queue'), apiFetch('/api/admin/model-versions'), apiFetch('/api/admin/gpu-profiles'), apiFetch('/api/admin/users'), apiFetch('/api/admin/voices')]); setMetrics(metricData); setJobs(jobData); setAbuse(abuseData); setQueue(queueData); setVersions(versionData); setGpuProfiles(gpuData); setUsers(userData); setVoices(voiceData); if (!selectedUserId && userData[0]) setSelectedUserId(userData[0].id); } catch (error) { onToast(error.message); } };
  useEffect(() => { reload(); }, []);
  const retry = async id => { try { await apiFetch(`/api/admin/jobs/${id}/retry`, { method: 'POST' }); await reload(); onToast('Job requeued.'); } catch (error) { onToast(error.message); } };
  const cancel = async id => { try { await apiFetch(`/api/admin/jobs/${id}/cancel`, { method: 'POST' }); await reload(); onToast('Job cancelled.'); } catch (error) { onToast(error.message); } };
  const resolve = async id => { try { await apiFetch(`/api/admin/abuse/${id}/resolve`, { method: 'POST' }); await reload(); onToast('Abuse event resolved.'); } catch (error) { onToast(error.message); } };
  const disableUser = async id => { try { await apiFetch(`/api/admin/users/${id}/disable`, { method: 'POST' }); await reload(); onToast('Account disabled.'); } catch (error) { onToast(error.message); } };
  const adjustCredits = async delta => { const amount = Number(creditAmount); if (!selectedUserId || !Number.isInteger(amount) || amount <= 0) return onToast('Choose a user and enter a positive whole number of credits.'); const action = delta > 0 ? 'grant' : 'revoke'; const reference = `operator:${crypto.randomUUID()}`; try { await apiFetch(`/api/admin/credits/${action}?user_id=${encodeURIComponent(selectedUserId)}&credits=${amount}&reference_key=${encodeURIComponent(reference)}`, { method: 'POST' }); await reload(); onToast(delta > 0 ? 'Credits granted.' : 'Credits revoked.'); } catch (error) { onToast(error.message); } };
  const revokeVoice = async id => { try { await apiFetch(`/api/admin/voices/${id}/revoke`, { method: 'POST' }); await reload(); onToast('Voice profile revoked.'); } catch (error) { onToast(error.message); } };
  const visibleJobs = jobs.filter(job => `${job.id} ${job.operation} ${job.state} ${job.user_id || ''}`.toLowerCase().includes(search.toLowerCase())).slice(0, 50);
  const cost = value => value == null ? '—' : `$${Number(value).toFixed(2)}`;
  const measuredCost = value => value == null ? '—' : cost(value);
  return <div className="operator-workspace"><div className="operator-toolbar"><p>Operational data only; no demo metrics are generated.</p><button onClick={reload}>Refresh</button></div>{metrics && <div className="metric-grid"><Metric label="Completed jobs" value={metrics.job_counts?.completed || 0} /><Metric label="Failed jobs" value={metrics.job_counts?.failed || 0} /><Metric label="Audio minutes" value={Number(metrics.processed_audio_minutes || 0).toFixed(1)} /><Metric label="Video minutes" value={Number(metrics.processed_video_minutes || 0).toFixed(1)} /><Metric label="Credits used" value={metrics.credits_used || 0} /><Metric label="Estimated cost" value={metrics.estimated_cost_available ? cost(metrics.estimated_compute_cost_usd) : '—'} /><Metric label="Measured cost" value={metrics.measured_cost_available ? cost(metrics.measured_compute_cost_usd) : '—'} /><Metric label="Cost / minute" value={metrics.cost_per_processed_minute_usd == null ? '—' : cost(metrics.cost_per_processed_minute_usd)} /><Metric label="Queue visible" value={queue?.visible ?? '—'} /><Metric label="Queue in flight" value={queue?.in_flight ?? '—'} /><Metric label="Active workers" value={metrics.active_workers_available ? metrics.active_workers : '—'} /><Metric label="Retry attempts" value={Object.values(metrics.operation_counts || {}).reduce((sum, item) => sum + item.retry_attempts, 0)} /></div>}<div className="operator-columns"><section className="operator-card"><h2>Jobs</h2><input className="operator-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by job, tool, state, or user" />{visibleJobs.map(job => <div className="operator-row" key={job.id}><span><strong>{job.operation} · {job.state}</strong><small>{job.id.slice(0, 8)} · {job.user_id || 'unknown user'}</small></span><span className="operator-actions">{job.state === 'failed' || job.state === 'cancelled' ? <button onClick={() => retry(job.id)}>Retry</button> : null}{!['completed', 'failed', 'cancelled'].includes(job.state) ? <button onClick={() => cancel(job.id)}>Cancel</button> : null}</span></div>)}{visibleJobs.length === 0 && <p>No matching jobs.</p>}</section><section className="operator-card"><h2>Users and credits</h2><div className="operator-credit-form"><select value={selectedUserId} onChange={event => setSelectedUserId(event.target.value)}><option value="">Choose user</option>{users.map(user => <option key={user.id} value={user.id}>{user.email} · {user.plan}</option>)}</select><input value={creditAmount} onChange={event => setCreditAmount(event.target.value)} inputMode="numeric" aria-label="Credit amount" /><button onClick={() => adjustCredits(1)}>Grant</button><button onClick={() => adjustCredits(-1)}>Revoke</button></div>{users.slice(0, 20).map(user => <div className="operator-row" key={user.id}><span><strong>{user.display_name || user.email}</strong><small>{user.email} · {user.role} · {user.is_active ? 'active' : 'disabled'}</small></span>{user.id !== metrics?.operator_id && user.is_active ? <button onClick={() => disableUser(user.id)}>Disable</button> : null}</div>)}</section></div><div className="operator-columns"><section className="operator-card"><h2>Failed jobs</h2>{jobs.filter(job => job.state === 'failed').slice(0, 20).map(job => <div className="operator-row" key={job.id}><span><strong>{job.operation}</strong><small>{job.id.slice(0, 8)} · {job.error_code || 'failed'}</small></span><button onClick={() => retry(job.id)}>Retry</button></div>)}{jobs.filter(job => job.state === 'failed').length === 0 && <p>No failed jobs.</p>}</section><section className="operator-card"><h2>Abuse events</h2>{abuse.filter(event => event.status === 'open').slice(0, 20).map(event => <div className="operator-row" key={event.id}><span><strong>{event.event_type}</strong><small>{event.description}</small></span><button onClick={() => resolve(event.id)}>Resolve</button></div>)}{abuse.filter(event => event.status === 'open').length === 0 && <p>No open abuse events.</p>}</section></div><div className="operator-columns"><section className="operator-card"><h2>Voice profiles</h2>{voices.filter(voice => voice.status === 'active').slice(0, 20).map(voice => <div className="operator-row" key={voice.id}><span><strong>{voice.name}</strong><small>{voice.user_id} · {voice.created_at}</small></span><button onClick={() => revokeVoice(voice.id)}>Revoke</button></div>)}{voices.filter(voice => voice.status === 'active').length === 0 && <p>No active voice profiles.</p>}</section><section className="operator-card"><h2>Cost breakdown</h2>{(metrics?.cost_by_tool || []).map(item => <div className="operator-row" key={item.operation}><span><strong>{item.operation}</strong><small>Estimated {measuredCost(item.estimated_cost_usd)} · Actual {measuredCost(item.actual_cost_usd)}</small></span></div>)}{(metrics?.cost_by_tool || []).length === 0 && <p>No measured cost records yet.</p>}{(metrics?.cost_by_model || []).map(item => <div className="operator-row" key={item.model_version}><span><strong>{item.model_version || 'unknown model'}</strong><small>Estimated {measuredCost(item.estimated_cost_usd)} · Actual {measuredCost(item.actual_cost_usd)}</small></span></div>)}{(metrics?.cost_by_worker || []).map(item => <div className="operator-row" key={`${item.worker_type}-${item.gpu_type}`}><span><strong>{item.worker_type || 'unknown worker'} · {item.gpu_type || 'unknown GPU'}</strong><small>Estimated {measuredCost(item.estimated_cost_usd)} · Actual {measuredCost(item.actual_cost_usd)}</small></span></div>)}<p>Gross margin is intentionally unavailable until a payment provider and revenue price exist.</p></section></div><div className="operator-card"><h2>Configured versions and GPU profiles</h2><p>{versions.length} model versions · lease-based active worker count expires after the worker heartbeat TTL.</p></div></div>;
}

function Metric({ label, value }) { return <div className="metric-card"><span>{label}</span><strong>{value}</strong></div>; }

function ProjectHistory({ jobs, projects, selectedProjectId, onSelectProject, onCreateProject }) {
  const [selected, setSelected] = useState(null);
  const [previewText, setPreviewText] = useState('');
  const [previewArtifactId, setPreviewArtifactId] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [savingPreview, setSavingPreview] = useState(false);
  const [projectName, setProjectName] = useState('');
  const openJob = async job => {
    setBusy(true);
    try { setSelected(await apiFetch(`/api/jobs/${job.id}`)); setPreviewText(''); setPreviewArtifactId(null); setError(''); } catch (nextError) { setSelected(null); setError(nextError.message); } finally { setBusy(false); }
  };
  const download = artifact => window.open(`${apiUrl}/api/jobs/${selected.id}/artifacts/${artifact.id}/download`, '_blank', 'noopener');
  const preview = async artifact => { try { const result = await apiFetch(`/api/jobs/${selected.id}/artifacts/${artifact.id}/preview`); setPreviewText(result.text); setPreviewArtifactId(artifact.id); } catch (error) { setPreviewText(error.message); } };
  const savePreview = async () => { if (!selected || !previewArtifactId) return; setSavingPreview(true); try { await apiFetch(`/api/jobs/${selected.id}/artifacts/${previewArtifactId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: previewText }) }); setError(''); } catch (saveError) { setError(saveError.message); } finally { setSavingPreview(false); } };
  const visibleJobs = selectedProjectId ? jobs.filter(job => job.project_id === selectedProjectId) : jobs;
  return <div className="project-history"><div className="history-heading"><h2>Recent projects</h2><span>{visibleJobs.length} saved jobs</span></div><div className="project-controls"><select value={selectedProjectId} onChange={event => onSelectProject(event.target.value)}><option value="">All projects</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select><form onSubmit={event => { event.preventDefault(); const name = projectName.trim(); if (name) { onCreateProject(name); setProjectName(''); } }}><input value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="New project name" maxLength={255} /><button type="submit" disabled={!projectName.trim()}>Create</button></form></div>{error && <p className="job-error">{error}</p>}{visibleJobs.length === 0 ? <div className="empty-projects">No jobs yet. Start with a media tool.</div> : visibleJobs.map(job => <div className="history-row" key={job.id}><div><strong>{job.operation}</strong><span>{new Date(job.created_at).toLocaleString()}</span></div><em className={job.state === 'completed' ? 'green' : job.state === 'failed' ? 'red' : 'blue'}>{job.state}</em><button onClick={() => openJob(job)} disabled={busy}>{busy ? 'Loading…' : 'Open'}</button></div>)}{selected && <div className="history-detail"><JobStatus job={selected} onDownload={download} onPreview={preview} />{previewArtifactId !== null && <EditableTextArtifact value={previewText} onChange={setPreviewText} onSave={savePreview} saving={savingPreview} />}</div>}</div>;
}

export { AuthView };
