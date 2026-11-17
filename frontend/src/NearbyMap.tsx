/* Блок «Госуслуги рядом» на главной: Яндекс Карты (JS API 2.1) и геолокация.
   Ключи: frontend/.env.local (см. frontend/.env.example).
   VITE_YANDEX_MAPS_KEY — ключ JavaScript API, рисует карту.
   VITE_YANDEX_SEARCH_KEY — ключ API Поиска по организациям (HTTP API, type=biz). Необязателен.
   Если поиск Яндекса недоступен (нет ключа, 403, сбой сети), учреждения берутся из OpenStreetMap (Overpass API).
   Показываем только государственные учреждения: поиск идёт по фиксированным запросам,
   а найденное дополнительно фильтруется по названию и рубрикам организации. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { Lang } from './i18n';
import './nearby.css';

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    ymaps?: any;
  }
}

type Coords = [number, number];
type Category = { id: string; ru: string; be: string; query: string; match: RegExp };
type Place = {
  id: string;
  cats: string[];
  name: string;
  address: string;
  coords: Coords;
  dist: number;
  hours?: string;
  phone?: string;
};
type Origin = { coords: Coords; real: boolean };
type Status = 'idle' | 'locating' | 'searching' | 'ready' | 'error';
type Source = 'yandex' | 'osm';

const KEY = import.meta.env.VITE_YANDEX_MAPS_KEY as string | undefined;
const SEARCH_KEY = (import.meta.env.VITE_YANDEX_SEARCH_KEY as string | undefined) || KEY;
const MINSK: Coords = [53.9006, 27.559];
const MAX_SHOWN = 8;
const MAX_DIST = 25000;
const OSM_RADIUS = 10000;

let yandexDown = false;

const ALL = { id: 'all', ru: 'Все', be: 'Усе' };
const CATEGORIES: Category[] = [
  { id: 'zhes', ru: 'ЖЭС', be: 'ЖЭС', query: 'ЖЭС', match: /жэс|жилищн|жрэо|жрэу|эксплуатац|жыллёв|жыллев/i },
  { id: 'passport', ru: 'Паспортный стол', be: 'Пашпартны стол', query: 'паспортный стол', match: /паспорт|пашпарт|миграц|міграц|гражданств|грамадзянств/i },
  { id: 'military', ru: 'Военкомат', be: 'Ваенкамат', query: 'военкомат', match: /военн|ваенн|комиссариат|камісарыят|военкомат|ваенкамат/i },
  { id: 'exec', ru: 'Исполком', be: 'Выканкам', query: 'исполком', match: /исполком|выканкам|исполнительн|выканаўч|администрац|адміністрацы/i },
  { id: 'zags', ru: 'ЗАГС', be: 'ЗАГС', query: 'ЗАГС', match: /загс|регистрации актов|рэгістрацыі актаў/i },
  { id: 'tax', ru: 'Налоговая', be: 'Падатковая', query: 'налоговая инспекция', match: /налог|падатк|инспекц|інспекцы/i },
  { id: 'social', ru: 'Соцзащита', be: 'Сацзахада', query: 'управление социальной защиты', match: /социальн|сацыяльн|занятост|занятасц|труд|працы|пенсион|фсзн/i },
];

const GOV_NAME =
  /жэс|жрэо|жрэу|жыллёв|жыллев|военкомат|ваенкамат|(военн|ваенн)\S*\s+(комиссариат|камісарыят)|исполком|выканкам|(исполнительн|выканаўч)\S*\s+(комитет|камітэт)|загс|паспортн|пашпартн|налогов\S*\s+инспекц|падатков\S*\s+інспекц|(социальн|сацыяльн)\S*\s+(защит|абарон)|занятост|занятасц/i;
const OSM_NAME =
  'ЖЭС|ЖРЭО|ЖРЭУ|жилищно|жыллёв|паспортн|пашпартн|военн|ваенн|комиссариат|камісарыят|военкомат|исполком|исполнительн|выканкам|выканаўч|ЗАГС|налогов|падатков|социальн|сацыяльн|занятост|занятасц';

const T = {
  ru: {
    title: 'Госуслуги рядом',
    sub: 'Ближайшие ЖЭС, паспортные столы, военкоматы и другие государственные учреждения. Других заведений здесь нет.',
    filter: 'Тип учреждения',
    locate: 'Обновить местоположение',
    locating: 'Определяем ваше местоположение…',
    searching: 'Ищем учреждения рядом…',
    denied: 'Геолокация недоступна, показываем окрестности центра Минска.',
    empty: 'Рядом ничего не нашлось. Попробуйте другой тип учреждения.',
    error: 'Не удалось получить список учреждений. Попробуйте позже.',
    osmNote: 'Список собран из открытых данных OpenStreetMap: часть учреждений могла не попасть в неё.',
    nokey: 'Карта подключается ключом Яндекс Карт: добавьте VITE_YANDEX_MAPS_KEY в frontend/.env.local и перезапустите сайт.',
    route: 'Маршрут',
    you: 'Вы здесь',
    mapLabel: 'Карта государственных учреждений',
    credit: 'Данные Яндекс Карт. Часы работы и адрес лучше уточнить перед визитом.',
    creditOsm: 'Данные © участники OpenStreetMap. Часы работы и адрес лучше уточнить перед визитом.',
  },
  be: {
    title: 'Дзяржаўныя ўстановы побач',
    sub: 'Бліжэйшыя ЖЭС, пашпартныя сталы, ваенкаматы і іншыя дзяржаўныя ўстановы. Іншых месцаў тут няма.',
    filter: 'Тып установы',
    locate: 'Абнавіць месцазнаходжанне',
    locating: 'Вызначаем ваша месцазнаходжанне…',
    searching: 'Шукаем ўстановы побач…',
    denied: 'Геалакацыя недаступная, паказваем наваколле цэнтра Мінска.',
    empty: 'Побач нічога не знайшлося. Паспрабуйце іншы тып установы.',
    error: 'Не ўдалося атрымаць спіс ўстаноў. Паспрабуйце пазней.',
    osmNote: 'Спіс зобраны з адкрытых дадзеных OpenStreetMap: частка ўстаноў магла не трапіць у яго.',
    nokey: 'Карта падключаецца ключом Яндэкс Карт: дадайце VITE_YANDEX_MAPS_KEY у frontend/.env.local і перазапусціце сайт.',
    route: 'Маршрут',
    you: 'Вы тут',
    mapLabel: 'Карта дзяржаўных ўстаноў',
    credit: 'Даныя Яндэкс Карт. Гадзіны працы і адрас лепш удакладніць перад візітам.',
    creditOsm: 'Даныя © удзельнікі OpenStreetMap. Гадзіны працы і адрас лепш удакладніць перад візітам.',
  },
} as const;

let loader: Promise<any> | null = null;

function loadYmaps(key: string): Promise<any> {
  if (loader) return loader;
  loader = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = `https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(key)}&lang=ru_RU`;
    script.async = true;
    script.onload = () => (window.ymaps ? window.ymaps.ready(() => resolve(window.ymaps)) : reject(new Error('ymaps')));
    script.onerror = () => {
      loader = null;
      reject(new Error('ymaps'));
    };
    document.head.appendChild(script);
  });
  return loader;
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c);

const fmt = (m: number) =>
  m < 1000 ? `${Math.max(10, Math.round(m / 10) * 10)} м` : `${(m / 1000).toFixed(1).replace('.', ',')} км`;

function distance(a: Coords, b: Coords): number {
  const rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad;
  const dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

async function searchCategory(cat: Category, c: Coords): Promise<Omit<Place, 'cats'>[]> {
  const params = new URLSearchParams({
    apikey: SEARCH_KEY ?? '',
    text: cat.query,
    type: 'biz',
    lang: 'ru_RU',
    ll: `${c[1]},${c[0]}`,
    spn: '0.5,0.3',
    rspn: '1',
    results: '20',
  });
  const res = await fetch(`https://search-maps.yandex.ru/v1/?${params.toString()}`);
  if (!res.ok) {
    let detail = '';
    try {
      detail = String((await res.json()).message ?? '');
    } catch {
      /* тело ответа не JSON */
    }
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ''}`);
  }
  const data = await res.json();
  const out: Omit<Place, 'cats'>[] = [];
  for (const f of data.features ?? []) {
    const meta = f.properties?.CompanyMetaData ?? {};
    const name = String(meta.name ?? f.properties?.name ?? '');
    const rubrics = (meta.Categories ?? []).map((x: any) => x.name).join(' ');
    if (!name || !cat.match.test(`${name} ${rubrics}`)) continue;
    const [lon, lat] = f.geometry.coordinates as [number, number];
    const coords: Coords = [lat, lon];
    out.push({
      id: String(meta.id ?? `${name}-${lat},${lon}`),
      name,
      address: String(meta.address ?? f.properties?.description ?? ''),
      coords,
      dist: distance(c, coords),
      hours: meta.Hours?.text,
      phone: meta.Phones?.[0]?.formatted,
    });
  }
  return out;
}

async function searchOsm(c: Coords): Promise<Place[]> {
  const around = `(around:${OSM_RADIUS},${c[0]},${c[1]})`;
  const query =
    `[out:json][timeout:25];(` +
    `nwr["name"~"${OSM_NAME}",i]${around};` +
    `nwr["name:ru"~"${OSM_NAME}",i]${around};` +
    `nwr["amenity"~"^(townhall|register_office)$"]${around};` +
    `);out tags center 250;`;
  const res = await fetch('https://overpass-api.de/api/interpreter', {
    method: 'POST',
    body: new URLSearchParams({ data: query }),
  });
  if (!res.ok) throw new Error(`OSM HTTP ${res.status}`);
  const data = await res.json();
  const out: Place[] = [];
  for (const el of data.elements ?? []) {
    const tags = el.tags ?? {};
    const name = String(tags['name:ru'] ?? tags.name ?? '');
    if (!name) continue;
    if (tags.shop || /^(restaurant|cafe|fast_food|bar|pub|pharmacy|bank|clinic|hospital|school|kindergarten|place_of_worship)$/.test(tags.amenity ?? '')) continue;
    const text = `${name} ${tags.name ?? ''}`;
    const govTag =
      ['government', 'tax', 'administrative', 'military'].includes(tags.office) ||
      ['townhall', 'register_office', 'public_building'].includes(tags.amenity) ||
      Boolean(tags.government);
    if (!govTag && !GOV_NAME.test(text)) continue;
    const cats = CATEGORIES.filter(
      (k) => k.match.test(text) || (k.id === 'exec' && tags.amenity === 'townhall') || (k.id === 'zags' && tags.amenity === 'register_office'),
    ).map((k) => k.id);
    if (!cats.length) continue;
    const lat = el.lat ?? el.center?.lat;
    const lon = el.lon ?? el.center?.lon;
    if (typeof lat !== 'number' || typeof lon !== 'number') continue;
    const coords: Coords = [lat, lon];
    const street = tags['addr:street'] ? `${tags['addr:street']}${tags['addr:housenumber'] ? `, ${tags['addr:housenumber']}` : ''}` : '';
    out.push({
      id: `osm-${el.type}-${el.id}`,
      cats,
      name,
      address: [tags['addr:city'], street].filter(Boolean).join(', '),
      coords,
      dist: distance(c, coords),
      hours: tags.opening_hours,
      phone: tags.phone ?? tags['contact:phone'],
    });
  }
  return out;
}

export default function NearbyMap({ lang }: { lang: Lang }) {
  const t = T[lang];
  const sectionRef = useRef<HTMLElement | null>(null);
  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<any>(null);
  const marks = useRef(new Map<string, any>());
  const [started, setStarted] = useState(false);
  const [origin, setOrigin] = useState<Origin | null>(null);
  const [denied, setDenied] = useState(false);
  const [status, setStatus] = useState<Status>('idle');
  const [source, setSource] = useState<Source>('yandex');
  const [errorText, setErrorText] = useState('');
  const [all, setAll] = useState<Place[]>([]);
  const [cat, setCat] = useState('all');
  const [active, setActive] = useState<string | null>(null);

  const shown = useMemo(
    () => all.filter((p) => cat === 'all' || p.cats.includes(cat)).slice(0, MAX_SHOWN),
    [all, cat],
  );

  const locate = useCallback(() => {
    setStarted(true);
    setStatus('locating');
    setDenied(false);
    const fallback = () => {
      setDenied(true);
      setOrigin({ coords: MINSK, real: false });
    };
    if (!navigator.geolocation) {
      fallback();
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => setOrigin({ coords: [pos.coords.latitude, pos.coords.longitude], real: true }),
      fallback,
      { timeout: 10000, maximumAge: 300000 },
    );
  }, []);

  useEffect(() => {
    if (!KEY) return;
    const el = sectionRef.current;
    if (!el || !('IntersectionObserver' in window)) {
      locate();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io.disconnect();
          locate();
        }
      },
      { rootMargin: '0px 0px -20% 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [locate]);

  useEffect(() => {
    if (!KEY || !origin) return;
    let cancelled = false;
    setStatus('searching');
    setErrorText('');
    setActive(null);

    const run = async (): Promise<{ list: Place[]; src: Source }> => {
      let yandexError = '';
      if (SEARCH_KEY && !yandexDown) {
        const results = await Promise.allSettled(CATEGORIES.map((c) => searchCategory(c, origin.coords)));
        const failed = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
        if (failed.length < results.length) {
          const merged = new Map<string, Place>();
          results.forEach((r, i) => {
            if (r.status !== 'fulfilled') return;
            r.value.forEach((p) => {
              const prev = merged.get(p.id);
              if (prev) prev.cats.push(CATEGORIES[i].id);
              else merged.set(p.id, { ...p, cats: [CATEGORIES[i].id] });
            });
          });
          return { list: [...merged.values()], src: 'yandex' };
        }
        yandexDown = true;
        const reason = failed[0]?.reason;
        yandexError = `Яндекс: ${reason instanceof Error ? reason.message : String(reason)}`;
        console.error('[NearbyMap] Yandex search failed, using OpenStreetMap', reason);
      }
      try {
        return { list: await searchOsm(origin.coords), src: 'osm' };
      } catch (e) {
        const own = e instanceof Error ? e.message : String(e);
        throw new Error([yandexError, own].filter(Boolean).join(' | '));
      }
    };

    run()
      .then(({ list, src }) => {
        if (cancelled) return;
        setAll(list.filter((p) => p.dist <= MAX_DIST).sort((a, b) => a.dist - b.dist));
        setSource(src);
        setStatus('ready');
      })
      .catch((e) => {
        if (cancelled) return;
        console.error('[NearbyMap] search failed', e);
        setErrorText(e instanceof Error ? e.message : String(e));
        setStatus('error');
      });
    return () => {
      cancelled = true;
    };
  }, [origin]);

  useEffect(() => {
    if (!KEY || !started) return;
    let cancelled = false;
    loadYmaps(KEY)
      .then((ymaps) => {
        if (cancelled || !mapEl.current) return;
        const center = origin?.coords ?? MINSK;
        if (!mapRef.current) {
          mapRef.current = new ymaps.Map(
            mapEl.current,
            { center, zoom: 12, controls: ['zoomControl'] },
            { suppressMapOpenBlock: true },
          );
        }
        const map = mapRef.current;
        map.geoObjects.removeAll();
        marks.current.clear();
        if (origin?.real) {
          map.geoObjects.add(
            new ymaps.Placemark(origin.coords, { hintContent: t.you }, { preset: 'islands#circleDotIcon', iconColor: '#233883' }),
          );
        }
        shown.forEach((p) => {
          const mark = new ymaps.Placemark(
            p.coords,
            { hintContent: esc(p.name), balloonContentHeader: esc(p.name), balloonContentBody: esc(p.address) },
            { preset: 'islands#governmentCircleIcon', iconColor: '#2563eb' },
          );
          mark.events.add('click', () => setActive(p.id));
          marks.current.set(p.id, mark);
          map.geoObjects.add(mark);
        });
        const bounds = map.geoObjects.getBounds();
        if (bounds && shown.length) map.setBounds(bounds, { checkZoomRange: true, zoomMargin: 56 });
        else map.setCenter(center, 12);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [started, origin, shown, t.you]);

  useEffect(() => {
    const mark = active ? marks.current.get(active) : undefined;
    if (!mark || !mapRef.current) return;
    mapRef.current.panTo(mark.geometry.getCoordinates(), { flying: true }).then(() => mark.balloon.open());
  }, [active]);

  useEffect(
    () => () => {
      mapRef.current?.destroy();
      mapRef.current = null;
    },
    [],
  );

  const busy = status === 'locating' || status === 'searching';
  const routeHref = (p: Place) => {
    const to = `${p.coords[0]},${p.coords[1]}`;
    const from = origin?.real ? `${origin.coords[0]},${origin.coords[1]}` : '';
    return `https://yandex.by/maps/?rtext=${from}~${to}&rtt=auto`;
  };

  return (
    <section className='wrap section nearby' id='nearby' ref={sectionRef} aria-labelledby='nearby-title'>
      <div className='sec-head'>
        <h2 id='nearby-title'>{t.title}</h2>
        <p>{t.sub}</p>
      </div>
      {!KEY ? (
        <p className='nearby-note' role='status'>
          {t.nokey}
        </p>
      ) : (
        <>
          <div className='nearby-bar'>
            <ul className='chips' aria-label={t.filter}>
              {[ALL, ...CATEGORIES].map((c) => (
                <li key={c.id}>
                  <button className='chip' type='button' aria-pressed={cat === c.id} onClick={() => setCat(c.id)}>
                    {c[lang]}
                  </button>
                </li>
              ))}
            </ul>
            <button className='btn btn-outline nearby-locate' type='button' onClick={locate} disabled={busy}>
              {t.locate}
            </button>
          </div>
          <div className='nearby-card'>
            <div className='nearby-map' ref={mapEl} role='region' aria-label={t.mapLabel} />
            <div className='nearby-list-wrap'>
              <div aria-live='polite'>
                {denied && status !== 'locating' && <p className='nearby-note'>{t.denied}</p>}
                {status === 'locating' && <p className='nearby-note'>{t.locating}</p>}
                {status === 'searching' && <p className='nearby-note'>{t.searching}</p>}
                {status === 'ready' && source === 'osm' && <p className='nearby-note'>{t.osmNote}</p>}
                {status === 'error' && (
                  <p className='nearby-note is-error'>
                    {t.error}
                    {errorText && (
                      <>
                        <br />
                        <small>{errorText}</small>
                      </>
                    )}
                  </p>
                )}
                {status === 'ready' && shown.length === 0 && <p className='nearby-note'>{t.empty}</p>}
              </div>
              <ul className='nearby-list'>
                {shown.map((p) => (
                  <li key={p.id}>
                    <button
                      className='nearby-item'
                      type='button'
                      aria-current={active === p.id ? 'true' : undefined}
                      onClick={() => setActive(p.id)}
                    >
                      <span className='nearby-pin' aria-hidden='true'>
                        <svg width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='2' strokeLinecap='round' strokeLinejoin='round'>
                          <path d='M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z' />
                          <circle cx='12' cy='10' r='2.5' />
                        </svg>
                      </span>
                      <span className='nearby-info'>
                        <strong>{p.name}</strong>
                        {p.address && <small>{p.address}</small>}
                        {p.hours && <small>{p.hours}</small>}
                      </span>
                      <span className='nearby-dist'>{fmt(p.dist)}</span>
                    </button>
                    {active === p.id && (
                      <div className='nearby-actions'>
                        <a href={routeHref(p)} target='_blank' rel='noreferrer'>
                          {t.route} ↗
                        </a>
                        {p.phone && <a href={`tel:${p.phone.replace(/[^+\d]/g, '')}`}>{p.phone}</a>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <p className='nearby-credit muted'>{source === 'osm' ? t.creditOsm : t.credit}</p>
        </>
      )}
    </section>
  );
}
