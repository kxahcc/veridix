#!/usr/bin/env node
import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { Command } from "commander";
import {
  DEFAULT_CONFIG,
  createConfigSnapshot,
  explainKey,
} from "@veridix/config";
import { PROFILE_NAMES, type ProfileName } from "@veridix/contracts";
import {
  ControlClient,
  probeProvider,
  runDoctorChecks,
  runSelfTest,
} from "@veridix/sdk-typescript";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";
import {
  initConfig,
  loadConfig,
  projectConfigPath,
  setProfile,
  userConfigPath,
} from "./config-files.js";
import { findPython, findRepoRoot } from "./paths.js";
import { Supervisor } from "./supervisor.js";
import { applyWorkerEnv } from "./worker-env.js";
import { attachOnce } from "./attach.js";
import { downloadReport } from "./report.js";

interface GlobalOptions {
  json?: boolean;
  quiet?: boolean;
  verbose?: boolean;
}

const program = new Command();
program
  .name("veridix")
  .description("Veridix command line")
  .version("0.1.0")
  .option("--json", "emit JSON output")
  .option("-q, --quiet", "minimal output")
  .option("-v, --verbose", "verbose output");

program
  .command("init")
  .description("create project config, runtime directories, and SecretRefs")
  .action(async (options, command: Command) => {
    const root = findRepoRoot();
    const created = initConfig(root);
    const result = { root, created, profile: DEFAULT_CONFIG.profile };
    print(result);
  });

const profile = program
  .command("profile")
  .description("manage runtime profiles");

profile
  .command("list")
  .action((options, command: Command) => {
    const profiles = PROFILE_NAMES.map((name) => ({
      name,
      label: name,
    }));
    print(profiles);
  });

profile
  .command("use")
  .argument("<profile>", "desktop | lab | server | airgap")
  .action((value: string, options, command: Command) => {
    if (!PROFILE_NAMES.includes(value as never)) {
      throw new Error(`unknown profile ${value}; expected ${PROFILE_NAMES.join(", ")}`);
    }
    const root = findRepoRoot();
    const config = setProfile(root, value as ProfileName);
    print({ profile: config.profile, path: projectConfigPath(root) });
  });

const configCommand = program
  .command("config")
  .description("inspect merged configuration");

configCommand
  .command("explain")
  .description("show the final value, source layer, and any security clipping")
  .argument("<key>", "dotted config key, for example security.sandbox.network")
  .action((key: string) => {
    const root = findRepoRoot();
    const merged = loadConfig(root);
    print(explainKey(merged, key));
  });

program
  .command("doctor")
  .description("check runtime capabilities")
  .option("--bundle <path>", "write checks as a support bundle")
  .action(async (options: { bundle?: string }) => {
    const root = findRepoRoot();
    const runtimeDir = path.join(root, "runtime");
    const checks = await runDoctorChecks({
      runtimeDir,
      controlHealthUrl: `http://127.0.0.1:8787/healthz`,
      provider: loadConfig(root).config.provider,
    });
    if (options.bundle) {
      writeFileSync(
        options.bundle,
        JSON.stringify(
          {
            generated_at: new Date().toISOString(),
            checks,
          },
          null,
          2,
        ),
        "utf8",
      );
    }
    print(checks);
  });

program
  .command("up")
  .description("start control-plane and agent-worker; stays attached as supervisor")
  .option("--check", "run tool pack preflight plan only", false)
  .option("--packs <names...>", "Tool Packs to ensure")
  .option("--no-build", "do not build the tools image when missing", false)
  .option("--fetch", "fetch optional tool binaries before building", false)
  .option("--registry <host>", "private registry to pull the tools image from")
  .action(
    async (options: {
      check: boolean;
      packs?: string[];
      noBuild: boolean;
      fetch: boolean;
      registry?: string;
    }) => {
      const root = findRepoRoot();
      const python = findPython(root);
      const runtimeDir = path.join(root, "runtime");
      const preflightArgs = ["-m", "services.tool_pack.preflight"];
      if (options.check) {
        preflightArgs.push("--dry-run");
      }
      if (options.packs?.length) {
        preflightArgs.push("--packs", ...options.packs);
      }
      if (!options.noBuild) {
        preflightArgs.push("--build");
      }
      if (options.fetch) {
        preflightArgs.push("--fetch");
      }
      if (options.registry) {
        preflightArgs.push("--registry", options.registry);
      }
      if (!options.check) {
        preflightArgs.push(
          "--snapshot-out",
          path.join(runtimeDir, "tool-environment.json"),
        );
      }
      const preflight = JSON.parse(
        (
          await execFileAsync("python", preflightArgs, {
            cwd: root,
            timeout: 120_000,
          })
        ).stdout,
      );
      print({ tool_packs: preflight });
      if (options.check) {
        return;
      }
      const unhealthy = preflight.filter(
        (pack: { health: string }) => pack.health !== "ok",
      );
      if (unhealthy.length) {
        throw new Error(
          `tool packs not healthy: ${unhealthy
            .map((pack: { name: string }) => pack.name)
            .join(", ")}`,
        );
      }
      applyWorkerEnv(loadConfig(root).config);
      const supervisor = new Supervisor({
        rootDir: root,
        runtimeDir,
        controlCommand: [python, "-m", "services.control_plane.app.main"],
        agentCommand: [python, "-m", "services.agent_runtime.app.main"],
        controlHealthUrl: "http://127.0.0.1:8787/healthz",
        agentHeartbeatFile: path.join(runtimeDir, "state", "agent-worker.heartbeat"),
      });
      const status = await supervisor.start();
      print(status);
      await new Promise<void>((resolve) => {
        const shutdown = () => {
          void supervisor.stop().then(() => resolve());
        };
        process.once("SIGINT", shutdown);
        process.once("SIGTERM", shutdown);
      });
    },
  );

program
  .command("down")
  .description("stop local services")
  .action(async () => {
    const root = findRepoRoot();
    const runtimeDir = path.join(root, "runtime");
    const python = findPython(root);
    const supervisor = new Supervisor({
      rootDir: root,
      runtimeDir,
      controlCommand: [python, "-m", "services.control_plane.app.main"],
      agentCommand: [python, "-m", "services.agent_runtime.app.main"],
      controlHealthUrl: "http://127.0.0.1:8787/healthz",
      agentHeartbeatFile: path.join(runtimeDir, "state", "agent-worker.heartbeat"),
    });
    const status = await supervisor.stop();
    print(status);
  });

