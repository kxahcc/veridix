import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import type { AgentEvent, ControlClient } from "@veridix/sdk-typescript";
import { attachOnce } from "./attach.js";

export interface AttachInteractiveOptions {
  input?: NodeJS.ReadableStream;
  output?: NodeJS.WritableStream;
  pollInterval?: number;
  onEvent?: (event: AgentEvent) => void;
}

export interface AttachInteractiveResult {
  cursor: number;
  terminal: boolean;
  quit: boolean;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function attachInteractive(
  client: ControlClient,
  runId: string,
  options: AttachInteractiveOptions = {},
): Promise<AttachInteractiveResult> {
  const input = options.input ?? process.stdin;
  const output = options.output ?? process.stdout;
  const pollInterval = options.pollInterval ?? 1000;
  const queue: string[] = [];
  let closed = false;
  const rl = createInterface({ input, output, terminal: false });
  rl.on("line", (line) => {
    if (!closed) {
      queue.push(line.trim());
    }
  });
  rl.on("close", () => {
    closed = true;
  });
  const write = (text: string) => {
    output.write(text);
  };

  let cursor = 0;
  let terminal = false;
  let quit = false;
  while (!terminal && !quit && !closed) {
    const result = await attachOnce(client, runId, cursor);
    for (const event of result.events) {
      if (options.onEvent) {
        options.onEvent(event);
      } else {
        write(`${JSON.stringify(event)}\n`);
      }
    }
    cursor = result.cursor;
    terminal = result.terminal;
    while (queue.length && !quit) {
      const line = queue.shift() ?? "";
      if (!line) {
        continue;
      }
      const [command, ...rest] = line.split(/\s+/);
      switch (command) {
        case "pause":
          await client.runCommand(runId, "pause", randomUUID());
          write("paused\n");
          break;
        case "resume":
          await client.runCommand(runId, "resume", randomUUID());
          write("resumed\n");
          break;
        case "cancel":
          await client.runCommand(runId, "cancel", randomUUID());
          write("cancelled\n");
          terminal = true;
          break;
        case "message": {
          const text = rest.join(" ");
          if (!text) {
            write("usage: message <text>\n");
            break;
          }
          await client.sendMessage(runId, text, randomUUID(), "cli-operator");
          write("message sent\n");
          break;
        }
        case "help":
          write("commands: pause, resume, cancel, message <text>, quit\n");
          break;
        case "quit":
        case "exit":
          quit = true;
          break;
        default:
          write(`unknown command: ${command}\n`);
          break;
      }
    }
    if (!terminal && !quit && !closed) {
      await delay(pollInterval);
    }
  }
  rl.close();
  return { cursor, terminal, quit };
}
