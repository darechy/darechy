import { flushSync } from 'react-dom';
import { useCallback, useEffect, useMemo, useState } from 'react';

import Assistant, { type AssistantRequest } from './Assistant';
import { fetchServices, fetchSituations, type Service } from './api';
import { localizeService } from './catalog_be';
import { AppCtx, type OpenAssistantOptions } from './context';
import { useAsync, type AsyncState } from './hooks';
import { LangCtx, useT, type Lang } from './i18n';
import { BrandMark } from './icons';
import { KioskApp } from './Kiosk';
import { Home, NotFound, ServicePage, SituationPage } from './pages';
import { useRoute } from './router';
import { stopSpeaking } from './speech';
import { useU } from './ui';

const IDLE_MS = 90_000;
const WARN_MS = 30_000;
const PARAMS = new URLSearchParams(window.location.search);
const KIOSK = PARAMS.get('kiosk') === '1';
// Тест слайдера: открыть сайт с ?stub=1 – к каталогу подмешивается четвёртая услуга. Без параметра её нет.
const STUB_ON = PARAMS.get('stub') === '1';

const STUB_SERVICE: Service = {
  id: 'stub-demo',
  title: 'Тестовая услуга (заглушка)',
  situation: 'demo',
  summary: 'Демо-карточка для проверки слайдера на главной. В каталоге бэкенда её нет, появляется только при ?stub=1.',
  who: 'Только для проверки интерфейса.',
  amount: 'Без выплаты',
  deadline: 'Не применяется',
  checklist: [
    { item: 'Первый документ для проверки галочки', where: 'у вас' },
    { item: 'Второй документ', where: 'исполком' },
  ],
  sources: [{ title: 'Тестовый источник для проверки верстки', url: 'https://pravo.by', point: 'пункт 1 демо-акта' }],
};

// Убираем ведущие «❗» из данных бэкенда: эмодзи в интерфейсе не используем.
const strip = (s: string): string => s.replace(/^[\s\u2757\u203C\u26A0\uFE0F]+/, '');

const cleanService = (s: Service): Service => ({
  ...s,
  title: strip(s.title),
  summary: strip(s.summary),
  who: strip(s.who),
  amount: strip(s.amount),
  deadline: strip(s.deadline),
  checklist: s.checklist.map((row) => ({ item: strip(row.item), where: strip(row.where) })),
  sources: s.sources.map((src) => ({ ...src, title: strip(src.title), point: src.point ? strip(src.point) : src.point })),
});

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
  const rawServices = useAsync(fetchServices);
  const situations = useAsync(fetchSituations);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<AssistantRequest>(null);
  const [session, setSession] = useState(0);
  const [warn, setWarn] = useState(false);

  const services = useMemo<AsyncState<Service[]>>(() => {
    if (rawServices.kind !== 'ok') return rawServices;
    const base = STUB_ON ? [...rawServices.data, STUB_SERVICE] : rawServices.data;
    return { kind: 'ok', data: base.map((s) => cleanService(localizeService(s, lang))) };
  }, [rawServices, lang]);

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

type ViewTransitionDoc = { startViewTransition?: (callback: () => void) => unknown };

export default function App() {
  const [lang, setLangState] = useState<Lang>(storedLang);

  // Смена языка: старая версия остаётся на экране, новая проявляется «шторкой» (lang.css).
  const setLang = useCallback(
    (next: Lang) => {
      if (next === lang) return;
      const apply = () => {
        flushSync(() => setLangState(next));
        try {
          localStorage.setItem('darechy:lang', next);
        } catch {
          /* ignore */
        }
      };
      const doc = document as unknown as ViewTransitionDoc;
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (doc.startViewTransition && !reduced) {
        doc.startViewTransition(apply);
        return;
      }
      apply();
      if (!reduced) {
        const root = document.documentElement;
        root.classList.add('lang-soft');
        window.setTimeout(() => root.classList.remove('lang-soft'), 600);
      }
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return (
    <LangCtx.Provider value={value}>
      <Shell />
    </LangCtx.Provider>
  );
}
