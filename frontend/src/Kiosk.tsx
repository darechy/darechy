import { useContext, useState } from 'react';

import type { Service } from './api';
import { AppCtx } from './context';
import { SITUATIONS, useT, type Lang } from './i18n';
import { BrandMark, Icon, situationIcon } from './icons';
import { Status, useSituationCards } from './pages';
import type { Route } from './router';
import { speak, stopSpeaking } from './speech';
import { useU } from './ui';

const clean = (s: string): string => s.replace(/^[\s\u2757\u203C\u26A0\uFE0F]+/, '');

function KioskHome() {
  const u = useU();
  const { openAssistant, situations } = useContext(AppCtx);
  const cards = useSituationCards().filter((c) => c.active);
  return (
    <>
      <h1 className='k-title'>{u('k_title')}</h1>
      <p className='k-sub'>{u('k_sub')}</p>

      <button className='k-voice' type='button' onClick={() => openAssistant({ voice: true })}>
        <span className='k-voice-ic'>
          <Icon name='mic' size={56} />
        </span>
        <span>
          <strong>{u('k_voice')}</strong>
          <small>{u('k_voice_d')}</small>
        </span>
      </button>

      <h2 className='k-h2'>{u('k_topics')}</h2>
      {situations.kind !== 'ok' ? (
        <Status state={situations} />
      ) : (
        <div className='k-grid'>
          {cards.map((card) => (
            <a key={card.id} className='k-tile' href={`#/situation/${encodeURIComponent(card.id)}`}>
              <span className='k-tile-ic'>
                <Icon name={situationIcon(card.id)} size={44} />
              </span>
              <span>{card.title}</span>
            </a>
          ))}
          <a className='k-tile' href='#/services'>
            <span className='k-tile-ic'>
              <Icon name='list' size={44} />
            </span>
            <span>{u('k_all')}</span>
          </a>
        </div>
      )}
    </>
  );
}

function KioskServices() {
  const { t } = useT();
  const { services } = useContext(AppCtx);
  return (
    <>
      <h1 className='k-title'>{t('svc_title')}</h1>
      <Status state={services} />
      {services.kind === 'ok' && (
        <div className='k-steps'>
          {services.data.map((service) => (
            <a key={service.id} className='k-step' href={`#/service/${encodeURIComponent(service.id)}`}>
              <span className='k-step-txt'>
                <strong>{service.title}</strong>
                <small>{service.summary}</small>
              </span>
              <Icon name='arrow' size={36} />
            </a>
          ))}
        </div>
      )}
    </>
  );
}

function KioskSituation({ id }: { id: string }) {
  const u = useU();
  const { lang } = useT();
  const { services, situations, openAssistant } = useContext(AppCtx);
  if (situations.kind !== 'ok' || services.kind !== 'ok') return <Status state={situations.kind !== 'ok' ? situations : services} />;
  const situation = situations.data.find((s) => s.id === id);
  if (!situation) return <KioskNotFound />;
  const meta = SITUATIONS.find((m) => m.id === id);
  const title = meta ? meta[lang].title : situation.title;
  const steps = situation.service_ids
    .map((sid) => services.data.find((s) => s.id === sid))
    .filter((s): s is Service => s !== undefined);
  return (
    <>
      <h1 className='k-title'>{title}</h1>
      {meta && <p className='k-sub'>{meta[lang].hint}</p>}
      <div className='k-steps'>
        {steps.map((service, index) => (
          <a key={service.id} className='k-step' href={`#/service/${encodeURIComponent(service.id)}`}>
            <span className='k-step-n'>{index + 1}</span>
            <span className='k-step-txt'>
              <strong>{service.title}</strong>
              <small>{service.summary}</small>
            </span>
            <Icon name='arrow' size={36} />
          </a>
        ))}
      </div>
      <button className='k-btn k-btn-primary k-wide' type='button' onClick={() => openAssistant({ voice: true })}>
        <Icon name='mic' size={34} /> {u('k_ask_here')}
      </button>
    </>
  );
}