program
  .command("status")
  .description("show local service status")
  .action(async () => {
    const root = findRepoRoot();
    const runtimeDir = path.join(root, "runtime");
    const python = findPython(root);
    const supervisor = new Supervisor({
      rootDir: root,
      runtimeDir,
      controlCommand: [python, "-m", "services.control_plane.app.main"],
      agentCommand: [python, "-m", "services.agent_runtime.app.main"],
      controlHealthUrl: "http://127.0.0.1:8787/healthz",
      agentHeartbeatFile: path.join(runtimeDir, "state", "agent-worker.heartbeat"),
    });
    const state = supervisor.readState() ?? { processes: {}, workerStatus: "stopped", updatedAt: "" };
    let devStackPids: Record<string, number> = {};
    try {
      const devStack = JSON.parse(
        readFileSync(path.join(runtimeDir, "dev-stack.json"), "utf-8"),
      ) as { control_pid?: number; worker_pid?: number };
      devStackPids = {
        control: devStack.control_pid ?? 0,
        worker: devStack.worker_pid ?? 0,
      };
    } catch {
      devStackPids = {};
    }
    let controlOnline = false;
    try {
      const response = await fetch(
        "http://127.0.0.1:8787/healthz",
        { signal: AbortSignal.timeout(2000) },
      );
      controlOnline = response.ok;
    } catch {
      controlOnline = false;
    }
    const heartbeatFile = path.join(
      runtimeDir,
      "state",
      "agent-worker.heartbeat",
    );
    let heartbeatFresh = false;
    if (existsSync(heartbeatFile)) {
      try {
        heartbeatFresh =
          Date.now() - statSync(heartbeatFile).mtimeMs <= 45_000;
      } catch {
        heartbeatFresh = false;
      }
    }
    const processes = { ...(state.processes ?? {}) };
    if (controlOnline) {
      processes["control-plane"] = {
        status: "running",
        restarts: state.processes?.["control-plane"]?.restarts ?? 0,
        pid:
          state.processes?.["control-plane"]?.pid ??
          devStackPids.control ??
          null,
      };
    }
    if (heartbeatFresh) {
      processes["agent-worker"] = {
        status: "running",
        restarts: state.processes?.["agent-worker"]?.restarts ?? 0,
        pid:
          state.processes?.["agent-worker"]?.pid ??
          devStackPids.worker ??
          null,
      };
    }
    const workerStatus = heartbeatFresh
      ? "ok"
      : state.workerStatus;
    print({
      processes,
      workerStatus,
      updatedAt: new Date().toISOString(),
    });
  });

program
  .command("self-test")
  .description("run tiered self-test")
  .action(async (options, command: Command) => {
    const root = findRepoRoot();
    const runtimeDir = path.join(root, "runtime");
    const result = await runSelfTest({
      runtimeDir,
      controlHealthUrl: "http://127.0.0.1:8787/healthz",
      provider: loadConfig(root).config.provider,
    });
    print(result);
  });

const controlUrl = () =>
  process.env.VERIDIX_CONTROL_URL ?? "http://127.0.0.1:8787";
const newKey = (provided?: string) => provided ?? randomUUID();
const execFileAsync = promisify(execFile);

const project = program.command("project").description("manage projects");

project
  .command("list")
  .description("list projects in the control plane")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.listProjects());
  });

project
  .command("create")
  .argument("<name>", "project name")
  .description("create a project in the control plane")
  .action(async (name: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.createProject(name));
  });

const provider = program
  .command("provider")
  .description("manage and probe OpenAI-compatible providers");

provider
  .command("list")
  .description("list registered model providers")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.listProviders());
  });

provider
  .command("probe")
  .description("probe an OpenAI-compatible provider")
  .argument("<endpoint>", "provider base URL")
  .argument("<model>", "model name")
  .option("--api-key-ref <ref>", "api key env reference, for example env:OPENAI_API_KEY")
  .option("--timeout <seconds>", "timeout in seconds", "10")
  .action(
    async (
      endpoint: string,
      model: string,
      options: { apiKeyRef?: string; timeout: string },
    ) => {
      print(
        await probeProvider(
          {
            providerId: "cli",
            model,
            endpoint,
            apiKeyRef: options.apiKeyRef,
            dataPolicy: "local",
            timeoutSeconds: Number(options.timeout),
          },
          "inference",
        ),
      );
    },
  );

provider
  .command("register")
  .argument("<provider-id>", "provider id")
  .requiredOption("--endpoint <url>", "provider base URL")
  .requiredOption("--model <model>", "default model name")
  .option("--api-key-ref <ref>", "api key environment reference")
  .option("--reasoning-effort <level>", "none | low | medium | high")
  .option("--retries <n>", "provider retry count", "5")
  .option("--streaming", "enable streaming", false)
  .option("--max-tokens <n>", "max completion tokens")
  .action(
    async (
      providerId: string,
      options: {
        endpoint: string;
        model: string;
        apiKeyRef?: string;
        reasoningEffort?: string;
        retries: string;
        streaming: boolean;
        maxTokens?: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.registerProvider({
          provider_id: providerId,
          endpoint: options.endpoint,
          model: options.model,
          status: "ok",
          api_key_ref: options.apiKeyRef || undefined,
          reasoning_effort:
            options.reasoningEffort === "none"
              ? undefined
              : options.reasoningEffort,
          retries: Number(options.retries) || undefined,
          streaming: options.streaming,
          max_tokens: options.maxTokens
            ? Number(options.maxTokens)
            : undefined,
        }),
      );
    },
  );

provider
  .command("default")
  .argument("<provider-id>", "provider id")
  .requiredOption("--endpoint <url>", "provider base URL")
  .requiredOption("--model <model>", "default model name")
  .option("--api-key-ref <ref>", "api key environment reference")
  .action(
    async (
      providerId: string,
      options: {
        endpoint: string;
        model: string;
        apiKeyRef?: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.setProviderDefault({
          provider_id: providerId,
          endpoint: options.endpoint,
          model: options.model,
          api_key_ref: options.apiKeyRef || undefined,
        }),
      );
    },
  );

program
  .command("target")
  .argument("<project-id>", "project id")
  .requiredOption("--url <url>", "target URL")
  .action(async (projectId: string, options: { url: string }) => {
    const client = new ControlClient(controlUrl());
    print(await client.createTarget(projectId, options.url));
  });

