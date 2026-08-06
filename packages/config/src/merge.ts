import type { VeridixConfig } from "@veridix/contracts";
import { ConfigLayer, ConfigMergeResult } from "./types.js";

const NETWORK_RESTRICTION_ORDER = ["none", "proxy", "direct"] as const;

export function mergeConfig(
  layers: Array<{ layer: ConfigLayer; value: unknown }>,
  defaults: VeridixConfig,
): ConfigMergeResult {
  let merged: Record<string, unknown> = structuredClone(
    defaults,
  ) as unknown as Record<string, unknown>;
  const sources: Record<string, ConfigLayer> = {};
  const clipped: ConfigMergeResult["clipped"] = [];

  for (const key of Object.keys(flatten(merged))) {
    sources[key] = "defaults";
  }

  for (const { layer, value } of layers) {
    if (value === undefined || value === null) {
      continue;
    }
    const incoming = flatten(value);
    for (const [key, incomingValue] of Object.entries(incoming)) {
      const previous = getByPath(merged, key);
      const restriction = tightenSecurityField(key, previous, incomingValue);
      if (restriction) {
        if (restriction.clipped) {
          clipped.push({
            key,
            layer,
            reason: restriction.reason,
          });
          continue;
        }
        setByPath(merged, key, restriction.value);
        sources[key] = layer;
        continue;
      }
      setByPath(merged, key, incomingValue);
      sources[key] = layer;
    }
  }

  return {
    config: merged as unknown as VeridixConfig,
    sources,
    clipped,
  };
}

export function explainKey(
  result: ConfigMergeResult,
  key: string,
) {
  const clippedEntry = result.clipped.find((entry) => entry.key === key);
  return {
    key,
    value: getByPath(result.config, key),
    source: result.sources[key] ?? null,
    clippedReason: clippedEntry?.reason ?? null,
  };
}

function tightenSecurityField(
  key: string,
  previous: unknown,
  incoming: unknown,
): { value: unknown; clipped: boolean; reason: string } | null {
  if (previous === undefined) {
    return null;
  }
  if (key === "security.targetScope.allowed") {
    return intersectLists(key, previous, incoming, "allowed target scope");
  }
  if (key === "security.targetScope.excluded") {
    return unionLists(key, previous, incoming, "excluded target scope");
  }
  if (key === "security.dataPolicy.allowedLabels") {
    return intersectLists(key, previous, incoming, "data policy labels");
  }
  if (key === "security.sandbox.maxMemoryMb") {
    return minimumNumber(key, previous, incoming);
  }
  if (key === "security.sandbox.maxCpus") {
    return minimumNumber(key, previous, incoming);
  }
  if (key === "security.sandbox.network") {
    return mostRestrictiveNetwork(key, previous, incoming);
  }
  return null;
}

function intersectLists(
  key: string,
  previous: unknown,
  incoming: unknown,
  label: string,
) {
  if (!Array.isArray(previous) || !Array.isArray(incoming)) {
    return null;
  }
  if (previous.length === 0) {
    return null;
  }
  const next = previous.filter((item) => incoming.includes(item));
  if (JSON.stringify(next) !== JSON.stringify(incoming)) {
    return {
      value: next,
      clipped: false,
      reason: `${label} clipped to intersection of lower layers`,
    };
  }
  return null;
}

function unionLists(
  key: string,
  previous: unknown,
  incoming: unknown,
  label: string,
) {
  if (!Array.isArray(previous) || !Array.isArray(incoming)) {
    return null;
  }
  if (previous.length === 0) {
    return null;
  }
  const next = Array.from(new Set([...previous, ...incoming]));
  if (next.length !== incoming.length) {
    return {
      value: next,
      clipped: false,
      reason: `${label} expanded with lower-layer values`,
    };
  }
  return null;
}

function minimumNumber(key: string, previous: unknown, incoming: unknown) {
  if (typeof previous !== "number" || typeof incoming !== "number") {
    return null;
  }
  if (incoming > previous) {
    return {
      value: previous,
      clipped: true,
      reason: `higher layer cannot widen ${key}`,
    };
  }
  return null;
}

function mostRestrictiveNetwork(key: string, previous: unknown, incoming: unknown) {
  const prevIndex = NETWORK_RESTRICTION_ORDER.indexOf(previous as never);
  const incomingIndex = NETWORK_RESTRICTION_ORDER.indexOf(incoming as never);
  if (prevIndex < 0 || incomingIndex < 0) {
    return null;
  }
  if (incomingIndex > prevIndex) {
    return {
      value: previous,
      clipped: true,
      reason: `higher layer cannot widen ${key}`,
    };
  }
  return null;
}

function flatten(
  value: unknown,
  prefix = "",
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (value === null || typeof value !== "object") {
    if (prefix) {
      out[prefix] = value;
    }
    return out;
  }
  if (Array.isArray(value)) {
    if (prefix) {
      out[prefix] = value;
    }
    return out;
  }
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item !== null && typeof item === "object" && !Array.isArray(item)) {
      Object.assign(out, flatten(item, path));
    } else {
      out[path] = item;
    }
  }
  return out;
}

function getByPath(root: unknown, path: string): unknown {
  let current: unknown = root;
  for (const part of path.split(".")) {
    if (
      current === null ||
      typeof current !== "object" ||
      !(part in (current as Record<string, unknown>))
    ) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function setByPath(root: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split(".");
  let current = root;
  for (const part of parts.slice(0, -1)) {
    const next = current[part];
    if (next === null || typeof next !== "object") {
      current[part] = {};
    }
    current = current[part] as Record<string, unknown>;
  }
  current[parts[parts.length - 1]] = value;
}
