// Client for the coach chat (iagent/ui/coach.py): one POST per message, answered with a stream of
// JSON lines.

export interface UiAction {
  corners?: number[];
  range?: [number, number];
  view?: "lap" | "corner";
  lap?: string;
  ref?: string;
}

export type ChatEvent =
  | { type: "run"; run_id: string }
  | { type: "session"; session_id: string }
  | { type: "text_start" }
  | { type: "text"; text: string }
  | { type: "tool"; id: string; command: string }
  | { type: "ui"; action: UiAction }
  | { type: "done"; session_id: string | null; is_error: boolean }
  | { type: "error"; message: string };

/** What's on screen, sent with every message so the coach knows what "this corner" means. */
export interface ScreenContext {
  track?: string;
  car?: string;
  lap?: string;
  ref?: string;
  ref_driver?: string | null;
  corners?: number[];
  range?: [number, number] | null;
}

export async function streamChat(
  body: { message: string; session_id: string | null; context: ScreenContext },
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ error: `${res.status} ${res.statusText}` }));
    throw new Error(err.error ?? "The coach didn't answer.");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as ChatEvent);
    if (done) break;
  }
}

export function stopChat(runId: string): Promise<Response> {
  return fetch("/api/chat/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  });
}
