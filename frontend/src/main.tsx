import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App.tsx";
import { store } from "./store.ts";
import "../../mockups/folio.css";
import "./styles/pages.css";

document.documentElement.dataset.theme = store.theme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
