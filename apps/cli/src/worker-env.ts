import type { VeridixConfig } from "@veridix/contracts";

export interface WorkerEnv {
  providerEndpoint?: string;
  providerModel?: string;
  apiKeyRef?: string;
  autopilot: boolean;
  streaming: boolean;
}

export function workerEnvFromConfig(config: VeridixConfig): WorkerEnv {
  const inference = config.provider?.inference;
  const endpoint = inference?.endpoint?.trim();
  const model = inference?.model?.trim();
  return {
    providerEndpoint: endpoint || undefined,
    providerModel: model || undefined,
    apiKeyRef: inference?.apiKeyRef || undefined,
    autopilot: Boolean(endpoint && model),
    streaming: Boolean(inference?.capabilities?.streaming),
  };
}

export function applyWorkerEnv(config: VeridixConfig): void {
  const env = workerEnvFromConfig(config);
  if (env.providerEndpoint) {
    process.env.VERIDIX_PROVIDER_ENDPOINT = env.providerEndpoint;
  }
  if (env.providerModel) {
    process.env.VERIDIX_PROVIDER_MODEL = env.providerModel;
  }
  if (env.apiKeyRef) {
    process.env.VERIDIX_PROVIDER_API_KEY_REF = env.apiKeyRef;
  }
  if (env.autopilot) {
    process.env.VERIDIX_WORKER_AUTOPILOT = "1";
    process.env.VERIDIX_WORKER_STREAMING = env.streaming ? "1" : "0";
  }
}