program
  .command("mission")
  .argument("<project-id>", "project id")
  .argument("<name>", "mission name")
  .option("--spec <json>", "mission spec as JSON")
  .option("--spec-file <path>", "read mission spec from a JSON file")
  .option("--template <name>", "mission template: scanner-verify | code-audit")
  .option("--target-id <id>", "target id to bind to this mission")
  .option("--required-categories <list>", "comma separated categories")
  .option(
    "--min-severity <level>",
    "minimum severity for scanner-verify/code-audit",
  )
  .option("--require-evidence", "require verifiable evidence", true)
  .option("--dedupe", "deduplicate repeated findings", true)
  .option("--block-conflicts", "block verification on negative evidence", true)
  .option("--scanner-tools <list>", "comma separated scanner tools")
  .option("--tool-args <json>", "tool argument defaults as JSON")
  .option("--tool-args-file <path>", "read tool argument defaults from a JSON file")
  .option("--forced-tool-args <json>", "tool arguments that override model input")
  .option("--forced-tool-args-file <path>", "read forced tool arguments from a JSON file")
  .option("--loop-profiles <json>", "per-role Loop Profile overrides as JSON")
  .option("--loop-profiles-file <path>", "read per-role Loop Profile overrides from a JSON file")
  .option("--loop-preset <id>", "apply a named Loop Profile preset (nikto-focused, web-scan, code-audit, ...)")
  .action(
    async (
      projectId: string,
      name: string,
      options: {
        spec?: string;
        specFile?: string;
        template?: string;
        targetId?: string;
        requiredCategories?: string;
        minSeverity?: string;
        requireEvidence?: boolean;
        dedupe?: boolean;
        blockConflicts?: boolean;
        scannerTools?: string;
        toolArgs?: string;
        toolArgsFile?: string;
        forcedToolArgs?: string;
        forcedToolArgsFile?: string;
        loopProfiles?: string;
        loopProfilesFile?: string;
        loopPreset?: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      const spec = options.specFile
        ? JSON.parse(readFileSync(options.specFile, "utf-8"))
        : options.spec
          ? JSON.parse(options.spec)
          : {};
      const root = findRepoRoot();
      spec.config_hash = createConfigSnapshot(loadConfig(root).config).hash;
      if (options.targetId) {
        spec.target_id = options.targetId;
      }
      if (options.loopProfiles) {
        spec.loop_profiles = JSON.parse(options.loopProfiles);
      }
      if (options.loopProfilesFile) {
        const fs = await import("node:fs/promises");
        spec.loop_profiles = JSON.parse(
          await fs.readFile(options.loopProfilesFile, "utf8"),
        );
      }
      if (options.loopPreset) {
        const presets = await client.listLoopPresets();
        const preset = presets[options.loopPreset] as
          | { loop_overrides?: Record<string, Record<string, unknown>> }
          | undefined;
        if (!preset) {
          throw new Error(
            `unknown loop preset ${options.loopPreset}; ` +
              `available: ${Object.keys(presets).join(", ")}`,
          );
        }
        const user =
          (spec.loop_profiles as
            | Record<string, Record<string, unknown>>
            | undefined) ?? {};
        const presetOverrides = preset.loop_overrides ?? {};
        const merged: Record<string, Record<string, unknown>> = {};
        for (const role of new Set([
          ...Object.keys(presetOverrides),
          ...Object.keys(user),
        ])) {
          merged[role] = {
            ...(presetOverrides[role] ?? {}),
            ...(user[role] ?? {}),
          };
        }
        spec.loop_profiles = merged;
      }
      if (options.template === "scanner-verify") {
        spec.mode = "multi_role";
        spec.role_template = "scanner_verify";
        spec.required_categories = (
          options.requiredCategories ?? ""
        )
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        if (options.minSeverity) {
          spec.min_severity = options.minSeverity;
        }
        if (options.requireEvidence === false) {
          spec.require_evidence = false;
        }
        if (options.dedupe === false) {
          spec.dedupe = false;
        }
        if (options.blockConflicts === false) {
          spec.conflict_blocks = false;
        }
        if (options.scannerTools) {
          spec.scanner_tools = options.scannerTools
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        }
        spec.allowed_tools = [
          ...(spec.scanner_tools as string[]),
          "run.finish",
        ];
        if (options.toolArgs) {
          spec.tool_args = JSON.parse(options.toolArgs);
        }
        if (options.toolArgsFile) {
          const fs = await import("node:fs/promises");
          spec.tool_args = JSON.parse(
            await fs.readFile(options.toolArgsFile, "utf8"),
          );
        }
        if (options.forcedToolArgs) {
          spec.forced_tool_args = JSON.parse(options.forcedToolArgs);
        }
        if (options.forcedToolArgsFile) {
          const fs = await import("node:fs/promises");
          spec.forced_tool_args = JSON.parse(
            await fs.readFile(options.forcedToolArgsFile, "utf8"),
          );
        }
      } else if (options.template === "code-audit") {
        spec.mode = "multi_role";
        spec.role_template = "code_audit";
        spec.required_categories = (
          options.requiredCategories ?? "security,HardcodedSecret"
        )
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        spec.min_severity = options.minSeverity ?? "low";
        spec.code_tools = (
          options.scannerTools ?? "code.sast.semgrep,code.secrets.detect"
        )
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        spec.scanner_tools = [...spec.code_tools];
        spec.allowed_tools = [
          ...spec.code_tools,
          "run.finish",
        ];
        if (options.toolArgs) {
          spec.tool_args = JSON.parse(options.toolArgs);
        }
        if (options.toolArgsFile) {
          const fs = await import("node:fs/promises");
          spec.tool_args = JSON.parse(
            await fs.readFile(options.toolArgsFile, "utf8"),
          );
        }
        if (options.forcedToolArgs) {
          spec.forced_tool_args = JSON.parse(options.forcedToolArgs);
        }
        if (options.forcedToolArgsFile) {
          const fs = await import("node:fs/promises");
          spec.forced_tool_args = JSON.parse(
            await fs.readFile(options.forcedToolArgsFile, "utf8"),
          );
        }
      }
      print(await client.createMission(projectId, name, spec));
    },
  );

const run = program.command("run").description("control plane run lifecycle");

program
  .command("report")
  .description("export a run report as markdown, html, or the bundle zip")
  .argument("<run-id>", "run id")
  .option("--out <dir>", "output directory", ".")
  .option("--format <format>", "markdown, html, or bundle", "bundle")
  .action(
    async (
      runId: string,
      options: { out: string; format: string },
    ) => {
      if (options.format === "markdown" || options.format === "html") {
        const extension = options.format === "html" ? "html" : "md";
        const target = path.join(
          options.out,
          `report-${runId}.${extension}`,
        );
        mkdirSync(path.dirname(path.resolve(target)), { recursive: true });
        const suffix =
          options.format === "html"
            ? "/report.html"
            : "/report";
        const response = await fetch(
          `${controlUrl()}/api/v1/runs/${encodeURIComponent(runId)}${suffix}`,
        );
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        writeFileSync(target, await response.text(), "utf-8");
        print({ report: target, format: options.format });
        return;
      }
    const outPath = await downloadReport(runId, controlUrl(), options.out);
      print({ report: outPath, format: "bundle" });
    },
  );

run
  .command("start")
  .argument("<mission-id>", "mission id")
  .option("--idempotency-key <key>", "idempotency key")
  .action(
    async (missionId: string, options: { idempotencyKey?: string }) => {
      const client = new ControlClient(controlUrl());
      print(await client.startRun(missionId, newKey(options.idempotencyKey)));
    },
  );

run
  .command("status")
  .argument("<run-id>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.getRun(runId));
  });

