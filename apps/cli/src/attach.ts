import type {
  AgentEvent,
  ControlClient,
  RunState,
} from "@veridix/sdk-typescript";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export interface AttachResult {
  cursor: number;
  terminal: boolean;
  events: AgentEvent[];
  run: RunState;
}

export async function attachOnce(
  client: ControlClient,
  runId: string,
  cursor: number,
): Promise<AttachResult> {
  const events = await client.getEvents(runId, cursor);
  const last = events[events.length - 1];
  const nextCursor = last?.sequence ?? cursor;
  const run = await client.getRun(runId);
  return {
    cursor: nextCursor,
    terminal: TERMINAL_STATUSES.has(run.status),
    events,
    run,
  };
}
