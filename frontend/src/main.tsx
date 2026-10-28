import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import "./brand.css";
import "./lang.css";
import "./polish.css";
import "./layout.css";
import "./kiosk.css";
import "./kiosk-list.css";
import "./kiosk-chat.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
