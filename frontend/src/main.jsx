import React, { useState } from 'react';
import { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { AuthView, RealAppShell } from './real-shell';
import { apiFetch } from './api';

const languages = ['Spanish', 'French', 'German', 'Portuguese', 'Japanese', 'Turkish'];
const toolItems = [
  ['video', 'Video Translator'],
  ['voice', 'Voice Studio'],
  ['stems', 'Stem Splitter'],
  ['noise', 'Noise Remover'],
  ['transcript', 'Transcription'],
];

function WaveMark({ compact = false }) {
  return <span className={`wave-mark ${compact ? 'compact' : ''}`} aria-hidden="true"><i /><i /><i /><i /><i /></span>;
}

function Icon({ name, size = 18 }) {
  const paths = {
    home: <><path d="m3 9 6-5 6 5" /><path d="M5 8.5V15h8V8.5" /><path d="M8 15v-4h2v4" /></>,
    video: <><rect x="2.5" y="4" width="10" height="9" rx="2" /><path d="m12.5 7 3-2v7l-3-2" /><path d="m6 7 3 1.8L6 10.5z" /></>,
    voice: <><rect x="6" y="2.5" width="4" height="8" rx="2" /><path d="M3.5 8.5a4.5 4.5 0 0 0 9 0M8 13v3M5.5 16h5" /></>,
    stems: <><path d="M3 8v4M6 5v10M9 3v14M12 6v8M15 8v4" /></>,
    noise: <><path d="M2.5 9.5h2l1.5-4 2.5 8 2-5 1.5 3h3" /><path d="M13 3.5 15 5.5 13 7.5" /></>,
    transcript: <><path d="M4 2.5h7l3 3v9H4z" /><path d="M11 2.5v3h3M6.5 9h5M6.5 12h5" /></>,
    folder: <><path d="M2.5 5.5h5l1.5 1.5h6v7.5h-12z" /><path d="M2.5 7h12" /></>,
    card: <><rect x="2.5" y="4" width="13" height="10" rx="1.5" /><path d="M2.5 7.5h13M5 11h3" /></>,
    settings: <><path d="M8 2.5v2M8 11.5v2M2.5 8h2M11.5 8h2M4.1 4.1l1.4 1.4M10.5 10.5l1.4 1.4M11.9 4.1l-1.4 1.4M5.5 10.5l-1.4 1.4" /><circle cx="8" cy="8" r="2.5" /></>,
    arrow: <><path d="M3 8h10" /><path d="m9 4 4 4-4 4" /></>,
    upload: <><path d="M8 11V3M5 6l3-3 3 3" /><path d="M3 10.5v2.5h10v-2.5" /></>,
    play: <path d="m6 4 7 4-7 4z" fill="currentColor" stroke="none" />,
    check: <path d="m4 8 2.5 2.5L12.5 4" />,
    sparkle: <><path d="m8 2 1.2 3.8L13 7l-3.8 1.2L8 12l-1.2-3.8L3 7l3.8-1.2z" /><path d="m13.5 11.5.5 1.5 1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5z" /></>,
    download: <><path d="M8 2.5v8M5 8l3 3 3-3M3 13.5h10" /></>,
    close: <><path d="m4 4 8 8M12 4l-8 8" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">{paths[name] || paths.sparkle}</svg>;
}

function Button({ children, variant = 'primary', icon, onClick, type = 'button', className = '' }) {
  return <button type={type} className={`button ${variant} ${className}`} onClick={onClick}>{children}{icon && <Icon name={icon} size={16} />}</button>;
}

function PublicSite({ onStart }) {
  const [openFaq, setOpenFaq] = useState(null);
  const faqItems = [
    ['How does voice preservation work?', 'LingoWave keeps the original performance context while the selected voice profile generates the translated speech.'],
    ['Which video formats are supported?', 'MP4, MOV, WEBM, and common audio formats are supported, with server-side media inspection before processing.'],
    ['How accurate are the translations?', 'The default flow uses fast translation first, then applies timing-aware refinement when the spoken result needs shortening.'],
    ['Is my content safe and private?', 'Uploaded media, generated artifacts, and consented voice references stay tied to your account and can be removed from the workspace.'],
  ];
  return <div className="public-site">
    <nav className="public-nav page-width">
      <button className="brand-button" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}><WaveMark /><span>Lingo<span>Wave</span></span></button>
      <div className="nav-links"><a href="#product">Product</a><a href="#tools">Tools</a><a href="#pricing">Pricing</a><a href="#safety">Safety</a></div>
      <div className="nav-actions"><button className="link-button" onClick={onStart}>Log in</button><Button variant="dark" onClick={onStart}>Start creating</Button></div>
    </nav>
    <main>
      <section className="hero page-width" id="product">
        <div className="hero-copy"><h1>Your voice,<br />everywhere.</h1><p>Translate videos, keep the emotion.</p><div className="hero-actions"><Button onClick={onStart} icon="arrow">Translate a video</Button><button className="text-action" onClick={() => document.querySelector('#tools')?.scrollIntoView({ behavior: 'smooth' })}>Explore the tools <Icon name="arrow" size={18} /></button></div></div>
        <HeroPreview />
        <div className="wave-ribbons" aria-hidden="true"><span /><span /><span /><span /></div>
      </section>
      <section className="tool-strip page-width" id="tools">{[['video','Video Translator','Translate and dub videos while preserving your voice, tone, and background sound.'],['voice','Voice Studio','Clone, enhance, and guide your voice for consistent results across languages.'],['stems','Stem Splitter','Separate voice, music, and effects into clean stems for total control.'],['noise','Noise Remover','Remove background noise and room tone for crisp, studio-quality audio.']].map(([icon,title,copy]) => <div className="tool-item" key={title}><div className={`tool-icon ${icon}`}><Icon name={icon} size={25} /></div><div><h3>{title}</h3><p>{copy}</p></div></div>)}</section>
      <section className="preserve-section page-width"><div><h2>Keep the soul<br />of your sound.</h2><p>LingoWave translates and dubs your videos while preserving the performance, voice, and background sound that make your content yours.</p><div className="check-list"><span><Icon name="check" size={16} />Preserve the original voice and emotion</span><span><Icon name="check" size={16} />Keep music, ambience, and effects intact</span><span><Icon name="check" size={16} />Keep translated timing aligned to the source</span></div></div><div className="sound-art"><div className="sound-wave" /><div className="sound-bars">{Array.from({ length: 18 }, (_, i) => <i key={i} style={{ height: `${22 + ((i * 17) % 54)}%` }} />)}</div></div></section>
      <section className="pricing-section page-width" id="pricing"><div className="pricing-copy"><h2>Simple, transparent pricing</h2><p>Choose the plan that fits your workflow.</p><div className="plan-row">{[['Creator','$19'],['Pro','$49'],['Studio','$129']].map(([name,price], i) => <div className={`plan ${i === 1 ? 'featured' : ''}`} key={name}>{i === 1 && <span className="popular">Most popular</span>}<h3>{name}</h3><strong>{price}<small>/mo</small></strong><p>Billed monthly</p><button onClick={onStart}>Get started</button></div>)}</div></div><div className="faq" id="safety"><h2>FAQ</h2>{faqItems.map(([question, answer], index) => <div className="faq-item" key={question}><button className="faq-row" aria-expanded={openFaq === 'all' || openFaq === index} onClick={() => setOpenFaq(openFaq === index ? null : index)}>{question}<span>{openFaq === 'all' || openFaq === index ? '⌃' : '⌄'}</span></button>{(openFaq === 'all' || openFaq === index) && <p className="faq-answer">{answer}</p>}</div>)}<button className="text-action" onClick={() => setOpenFaq(openFaq === 'all' ? null : 'all')}>{openFaq === 'all' ? 'Collapse answers' : 'View all answers'} <Icon name="arrow" size={17} /></button></div></section>
    </main>
  </div>;
}

function HeroPreview() {
  return <div className="hero-preview"><div className="preview-top"><span>Project</span><b>Marketing Launch Video</b><span>⌄</span><span className="preview-spacer" /><span>↶</span><span>↷</span><span className="preview-export"><Icon name="upload" size={14} />Export⌄</span></div><div className="preview-main"><div className="video-frame"><div className="video-scene"><div className="person" /><div className="mic" /></div><div className="video-controls"><Icon name="play" size={16} /><span>00:12.45 / 01:34.20</span><span className="speed">1.0x</span></div></div><div className="translate-card"><label>Translate to</label><div className="select-like">◎ Spanish (Español) <span>⌄</span></div><label>Preserve</label><div className="preserve-pills"><span><WaveMark compact /> Voice</span><span><WaveMark compact /> Background sound</span></div></div></div><div className="timeline">{['Original','Voice','Music','Effects'].map((label, row) => <div className="track" key={label}><span>{label}</span><div className={`track-wave row-${row}`} /></div>)}</div><div className="translated-output"><div className="output-label"><span>ES</span> Translated output</div><div className="output-scene"><div className="person" /></div><strong>Llevamos tu mensaje<br />más lejos, sin perder tu voz.</strong><div className="output-wave" /></div></div>;
}

function Toggle({ checked, onChange, label, premium = false }) { return <label className="toggle-row"><span>{label}{premium && <em>Premium</em>}</span><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} /><i /></label>; }

function App() {
  const [view, setView] = useState(() => new URLSearchParams(window.location.search).has('auth_error') ? 'auth' : 'public');
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    apiFetch('/api/auth/me')
      .then(result => { setUser(result.user); setView('app'); })
      .catch(() => {})
      .finally(() => setCheckingSession(false));
  }, []);

  if (checkingSession) return <div className="auth-page"><div className="auth-card"><button className="brand-button">Lingo<span>Wave</span></button><p>Restoring your workspace…</p></div></div>;
  return view === 'public' ? <PublicSite onStart={() => setView('auth')} /> : view === 'auth' ? <AuthView onBack={() => setView('public')} onAuthenticated={result => { setUser(result.user); setView('app'); }} /> : <RealAppShell initialUser={user} onExit={() => { setUser(null); setView('public'); }} />;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