run
  .command("events")
  .argument("<run-id>", "run id")
  .option("--after <cursor>", "event cursor", "0")
  .action(
    async (runId: string, options: { after: string }) => {
      const client = new ControlClient(controlUrl());
      print(await client.getEvents(runId, Number(options.after)));
    },
  );

run
  .command("trace")
  .argument("<run-id>", "run id")
  .option("--out <path>", "write trace JSON to this path")
  .action(
    async (runId: string, options: { out?: string }) => {
      const client = new ControlClient(controlUrl());
      const trace = await client.requestPublic(
        `/api/v1/runs/${encodeURIComponent(runId)}/trace`,
      );
      if (options.out) {
        const target = path.resolve(options.out);
        mkdirSync(path.dirname(target), { recursive: true });
        writeFileSync(
          target,
          JSON.stringify(trace, null, 2),
          "utf-8",
        );
        print({ trace: target });
      } else {
        print(trace);
      }
    },
  );

run
  .command("attach")
  .argument("<run-id>", "run id")
  .option("--after <cursor>", "event cursor", "0")
  .option("--once", "print available events and exit", false)
  .option(
    "--interactive",
    "interactive session: send pause/resume/message commands",
    false,
  )
  .option("--poll-interval <ms>", "poll interval", "1000")
  .action(
    async (
      runId: string,
      options: {
        after: string;
        once: boolean;
        interactive: boolean;
        pollInterval: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      if (options.interactive) {
        const { attachInteractive } = await import(
          "./attach-interactive.js"
        );
        await attachInteractive(client, runId, {
          pollInterval: Number(options.pollInterval),
        });
        return;
      }
      let cursor = Number(options.after);
      let terminal = false;
      while (!terminal) {
        const result = await attachOnce(client, runId, cursor);
        for (const event of result.events) {
          print(event);
        }
        cursor = result.cursor;
        terminal = result.terminal;
        if (options.once) {
          break;
        }
        if (!terminal) {
          await delay(Number(options.pollInterval));
        }
      }
    },
  );

run
  .command("observations")
  .argument("<run-id>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.getWebObservations(runId));
  });

run
  .command("findings")
  .argument("<run-id>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.listFindings(runId));
  });

for (const command of ["pause", "resume", "cancel"] as const) {
  run
    .command(command)
    .argument("<run-id>", "run id")
    .option("--idempotency-key <key>", "idempotency key")
    .action(
      async (runId: string, options: { idempotencyKey?: string }) => {
        const client = new ControlClient(controlUrl());
        print(await client.runCommand(runId, command, newKey(options.idempotencyKey)));
      },
    );
}

run
  .command("fork")
  .argument("<run-id>", "run id")
  .option("--idempotency-key <key>", "idempotency key")
  .action(async (runId: string, options: { idempotencyKey?: string }) => {
    const client = new ControlClient(controlUrl());
    print(await client.forkRun(runId, newKey(options.idempotencyKey)));
  });

run
  .command("takeover")
  .argument("<run-id>", "run id")
  .requiredOption("--taken-by <name>", "person taking over")
  .option("--reason <text>", "reason for takeover")
  .option("--idempotency-key <key>", "idempotency key")
  .action(
    async (
      runId: string,
      options: { takenBy: string; reason?: string; idempotencyKey?: string },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.takeoverRun(
          runId,
          options.takenBy,
          newKey(options.idempotencyKey),
          options.reason ?? "",
        ),
      );
    },
  );

run
  .command("golden")
  .description("run the Reference Golden fixture through a provider")
  .requiredOption("--endpoint <url>", "OpenAI-compatible endpoint")
  .requiredOption("--model <name>", "model name")
  .requiredOption("--target <url>", "target URL")
  .option("--mission <text>", "mission prompt", "find an exposed admin panel")
  .option("--api-key-ref <ref>", "api key env reference, for example env:OPENAI_API_KEY")
  .option("--max-turns <n>", "max turns", "5")
  .option("--thinking-mode <mode>", "thinking mode: enabled | disabled")
  .option("--tool-choice <choice>", "tool choice: auto | none | required")
  .option("--dry-run", "validate inputs without calling the provider")
  .action(
    async (options: {
      endpoint: string;
      model: string;
      target: string;
      mission: string;
      apiKeyRef?: string;
      maxTurns: string;
      thinkingMode?: string;
      toolChoice?: string;
      dryRun: boolean;
    }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.agent_runtime.golden_cli",
        "--run-id",
        `golden_${Date.now()}`,
        "--mission",
        options.mission,
        "--target",
        options.target,
        "--behavior",
        `behavior_golden_${Date.now()}`,
        "--endpoint",
        options.endpoint,
        "--model",
        options.model,
        "--max-turns",
        options.maxTurns,
      ];
      if (options.apiKeyRef) {
        args.push("--api-key-ref", options.apiKeyRef);
      }
      if (options.thinkingMode) {
        args.push("--thinking-mode", options.thinkingMode);
      }
      if (options.toolChoice) {
        args.push("--tool-choice", options.toolChoice);
      }
      if (options.dryRun) {
        args.push("--dry-run");
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 120_000,
      });
      print(JSON.parse(stdout));
    },
  );

program
  .command("bench")
  .description("run local benchmark suites")
  .option("--scenario <id>", "scenario id", "webappsec")
  .option("--runs <n>", "runs", "1")
  .option("--suite <name>", "rag | role", "rag")
  .option("--dry-run", "validate inputs without running", false)
  .action(
    async (options: {
      scenario: string;
      runs: string;
      suite: string;
      dryRun: boolean;
    }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.research_service.bench_cli",
        "--scenario",
        options.scenario,
        "--runs",
        options.runs,
        "--suite",
        options.suite,
      ];
      if (options.dryRun) {
        args.push("--dry-run");
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 120_000,
      });
      print(JSON.parse(stdout));
    },
  );

program
  .command("trace")
  .description("inspect a run's event trajectory")
  .argument("<run-id>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    const events = await client.getEvents(runId, 0);
    const byType: Record<string, number> = {};
    for (const event of events) {
      const type = String(event.event_type ?? "unknown");
      byType[type] = (byType[type] ?? 0) + 1;
    }
    const policies = events
      .filter((event) => event.event_type === "graph.started")
      .flatMap(
        (event) =>
          (event.payload.roles as
            | Array<{
                role_id: string;
                budget: Record<string, unknown>;
              }>
            | undefined) ?? [],
      );
    print({
      run_id: runId,
      event_count: events.length,
      by_type: byType,
      policies,
    });
  });

