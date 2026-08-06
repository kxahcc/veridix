import type { InferenceProfile } from "@veridix/contracts";

export type ProviderStatus =
  | "ok"
  | "degraded"
  | "unavailable"
  | "not_configured";

export interface ProviderProbeResult {
  providerId: string;
  kind: "inference" | "embedding" | "rerank";
  status: ProviderStatus;
  detail: string;
  capabilities?: {
    models?: string[];
    dimensions?: number;
    supportsBatch?: boolean;
    dataPolicy?: string;
  };
}

export interface ProviderConfig {
  inference?: InferenceProfile;
  embedding?: InferenceProfile;
  rerank?: InferenceProfile;
}

export async function probeConfiguredProviders(
  config: ProviderConfig | undefined,
): Promise<ProviderProbeResult[]> {
  if (!config) {
    return [];
  }
  const results: ProviderProbeResult[] = [];
  const kinds: Array<"inference" | "embedding" | "rerank"> = [
    "inference",
    "embedding",
    "rerank",
  ];
  for (const kind of kinds) {
    const profile = config[kind];
    if (!profile) {
      continue;
    }
    results.push(await probeProvider(profile, kind));
  }
  return results;
}

export async function probeProvider(
  profile: InferenceProfile,
  kind: ProviderProbeResult["kind"],
): Promise<ProviderProbeResult> {
  try {
    if (kind === "inference") {
      const models = await probeModelList(profile);
      return {
        providerId: profile.providerId,
        kind,
        status: "ok",
        detail: `models: ${models.length}`,
        capabilities: {
          models,
          dataPolicy: profile.dataPolicy,
        },
      };
    }
    if (kind === "embedding") {
      const dimensions = await probeEmbeddingDimensions(profile);
      return {
        providerId: profile.providerId,
        kind,
        status: "ok",
        detail: `dimensions: ${dimensions}`,
        capabilities: {
          dimensions,
          dataPolicy: profile.dataPolicy,
        },
      };
    }
    const supportsBatch = await probeRerankBatch(profile);
    return {
      providerId: profile.providerId,
      kind,
      status: "ok",
      detail: `batch: ${supportsBatch ? "yes" : "no"}`,
      capabilities: {
        supportsBatch,
        dataPolicy: profile.dataPolicy,
      },
    };
  } catch (error) {
    return {
      providerId: profile.providerId,
      kind,
      status: "unavailable",
      detail: `${kind}_unavailable: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

async function probeModelList(profile: InferenceProfile): Promise<string[]> {
  for (const path of ["/models", "/v1/models"]) {
    try {
      const body = await requestJson(profile, path, { method: "GET" });
      const data = (body as { data?: Array<{ id?: string }> }).data;
      if (Array.isArray(data)) {
        const models = data
          .map((item) => item.id)
          .filter((id): id is string => Boolean(id))
          .sort();
        if (models.length > 0) {
          return models;
        }
      }
    } catch {
      // try next path
    }
  }
  throw new Error("no model list returned");
}

async function probeEmbeddingDimensions(
  profile: InferenceProfile,
): Promise<number> {
  const body = await requestJson(profile, "/embeddings", {
    method: "POST",
    body: JSON.stringify({ model: profile.model, input: ["probe"] }),
  });
  const data = (body as { data?: Array<{ embedding?: number[] }> }).data;
  const dimensions = data?.[0]?.embedding?.length;
  if (!dimensions) {
    throw new Error("no embedding dimensions returned");
  }
  return dimensions;
}

async function probeRerankBatch(profile: InferenceProfile): Promise<boolean> {
  const body = await requestJson(profile, "/rerank", {
    method: "POST",
    body: JSON.stringify({
      model: profile.model,
      query: "probe",
      documents: ["first", "second"],
    }),
  });
  const results = (body as { results?: unknown[] }).results;
  return Array.isArray(results) && results.length >= 2;
}

async function requestJson(
  profile: InferenceProfile,
  path: string,
  init: RequestInit,
): Promise<unknown> {
  const endpoint = profile.endpoint.replace(/\/+$/, "");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const apiKey = resolveSecret(profile.apiKeyRef);
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }
  const response = await fetch(`${endpoint}${path}`, {
    ...init,
    headers,
    signal: AbortSignal.timeout(profile.timeoutSeconds * 1000),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function resolveSecret(ref: string | undefined): string | null {
  if (!ref) {
    return null;
  }
  const [scheme, name] = ref.split(":");
  if (scheme === "env" && name) {
    return process.env[name] ?? null;
  }
  return null;
}
