import { useCallback, useEffect, useMemo, useState } from "react";

import Assistant, { type AssistantRequest } from "./Assistant";
import { fetchServices, fetchSituations } from "./api";
import { AppCtx, type OpenAssistantOptions } from "./context";
import { useAsync } from "./hooks";
import { LangCtx, useT, type Lang } from "./i18n";
import { Home, ServicePage, SituationPage } from "./pages";
import { useRoute } from "./router";

const IDLE_MS = 120_000;
const KIOSK = new URLSearchParams(window.location.search).get("kiosk") === "1";

function storedLang(): Lang {
  try {
    return localStorage.getItem("darechy:lang") === "be" ? "be" : "ru";
  } catch {
    return "ru";
  }
}

function Shell() {
  const { t, lang, setLang } = useT();
  const { route, hash } = useRoute();
  const services = useAsync(fetchServices);
  const situations = useAsync(fetchSituations);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState<AssistantRequest>(null);
  const [session, setSession] = useState(0);

  const openAssistant = useCallback((options?: OpenAssistantOptions) => {
    setRequest((prev) => ({ text: options?.text, serviceId: options?.serviceId, nonce: (prev?.nonce ?? 0) + 1 }));
    setOpen(true);
  }, []);
  const closeAssistant = useCallback(() => setOpen(false), []);

  useEffect(() => {
    document.documentElement.classList.toggle("kiosk", KIOSK);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    if (route.name === "home" && route.section) {
      const target = route.section;
      requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ behavior: "smooth" }));
    } else {
      window.scrollTo(0, 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hash]);

  // Киоск: после паузы сбрасываем экран и диалог — следующий посетитель начинает с нуля.
  useEffect(() => {
    if (!KIOSK) return;
    let timer = 0;
    const reset = () => {
      setOpen(false);
      setRequest(null);
      setSession((s) => s + 1);
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
      if (window.location.hash && window.location.hash !== "#/") window.location.hash = "#/";
    };
    const bump = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(reset, IDLE_MS);
    };
    const events = ["pointerdown", "keydown", "touchstart"];
    events.forEach((name) => window.addEventListener(name, bump));
    bump();
    return () => {
      window.clearTimeout(timer);
      events.forEach((name) => window.removeEventListener(name, bump));
    };
  }, []);

  const ctx = useMemo(
    () => ({ services, situations, openAssistant, kiosk: KIOSK }),
    [services, situations, openAssistant],
  );

  return (
    <AppCtx.Provider value={ctx}>
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>
        {t("skip")}
      </a>
      <div className="page-shell">
        <header className="hdr">
          <div className="wrap hdr-row">
            <a className="brand" href="#/" aria-label="ДаРэчы">
              <svg className="brand-mark" viewBox="0 0 16 16" width="26" height="26" aria-hidden="true">
                <path d="M8 1l7 7-7 7-7-7z" fill="currentColor" />
                <path d="M8 5l3 3-3 3-3-3z" fill="var(--paper)" />
              </svg>
              <span className="brand-name">ДаРэчы</span>
              <span className="brand-tag">{t("brand_tag")}</span>
            </a>
            <nav className="nav" aria-label="main">
              <a href="#/situations">{t("nav_situations")}</a>
              <a href="#/services">{t("nav_services")}</a>
              <div className="lang" role="group" aria-label="language">
                {(["ru", "be"] as Lang[]).map((code) => (
                  <button key={code} type="button" aria-pressed={lang === code} onClick={() => setLang(code)}>
                    {code === "ru" ? "RU" : "БЕЛ"}
                  </button>
                ))}
              </div>
              <button className="btn btn-primary" type="button" onClick={() => openAssistant()}>
                {t("nav_ask")}
              </button>
            </nav>
          </div>
          <div className="orn" aria-hidden="true" />
        </header>

        <main id="main" tabIndex={-1}>
          {route.name === "home" && <Home />}
          {route.name === "situation" && <SituationPage id={route.id} />}
          {route.name === "service" && <ServicePage id={route.id} />}
          {route.name === "notfound" && (
            <section className="wrap section">
              <h1 className="page-title">{t("notfound")}</h1>
              <p className="muted">{t("notfound_d")}</p>
              <a className="btn btn-outline" href="#/">
                {t("home")}
              </a>
            </section>
          )}
        </main>

        <footer className="ftr">
          <div className="orn" aria-hidden="true" />
          <div className="wrap">
            <p>
              <strong>ДаРэчы</strong> · {t("foot_a")}
            </p>
            <p className="muted">{t("foot_b")}</p>
          </div>
        </footer>
      </div>

      <Assistant key={session} open={open} onClose={closeAssistant} request={request} />
    </AppCtx.Provider>
  );
}

export default function App() {
  const [lang, setLangState] = useState<Lang>(storedLang);
  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem("darechy:lang", next);
    } catch {
      // не критично
    }
  }, []);
  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return (
    <LangCtx.Provider value={value}>
      <Shell />
    </LangCtx.Provider>
  );
}
