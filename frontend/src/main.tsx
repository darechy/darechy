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
import "./kiosk-fix.css";
import "./kiosk-bar.css";
import "./mobile.css";
import "./input-fit.css";
import "./inline-chat.css";
import "./glass.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
