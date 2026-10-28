import { useCallback, useEffect, useMemo, useState } from 'react';

import Assistant, { type AssistantRequest } from './Assistant';
import { fetchServices, fetchSituations } from './api';
import { AppCtx, type OpenAssistantOptions } from './context';
import { useAsync } from './hooks';
import { LangCtx, useT, type Lang } from './i18n';
import { BrandMark } from './icons';
import { KioskApp } from './Kiosk';
import { Home, NotFound, ServicePage, SituationPage } from './pages';
import { useRoute } from './router';
import { stopSpeaking } from './speech';
import { useU } from './ui';

const IDLE_MS = 90_000;
const WARN_MS = 30_000;
const KIOSK = new URLSearchParams(window.location.search).get('kiosk') === '1';

function storedLang(): Lang {
  try {
    return localStorage.getItem('darechy:lang') === 'be' ? 'be' : 'ru';
  } catch {
    return 'ru';
  }
}

function Shell() {
  const { t, lang, setLang } = useT();
  const u = useU();
  const { route, hash } = useRoute();
  const services = useAsync(fetchServices);
  const situations = useAsync(fetchSituations);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<AssistantRequest>(null);
  const [session, setSession] = useState(0);
  const [warn, setWarn] = useState(false);

  const openAssistant = useCallback((options?: OpenAssistantOptions) => {
    setRequest((prev) => ({
      text: options?.text,
      serviceId: options?.serviceId,
      voice: options?.voice,
      nonce: (prev?.nonce ?? 0) + 1,
    }));
    setOpen(true);
  }, []);
  const closeAssistant = useCallback(() => setOpen(false), []);

  useEffect(() => {
    document.documentElement.classList.toggle('kiosk', KIOSK);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    if (route.name === 'home' && route.section && !KIOSK) {
      const target = route.section;
      requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ behavior: 'smooth' }));
    } else {
      window.scrollTo(0, 0);
    }
  }, [hash, route]);

  useEffect(() => {
    if (!KIOSK) return;
    let warnTimer = 0;
    let resetTimer = 0;
    const reset = () => {
      setWarn(false);
      setOpen(false);
      setRequest(null);
      setSession((s) => s + 1);
      stopSpeaking();
      if (window.location.hash && window.location.hash !== '#/') window.location.hash = '#/';
    };
    const bump = () => {
      window.clearTimeout(warnTimer);
      window.clearTimeout(resetTimer);
      setWarn(false);
      warnTimer = window.setTimeout(() => {
        setWarn(true);
        resetTimer = window.setTimeout(reset, WARN_MS);
      }, IDLE_MS);
    };
    const events = ['pointerdown', 'keydown', 'touchstart'];
    events.forEach((name) => window.addEventListener(name, bump));
    bump();
    return () => {
      window.clearTimeout(warnTimer);
      window.clearTimeout(resetTimer);
      events.forEach((name) => window.removeEventListener(name, bump));
    };
  }, []);

  const ctx = useMemo(() => ({ services, situations, openAssistant, kiosk: KIOSK }), [services, situations, openAssistant]);

  return (
    <AppCtx.Provider value={ctx}>
      {KIOSK ? (
        <KioskApp route={route} />
      ) : (
        <>
          <a className='skip' href='#main' onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}>
            {t('skip')}
          </a>
          <header className='hdr'>
            <div className='wrap hdr-row'>
              <a className='brand' href='#/' aria-label='ДаРэчы'>
                <BrandMark size={48} />
                <span className='brand-name'>ДаРэчы</span>
                <span className='brand-tag'>{t('brand_tag')}</span>
              </a>
              <nav className='nav' aria-label='main'>
                <a href='#/situations'>{t('nav_situations')}</a>
                <a href='#/services'>{t('nav_services')}</a>
                <div className='lang' role='group' aria-label='language'>
                  {(['ru', 'be'] as Lang[]).map((code) => (
                    <button key={code} type='button' aria-pressed={lang === code} onClick={() => setLang(code)}>
                      {code === 'ru' ? 'RU' : 'БЕЛ'}
                    </button>
                  ))}
                </div>
                <button className='btn btn-primary nav-ask' type='button' onClick={() => openAssistant()}>
                  {t('nav_ask')}
                </button>
              </nav>
            </div>
          </header>
          <main id='main' tabIndex={-1}>
            {route.name === 'home' && <Home />}
            {route.name === 'situation' && <SituationPage id={route.id} />}
            {route.name === 'service' && <ServicePage id={route.id} />}
            {route.name === 'notfound' && <NotFound />}
          </main>
          <footer className='ftr'>
            <div className='wrap'>
              <p><strong>ДаРэчы</strong> · {t('foot_a')}</p>
              <p className='muted'>{t('foot_b')}</p>
            </div>
          </footer>
        </>
      )}
      <Assistant key={session} open={open} onClose={closeAssistant} request={request} />
      {KIOSK && warn && (
        <div className='k-warn' role='alertdialog' aria-label={u('k_still')}>
          <div className='k-warn-card'>
            <h2>{u('k_still')}</h2>
            <p>{u('k_still_d')}</p>
            <button className='k-btn k-btn-primary' type='button'>{u('k_continue')}</button>
          </div>
        </div>
      )}
    </AppCtx.Provider>
  );
}

export default function App() {
  const [lang, setLangState] = useState<Lang>(storedLang);
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try { localStorage.setItem('darechy:lang', next); } catch { /* ignore */ }
  }, []);
  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return (
    <LangCtx.Provider value={value}>
      <Shell />
    </LangCtx.Provider>
  );
}
