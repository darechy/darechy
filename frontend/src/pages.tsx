import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import type { Service } from './api';
import { AppCtx } from './context';
import type { AsyncState } from './hooks';
import { EXAMPLES, SITUATIONS, useT } from './i18n';
import { Icon, situationIcon } from './icons';
import { InlineChat, useInlineChat, useIsPhone } from './InlineChat';
import { useU } from './ui';

const MAX_SLIDES = 15;

// В данных бывают заглушки с «❗»: убираем знак, текст оставляем.
const clean = (s: string): string => s.replace(/^[\s\u2757\u203C\u26A0\uFE0F]+/, '');

// Полоса под шапкой: большая круглая кнопка «Главная» и путь до текущей страницы.
function BackBar({ trail }: { trail?: { href?: string; label: string } }) {
  const { t } = useT();
  return (
    <div className='backbar'>
      <div className='wrap backbar-row'>
        <a className='backbar-link' href='#/'>
          <span className='backbar-ic'>
            <Icon name='back' size={20} />
          </span>
          <span>{t('home')}</span>
        </a>
        {trail &&
          (trail.href ? (
            <a className='backbar-trail' href={trail.href}>
              {trail.label}
            </a>
          ) : (
            <span className='backbar-trail'>{trail.label}</span>
          ))}
      </div>
    </div>
  );
}

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

function ServiceCard({ service }: { service: Service }) {
  const { t } = useT();
  return (
    <a className='svc' href={`#/service/${encodeURIComponent(service.id)}`}>
      <h3>{service.title}</h3>
      <p>{service.summary}</p>
      <dl>
        <div>
          <dt>{t('amount')}</dt>
          <dd>{clean(service.amount)}</dd>
        </div>
        <div>
          <dt>{t('deadline')}</dt>
          <dd>{clean(service.deadline)}</dd>
        </div>
      </dl>
    </a>
  );
}

/* ---------- Главная ---------- */

export function Home() {
  const { t, lang } = useT();
  const u = useU();
  const { services, openAssistant } = useContext(AppCtx);
  const cards = useSituationCards();
  const [question, setQuestion] = useState('');
  const [search, setSearch] = useState('');
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [edge, setEdge] = useState({ start: true, end: true });
  const phone = useIsPhone();
  const chat = useInlineChat();

  const query = search.trim().toLowerCase();

  const filtered = useMemo(() => {
    if (services.kind !== 'ok' || !query) return [];
    return services.data.filter((s) => `${s.title} ${s.summary} ${s.who}`.toLowerCase().includes(query));
  }, [services, query]);

  // Слайдер: самые новые первыми (последняя добавленная услуга в каталоге), не больше 15.
  const recent = useMemo(() => (services.kind === 'ok' ? [...services.data].reverse().slice(0, MAX_SLIDES) : []), [services]);

  const updateEdge = () => {
    const el = trackRef.current;
    if (!el) return;
    setEdge({ start: el.scrollLeft <= 2, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2 });
  };

  useEffect(() => {
    updateEdge();
    window.addEventListener('resize', updateEdge);
    return () => window.removeEventListener('resize', updateEdge);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recent.length, query]);

  const slide = (dir: 1 | -1) => {
    const el = trackRef.current;
    if (el) el.scrollBy({ left: dir * Math.max(340, el.clientWidth * 0.8), behavior: 'smooth' });
  };

  // На телефоне ответ появляется над полем ввода, на остальных экранах – боковая панель.
  const ask = (text: string) => {
    if (phone) void chat.send(text);
    else openAssistant({ text });
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) return;
    ask(question);
    setQuestion('');
  };

  const showSlider = !query && recent.length > 0;
  const needArrows = showSlider && !(edge.start && edge.end);

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
              {phone && <InlineChat chat={chat} />}
              <label htmlFor='hero-q'>{t('ask_label')}</label>
              <div className='askbox-row'>
                <input id='hero-q' value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={t('ask_ph')} autoComplete='off' />
                <button className='btn btn-primary btn-lg' type='submit' disabled={phone && chat.busy}>
                  {t('ask_btn')}
                </button>
              </div>
            </form>
            <ul className='chips'>
              {EXAMPLES[lang].map((example) => (
                <li key={example}>
                  <button className='chip' type='button' disabled={phone && chat.busy} onClick={() => ask(example)}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <aside className='principles' aria-label={t('principles')}>
            <h2>{t('principles')}</h2>
            <ul>
              {[1, 2, 3, 4, 5].map((n) => (
                <li key={n}>
                  <span className='check'>
                    <Icon name='check' size={18} />
                  </span>
                  <div>
                    <h3>{n > 3 ? u(`p${n}_t`) : t(`p${n}_t`)}</h3>
                    <p>{n > 3 ? u(`p${n}_d`) : t(`p${n}_d`)}</p>
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
              {query && (
                <span className='muted'>
                  {t('found')}: {filtered.length}
                </span>
              )}
              {needArrows && (
                <div className='slider-ctl'>
                  <button className='slider-btn' type='button' onClick={() => slide(-1)} disabled={edge.start} aria-label={u('sl_prev')}>
                    <Icon name='back' size={20} />
                  </button>
                  <button className='slider-btn' type='button' onClick={() => slide(1)} disabled={edge.end} aria-label={u('sl_next')}>
                    <Icon name='arrow' size={20} />
                  </button>
                </div>
              )}
            </div>

            {query ? (
              filtered.length === 0 ? (
                <p className='status'>{t('nothing')}</p>
              ) : (
                <div className='svc-list'>
                  {filtered.map((service) => (
                    <ServiceCard key={service.id} service={service} />
                  ))}
                </div>
              )
            ) : (
              <div className='slider-track' ref={trackRef} onScroll={updateEdge}>
                {recent.map((service) => (
                  <ServiceCard key={service.id} service={service} />
                ))}
              </div>
            )}
          </>
        )}
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
        // приватный режим: просто не сохраняем
      }
      return next;
    });
  return { done, toggle };
}

