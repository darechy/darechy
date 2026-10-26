import { useEffect, useState } from "react";

import { fetchServices, type Service } from "./api";

// Каркас главной страницы портала «ДаРэчы».
// Дальше (кент, неделя 1–2): роутинг (/ситуация/:id, /услуга/:id), ассистент-панель,
// киоск-режим (?kiosk=1 — крупный шрифт + голосовой ввод), переключатель RU/BE.

type ApiState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; services: Service[] };

const LIFE_SITUATIONS = [
  { id: "rozhdenie-rebenka", icon: "👶", title: "Родился ребёнок", hint: "Пособия, выплаты, документы за 5 минут", active: true },
  { id: "poterya-dokumentov", icon: "🪪", title: "Потерял документы", hint: "Паспорт, права, свидетельства", active: false },
  { id: "pensia", icon: "👴", title: "Выход на пенсию", hint: "Какие справки и когда", active: false },
  { id: "ip", icon: "💼", title: "Открыл своё дело", hint: "ИП, самозанятость, налоги", active: false },
  { id: "lgoty", icon: "🏠", title: "Льготы и субсидии", hint: "ЖКХ, транспорт, лекарства", active: false },
  { id: "spravki", icon: "📄", title: "Справки и выписки", hint: "Где взять, сколько ждёт", active: false },
];

export default function App() {
  const [api, setApi] = useState<ApiState>({ kind: "loading" });

  useEffect(() => {
    fetchServices()
      .then((services) => setApi({ kind: "ok", services }))
      .catch((err: unknown) =>
        setApi({
          kind: "error",
          message: err instanceof Error ? err.message : String(err),
        }),
      );
  }, []);

  return (
    <div className="page">
      <header className="header">
        <div className="container header-row">
          <a className="logo" href="/">
            ДаРэчы
          </a>
          <nav className="nav">
            <a href="#situations">Жизненные ситуации</a>
            <a href="#services">Услуги</a>
            <button className="btn btn-ghost" type="button" title="Появится в неделе 2">
              Рў
            </button>
            <button className="btn btn-primary" type="button" title="Ассистент появится в неделе 2">
              Спросить ассистента
            </button>
          </nav>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="container">
            <h1>
              Государство отвечает <span className="accent">за 5 минут</span>
            </h1>
            <p className="hero-sub">
              Опишите ситуацию обычными словами — ДаРэчы подскажет, какие услуги вам нужны,
              какие документы собрать и где каждый из них берётся. Каждый ответ — с цитатой
              из официального нормативного акта.
            </p>
            <div className="hero-actions">
              <button className="btn btn-primary btn-lg" type="button">
                Описать свою ситуацию
              </button>
              <a className="btn btn-outline btn-lg" href="#situations">
                Выбрать из списка
              </a>
            </div>
          </div>
        </section>

        <section className="container section" id="situations">
          <h2>Жизненные ситуации</h2>
          <p className="section-sub">Выберите, что у вас произошло, — покажем весь маршрут.</p>
          <div className="grid">
            {LIFE_SITUATIONS.map((situation) => (
              <div
                key={situation.id}
                className={`card situation ${situation.active ? "" : "card-disabled"}`}
                title={situation.active ? undefined : "Ситуация появится в следующих неделях"}
              >
                <span className="situation-icon">{situation.icon}</span>
                <span className="situation-title">{situation.title}</span>
                <span className="situation-hint">{situation.hint}</span>
                {situation.active && <span className="badge">MVP</span>}
              </div>
            ))}
          </div>
        </section>

        <section className="container section" id="services">
          <h2>Услуги в MVP</h2>
          <p className="section-sub">
            Живые данные с бэкенда. На странице каждой услуги — простыми словами: кому, сколько,
            в какой срок, чек-лист документов и ссылка на нормативный акт.
          </p>
          {api.kind === "loading" && <p className="muted">Загружаем услуги…</p>}
          {api.kind === "error" && (
            <div className="api-error">
              Бэкенд не отвечает ({api.message}). Подними его: <code>cd backend && uvicorn app.main:app --reload</code> —
              инструкция в README.
            </div>
          )}
          {api.kind === "ok" && (
            <div className="service-list">
              {api.services.map((service) => (
                <details key={service.id} className="card service">
                  <summary>
                    <strong>{service.title}</strong>
                    <span className="muted"> — {service.summary}</span>
                  </summary>
                  <dl className="service-facts">
                    <div>
                      <dt>Кому</dt>
                      <dd>{service.who}</dd>
                    </div>
                    <div>
                      <dt>Размер</dt>
                      <dd>{service.amount}</dd>
                    </div>
                    <div>
                      <dt>Срок</dt>
                      <dd>{service.deadline}</dd>
                    </div>
                  </dl>
                  <h3>Документы</h3>
                  <ul className="checklist">
                    {service.checklist.map((row, index) => (
                      <li key={index}>
                        {row.item} <span className="muted">— {row.where}</span>
                      </li>
                    ))}
                  </ul>
                  <h3>Источники</h3>
                  <ul className="sources">
                    {service.sources.map((source, index) => (
                      <li key={index}>
                        {source.url ? (
                          <a href={source.url} target="_blank" rel="noreferrer">
                            {source.title}
                          </a>
                        ) : (
                          source.title
                        )}
                        {source.point && <span className="muted"> · {source.point}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="footer">
        <div className="container">
          <p>
            <strong>ДаРэчы</strong> · ИИ-проводник по госуслугам · «100 идей для Беларуси», сезон 2026/2027
          </p>
          <p className="muted">
            Ассистент консультирует по официальным нормативным актам и не заменяет юридически
            значимые решения государственных органов. Официальную консультацию даёт орган.
          </p>
        </div>
      </footer>
    </div>
  );
}
