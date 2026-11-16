import { useContext, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { postChat, transcribe, type ChatResponse } from './api';
import { AppCtx } from './context';
import { EXAMPLES, useT } from './i18n';
import { Icon } from './icons';
import { speak, stopSpeaking } from './speech';
import { useU } from './ui';

export type AssistantRequest = { text?: string; serviceId?: string; voice?: boolean; nonce: number } | null;

type Msg =
  | { role: 'user'; text: string }
  | { role: 'bot'; resp: ChatResponse; rating?: 'like' | 'dislike' }
  | { role: 'error'; text: string };

type SavedChat = { id: string; messages: Msg[] };
const historyKey = 'darechy.chat.history.v1';

function readHistory(): SavedChat[] {
  try {
    const saved = JSON.parse(localStorage.getItem(historyKey) ?? '[]');
    return Array.isArray(saved) ? saved.filter(chat =>
      typeof chat?.id === 'string' && Array.isArray(chat.messages)) : [];
  } catch {
    return [];
  }
}

function CopyMessage({ text, be }: { text: string; be: boolean }) {
  const [status, setStatus] = useState<'idle' | 'done' | 'error'>('idle');
  const label = be ? 'Скапіяваць' : 'Копировать';
  return <button type="button" title={label} aria-label={label} onClick={async () => {
    try {
      await navigator.clipboard.writeText(text);
      setStatus('done');
    } catch {
      setStatus('error');
    }
  }}>
    <Icon name={status === 'done' ? 'check' : 'copy'} size={14} />
    <span role="status" className="message-action-status">
      {status === 'done' ? (be ? 'Скапіявана' : 'Скопировано') :
        status === 'error' ? (be ? 'Не ўдалося скапіяваць' : 'Не удалось скопировать') : ''}
    </span>
  </button>;
}

function newSessionId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function Assistant({
  open,
  page = false,
  onClose,
  request,
}: {
  open: boolean;
  page?: boolean;
  onClose: () => void;
  request: AssistantRequest;
}) {
  const { t, lang } = useT();
  const u = useU();
  const { kiosk, services } = useContext(AppCtx);
  const [history, setHistory] = useState(() => page ? readHistory() : []);
  const [messages, setMessages] = useState<Msg[]>(() => history[0]?.messages ?? []);
  const [storageFailed, setStorageFailed] = useState(false);
  const [effort, setEffort] = useState<'quick' | 'standard' | 'deep'>('standard');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [typing, setTyping] = useState(false);
  const [serviceId, setServiceId] = useState<string | undefined>();
  const serviceRef = useRef<string | undefined>(undefined);
  const busyRef = useRef(false);
  const discard = useRef(false);
  const sessionId = useRef(history[0]?.id ?? newSessionId());
  const recorder = useRef<MediaRecorder | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!page || !messages.some(message => message.role === 'user')) return;
    const id = sessionId.current;
    setHistory(previous => {
      if (previous[0]?.id === id && previous[0].messages === messages) return previous;
      return [{ id, messages }, ...previous.filter(chat => chat.id !== id)];
    });
  }, [messages, page]);

  useEffect(() => {
    if (!page || !history.length) return;
    try {
      localStorage.setItem(historyKey, JSON.stringify(history));
      setStorageFailed(false);
    } catch {
      setStorageFailed(true);
    }
  }, [history, page]);

  function switchChat(chat?: SavedChat) {
    if (busyRef.current || recording || transcribing) return;
    stopSpeaking();
    sessionId.current = chat?.id ?? newSessionId();
    serviceRef.current = undefined;
    setServiceId(undefined);
    setMessages(chat?.messages ?? []);
    setInput('');
  }

  const modeMenu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (!page || !open) return;
    function closeOutside(event: PointerEvent) {
      const menu = modeMenu.current;
      if (menu?.open && !event.composedPath().includes(menu)) menu.open = false;
    }
    function closeOnEscape(event: KeyboardEvent) {
      const menu = modeMenu.current;
      if (event.key !== 'Escape' || !menu?.open) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      menu.open = false;
      menu.querySelector('summary')?.focus();
    }
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape, true);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape, true);
    };
  }, [page, open]);

  const serviceList = services.kind === 'ok' ? services.data : [];
  const contextService = serviceList.find((s) => s.id === serviceId);

  const pushError = (text: string) => setMessages((prev) => [...prev, { role: 'error', text }]);

  const send = async (raw: string) => {
    const message = raw.trim();
    if (!message || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', text: message }]);
    try {
      const resp = await postChat({ message, session_id: sessionId.current, service_id: serviceRef.current, top_k: effort === 'quick' ? 4 : effort === 'deep' ? 8 : 6, effort });
      setMessages((prev) => [...prev, { role: 'bot', resp }]);
      if (kiosk) speak(resp.answer, lang);
    } catch (err) {
      pushError(`${t('chat_err')}: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const toggleRecording = async () => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    if (busyRef.current || transcribing) return;
    stopSpeaking();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (event) => chunks.push(event.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        if (discard.current) {
          discard.current = false;
          return;
        }
        setTranscribing(true);
        try {
          const text = await transcribe(new Blob(chunks, { type: mr.mimeType || 'audio/webm' }));
          if (text.trim()) void send(text);
        } catch (err) {
          pushError(`${t('chat_err')}: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
          setTranscribing(false);
        }
      };
      mr.start();
      recorder.current = mr;
      setRecording(true);
    } catch {
      pushError(t('mic_err'));
    }
  };

  useEffect(() => {
    if (!request) return;
    serviceRef.current = request.serviceId;
    setServiceId(request.serviceId);
    if (request.text) void send(request.text);
    else if (request.voice) void toggleRecording();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.nonce]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, busy]);

  useEffect(() => {
    if (!open) {
      stopSpeaking();
      if (recorder.current?.state === 'recording') {
        discard.current = true;
        recorder.current.stop();
      }
      return;
    }
    const timer = window.setTimeout(() => {
      if (!kiosk) inputRef.current?.focus();
    }, 250);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose, kiosk]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(input);
  };

  const modes = [
    { value: 'quick', label: lang === 'be' ? 'Хутка' : 'Быстро' },
    { value: 'standard', label: lang === 'be' ? 'Звычайна' : 'Обычно' },
    { value: 'deep', label: lang === 'be' ? 'Максімум' : 'Максимум' },
  ] as const;
  const modeLabel = lang === 'be' ? 'Рэжым адказу' : 'Режим ответа';

  const form = (
    <form className='asst-form' onSubmit={onSubmit}>
      {!kiosk && (
        <button
          className={`mic ${recording ? 'is-rec' : ''}`}
          type='button'
          onClick={() => void toggleRecording()}
          aria-label={recording ? t('mic_stop') : t('mic')}
          title={recording ? t('mic_stop') : t('mic')}
        >
          <Icon name={recording ? 'stop' : 'mic'} size={22} />
        </button>
      )}
      <input
        ref={inputRef}
        value={recording ? t('listening') : input}
        onChange={(event) => setInput(event.target.value)}
        placeholder={t('chat_ph')}
        disabled={recording}
        aria-label={t('chat_ph')}
      />
      {page && <details className="composer-mode" ref={modeMenu}>
        <summary aria-label={modeLabel + ': ' + modes.find(mode => mode.value === effort)?.label}>
          <strong>Veda V-4</strong>
          <span className="composer-mode-value" key={effort}>{modes.find(mode => mode.value === effort)?.label}</span>
        </summary>
        <fieldset disabled={busy}>
          <legend>{modeLabel}</legend>
          <div className="composer-segments">
            {modes.map(mode => <label key={mode.value}>
              <input type="radio" name="answer-effort" checked={effort === mode.value}
                onChange={() => setEffort(mode.value)} />
              <span>{mode.label}</span>
            </label>)}
          </div>
        </fieldset>
      </details>}
      <button className='btn btn-primary' type='submit' aria-label={t('send')} disabled={busy || recording || transcribing || !input.trim()}>
        {page ? <span className='chat-send-icon'><Icon name='arrow' size={20} /></span> : t('send')}
      </button>
    </form>
  );

  return (
    <>
      {!page && <div className={`asst-overlay ${open ? 'is-open' : ''}`} onClick={onClose} aria-hidden='true' />}
      <aside className={`asst ${page ? "chat-page" : ""} ${open ? 'is-open' : ''}`} role={page ? 'main' : 'dialog'} aria-modal={page ? undefined : false} aria-label={t('chat_title')} aria-hidden={!open}>
        {page && <nav className="chat-sidebar" aria-label="Darechy AI">
          <a className="chat-brand" href="#/">Darechy AI</a>
          <button type="button" onClick={() => switchChat()} disabled={busy || recording || transcribing}>
            <span aria-hidden="true">＋</span> {lang === 'be' ? 'Новы чат' : 'Новый чат'}
          </button>
          <div className="chat-history" aria-label={lang === 'be' ? 'Гісторыя чатаў' : 'История чатов'}>
            {history.map(chat => <button key={chat.id} type="button"
              aria-current={sessionId.current === chat.id ? 'true' : undefined}
              disabled={busy || recording || transcribing} onClick={() => switchChat(chat)}>
              <span>{chat.messages.find(message => message.role === 'user')?.text}</span>
            </button>)}
          </div>
          {storageFailed && <p role="status">{lang === 'be' ? 'Не ўдалося захаваць гісторыю' : 'Не удалось сохранить историю'}</p>}
        </nav>}
        <div className="chat-workspace">
        <header className='asst-head'>
          <div>
            <h2>{page ? 'Veda V-4' : kiosk ? u('k_asst_title') : t('chat_title')}</h2>
            {contextService && (
              <p className='asst-context'>
                {t('chat_about')}: {contextService.title}
              </p>
            )}
          </div>
          {kiosk ? (
            <button className='k-btn' type='button' onClick={onClose}>
              <Icon name='close' size={30} /> {t('close')}
            </button>
          ) : (
            <button className='icon-btn' type='button' onClick={onClose} aria-label={t('close')}>
              <Icon name='close' size={22} />
            </button>
          )}
        </header>

        <div className='asst-body' aria-live='polite'>
          {messages.length === 0 && (
            <div className='asst-empty'>
              <p>{t('chat_empty')}</p>
              <p className='asst-try'>{u('a_try')}</p>
              <ul className='chips'>
                {EXAMPLES[lang].map((example) => (
                  <li key={example}>
                    <button className='chip' type='button' onClick={() => void send(example)}>
                      {example}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {messages.map((msg, index) => {
            if (msg.role === 'user') {
              return (
                <div key={index} className='msg msg-user'>
                  {msg.text}
                  {page && <div className="message-actions"><CopyMessage key={sessionId.current} text={msg.text} be={lang === 'be'} /></div>}
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
            const suggested = (resp.refusal ? [] : resp.suggested_services)
              .map((id) => serviceList.find((s) => s.id === id))
              .filter((s) => s !== undefined);
            return (
              <div key={index} className={`msg msg-bot ${resp.refusal ? 'msg-refusal' : ''}`}>
                {!page && resp.refusal && <span className='msg-flag'>{t('refusal')}</span>}
                <p className='msg-text'>{resp.answer}</p>
                {!page && <button className='link-btn' type='button' onClick={() => speak(resp.answer, lang)}>
                  <Icon name='speaker' size={kiosk ? 28 : 20} /> {u('a_speak')}
                </button>}
                {resp.citations.length > 0 && (
                  <details className='cites' open={!page && !kiosk}>
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
                          <a href={`#/service/${encodeURIComponent(s.id)}`} onClick={onClose}>
                            {s.title}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {page && <div className="message-actions">
                  {(['like', 'dislike'] as const).map(rating => {
                    const label = rating === 'like' ? (lang === 'be' ? 'Падабаецца' : 'Нравится') :
                      (lang === 'be' ? 'Не падабаецца' : 'Не нравится');
                    return <button key={rating} type="button" title={label} aria-label={label}
                      aria-pressed={msg.rating === rating}
                      onClick={() => setMessages(previous => previous.map((entry, position) =>
                        position === index && entry.role === 'bot'
                          ? { ...entry, rating: entry.rating === rating ? undefined : rating }
                          : entry))}>
                      <Icon name={rating} size={14} />
                    </button>;
                  })}
                  <CopyMessage key={sessionId.current} text={resp.answer} be={lang === 'be'} />
                  <button type="button" title={u('a_speak')} aria-label={u('a_speak')}
                    onClick={() => speak(resp.answer, lang)}><Icon name="speaker" size={14} /></button>
                </div>}
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

        {kiosk ? (
          <div className='voice'>
            <button className={`voice-btn ${recording ? 'is-rec' : ''}`} type='button' onClick={() => void toggleRecording()} disabled={busy || transcribing}>
              <span className='voice-ic'>
                <Icon name={recording ? 'stop' : 'mic'} size={44} />
              </span>
              <span>{transcribing ? u('v_busy') : recording ? u('v_stop') : u('v_start')}</span>
            </button>
            <p className='voice-hint'>{recording ? u('v_hint_rec') : u('v_hint')}</p>
            <button className='link-btn' type='button' onClick={() => setTyping((v) => !v)}>
              <Icon name='keyboard' size={28} /> {u('k_type')}
            </button>
            {typing && form}
          </div>
        ) : (
          form
        )}
        </div>
      </aside>
    </>
  );
}