program
  .command("upgrade")
  .description("check pinned runtime and container versions")
  .option("--check", "show current pinned versions", false)
  .action(() => {
    const root = findRepoRoot();
    const versions = JSON.parse(
      readFileSync(
        path.join(root, "deploy", "manifests", "versions.json"),
        "utf8",
      ),
    );
    print({
      runtime: versions.runtime,
      container: versions.container,
      note: versions.note,
    });
  });

const pack = program
  .command("pack")
  .description("manage Tool Packs");

pack
  .command("list")
  .action(async () => {
    const root = findRepoRoot();
    const { stdout } = await execFileAsync(
      "python",
      ["-m", "services.tool_pack.pack_cli", "list"],
      { cwd: root, timeout: 30_000 },
    );
    print(JSON.parse(stdout));
  });

pack
  .command("show")
  .argument("<name>", "pack name")
  .action(async (name: string) => {
    const root = findRepoRoot();
    const { stdout } = await execFileAsync(
      "python",
      ["-m", "services.tool_pack.pack_cli", "show", name],
      { cwd: root, timeout: 30_000 },
    );
    print(JSON.parse(stdout));
  });

pack
  .command("install")
  .argument("<name>", "pack name")
  .option("--dry-run", "validate without probing the image", false)
  .action(
    async (name: string, options: { dryRun: boolean }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.tool_pack.pack_cli",
        "install",
        name,
      ];
      if (options.dryRun) {
        args.push("--dry-run");
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 120_000,
      });
      print(JSON.parse(stdout));
    },
  );

pack
  .command("export")
  .option("--out <path>", "output tar.gz path")
  .option("--dry-run", "validate without exporting", false)
  .action(
    async (options: { out: string; dryRun: boolean }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.tool_pack.pack_cli",
        "export",
        "--out",
        options.out,
      ];
      if (options.dryRun) {
        args.push("--dry-run");
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 1800_000,
      });
      print(JSON.parse(stdout));
    },
  );

pack
  .command("airgap")
  .option("--out <path>", "output airgap zip")
  .option("--desktop-zip <path>", "desktop product zip")
  .option("--tools-tar <path>", "offline tools tar.gz")
  .option("--key <hex>", "signing private key hex")
  .option("--sbom <path>", "optional sbom json")
  .option("--versions <path>", "optional versions json")
  .option("--knowledge-index <path>", "optional knowledge sqlite")
  .option("--dry-run", "validate without assembling", false)
  .action(
    async (options: {
      out: string;
      desktopZip: string;
      toolsTar: string;
      key: string;
      sbom?: string;
      versions?: string;
      knowledgeIndex?: string;
      dryRun: boolean;
    }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.tool_pack.pack_cli",
        "airgap",
        "--out",
        options.out,
        "--desktop-zip",
        options.desktopZip,
        "--tools-tar",
        options.toolsTar,
        "--key",
        options.key,
      ];
      if (options.sbom) {
        args.push("--sbom", options.sbom);
      }
      if (options.versions) {
        args.push("--versions", options.versions);
      }
      if (options.knowledgeIndex) {
        args.push("--knowledge-index", options.knowledgeIndex);
      }
      if (options.dryRun) {
        args.push("--dry-run");
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 1800_000,
      });
      print(JSON.parse(stdout));
    },
  );

const knowledge = program
  .command("knowledge")
  .description("manage the local knowledge index");

knowledge
  .command("add")
  .option("--db <path>", "knowledge db path")
  .option("--file <path>", "JSON chunk file")
  .option("--content <text>", "chunk content")
  .option("--source-ref <ref>", "source reference")
  .option("--chunk-id <id>", "chunk id")
  .option("--subjects <subjects...>", "subjects")
  .action(
    async (options: {
      db?: string;
      file?: string;
      content?: string;
      sourceRef?: string;
      chunkId?: string;
      subjects?: string[];
    }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.knowledge_service.knowledge_cli",
      ];
      if (options.db) {
        args.push("--db", options.db);
      }
      args.push("add");
      if (options.file) {
        args.push("--file", options.file);
      }
      if (options.content) {
        args.push("--content", options.content);
      }
      if (options.sourceRef) {
        args.push("--source-ref", options.sourceRef);
      }
      if (options.chunkId) {
        args.push("--chunk-id", options.chunkId);
      }
      if (options.subjects?.length) {
        args.push("--subjects", ...options.subjects);
      }
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 60_000,
      });
      print(JSON.parse(stdout));
    },
  );

knowledge
  .command("list")
  .option("--db <path>", "knowledge db path")
  .action(async (options: { db?: string }) => {
    const root = findRepoRoot();
    const args = [
      "-m",
      "services.knowledge_service.knowledge_cli",
    ];
    if (options.db) {
      args.push("--db", options.db);
    }
    args.push("list");
    const { stdout } = await execFileAsync("python", args, {
      cwd: root,
      timeout: 60_000,
    });
    print({ chunks: JSON.parse(stdout) });
  });

knowledge
  .command("search")
  .argument("<query>", "search query")
  .option("--db <path>", "knowledge db path")
  .option("--limit <n>", "result limit", "10")
  .action(
    async (query: string, options: { db?: string; limit: string }) => {
      const root = findRepoRoot();
      const args = [
        "-m",
        "services.knowledge_service.knowledge_cli",
      ];
      if (options.db) {
        args.push("--db", options.db);
      }
      args.push("search", query, "--limit", options.limit);
      const { stdout } = await execFileAsync("python", args, {
        cwd: root,
        timeout: 60_000,
      });
      print(JSON.parse(stdout));
    },
  );

knowledge
  .command("delete")
  .argument("<chunk-id>", "knowledge chunk id")
  .action(async (chunkId: string) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestJson(
        "DELETE",
        `/api/v1/knowledge/${encodeURIComponent(chunkId)}`,
      ),
    );
  });

