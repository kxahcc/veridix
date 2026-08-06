import { ControlClient } from "@veridix/sdk-typescript";

const runtimeControlUrl = (
  globalThis as { __VERIDIX_CONTROL_URL__?: string }
).__VERIDIX_CONTROL_URL__;
const baseUrl =
  runtimeControlUrl ??
  (import.meta.env.VITE_CONTROL_URL as string | undefined) ??
  "http://127.0.0.1:8787";

export const control = new ControlClient(baseUrl);
export const CONTROL_URL = baseUrl;

export function setControlToken(token: string | null): void {
  control.setToken(token);
}
