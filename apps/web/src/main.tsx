import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { journalApi } from "./api/client";
import "./styles.css";

const root = document.querySelector<HTMLElement>("#root");

if (!root) {
  throw new Error("The application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App api={journalApi} />
    </BrowserRouter>
  </StrictMode>,
);