const assetsCommand = program
  .command("assets")
  .description("manage project assets and list runtime assets")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const [tools, skills, mcp, diagnostics] = await Promise.all([
      client.requestPublic("/api/v1/runtime/tools"),
      client.requestPublic("/api/v1/runtime/skills"),
      client.requestPublic("/api/v1/runtime/mcp"),
      client.requestPublic("/api/v1/diagnostics"),
    ]);
    const toolRows = Array.isArray(tools)
      ? tools.map((tool) => ({
          tool_ref: String(tool.tool_ref ?? tool.ref ?? ""),
          name: String(tool.name ?? ""),
          capability: String(tool.capability ?? ""),
          status: String(tool.status ?? ""),
          runner: String(tool.runner ?? ""),
        }))
      : tools;
    const skillRows = Array.isArray(skills)
      ? skills.slice(0, 20).map((skill) => ({
          skill_ref: String(skill.skill_ref ?? skill.name ?? ""),
          name: String(skill.name ?? ""),
          version: String(skill.version ?? ""),
          category: String(skill.category ?? ""),
          risk_level: String(skill.risk_level ?? ""),
          required_tools: Array.isArray(skill.required_tools)
            ? skill.required_tools
            : [],
          runner: String(skill.required_runner ?? skill.runner ?? ""),
        }))
      : skills;
    const mcpRows = Array.isArray(mcp)
      ? mcp.map((server) => ({
          server_id: String(server.server_id ?? ""),
          name: String(server.name ?? ""),
          kind: String(server.kind ?? ""),
          command: String(server.command ?? ""),
          status: String(server.status ?? ""),
        }))
      : mcp;
    print({
      tools: toolRows,
      skills: skillRows,
      skills_count: Array.isArray(skills) ? skills.length : 0,
      mcp: mcpRows,
      storage: (diagnostics as Record<string, unknown>).storage as unknown,
    });
  });

assetsCommand
  .command("list")
  .description("list project assets")
  .option("--project-id <id>", "filter by project")
  .action(async (options: { projectId?: string }) => {
    const client = new ControlClient(controlUrl());
    const pathName = options.projectId
      ? `/api/v1/assets?project_id=${encodeURIComponent(options.projectId)}`
      : "/api/v1/assets";
    print(await client.requestPublic(pathName));
  });

assetsCommand
  .command("get")
  .argument("<asset-id>", "asset id")
  .action(async (assetId: string) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestPublic(
        `/api/v1/assets/${encodeURIComponent(assetId)}`,
      ),
    );
  });

assetsCommand
  .command("add")
  .description("create or update a project asset")
  .requiredOption("--project-id <id>", "project id")
  .requiredOption("--value <value>", "asset value (URL/host)")
  .option("--kind <kind>", "asset kind", "url")
  .option("--source <source>", "asset source", "manual")
  .option("--status <status>", "asset status", "known")
  .option("--metadata <json>", "metadata JSON object")
  .action(
    async (options: {
      projectId: string;
      value: string;
      kind: string;
      source: string;
      status: string;
      metadata?: string;
    }) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson("POST", "/api/v1/assets", {
          project_id: options.projectId,
          kind: options.kind,
          value: options.value,
          source: options.source,
          status: options.status,
          metadata: options.metadata
            ? (JSON.parse(options.metadata) as Record<string, unknown>)
            : {},
        }),
      );
    },
  );

assetsCommand
  .command("update")
  .description("update an asset status or metadata")
  .argument("<asset-id>", "asset id")
  .option("--status <status>", "new lifecycle status")
  .option("--metadata <json>", "metadata JSON object")
  .action(
    async (
      assetId: string,
      options: { status?: string; metadata?: string },
    ) => {
      const client = new ControlClient(controlUrl());
      const body: Record<string, unknown> = {};
      if (options.status) {
        body.status = options.status;
      }
      if (options.metadata) {
        body.metadata = JSON.parse(options.metadata) as Record<string, unknown>;
      }
      print(
        await client.requestJson(
          "PATCH",
          `/api/v1/assets/${encodeURIComponent(assetId)}`,
          body,
        ),
      );
    },
  );

assetsCommand
  .command("delete")
  .description("delete an asset")
  .argument("<asset-id>", "asset id")
  .action(async (assetId: string) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestJson(
        "DELETE",
        `/api/v1/assets/${encodeURIComponent(assetId)}`,
      ),
    );
  });

assetsCommand
  .command("export")
  .description("export project assets to JSON")
  .option("--project-id <id>", "filter by project")
  .option("--out <path>", "output file", "assets.json")
  .action(async (options: { projectId?: string; out: string }) => {
    const client = new ControlClient(controlUrl());
    const rows = ((await client.requestPublic(
      options.projectId
        ? `/api/v1/assets?project_id=${encodeURIComponent(options.projectId)}`
        : "/api/v1/assets",
    )) as unknown) as unknown[];
    const target = path.resolve(options.out);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, JSON.stringify(rows, null, 2), "utf-8");
    print({ exported: rows.length, out: target });
  });

assetsCommand
  .command("import")
  .description("import assets from a JSON file")
  .argument("<file>", "assets JSON path")
  .option("--project-id <id>", "project id override")
  .action(async (file: string, options: { projectId?: string }) => {
    const raw = JSON.parse(readFileSync(file, "utf-8")) as unknown;
    const rows = Array.isArray(raw)
      ? (raw as Array<Record<string, unknown>>)
      : Array.isArray((raw as { assets?: unknown }).assets)
        ? ((raw as { assets: Array<Record<string, unknown>> }).assets)
        : [raw as Record<string, unknown>];
    const client = new ControlClient(controlUrl());
    let imported = 0;
    for (const row of rows) {
      const projectId = options.projectId ?? String(row.project_id ?? "");
      if (!projectId || !row.value) {
        continue;
      }
      await client.requestJson("POST", "/api/v1/assets", {
        project_id: projectId,
        kind: String(row.kind ?? "url"),
        value: String(row.value),
        source: String(row.source ?? "manual"),
        status: String(row.status ?? "known"),
        metadata: (row.metadata as Record<string, unknown>) ?? {},
      });
      imported += 1;
    }
    print({ imported, file });
  });

const vulns = program
  .command("vulns")
  .description("list and update vulnerabilities");

vulns
  .command("list")
  .description("list vulnerabilities with optional filters")
  .option("--project-id <id>", "filter by project")
  .option("--status <status>", "filter by finding status")
  .option("--severity <severity>", "filter by severity")
  .action(
    async (options: {
      projectId?: string;
      status?: string;
      severity?: string;
    }) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.listVulnerabilities({
          project_id: options.projectId,
          status: options.status,
          severity: options.severity,
        }),
      );
    },
  );