function KioskService({ id }: { id: string }) {
  const u = useU();
  const { lang } = useT();
  const { services, openAssistant } = useContext(AppCtx);
  const [reading, setReading] = useState(false);
  if (services.kind !== 'ok') return <Status state={services} />;
  const service = services.data.find((s) => s.id === id);
  if (!service) return <KioskNotFound />;

  const toggleRead = () => {
    if (reading) {
      stopSpeaking();
      setReading(false);
      return;
    }
    const text = [
      service.title,
      service.summary,
      `${u('k_who')}: ${clean(service.who)}`,
      `${u('k_amount')}: ${clean(service.amount)}`,
      `${u('k_when')}: ${clean(service.deadline)}`,
      `${u('k_bring')}:`,
      ...service.checklist.map((row, i) => `${i + 1}. ${clean(row.item)}. ${u('k_where')} ${clean(row.where)}`),
    ].join('. ');
    setReading(true);
    speak(text, lang as Lang, () => setReading(false));
  };

  return (
    <>
      <h1 className='k-title'>{service.title}</h1>
      <p className='k-sub'>{service.summary}</p>

      <div className='k-actions-row'>
        <button className='k-btn k-btn-primary' type='button' onClick={toggleRead}>
          <Icon name={reading ? 'stop' : 'speaker'} size={34} /> {reading ? u('k_stop_read') : u('k_read')}
        </button>
        <button className='k-btn' type='button' onClick={() => openAssistant({ serviceId: service.id, voice: true })}>
          <Icon name='mic' size={34} /> {u('k_ask_here')}
        </button>
      </div>

      <div className='k-facts'>
        <div>
          <span>{u('k_who')}</span>
          <strong>{clean(service.who)}</strong>
        </div>
        <div>
          <span>{u('k_amount')}</span>
          <strong>{clean(service.amount)}</strong>
        </div>
        <div>
          <span>{u('k_when')}</span>
          <strong>{clean(service.deadline)}</strong>
        </div>
      </div>

      <h2 className='k-h2'>{u('k_bring')}</h2>
      <ol className='k-list'>
        {service.checklist.map((row, index) => (
          <li key={index}>
            <span className='k-step-n'>{index + 1}</span>
            <span>
              <strong>{clean(row.item)}</strong>
              <small>
                {u('k_where')} {clean(row.where)}
              </small>
            </span>
          </li>
        ))}
      </ol>

      <details className='k-basis'>
        <summary>{u('k_basis')}</summary>
        <ul>
          {service.sources.map((source, index) => (
            <li key={index}>
              {source.title}
              {source.point ? ` – ${source.point}` : ''}
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

function KioskNotFound() {
  const { t } = useT();
  return (
    <>
      <h1 className='k-title'>{t('notfound')}</h1>
      <p className='k-sub'>{t('notfound_d')}</p>
    </>
  );
}

export function KioskApp({ route }: { route: Route }) {
  const { lang, setLang } = useT();
  const u = useU();
  const atHome = route.name === 'home' && route.section !== 'services';
  return (
    <div className='k'>
      <header className='k-bar'>
        <a className='k-brand' href='#/'>
          <BrandMark size={56} />
        </a>
        <div className='k-actions'>
          {!atHome && (
            <button className='k-btn' type='button' onClick={() => window.history.back()}>
              <Icon name='back' size={30} /> {u('k_back')}
            </button>
          )}
          {!atHome && (
            <a className='k-btn k-btn-primary' href='#/'>
              <Icon name='house' size={30} /> {u('k_start')}
            </a>
          )}
          <div className='k-lang' role='group' aria-label='language'>
            {(['ru', 'be'] as Lang[]).map((code) => (
              <button key={code} type='button' aria-pressed={lang === code} onClick={() => setLang(code)}>
                {code === 'ru' ? 'RU' : 'БЕЛ'}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className='k-main'>
        {route.name === 'home' && route.section === 'services' && <KioskServices />}
        {route.name === 'home' && route.section !== 'services' && <KioskHome />}
        {route.name === 'situation' && <KioskSituation id={route.id} />}
        {route.name === 'service' && <KioskService id={route.id} />}
        {route.name === 'notfound' && <KioskNotFound />}
      </main>
    </div>
  );
}
