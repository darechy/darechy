import { useEffect, useState } from "react";

type Article = {
  id: number; title: string; body: string; author: string; published: string;
  tags: string[]; minutes: number; has_photo: boolean; alt: string;
};

export default function News({lang}: {lang: string}) {
  const [articles, setArticles] = useState<Article[]>();
  const [error, setError] = useState(false);
  const be = lang === "be";
  useEffect(() => {
    const request = new AbortController();
    fetch("/api/news", {signal: request.signal})
      .then(response => {
        if (!response.ok) throw new Error();
        return response.json();
      })
      .then(setArticles)
      .catch(() => { if (!request.signal.aborted) setError(true); });
    return () => request.abort();
  }, []);
  return <section className="wrap news-feed">
    <h1>{be ? "Навіны" : "Новости"}</h1>
    {!articles?.length && <p role="status">{error
      ? (be ? "Не ўдалося загрузіць навіны" : "Не удалось загрузить новости")
      : articles ? (be ? "Пакуль няма навін" : "Пока нет новостей") : (be ? "Загрузка…" : "Загрузка…")}</p>}
    {articles?.map(article => <article key={article.id}>
      <h2>{article.title}</h2>
      <p className="muted">{article.author} · <time dateTime={article.published}>
        {new Date(article.published).toLocaleDateString(be ? "be-BY" : "ru-RU")}
      </time> · {article.minutes} {be ? "хв." : "мин."}</p>
      {article.has_photo && <img loading="lazy" src={`/api/news/${article.id}/photo`} alt={article.alt} />}
      <p className="news-body">{article.body}</p>
      {article.tags.length > 0 && <ul>{article.tags.map(tag => <li key={tag}>{tag}</li>)}</ul>}
    </article>)}
  </section>;
}
