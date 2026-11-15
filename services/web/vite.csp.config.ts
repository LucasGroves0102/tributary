// Throwaway (never committed): vite preview sending production's CSP from deploy/headers.conf.
import { readFileSync } from "node:fs";
import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config";

const conf = readFileSync(new URL("./deploy/headers.conf", import.meta.url), "utf8");
const csp = /Content-Security-Policy "([^"]+)"/.exec(conf)![1]!;

export default mergeConfig(base, defineConfig({ preview: { headers: { "Content-Security-Policy": csp } } }));
