import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted fonts (OFL), bundled into /assets by the build: the CSP allows fonts only from 'self'.
// Source Serif 4 with its optical-size axis, upright and italic; Archivo by weight.
import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "@fontsource-variable/archivo/wght.css";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SealSprite } from "./components/Seal";
import "./tokens.css";
import "./styles.css";
import "./seal.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SealSprite />
    <ErrorBoundary whole>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