export function NotFound() {
  const { t } = useT();
  return (
    <>
      <BackBar />
      <section className='wrap section'>
        <h1 className='page-title'>{t('notfound')}</h1>
        <p className='muted'>{t('notfound_d')}</p>
      </section>
    </>
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
    <>
      <BackBar
        trail={parent ? { href: `#/situation/${encodeURIComponent(parent.id)}`, label: parentMeta ? parentMeta[lang].title : parent.title } : undefined}
      />
      <article className='wrap section page'>
        <header className='page-head'>
          <h1 className='page-title'>{service.title}</h1>
          <p className='lead'>{service.summary}</p>
          <div className='page-actions'>
            <button className='btn btn-primary' type='button' onClick={() => openAssistant({ serviceId: service.id })}>
              {t('ask_service')}
            </button>
            <button className='btn btn-outline' type='button' onClick={() => window.print()}>
              {t('print')}
            </button>
          </div>
        </header>

        <dl className='facts'>
          <div>
            <dt>{t('who')}</dt>
            <dd>{clean(service.who)}</dd>
          </div>
          <div>
            <dt>{t('amount')}</dt>
            <dd>{clean(service.amount)}</dd>
          </div>
          <div>
            <dt>{t('deadline')}</dt>
            <dd>{clean(service.deadline)}</dd>
          </div>
        </dl>

        <section className='page-body' aria-labelledby='docs-h'>
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
                      <strong>{clean(row.item)}</strong>
                      <small>{clean(row.where)}</small>
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
                <span className='src-ic'>
                  <Icon name='doc' size={20} />
                </span>
                <div>
                  {source.url ? (
                    <a href={source.url} target='_blank' rel='noreferrer'>
                      {clean(source.title)} ↗
                    </a>
                  ) : (
                    <span className='src-title'>{clean(source.title)}</span>
                  )}
                  {source.point && <small>{clean(source.point)}</small>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </article>
    </>
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
  const u = useU();
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
    <>
      <BackBar trail={{ label: title }} />
      <article className='wrap section page'>
        <header className='page-head'>
          <p className='eyebrow'>{t('route')}</p>
          <h1 className='page-title'>{title}</h1>
          {meta && <p className='lead'>{meta[lang].hint}</p>}
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
                    {t('amount')}: {clean(service.amount)}
                  </span>
                  <span>
                    {t('deadline')}: {clean(service.deadline)}
                  </span>
                </p>
                <a href={`#/service/${encodeURIComponent(service.id)}`}>{t('open_service')} →</a>
              </div>
            </li>
          ))}
          <li className='route-end'>
            <span className='route-n'>?</span>
            <div className='route-ask'>
              <div>
                <h3>{u('s_help_t')}</h3>
                <p>{u('s_help_d')}</p>
              </div>
              <button className='btn btn-primary' type='button' onClick={() => openAssistant({ text: title })}>
                {t('nav_ask')}
              </button>
            </div>
          </li>
        </ol>
      </article>
    </>
  );
}
