import { useContext, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { postChat, transcribe, type ChatResponse } from "./api";
import { AppCtx } from "./context";
import { useT, type Lang } from "./i18n";

export type AssistantRequest = { text?: string; serviceId?: string; nonce: number } | null;

type Msg =
  | { role: "user"; text: string }
  | { role: "bot"; resp: ChatResponse }
  | { role: "error"; text: string };

function speak(text: string, lang: Lang) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = lang === "be" ? "be-BY" : "ru-RU";
  utterance.rate = 0.95;
  window.speechSynthesis.speak(utterance);
}

function newSessionId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
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
  const { kiosk, services } = useContext(AppCtx);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [serviceId, setServiceId] = useState<string | undefined>();
  const busyRef = useRef(false);
  const sessionId = useRef(newSessionId());
  const recorder = useRef<MediaRecorder | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const serviceList = services.kind === "ok" ? services.data : [];
  const contextService = serviceList.find((s) => s.id === serviceId);

  const send = async (raw: string, sid: string | undefined = serviceId) => {
    const message = raw.trim();
    if (!message || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    try {
      const resp = await postChat({ message, session_id: sessionId.current, service_id: sid, top_k: 6 });
      setMessages((prev) => [...prev, { role: "bot", resp }]);
      if (kiosk) speak(resp.answer, lang);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      setMessages((prev) => [...prev, { role: "error", text: `${t("chat_err")}: ${reason}` }]);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!request) return;
    setServiceId(request.serviceId);
    if (request.text) void send(request.text, request.serviceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.nonce]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => {
    if (!open) {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
      return;
    }
    const timer = window.setTimeout(() => inputRef.current?.focus(), 250);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  const pushError = (text: string) => setMessages((prev) => [...prev, { role: "error", text }]);

  const toggleRecording = async () => {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (event) => chunks.push(event.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        try {
          const text = await transcribe(new Blob(chunks, { type: mr.mimeType || "audio/webm" }));
          if (text.trim()) void send(text);
        } catch (err) {
          pushError(`${t("chat_err")}: ${err instanceof Error ? err.message : String(err)}`);
        }
      };
      mr.start();
      recorder.current = mr;
      setRecording(true);
    } catch {
      pushError(t("mic_err"));
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void send(input);
  };

  return (
    <>
      <div className={`asst-overlay ${open ? "is-open" : ""}`} onClick={onClose} aria-hidden="true" />
      <aside
        className={`asst ${open ? "is-open" : ""}`}
        role="dialog"
        aria-modal="false"
        aria-label={t("chat_title")}
        aria-hidden={!open}
      >
        <header className="asst-head">
          <div>
            <h2>{t("chat_title")}</h2>
            {contextService && (
              <p className="asst-context">
                {t("chat_about")}: {contextService.title}
              </p>
            )}
          </div>
          <button className="icon-btn" type="button" onClick={onClose} aria-label={t("close")}>
            ×
          </button>
        </header>

        <div className="asst-body" aria-live="polite">
          {messages.length === 0 && <p className="asst-empty">{t("chat_empty")}</p>}
          {messages.map((msg, index) => {
            if (msg.role === "user") {
              return (
                <div key={index} className="msg msg-user">
                  {msg.text}
                </div>
              );
            }
            if (msg.role === "error") {
              return (
                <div key={index} className="msg msg-error">
                  {msg.text}
                </div>
              );
            }
            const { resp } = msg;
            const suggested = resp.suggested_services
              .map((id) => serviceList.find((s) => s.id === id))
              .filter((s) => s !== undefined);
            return (
              <div key={index} className={`msg msg-bot ${resp.refusal ? "msg-refusal" : ""}`}>
                {resp.refusal && <span className="msg-flag">{t("refusal")}</span>}
                <p className="msg-text">{resp.answer}</p>
                {resp.citations.length > 0 && (
                  <div className="cites">
                    <h3>{t("citations")}</h3>
                    {resp.citations.map((c) => {
                      const meta = [c.doc_number, c.doc_date, c.location].filter(Boolean).join(" · ");
                      return (
                        <figure className="cite" key={c.chunk_id}>
                          <blockquote>{c.quote}</blockquote>
                          <figcaption>
                            <strong>{c.doc_title}</strong>
                            {meta && <span> · {meta}</span>}
                            {c.url && (
                              <>
                                {" "}
                                <a href={c.url} target="_blank" rel="noreferrer">
                                  pravo.by ↗
                                </a>
                              </>
                            )}
                          </figcaption>
                        </figure>
                      );
                    })}
                  </div>
                )}
                {suggested.length > 0 && (
                  <div className="suggested">
                    <h3>{t("suggested")}</h3>
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
            <div className="msg msg-bot msg-thinking">
              <span className="dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              {t("thinking")}
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form className="asst-form" onSubmit={onSubmit}>
          <button
            className={`mic ${recording ? "is-rec" : ""}`}
            type="button"
            onClick={() => void toggleRecording()}
            aria-label={recording ? t("mic_stop") : t("mic")}
            title={recording ? t("mic_stop") : t("mic")}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path
                d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0M12 17v4"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
            </svg>
          </button>
          <input
            ref={inputRef}
            value={recording ? t("listening") : input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={t("chat_ph")}
            disabled={recording}
            aria-label={t("chat_ph")}
          />
          <button className="btn btn-primary" type="submit" disabled={busy || recording || !input.trim()}>
            {t("send")}
          </button>
        </form>
      </aside>
    </>
  );
}
