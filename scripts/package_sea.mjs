#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const work = path.join(root, ".tmp", "sea");
const out = path.join(root, "dist-product");
const suffix = process.platform === "win32" ? ".exe" : "";
const cliExe = `veridix${suffix}`;
const tuiExe = `veridix-tui${suffix}`;

function run(command, args, cwd = root, shell = false) {
  execFileSync(command, args, { stdio: "inherit", cwd, shell });
}

function injectSea(entry, blobName, exeName) {
  const prep = path.join(work, blobName);
  writeFileSync(
    path.join(work, `sea-${exeName}.json`),
    JSON.stringify({
      main: entry,
      output: prep,
      disableExperimentalSEAWarning: true,
    }),
  );
  run(
    process.execPath,
    [
      "--experimental-sea-config",
      path.join(work, `sea-${exeName}.json`),
    ],
    root,
  );

  const exe = path.join(out, exeName);
  copyFileSync(process.execPath, exe);
  run(
    "npx",
    [
      "--yes",
      "postject",
      exe,
      "NODE_SEA_BLOB",
      prep,
      "--sentinel-fuse",
      "NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2",
    ],
    root,
    true,
  );
  return exe;
}

rmSync(work, { recursive: true, force: true });
mkdirSync(work, { recursive: true });
mkdirSync(out, { recursive: true });

run(
  "npx",
  [
    "--yes",
    "esbuild",
    "apps/cli/src/index.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    `--outfile=${path.join(work, "cli.cjs")}`,
  ],
  root,
  true,
);
console.log(injectSea(path.join(work, "cli.cjs"), "cli-prep.blob", cliExe));

// Ink imports its devtools hook with top-level await, which Node SEA cannot
// run as CJS. The hook only activates when DEV=true, so packaging replaces
// the block with a comment before bundling.
function resolveWorkspaceFile(packageName, relativePath) {
  try {
    return path.join(resolvePackageRoot(packageName), relativePath);
  } catch {
    return path.join(
      root,
      "apps",
      "tui",
      "node_modules",
      packageName,
      relativePath,
    );
  }
}

function resolvePackageRoot(packageName) {
  const entry = require.resolve(packageName);
  let current = entry;
  while (true) {
    const parent = path.dirname(current);
    const packageJson = path.join(parent, "package.json");
    if (existsSync(packageJson)) {
      try {
        const meta = JSON.parse(readFileSync(packageJson, "utf8"));
        if (meta.name === packageName) {
          return parent;
        }
      } catch {
        // Continue walking up on malformed package metadata.
      }
    }
    const nextParent = path.dirname(parent);
    if (nextParent === parent) {
      throw new Error(`cannot resolve package root for ${packageName}`);
    }
    current = parent;
  }
}

const reconcilerPath = resolveWorkspaceFile(
  "ink",
  "build/reconciler.js",
);
let reconcilerSource = readFileSync(reconcilerPath, "utf8");
const devtoolsStart = reconcilerSource.indexOf(
  "if (process.env['DEV'] === 'true') {",
);
if (devtoolsStart !== -1) {
  const devtoolsEnd = reconcilerSource.indexOf("const diff =", devtoolsStart);
  if (devtoolsEnd !== -1) {
    reconcilerSource =
      reconcilerSource.slice(0, devtoolsStart) +
      "// devtools import removed for SEA packaging\n" +
      reconcilerSource.slice(devtoolsEnd);
  }
}

await build({
  entryPoints: [path.join(root, "apps", "tui", "src", "index.tsx")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: path.join(work, "tui.mjs"),
  banner: {
    js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);",
  },
  define: { "process.env.DEV": "false" },
  alias: {
    react: resolveWorkspaceFile("react", "index.js"),
    "react/jsx-runtime": resolveWorkspaceFile("react", "jsx-runtime.js"),
    "yoga-wasm-web/auto": path.join(
      root,
      "node_modules",
      "yoga-wasm-web",
      "dist",
      "asm.js",
    ),
    "react-devtools-core": path.join(
      root,
      "scripts",
      "stubs",
      "react-devtools-core.js",
    ),
  },
  plugins: [
    {
      name: "ink-reconciler-patch",
      setup(buildContext) {
        buildContext.onLoad({ filter: /reconciler\.js$/ }, (args) => {
          const normalized = args.path.replaceAll("\\", "/");
          if (!normalized.endsWith("ink/build/reconciler.js")) {
            return null;
          }
          return {
            contents: reconcilerSource,
            loader: "js",
            resolveDir: path.dirname(reconcilerPath),
          };
        });
      },
    },
  ],
});
writeFileSync(
  path.join(work, "tui.cjs"),
  [
    "(async () => {",
    "  await import(\"./tui.mjs\");",
    "})().catch((error) => {",
    "  console.error(error);",
    "  process.exit(1);",
    "});",
    "",
  ].join("\n"),
);
console.log(
  injectSea(path.join(work, "tui.cjs"), "tui-prep.blob", tuiExe),
);
