import { useEffect, useState } from "react";

// Hash-роутинг без зависимостей: работает за любым статик-хостингом и в киоске.
export type Route =
  | { name: "home"; section?: "situations" | "services" }
  | { name: "situation"; id: string }
  | { name: "service"; id: string }
  | { name: "notfound" };

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { name: "home" };
  const [head, arg] = parts;
  if (head === "situations" && !arg) return { name: "home", section: "situations" };
  if (head === "services" && !arg) return { name: "home", section: "services" };
  if (head === "situation" && arg) return { name: "situation", id: decodeURIComponent(arg) };
  if (head === "service" && arg) return { name: "service", id: decodeURIComponent(arg) };
  return { name: "notfound" };
}

export function useRoute(): { route: Route; hash: string } {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return { route: parseHash(hash), hash };
}
