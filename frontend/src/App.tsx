import { useEffect, useState } from "react";

type Health = { status: string; game: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/healthz")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setError("backend unreachable"));
  }, []);

  return (
    <main
      style={{
        maxWidth: "42rem",
        margin: "0 auto",
        padding: "var(--space-4) var(--space-3)",
      }}
    >
      <p style={{ letterSpacing: "0.2em", fontSize: "0.75rem", color: "var(--ink-faint)" }}>
        ODDSTAGE
      </p>
      <h1 style={{ fontSize: "2.5rem", margin: "0 0 var(--space-2)" }}>Then I Am</h1>
      <p style={{ color: "var(--ink-soft)", fontStyle: "italic" }}>The escalation duel.</p>
      <hr style={{ border: 0, borderTop: "3px double var(--ink)", margin: "var(--space-3) 0" }} />
      <p style={{ fontFamily: "var(--mono)", fontSize: "0.85rem" }}>
        backend:{" "}
        <span style={{ color: error ? "var(--vermilion)" : "var(--ink)" }}>
          {error ?? (health ? `${health.status} · ${health.game}` : "checking...")}
        </span>
      </p>
    </main>
  );
}
