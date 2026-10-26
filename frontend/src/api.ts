// Типы каталога услуг — зеркалят backend/app/data/services_stub.json.
// Когда каталог переедет в Postgres (неделя 2), контракт должен остаться тем же.

export type ChecklistItem = {
  item: string;
  where: string;
};

export type Source = {
  title: string;
  url?: string;
  point?: string;
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
};

export async function fetchServices(): Promise<Service[]> {
  const res = await fetch("/api/services");
  if (!res.ok) {
    throw new Error(`API вернул ${res.status}`);
  }
  return res.json();
}
