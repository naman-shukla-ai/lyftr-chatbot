// API base URL: runtime config (Docker) -> build-time env (Vercel/Netlify) -> local default.
const API_URL = (
  (typeof window !== "undefined" && window.__CONFIG__?.API_URL) ||
  import.meta.env.VITE_API_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

export function newSessionId() {
  if (globalThis.crypto?.randomUUID) return `s-${crypto.randomUUID().slice(0, 8)}`;
  return `s-${Math.random().toString(36).slice(2, 10)}`;
}

/** Turn any failure into a short human-readable message. */
function describeError(status, body) {
  if (status === 503) return "All AI providers are unavailable right now. Please try again in a moment.";
  if (status === 422) {
    const d = body?.detail;
    const first = Array.isArray(d) ? d[0]?.msg : typeof d === "string" ? d : null;
    return `Your message was rejected: ${first ? first.replace(/^Value error, /, "") : "invalid input"}.`;
  }
  if (status === 429) return "Too many requests. Please slow down and retry.";
  if (typeof body?.detail === "string") return body.detail;
  return `Something went wrong (HTTP ${status}). Please try again.`;
}

/**
 * POST /chat. Resolves to { ok: true, data } or { ok: false, status, message }.
 * Never throws, so the UI can always show something readable.
 */
export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;
  const body = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) body.system_prompt = systemPrompt.trim();

  let res;
  try {
    res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch {
    return { ok: false, status: 0, message: "Cannot reach the server. Check your connection and try again." };
  }
  let json = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON error body */
  }
  if (res.ok && json) return { ok: true, data: json };
  return { ok: false, status: res.status, message: describeError(res.status, json) };
}

export { API_URL };
