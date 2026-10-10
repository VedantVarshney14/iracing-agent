import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { stopChat, streamChat, type ChatEvent, type ScreenContext, type UiAction } from "../chat";
import { metres } from "../format";

type Part = { kind: "text"; text: string } | { kind: "tool"; command: string } | { kind: "ui"; action: UiAction };
type Message = { role: "you"; text: string } | { role: "coach"; parts: Part[]; error?: string };

const STORE_KEY = "iagent.chat";

interface Props {
  context: ScreenContext;
  contextLabel: string[];
  suggestions: string[];
  prefill: { text: string; nonce: number } | null;
  onUiAction: (action: UiAction) => void;
}

export function Chat({ context, contextLabel, suggestions, prefill, onUiAction }: Props) {
  const [saved] = useState(load);
  const [messages, setMessages] = useState<Message[]>(saved.messages);
  const [sessionId, setSessionId] = useState<string | null>(saved.sessionId);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const runId = useRef<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => save(messages, sessionId), [messages, sessionId]);
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);
  useEffect(() => {
    if (!prefill) return;
    setInput(prefill.text);
    inputRef.current?.focus();
  }, [prefill]);

  // Apply each event to the coach's message being streamed (always the last one).
  const update = (fn: (parts: Part[], msg: Extract<Message, { role: "coach" }>) => void) =>
    setMessages((all) => {
      const last = all[all.length - 1];
      if (!last || last.role !== "coach") return all;
      const copy = { ...last, parts: [...last.parts] };
      fn(copy.parts, copy);
      return [...all.slice(0, -1), copy];
    });

  const onEvent = (e: ChatEvent) => {
    switch (e.type) {
      case "run":
        runId.current = e.run_id;
        break;
      case "session":
        setSessionId(e.session_id);
        break;
      case "text_start":
        update((parts) => parts.push({ kind: "text", text: "" }));
        break;
      case "text":
        update((parts) => {
          const last = parts[parts.length - 1];
          if (last?.kind === "text") parts[parts.length - 1] = { kind: "text", text: last.text + e.text };
          else parts.push({ kind: "text", text: e.text });
        });
        break;
      case "tool":
        update((parts) => parts.push({ kind: "tool", command: e.command }));
        break;
      case "ui":
        update((parts) => parts.push({ kind: "ui", action: e.action }));
        onUiAction(e.action);
        break;
      case "done":
        if (e.session_id) setSessionId(e.session_id);
        if (e.is_error) update((_, msg) => (msg.error = "The coach hit an error."));
        break;
      case "error":
        update((_, msg) => (msg.error = e.message));
        break;
    }
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || running) return;
    setInput("");
    setRunning(true);
    setMessages((all) => [...all, { role: "you", text: message }, { role: "coach", parts: [] }]);
    try {
      await streamChat({ message, session_id: sessionId, context }, onEvent);
    } catch (err) {
      update((_, msg) => (msg.error = (err as Error).message));
    } finally {
      runId.current = null;
      setRunning(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <aside className="card chat" aria-label="Coach chat">
      <div className="card-head">
        <div className="chat-title">
          <h2>Coach</h2>
          <span className="muted small">claude -p · coach plugin · your subscription</span>
        </div>
        <button
          type="button"
          className="btn icon"
          aria-label="New conversation"
          title="New conversation"
          disabled={running}
          onClick={() => {
            setMessages([]);
            setSessionId(null);
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </div>

      <div className="chat-list" ref={listRef} aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="muted">Ask about this lap. The coach can point at corners and zoom the traces.</p>
            {suggestions.map((s) => (
              <button key={s} type="button" className="suggestion" onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "you" ? (
            <div key={i} className="bubble you">{m.text}</div>
          ) : (
            <div key={i} className="coach-msg">
              {m.parts.map((p, j) =>
                p.kind === "text" ? (
                  <Fragment key={j}>{renderText(p.text)}</Fragment>
                ) : p.kind === "tool" ? (
                  <div key={j} className="tool-line mono" title={p.command}>› {p.command}</div>
                ) : (
                  <button key={j} type="button" className="ui-chip" onClick={() => onUiAction(p.action)}>
                    Showing {describe(p.action)}
                  </button>
                ),
              )}
              {running && i === messages.length - 1 && <span className="typing" aria-label="Coach is working" />}
              {m.error && <div className="chat-error" role="alert">{m.error}</div>}
            </div>
          ),
        )}
      </div>

      <div className="composer">
        <div className="chips">
          {contextLabel.map((c) => (
            <span key={c} className="chip">{c}</span>
          ))}
        </div>
        <div className="composer-row">
          <label htmlFor="ask" className="sr-only">Ask the coach</label>
          <textarea
            id="ask"
            ref={inputRef}
            rows={2}
            value={input}
            placeholder="Ask about this lap… (Enter to send)"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
          />
          {running ? (
            <button type="button" className="send stop" aria-label="Stop" onClick={() => runId.current && stopChat(runId.current)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
            </button>
          ) : (
            <button type="button" className="send" aria-label="Send" disabled={!input.trim()} onClick={() => send(input)}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

function describe(a: UiAction): string {
  const bits: string[] = [];
  if (a.lap || a.ref) bits.push("other laps");
  if (a.corners?.length) bits.push(a.corners.map((c) => `T${c}`).join(", "));
  if (a.range) bits.push(`${metres(a.range[0])} – ${metres(a.range[1])}`);
  if (a.view === "corner") bits.push("racing lines");
  return bits.join(" · ") || "on screen";
}

/** Just enough Markdown for the coach's replies: paragraphs, bullet lists, **bold** and `code`. */
export function renderText(text: string): ReactNode {
  const blocks = text.trim().split(/\n{2,}/);
  return blocks.map((block, i) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
      return (
        <ul key={i}>
          {lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>)}
        </ul>
      );
    }
    return <p key={i}>{inline(block.replace(/^#+\s*/, ""))}</p>;
  });
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((piece, i) => {
    if (piece.startsWith("**") && piece.endsWith("**")) return <b key={i}>{piece.slice(2, -2)}</b>;
    if (piece.startsWith("`") && piece.endsWith("`")) return <code key={i}>{piece.slice(1, -1)}</code>;
    return piece;
  });
}

function load(): { messages: Message[]; sessionId: string | null } {
  try {
    const raw = sessionStorage.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // Storage unavailable (private mode etc.): start fresh.
  }
  return { messages: [], sessionId: null };
}

function save(messages: Message[], sessionId: string | null) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify({ messages, sessionId }));
  } catch {
    // Not critical: the conversation just won't survive a reload.
  }
}