vulns
  .command("update")
  .description("update vulnerability metadata")
  .argument("<finding-id>", "finding id")
  .option("--severity <severity>", "severity level")
  .option("--asset-id <id>", "asset id")
  .option("--remediation <text>", "remediation advice")
  .option("--notes <text>", "triage notes")
  .option("--cvss <vector>", "CVSS vector")
  .action(
    async (
      findingId: string,
      options: {
        severity?: string;
        assetId?: string;
        remediation?: string;
        notes?: string;
        cvss?: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      const body: Record<string, unknown> = {};
      if (options.severity) {
        body.severity = options.severity;
      }
      if (options.assetId) {
        body.asset_id = options.assetId;
      }
      if (options.remediation) {
        body.remediation = options.remediation;
      }
      if (options.notes) {
        body.notes = options.notes;
      }
      if (options.cvss) {
        body.cvss_vector = options.cvss;
      }
      print(await client.updateVulnerability(findingId, body));
    },
  );

vulns
  .command("note")
  .description("append a note to a finding")
  .argument("<finding-id>", "finding id")
  .requiredOption("--note <text>", "note text to append")
  .action(
    async (findingId: string, options: { note: string }) => {
      const client = new ControlClient(controlUrl());
      print(await client.appendFindingNote(findingId, options.note));
    },
  );

program
  .command("risk")
  .description("print risk summary")
  .option("--project-id <id>", "filter by project")
  .action(async (options: { projectId?: string }) => {
    const client = new ControlClient(controlUrl());
    print(await client.riskSummary(options.projectId));
  });

const humanGates = program
  .command("human-gates")
  .description("manage run human gates");

humanGates
  .command("list")
  .argument("<runId>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.requestPublic(`/api/v1/runs/${runId}/human-gates`));
  });

