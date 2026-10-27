import { useContext, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';

import type { Service } from './api';
import { AppCtx } from './context';
import type { AsyncState } from './hooks';
import { EXAMPLES, SITUATIONS, useT } from './i18n';
import { Icon, situationIcon } from './icons';

export function Status({ state }: { state: AsyncState<unknown> }) {
  const { t } = useT();
  if (state.kind === 'loading') return <p className='status'>{t('loading')}</p>;
  if (state.kind === 'error') {
    return (
      <div className='status status-error' role='alert'>
        <strong>{t('api_err')}</strong> ({state.message}). <code>{t('api_hint')}</code>
      </div>
    );
  }
  return null;
}

export function useSituationCards() {
  const { lang } = useT();
  const { situations } = useContext(AppCtx);
  const apiIds = new Set(situations.kind === 'ok' ? situations.data.map((s) => s.id) : []);
  const extra = situations.kind === 'ok' ? situations.data.filter((s) => !SITUATIONS.some((m) => m.id === s.id)) : [];
  return [
    ...SITUATIONS.map((m) => ({ id: m.id, title: m[lang].title, hint: m[lang].hint, active: apiIds.has(m.id) })),
    ...extra.map((s) => ({ id: s.id, title: s.title, hint: '', active: true })),
  ];
}

/* ---------- Главная ---------- */

export function Home() {
  const { t, lang } = useT();
  const { services, openAssistant } = useContext(AppCtx);
  const cards = useSituationCards();
  const [question, setQuestion] = useState('');
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    if (services.kind !== 'ok') return [];
    const q = search.trim().toLowerCase();
    if (!q) return services.data;
    return services.data.filter((s) => `${s.title} ${s.summary} ${s.who}`.toLowerCase().includes(q));
  }, [services, search]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    openAssistant({ text: question });
    setQuestion('');
  };

  return (
    <>
      <section className='hero'>
        <div className='wrap hero-grid'>
          <div>
            <p className='eyebrow'>{t('hero_eyebrow')}</p>
            <h1>
              {t('hero_a')} <span>{t('hero_b')}</span>
            </h1>
            <p className='hero-sub'>{t('hero_sub')}</p>
            <form className='askbox' onSubmit={onSubmit}>
              <label htmlFor='hero-q'>{t('ask_label')}</label>
              <div className='askbox-row'>
                <input id='hero-q' value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={t('ask_ph')} autoComplete='off' />
                <button className='btn btn-primary btn-lg' type='submit'>
                  {t('ask_btn')}
                </button>
              </div>
            </form>
            <ul className='chips'>
              {EXAMPLES[lang].map((example) => (
                <li key={example}>
                  <button className='chip' type='button' onClick={() => openAssistant({ text: example })}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <aside className='principles' aria-label={t('principles')}>
            <h2>{t('principles')}</h2>
            <ul>
              {[1, 2, 3].map((n) => (
                <li key={n}>
                  <span className='check'>
                    <Icon name='check' size={18} />
                  </span>
                  <div>
                    <h3>{t(`p${n}_t`)}</h3>
                    <p>{t(`p${n}_d`)}</p>
                  </div>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      </section>

      <section className='wrap section' id='situations'>
        <div className='sec-head'>
          <h2>{t('sit_title')}</h2>
          <p>{t('sit_sub')}</p>
        </div>
        <div className='tiles'>
          {cards.map((card) => {
            const body = (
              <>
                <span className='tile-ic'>
                  <Icon name={situationIcon(card.id)} size={30} />
                </span>
                <span className='tile-txt'>
                  <span className='tile-title'>{card.title}</span>
                  {card.hint && <span className='tile-hint'>{card.hint}</span>}
                </span>
                {card.active ? (
                  <span className='tile-go'>
                    <Icon name='arrow' size={22} />
                  </span>
                ) : (
                  <span className='tile-soon'>{t('soon')}</span>
                )}
              </>
            );
            return card.active ? (
              <a key={card.id} className='tile' href={`#/situation/${encodeURIComponent(card.id)}`}>
                {body}
              </a>
            ) : (
              <div key={card.id} className='tile tile-off' aria-disabled='true'>
                {body}
              </div>
            );
          })}
        </div>
      </section>

      <section className='wrap section' id='services'>
        <div className='sec-head'>
          <h2>{t('svc_title')}</h2>
          <p>{t('svc_sub')}</p>
        </div>
        <Status state={services} />
        {services.kind === 'ok' && (
          <>
            <div className='search'>
              <input type='search' value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('search_ph')} aria-label={t('search_ph')} />
              <span className='muted'>
                {t('found')}: {filtered.length}
              </span>
            </div>
            {filtered.length === 0 ? (
              <p className='status'>{t('nothing')}</p>
            ) : (
              <div className='svc-list'>
                {filtered.map((service) => (
                  <a key={service.id} className='svc' href={`#/service/${encodeURIComponent(service.id)}`}>
                    <h3>{service.title}</h3>
                    <p>{service.summary}</p>
                    <dl>
                      <div>
                        <dt>{t('amount')}</dt>
                        <dd>{service.amount}</dd>
                      </div>
                      <div>
                        <dt>{t('deadline')}</dt>
                        <dd>{service.deadline}</dd>
                      </div>
                    </dl>
                  </a>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section className='wrap section'>
        <div className='sec-head'>
          <h2>{t('ch_title')}</h2>
        </div>
        <div className='channels'>
          {['portal', 'bot', 'kiosk'].map((key) => (
            <div className='channel' key={key}>
              <h3>{t(`ch_${key}`)}</h3>
              <p>{t(`ch_${key}_d`)}</p>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

/* ---------- Страница услуги ---------- */

function useChecked(id: string) {
  const key = `darechy:check:${id}`;
  const read = (): number[] => {
    try {
      return JSON.parse(localStorage.getItem(key) ?? '[]') as number[];
    } catch {
      return [];
    }
  };
  const [done, setDone] = useState<number[]>(read);
  useEffect(() => {
    setDone(read());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const toggle = (index: number) =>
    setDone((prev) => {
      const next = prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index];
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // приватный режим — просто не сохраняем
      }
      return next;
    });
  return { done, toggle };
}

export function NotFound() {
  const { t } = useT();
  return (
    <section className='wrap section'>
      <h1 className='page-title'>{t('notfound')}</h1>
      <p className='muted'>{t('notfound_d')}</p>
      <p>
        <a className='btn btn-outline' href='#/'>
          {t('home')}
        </a>
      </p>
    </section>
  );
}

function ServiceView({ service }: { service: Service }) {
  const { t, lang } = useT();
  const { situations, openAssistant } = useContext(AppCtx);
  const { done, toggle } = useChecked(service.id);
  const total = service.checklist.length;
  const count = done.filter((i) => i < total).length;
  const percent = total ? Math.round((count / total) * 100) : 0;
  const parent = situations.kind === 'ok' ? situations.data.find((s) => s.service_ids.includes(service.id)) : undefined;
  const parentMeta = parent ? SITUATIONS.find((m) => m.id === parent.id) : undefined;

  return (
    <article className='wrap section page'>
      <nav className='crumbs' aria-label='breadcrumbs'>
        <a href='#/'>{t('home')}</a>
        {parent && (
          <>
            <span>/</span>
            <a href={`#/situation/${encodeURIComponent(parent.id)}`}>{parentMeta ? parentMeta[lang].title : parent.title}</a>
          </>
        )}
      </nav>

      <header className='page-head'>
        <h1 className='page-title'>{service.title}</h1>
        <p className='lead'>{service.summary}</p>
      </header>

      <dl className='facts'>
        <div>
          <dt>{t('who')}</dt>
          <dd>{service.who}</dd>
        </div>
        <div>
          <dt>{t('amount')}</dt>
          <dd>{service.amount}</dd>
        </div>
        <div>
          <dt>{t('deadline')}</dt>
          <dd>{service.deadline}</dd>
        </div>
      </dl>

      <div className='cols'>
        <section aria-labelledby='docs-h'>
          <div className='docs-head'>
            <h2 id='docs-h'>{t('docs')}</h2>
            <span className='muted'>
              {count}/{total} {t('collected')}
            </span>
          </div>
          <div className='progress' role='progressbar' aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <ul className='checklist'>
            {service.checklist.map((row, index) => {
              const checked = done.includes(index);
              return (
                <li key={index} className={checked ? 'is-done' : ''}>
                  <label>
                    <input type='checkbox' checked={checked} onChange={() => toggle(index)} />
                    <span className='box' aria-hidden='true' />
                    <span>
                      <strong>{row.item}</strong>
                      <small>{row.where}</small>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>

          <h2>{t('sources')}</h2>
          <ul className='sources'>
            {service.sources.map((source, index) => (
              <li key={index}>
                {source.url ? (
                  <a href={source.url} target='_blank' rel='noreferrer'>
                    {source.title} ↗
                  </a>
                ) : (
                  <span>{source.title}</span>
                )}
                {source.point && <small>{source.point}</small>}
              </li>
            ))}
          </ul>
        </section>

        <aside className='aside'>
          <button className='btn btn-primary btn-lg' type='button' onClick={() => openAssistant({ serviceId: service.id })}>
            {t('ask_service')}
          </button>
          <button className='btn btn-outline' type='button' onClick={() => window.print()}>
            {t('print')}
          </button>
        </aside>
      </div>
    </article>
  );
}

export function ServicePage({ id }: { id: string }) {
  const { services } = useContext(AppCtx);
  if (services.kind !== 'ok') {
    return (
      <section className='wrap section'>
        <Status state={services} />
      </section>
    );
  }
  const service = services.data.find((s) => s.id === id);
  return service ? <ServiceView service={service} /> : <NotFound />;
}

/* ---------- Страница ситуации ---------- */

export function SituationPage({ id }: { id: string }) {
  const { t, lang } = useT();
  const { services, situations, openAssistant } = useContext(AppCtx);

  if (situations.kind !== 'ok' || services.kind !== 'ok') {
    return (
      <section className='wrap section'>
        <Status state={situations.kind !== 'ok' ? situations : services} />
      </section>
    );
  }
  const situation = situations.data.find((s) => s.id === id);
  if (!situation) return <NotFound />;

  const meta = SITUATIONS.find((m) => m.id === id);
  const title = meta ? meta[lang].title : situation.title;
  const steps = situation.service_ids
    .map((sid) => services.data.find((s) => s.id === sid))
    .filter((s): s is Service => s !== undefined);

  return (
    <article className='wrap section page'>
      <nav className='crumbs' aria-label='breadcrumbs'>
        <a href='#/'>{t('home')}</a>
      </nav>
      <header className='page-head'>
        <p className='eyebrow'>{t('route')}</p>
        <h1 className='page-title'>{title}</h1>
        {meta && <p className='lead'>{meta[lang].hint}</p>}
        <button className='btn btn-primary btn-lg' type='button' onClick={() => openAssistant({ text: title })}>
          {t('nav_ask')}
        </button>
      </header>

      <ol className='route'>
        {steps.map((service, index) => (
          <li key={service.id}>
            <span className='route-n'>{index + 1}</span>
            <div className='route-card'>
              <h2>{service.title}</h2>
              <p>{service.summary}</p>
              <p className='route-meta'>
                <span>
                  {t('amount')}: {service.amount}
                </span>
                <span>
                  {t('deadline')}: {service.deadline}
                </span>
              </p>
              <a href={`#/service/${encodeURIComponent(service.id)}`}>{t('open_service')} →</a>
            </div>
          </li>
        ))}
      </ol>
    </article>
  );
}
