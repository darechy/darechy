import { createContext } from "react";

import type { Service, Situation } from "./api";
import type { AsyncState } from "./hooks";

export type OpenAssistantOptions = { text?: string; serviceId?: string };

export type AppContext = {
  services: AsyncState<Service[]>;
  situations: AsyncState<Situation[]>;
  openAssistant: (options?: OpenAssistantOptions) => void;
  kiosk: boolean;
};

export const AppCtx = createContext<AppContext>({
  services: { kind: "loading" },
  situations: { kind: "loading" },
  openAssistant: () => {},
  kiosk: false,
});