humanGates
  .command("resolve")
  .argument("<runId>", "run id")
  .argument("<nodeId>", "human node id")
  .option("--approved <bool>", "approve or reject", "true")
  .action(
    async (
      runId: string,
      nodeId: string,
      options: { approved: string },
    ) => {
      const response = await fetch(
        `${controlUrl()}/api/v1/runs/${runId}/human-gates/${nodeId}/resolve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approved: options.approved === "true",
            reason: "cli-operator",
          }),
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      print(await response.json());
    },
  );

program
  .command("tools")
  .description("list integrated security tool packs")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const packs = await client.listToolPacks();
    print(
      packs.map((pack) => ({
        name: pack.name,
        image: pack.image,
        status: (pack.availability as Record<string, unknown> | undefined)
          ?.status,
        tools: (pack.tools as unknown[]).length,
      })),
    );
  });

program
  .command("skills")
  .description("list available skills")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const skills = await client.listSkills();
    print(
      skills.map((skill) => ({
        name: skill.name,
        version: skill.version,
        description: String(skill.description ?? "").slice(0, 96),
        category: skill.category,
        cwe: Array.isArray(skill.cwe_ids)
          ? (skill.cwe_ids as string[]).join(",")
          : "",
        tools: Array.isArray(skill.required_tools)
          ? (skill.required_tools as string[]).join(",")
          : "",
        runner: skill.required_runner ?? skill.runner ?? "",
        trigger: skill.trigger,
        risk: skill.risk_level,
        source: skill.source,
      })),
    );
  });

program
  .command("loop-profiles")
  .description("list declarative loop profiles used by the agent graph")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const profiles = await client.listLoopProfiles();
    print(
      Object.values(profiles).map((item) => {
        const profile = item as Record<string, unknown>;
        return {
        name: String(profile.name ?? ""),
        version: String(profile.version ?? ""),
        category: String(profile.category ?? ""),
        oracle: String(profile.oracle ?? ""),
        success: String(profile.success_criteria ?? "").slice(0, 80),
        risk: String(profile.risk_level ?? ""),
        sandbox: String(profile.sandbox_profile ?? ""),
        evidence: Array.isArray(profile.evidence_requirements)
          ? (profile.evidence_requirements as string[]).join(",")
          : "",
        };
      }),
    );
  });

program
  .command("loop-presets")
  .description("list reusable Loop Profile presets for mission creation")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const presets = await client.listLoopPresets();
    print(
      Object.values(presets).map((item) => {
        const preset = item as Record<string, unknown>;
        return {
          preset_id: String(preset.preset_id ?? ""),
          label: String(preset.label ?? ""),
          description: String(preset.description ?? "").slice(0, 96),
          templates: Array.isArray(preset.compatible_templates)
            ? (preset.compatible_templates as string[]).join(",")
            : "",
        };
      }),
    );
  });

program
  .command("skills-register")
  .argument("<skill-ref>", "skill ref")
  .requiredOption("--name <name>", "display name")
  .option("--version <v>", "version", "1")
  .option("--trigger <trigger>", "trigger words")
  .option("--runner <runner>", "runner requirement")
  .option("--risk-level <level>", "L1 | L2 | L3 | L4", "L1")
  .action(
    async (
      skillRef: string,
      options: {
        name: string;
        version: string;
        trigger?: string;
        runner?: string;
        riskLevel: string;
      },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.registerSkill({
          skill_ref: skillRef,
          name: options.name,
          version: options.version,
          status: "available",
          trigger: options.trigger || "",
          runner: options.runner || "",
          risk_level: options.riskLevel,
        }),
      );
    },
  );

program
  .command("skills-delete")
  .argument("<skill-ref>", "skill ref")
  .action(async (skillRef: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.deleteSkill(skillRef));
  });

program
  .command("providers")
  .description("list registered model providers")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.listProviders());
  });

program
  .command("audit")
  .description("show recent control-plane audit log entries")
  .option("--limit <n>", "number of entries", "50")
  .action(async (options: { limit: string }) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.listAuditLogs({
        limit: Number(options.limit) || 50,
      }),
    );
  });

const memory = program
  .command("memory")
  .description("manage project memory facts");

memory
  .command("list")
  .description("list project memory facts and snapshot")
  .option("--project-id <id>", "project id", "default")
  .option("--subject <subject>", "filter by subject or predicate", "")
  .option("--include-stale", "include stale facts", false)
  .option("--limit <n>", "maximum facts", "100")
  .action(
    async (options: {
      projectId: string;
      subject: string;
      includeStale: boolean;
      limit: string;
    }) => {
      const client = new ControlClient(controlUrl());
      const query = new URLSearchParams({
        project_id: options.projectId,
        subject: options.subject,
        include_stale: String(Boolean(options.includeStale)),
        limit: options.limit,
      }).toString();
      print(await client.requestPublic(`/api/v1/memory?${query}`));
    },
  );

memory
  .command("fix")
  .requiredOption("--subject <subject>", "fact subject")
  .requiredOption("--predicate <predicate>", "fact predicate")
  .requiredOption("--value <value>", "verified fact value")
  .option("--reason <reason>", "fix reason", "cli_fix")
  .action(
    async (options: {
      subject: string;
      predicate: string;
      value: string;
      reason: string;
    }) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson(
          "POST",
          "/api/v1/memory/fix",
          options,
        ),
      );
    },
  );

memory
  .command("record")
  .option("--project-id <id>", "project id", "default")
  .requiredOption("--subject <subject>", "fact subject")
  .requiredOption("--predicate <predicate>", "fact predicate")
  .requiredOption("--value <value>", "fact value")
  .option("--target <target>", "target reference", "")
  .option(
    "--source-refs <list>",
    "comma separated source refs",
    "",
  )
  .option("--confidence <n>", "confidence 0..1", "0.8")
  .option("--trust <trust>", "user_approved | project_trusted | project_observed", "user_approved")
  .option("--expires-in-seconds <n>", "optional TTL", "")
  .action(
    async (options: {
      projectId: string;
      subject: string;
      predicate: string;
      value: string;
      target: string;
      sourceRefs: string;
      confidence: string;
      trust: string;
      expiresInSeconds: string;
    }) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson(
          "POST",
          "/api/v1/memory/record",
          {
            project_id: options.projectId,
            subject: options.subject,
            predicate: options.predicate,
            value: options.value,
            target: options.target,
            source_refs: options.sourceRefs
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
            confidence: Number(options.confidence) || 0.8,
            trust: options.trust,
            expires_in_seconds:
              options.expiresInSeconds === ""
                ? null
                : Number(options.expiresInSeconds),
          },
        ),
      );
    },
  );

memory
  .command("forget")
  .argument("<fact-id>", "memory fact id")
  .option("--reason <reason>", "forget reason", "cli_forget")
  .action(async (factId: string, options: { reason: string }) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestJson(
        "POST",
        `/api/v1/memory/${factId}/forget`,
        { reason: options.reason },
      ),
    );
  });

memory
  .command("clear")
  .option("--reason <reason>", "clear reason", "cli_clear")
  .action(async (options: { reason: string }) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestJson(
        "POST",
        "/api/v1/memory/clear",
        { reason: options.reason },
      ),
    );
  });

program
  .command("health")
  .description("show component health from the control plane")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    const diagnostics = await client.requestPublic("/api/v1/diagnostics");
    print(
      (diagnostics.components as Record<string, Record<string, unknown>>) ?? {},
    );
  });

program
  .command("acceptance")
  .description("show the unified acceptance summary")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.requestPublic("/api/v1/acceptance"));
  });

const mcp = program.command("mcp").description("manage MCP servers");

mcp
  .command("list")
  .description("list registered MCP servers")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.requestPublic("/api/v1/runtime/mcp"));
  });

mcp
  .command("register")
  .argument("<server-id>", "server id")
  .requiredOption("--name <name>", "display name")
  .option("--kind <kind>", "local | http | sse", "local")
  .option("--command <cmd>", "stdio command or HTTP URL")
  .action(
    async (
      serverId: string,
      options: { name: string; kind: string; command?: string },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.registerMcp({
          server_id: serverId,
          name: options.name,
          status: "available",
          kind: options.kind,
          command: options.command || "",
        }),
      );
    },
  );

mcp
  .command("delete")
  .argument("<server-id>", "server id")
  .action(async (serverId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.deleteMcp(serverId));
  });

mcp
  .command("test")
  .argument("<server-id>", "server id")
  .action(async (serverId: string) => {
    const client = new ControlClient(controlUrl());
    print(await client.testMcp(serverId));
  });

const nodes = program.command("nodes").description("manage remote agent nodes");

nodes
  .command("list")
  .description("list registered remote agent nodes")
  .action(async () => {
    const client = new ControlClient(controlUrl());
    print(await client.requestPublic("/api/v1/remote/nodes"));
  });

nodes
  .command("register")
  .argument("<node-id>", "node id")
  .option("--version <v>", "node version", "0.1.0")
  .option("--capabilities <list>", "comma separated capabilities", "")
  .option("--public-key <key>", "node public key", "")
  .action(
    async (
      nodeId: string,
      options: { version: string; capabilities: string; publicKey: string },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson("POST", "/api/v1/remote/nodes", {
          node_id: nodeId,
          version: options.version,
          capabilities: options.capabilities
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          public_key: options.publicKey,
        }),
      );
    },
  );

nodes
  .command("heartbeat")
  .argument("<node-id>", "node id")
  .option("--lease-seconds <n>", "lease duration", "300")
  .action(async (nodeId: string, options: { leaseSeconds: string }) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestJson(
        "POST",
        `/api/v1/remote/nodes/${encodeURIComponent(nodeId)}/heartbeat`,
        { lease_seconds: Number(options.leaseSeconds) || 300 },
      ),
    );
  });

nodes
  .command("lease")
  .argument("<node-id>", "node id")
  .argument("<task-ref>", "task reference")
  .option("--lease-seconds <n>", "lease duration", "300")
  .action(
    async (
      nodeId: string,
      taskRef: string,
      options: { leaseSeconds: string },
    ) => {
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson(
          "POST",
          `/api/v1/remote/nodes/${encodeURIComponent(nodeId)}/leases`,
          { task_ref: taskRef, lease_seconds: Number(options.leaseSeconds) || 300 },
        ),
      );
    },
  );

nodes
  .command("results")
  .argument("<node-id>", "node id")
  .action(async (nodeId: string) => {
    const client = new ControlClient(controlUrl());
    print(
      await client.requestPublic(
        `/api/v1/remote/nodes/${encodeURIComponent(nodeId)}/results`,
      ),
    );
  });

nodes
  .command("dispatch")
  .argument("<node-id>", "node id")
  .argument("<task-ref>", "task reference")
  .option("--payload <json>", "task payload as JSON", "{}")
  .option("--lease-seconds <n>", "lease duration", "300")
  .action(
    async (
      nodeId: string,
      taskRef: string,
      options: { payload: string; leaseSeconds: string },
    ) => {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(options.payload || "{}");
      } catch {
        throw new Error("--payload must be valid JSON");
      }
      const client = new ControlClient(controlUrl());
      print(
        await client.requestJson(
          "POST",
          `/api/v1/remote/nodes/${encodeURIComponent(nodeId)}/dispatch`,
          {
            task_ref: taskRef,
            payload,
            lease_seconds: Number(options.leaseSeconds) || 300,
          },
        ),
      );
    },
  );

program
  .command("gate")
  .description("evaluate the evidence gate for a run and fail on rejection")
  .argument("<run-id>", "run id")
  .action(async (runId: string) => {
    const client = new ControlClient(controlUrl());
    const result = await client.getEvidenceGate(runId);
    print(result);
    if (result.gate_pass !== true) {
      process.exitCode = 1;
    }
  });

program.parseAsync().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(message);
  process.exitCode = 1;
});

function print(value: unknown) {
  const global = program.opts<GlobalOptions>();
  if (global.json) {
    console.log(JSON.stringify(value, null, 2));
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      console.log(typeof item === "string" ? item : JSON.stringify(item));
    }
    return;
  }
  console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
}
