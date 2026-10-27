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
  | { role: 'bot'; resp: ChatResponse }
  | { role: 'error'; text: string };

function newSessionId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function Assistant({
  open,
  onClose,
  request,
}: {
  open: boolean;
  onClose: () => void;
  request: AssistantRequest;
}) {
  const { t, lang } = useT();
  const u = useU();
  const { kiosk, services } = useContext(AppCtx);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [typing, setTyping] = useState(false);
  const [serviceId, setServiceId] = useState<string | undefined>();
  const serviceRef = useRef<string | undefined>(undefined);
  const busyRef = useRef(false);
  const discard = useRef(false);
  const sessionId = useRef(newSessionId());
  const recorder = useRef<MediaRecorder | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
      const resp = await postChat({ message, session_id: sessionId.current, service_id: serviceRef.current, top_k: 6 });
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
      <button className='btn btn-primary' type='submit' disabled={busy || recording || transcribing || !input.trim()}>
        {t('send')}
      </button>
    </form>
  );

  return (
    <>
      <div className={`asst-overlay ${open ? 'is-open' : ''}`} onClick={onClose} aria-hidden='true' />
      <aside className={`asst ${open ? 'is-open' : ''}`} role='dialog' aria-modal='false' aria-label={t('chat_title')} aria-hidden={!open}>
        <header className='asst-head'>
          <div>
            <h2>{kiosk ? u('k_asst_title') : t('chat_title')}</h2>
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
                  <Icon name='speaker' size={kiosk ? 28 : 20} /> {u('a_speak')}
                </button>
                {resp.citations.length > 0 && (
                  <details className='cites' open={!kiosk}>
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
              </div>
            );
          })}
          {busy && (
            <div className='msg msg-bot msg-thinking'>
              <span className='dots' aria-hidden='true'>
                <i />
                <i />
                <i />
              </span>
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
      </aside>
    </>
  );
}
