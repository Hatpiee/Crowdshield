"use client";

import { useEffect, useState } from "react";

import Panel from "./Panel";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

interface Turn {
  question: string;
  answer: string;
  citedTimestamps: number[];
}

async function authedFetch(token: string, path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

// Final Intelligence phase (Phase H): a dedicated, session-scoped grounded
// Q&A widget — NOT a generic chatbot. Every answer comes from a single
// backend call (POST /sessions/{id}/copilot/ask) grounded in THIS
// session's own already-computed report (see session_copilot.py); no chat
// history is sent back to the model on the next question (stateless per
// question, matching SessionCopilot's own statelessness), and no internal
// reasoning/chain-of-thought is ever rendered here — only the final answer
// text and any cited timestamps (rendered as "jump to event" actions).
export default function OperatorCopilot({
  sessionId,
  accessToken,
  onSeek,
}: {
  sessionId: string;
  accessToken: string;
  onSeek: (seconds: number) => void;
}) {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authedFetch(accessToken, `/api/v1/sessions/${sessionId}/copilot/suggested-questions`)
      .then((res) => res.json())
      .then((body) => {
        if (!cancelled && body.success) setSuggestions(body.data.questions);
      })
      .catch(() => {
        /* suggestions are a convenience only — silent on failure */
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, accessToken]);

  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || pending) return;
    setPending(true);
    setError(null);
    try {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/copilot/ask`, {
        method: "POST",
        body: JSON.stringify({ question: trimmed }),
      });
      const body = await res.json();
      if (!res.ok || !body.success) {
        setError(
          body?.error?.message ??
            "The Operator Copilot is currently unavailable. Please try again shortly."
        );
        return;
      }
      setTurns((prev) => [
        ...prev,
        { question: trimmed, answer: body.data.answer, citedTimestamps: body.data.cited_timestamps ?? [] },
      ]);
      setQuestion("");
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel label="CrowdShield Operator Copilot">
      <p className="mb-3 text-xs text-cs-muted">
        Ask questions about this session&apos;s analysis. Answers are grounded
        strictly in this session&apos;s own recorded evidence — the Copilot will
        say so honestly when the analysis does not establish something.
      </p>

      {turns.length > 0 && (
        <div className="mb-4 flex flex-col gap-3">
          {turns.map((turn, index) => (
            <div key={index} className="flex flex-col gap-1">
              <p className="self-end border border-cs-border bg-cs-bg px-3 py-1.5 text-sm text-cs-text">
                {turn.question}
              </p>
              <div className="border border-cs-border p-3">
                <p className="text-sm text-cs-text">{turn.answer}</p>
                {turn.citedTimestamps.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {turn.citedTimestamps.map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => onSeek(t)}
                        className="border border-cs-teal px-1.5 py-0.5 font-mono text-[10px] text-cs-teal hover:bg-cs-teal/10"
                      >
                        jump to t={t.toFixed(1)}s
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => submit(suggestion)}
              disabled={pending}
              className="border border-cs-border px-2 py-1 text-xs text-cs-muted hover:border-cs-teal hover:text-cs-teal disabled:opacity-50"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {error && <p className="mb-3 text-sm text-cs-amber">{error}</p>}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(question);
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Type a question about this session…"
          disabled={pending}
          className="flex-1 border border-cs-border bg-cs-bg px-3 py-2 text-sm text-cs-text placeholder:text-cs-muted focus:border-cs-teal focus:outline-none"
        />
        <button
          type="submit"
          disabled={pending || !question.trim()}
          className="border border-cs-teal px-4 py-2 text-sm text-cs-teal hover:bg-cs-teal/10 disabled:opacity-50"
        >
          {pending ? "Asking…" : "Ask"}
        </button>
      </form>
    </Panel>
  );
}
