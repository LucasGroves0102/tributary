import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted fonts (OFL), bundled into /assets by the build: the CSP allows fonts only from 'self'.
// Two families (.interface-design/system.md §2.1): Archivo, variable in weight and width (codes are set
// at 82% width), upright and italic, for the interface and every figure; Source Serif 4 with its
// optical-size axis for the filing's own words and the wordmark.
import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "@fontsource-variable/archivo/wdth.css";
import "@fontsource-variable/archivo/wdth-italic.css";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SealSprite } from "./components/Seal";
import { installMotion } from "./motion";
import "./tokens.css";
import "./styles.css";
import "./seal.css";
import "./ui.css";
import "./shell.css";
import "./company.css";

installMotion();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SealSprite />
    <ErrorBoundary whole>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
