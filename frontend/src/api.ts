// Типы зеркалят backend/app/models/schemas.py. Менять синхронно с бэкендом.

export type ChecklistItem = {
  item: string;
  where: string;
};

export type Source = {
  title: string;
  url?: string | null;
  point?: string | null;
};

export type Service = {
  id: string;
  title: string;
  situation: string;
  summary: string;
  who: string;
  amount: string;
  deadline: string;
  checklist: ChecklistItem[];
  sources: Source[];
  // Добавлено синхронно с backend/app/models/schemas.py:
  // куда обращаться (орган) и сколько стоит. В перечне административных
  // процедур эти два блока заполнены всегда, поэтому их видно на карточке.
  authority: string;
  cost: string;
  // «проверена» | «черновик»: сгенерированные карточки не показываем до ревью.
  status: string;
  // Триггеры ИИ-роутера (на портале не нужны, но контракт держим в синхроне).
  keywords: string[];
};

/** Услуга, которую можно показывать: черновики ждут проверки человеком. */
export const isPublished = (service: Service): boolean => service.status !== 'черновик';

export type Situation = {
  id: string;
  title: string;
  service_ids: string[];
};

export type Citation = {
  chunk_id: string;
  doc_title: string;
  doc_number?: string | null;
  doc_date?: string | null;
  location?: string | null;
  url?: string | null;
  quote: string;
};

export type ChatResponse = {
  answer: string;
  citations: Citation[];
  refusal: boolean;
  suggested_services: string[];
  latency_ms: number;
};

export type ChatRequest = {
  message: string;
  session_id?: string;
  service_id?: string;
  top_k?: number;
};

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.detail?.reason === "string") return body.detail.reason;
  } catch {
    // тело не JSON — отдаём статус
  }
  return `API вернул ${res.status}`;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export const fetchServices = () => getJson<Service[]>("/api/services");
export const fetchSituations = () => getJson<Situation[]>("/api/situations");

export async function postChat(body: ChatRequest): Promise<ChatResponse> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<ChatResponse>;
}

export async function transcribe(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("file", blob, "voice.webm");
  const res = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  if (!res.ok) throw new Error(await readError(res));
  const data = (await res.json()) as { text: string };
  return data.text;
}
