import { useEffect, useRef, useState } from "react";
import { API_URL, newSessionId, sendChat } from "./api.js";

const SIMULATIONS = [
  { value: "", label: "None (normal)" },
  { value: "key_1=429", label: "key_1 rate-limited (429) → failover" },
  { value: "key_1=500", label: "key_1 server error (500) → failover" },
  { value: "*=429", label: "All keys fail (429) → 503" },
];

function AssistantMessage({ m }) {
  const meta = m.metadata;
  const cached = meta.resolution_source === "LLM_CACHE";
  return (
    <div className="row assistant">
      <div className="bubble" data-testid="assistant-message">
        <div className="reply">{m.content}</div>
        <div className="meta">
          <span
            className={`badge ${cached ? "cache" : "live"}`}
            data-testid="meta-source"
            title="Where this answer came from"
          >
            {meta.resolution_source}
          </span>
          <span className="chip">
            model <b data-testid="meta-model">{meta.model}</b>
          </span>
          <span className="chip">
            latency <b data-testid="meta-latency">{meta.latency_ms} ms</b>
          </span>
          <span className="chip">
            in <b data-testid="meta-input-tokens">{meta.input_tokens}</b>
          </span>
          <span className="chip">
            out <b data-testid="meta-output-tokens">{meta.output_tokens}</b>
          </span>
          <span className="chip muted" title="Which API key answered, and how many failed first">
            {meta.key_id ?? "no key (cache)"} · {meta.fallback_attempts} fallback{meta.fallback_attempts === 1 ? "" : "s"}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId); // a new session on every page load
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);
    const result = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure: simulate });
    setLoading(false);
    setMessages((prev) => [
      ...prev,
      result.ok
        ? { role: "assistant", content: result.data.reply, metadata: result.data.metadata }
        : { role: "error", content: result.message },
    ]);
    inputRef.current?.focus();
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setMessages([]);
    setInput("");
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>Lyftr Chat</h1>
          <p className="sub">
            session <code>{sessionId}</code> · backend <code>{API_URL}</code>
          </p>
        </div>
        <button className="ghost" onClick={newChat} disabled={loading}>
          New chat
        </button>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt <span className="hint">(optional, applies to this session)</span>
          <textarea
            data-testid="system-prompt-input"
            rows={2}
            maxLength={4000}
            placeholder="e.g. You are a pirate. Answer in one sentence."
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
        </label>
        <label>
          Demo: simulate a key failure <span className="hint">(needs ENABLE_FAILURE_SIMULATION=true on the backend)</span>
          <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
            {SIMULATIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </details>

      <main className="chat" aria-live="polite">
        {messages.length === 0 && !loading && (
          <div className="empty">Send a message to start. Ask the same thing in a new chat to see a cache hit.</div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div className="row user" key={i}>
              <div className="bubble" data-testid="user-message">
                {m.content}
              </div>
            </div>
          ) : m.role === "assistant" ? (
            <AssistantMessage m={m} key={i} />
          ) : (
            <div className="row assistant" key={i}>
              <div className="bubble error" role="alert" data-testid="error-message">
                {m.content}
              </div>
            </div>
          ),
        )}
        {loading && (
          <div className="row assistant">
            <div className="bubble typing" data-testid="loading">
              Thinking…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      <footer>
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          rows={2}
          maxLength={4000}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button data-testid="send-button" onClick={send} disabled={loading || !input.trim()}>
          {loading ? "Sending…" : "Send"}
        </button>
      </footer>
    </div>
  );
}
