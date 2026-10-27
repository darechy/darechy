import { useEffect, useState } from "react";

export type AsyncState<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: T };

export function useAsync<T>(load: () => Promise<T>): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    load()
      .then((data) => {
        if (alive) setState({ kind: "ok", data });
      })
      .catch((err: unknown) => {
        if (alive) setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}
