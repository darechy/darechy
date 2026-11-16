import { useContext, useEffect, useRef, useState } from 'react';

import { postChat, type ChatResponse } from './api';
import { AppCtx } from './context';
import { useT } from './i18n';
import { Icon } from './icons';
import { speak } from './speech';
import { useU } from './ui';

const PHONE_QUERY = '(max-width: 640px)';

type Msg =
  | { role: 'user'; text: string }
  | { role: 'bot'; resp: ChatResponse }
  | { role: 'error'; text: string };

function newSessionId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Телефон ли это (узкий экран). Реагирует на поворот устройства и изменение размера окна.
export function useIsPhone(): boolean {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(PHONE_QUERY);
    const onChange = () => setPhone(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return phone;
}

// Состояние встроенного чата (как на главной киоска): сообщения появляются над полем ввода.
export function useInlineChat() {
  const { t } = useT();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const sessionId = useRef(newSessionId());

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessages((prev) => [...prev, { role: 'user', text }]);
    try {
      const resp = await postChat({ message: text, session_id: sessionId.current, top_k: 6 });
      setMessages((prev) => [...prev, { role: 'bot', resp }]);
    } catch (err) {
      setMessages((prev) => [...prev, { role: 'error', text: `${t('chat_err')}: ${err instanceof Error ? err.message : String(err)}` }]);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return { messages, busy, send };
}

export function InlineChat({ chat }: { chat: ReturnType<typeof useInlineChat> }) {
  const { t, lang } = useT();
  const u = useU();
  const { services } = useContext(AppCtx);
  const endRef = useRef<HTMLDivElement | null>(null);
  const serviceList = services.kind === 'ok' ? services.data : [];
  const { messages, busy } = chat;

  useEffect(() => {
    if (messages.length > 0 || busy) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, busy]);

  if (messages.length === 0 && !busy) return null;

  return (
    <div className='ichat' aria-live='polite'>
      {messages.map((msg, index) => {
        if (msg.role === 'user') {
          return (
            <div key={index} className='msg msg-user'>
              {msg.text}
            </div>
          );
        }
        if (msg.role === 'error') {
          return (
            <div key={index} className='msg msg-error'>
              {msg.text}
            </div>
          );
        }
        const { resp } = msg;
        const suggested = resp.suggested_services
          .map((id) => serviceList.find((s) => s.id === id))
          .filter((s) => s !== undefined);
        return (
          <div key={index} className={`msg msg-bot ${resp.refusal ? 'msg-refusal' : ''}`}>
            {resp.refusal && <span className='msg-flag'>{t('refusal')}</span>}
            <p className='msg-text'>{resp.answer}</p>
            <button className='link-btn' type='button' onClick={() => speak(resp.answer, lang)}>
              <Icon name='speaker' size={20} /> {u('a_speak')}
            </button>
            {resp.citations.length > 0 && (
              <details className='cites'>
                <summary>
                  {u('a_why')} ({resp.citations.length})
                </summary>
                {resp.citations.map((c) => {
                  const meta = [c.doc_number, c.doc_date, c.location].filter(Boolean).join(' · ');
                  return (
                    <figure className='cite' key={c.chunk_id}>
                      <blockquote>{c.quote}</blockquote>
                      <figcaption>
                        <strong>{c.doc_title}</strong>
                        {meta && <span> · {meta}</span>}
                        {c.url && (
                          <>
                            {' '}
                            <a href={c.url} target='_blank' rel='noreferrer'>
                              pravo.by ↗
                            </a>
                          </>
                        )}
                      </figcaption>
                    </figure>
                  );
                })}
              </details>
            )}
            {suggested.length > 0 && (
              <div className='suggested'>
                <h3>{t('suggested')}</h3>
                <ul>
                  {suggested.map((s) => (
                    <li key={s.id}>
                      <a href={`#/service/${encodeURIComponent(s.id)}`}>{s.title}</a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      })}
      {busy && (
        <div className='msg msg-bot msg-thinking' role='status'>
          <span className='thinking-pulse' aria-hidden='true' />
          {t('thinking')}
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
