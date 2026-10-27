const PATHS = {
  baby: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M8.5 14.2a4.5 4.5 0 0 0 7 0 M9 9.8h.01 M15 9.8h.01',
  card: 'M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z M9 11.5a1.8 1.8 0 1 0 0-3.6a1.8 1.8 0 1 0 0 3.6z M6 16c.6-1.8 5.4-1.8 6 0 M14.5 10h3.5 M14.5 14h3.5',
  clock: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3.2 2',
  bag: 'M5 8h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z M9 8V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2 M3 13h18',
  house: 'M3 11l9-8 9 8 M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5',
  doc: 'M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M14 3v5h5 M9.5 13h6 M9.5 17h6',
  mic: 'M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z M18 12a6 6 0 0 1-12 0 M12 18v3',
  speaker: 'M4 9.5v5h3.5L13 19V5L7.5 9.5z M16.5 9a4 4 0 0 1 0 6 M18.8 6.5a7.5 7.5 0 0 1 0 11',
  back: 'M15 5l-7 7 7 7',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  list: 'M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01',
  keyboard: 'M3 7h18a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z M6 11h.01 M10 11h.01 M14 11h.01 M18 11h.01 M7 15h10',
  stop: 'M7 7h10v10H7z',
  close: 'M6 6l12 12 M18 6L6 18',
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  return (
    <svg
      viewBox='0 0 24 24'
      width={size}
      height={size}
      fill='none'
      stroke='currentColor'
      strokeWidth={1.9}
      strokeLinecap='round'
      strokeLinejoin='round'
      aria-hidden='true'
      focusable='false'
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg viewBox='0 0 32 32' width={size} height={size} aria-hidden='true' focusable='false'>
      <path d='M16 2l14 14-14 14L2 16z' fill='var(--primary)' />
      <path d='M16 9l7 7-7 7-7-7z' fill='var(--bg)' />
    </svg>
  );
}

const SITUATION_ICONS: Record<string, IconName> = {
  'rozhdenie-rebenka': 'baby',
  'poterya-dokumentov': 'card',
  pensia: 'clock',
  ip: 'bag',
  lgoty: 'house',
  spravki: 'doc',
};

export const situationIcon = (id: string): IconName => SITUATION_ICONS[id] ?? 'doc';
