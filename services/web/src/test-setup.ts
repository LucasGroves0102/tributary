// Runs before every test file (vite.config.ts test.setupFiles).
import { afterEach } from "vitest";

import { clearToasts } from "./components/Toasts";

// toasts live in a module store: one test's must not show in the next
afterEach(() => clearToasts());
