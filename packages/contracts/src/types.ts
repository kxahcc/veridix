export const PROFILE_NAMES = ["desktop", "lab", "server", "airgap"] as const;
export type ProfileName = (typeof PROFILE_NAMES)[number];

export type DataPolicyMode = "local" | "project_remote_allowed" | "remote";

export interface InferenceProfile {
  providerId: string;
  model: string;
  endpoint: string;
  apiKeyRef?: string;
  dataPolicy: DataPolicyMode;
  timeoutSeconds: number;
  maxContextTokens?: number;
  capabilities?: {
    toolCalling?: boolean;
    streaming?: boolean;
  };
  requestOptions?: {
    thinkingMode?: "enabled" | "disabled";
    toolChoice?: "auto" | "none" | "required";
  };
}

export interface TargetScope {
  allowed?: string[];
  excluded?: string[];
}

export interface SandboxLimits {
  maxMemoryMb?: number;
  maxCpus?: number;
  network?: "none" | "proxy" | "direct";
}

export interface DataPolicy {
  allowedLabels?: Array<"public" | "project" | "sensitive" | "secret">;
}

export interface VeridixConfig {
  version: 1;
  project: {
    id: string;
    name: string;
  };
  profile: ProfileName;
  runtime?: {
    dir?: string;
  };
  provider?: {
    inference?: InferenceProfile;
    embedding?: InferenceProfile;
    rerank?: InferenceProfile;
  };
  security?: {
    targetScope?: TargetScope;
    sandbox?: SandboxLimits;
    dataPolicy?: DataPolicy;
  };
}

export interface RuntimeProfile {
  name: ProfileName;
  label: string;
  dependencies: Array<
    | "node"
    | "python"
    | "docker"
    | "browser"
    | "mitmproxy"
    | "postgres"
    | "object-store"
  >;
}
