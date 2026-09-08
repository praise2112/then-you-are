import { useEffect, useState, type FormEvent } from "react";

import { navigate, ThemeToggle } from "../App.tsx";
import { api, type TemplateView } from "../api.ts";
import { store } from "../store.ts";

/** Entry to a new match: the first-play card once, then straight into a duel. */
export function Play() {
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [stageName, setStageName] = useState(store.stageName());
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const firstPlay = !store.firstPlayDone();

  async function start(name: string) {
    setStarting(true);
    try {
      const match = await api.createMatch(name || undefined);
      navigate(`/m/${match.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The stage door is stuck.");
      setStarting(false);
    }
  }

  useEffect(() => {
    api.template().then(setTemplate, () => setError("The backend is not answering."));
  }, []);

  useEffect(() => {
    if (firstPlay) return;
    api.createMatch(store.stageName() || undefined).then(
      (match) => navigate(`/m/${match.id}`),
      (e) => setError(e instanceof Error ? e.message : "The stage door is stuck."),
    );
  }, [firstPlay]);

  if (!firstPlay || !template) {
    return <p className="page-status">{error ?? "Raising the curtain."}</p>;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const name = stageName.trim().slice(0, 24);
    store.setStageName(name);
    store.markFirstPlayDone();
    void start(name);
  }

  return (
    <>
      <div className="backdrop" style={{ padding: "var(--space-3)" }}>
        <header className="masthead">
          <span className="wordmark">Oddstage</span>
          <span className="small-caps">Round 1</span>
          <ThemeToggle />
        </header>
      </div>
      <div className="scrim">
        <form className="sheet first-play" role="dialog" aria-modal="true" aria-labelledby="fp-title" onSubmit={submit}>
          <h2 id="fp-title">{template.title}</h2>
          <p className="kicker">Two minutes to learn. A lifetime to master, allegedly.</p>
          <ol className="steps">
            <li>
              <div>
                <b>Something is standing.</b>
                <p>The opening form is waiting for you. Your job is to beat it, not to be it.</p>
              </div>
            </li>
            <li>
              <div>
                <b>Become the thing that beats it.</b>
                <p>Say what you are and why it wins. Plain words. Under {template.max_chars} characters.</p>
                <p className="example">
                  For example: <span>{template.move_example}</span>
                </p>
              </div>
            </li>
            <li>
              <div>
                <b>The judge rules every move.</b>
                <p>
                  One move that fails ends the match. {template.move_budget} moves with nobody
                  falling goes to points. A muddled move comes back to you, no harm done.
                </p>
              </div>
            </li>
          </ol>
          <div className="name-field">
            <label className="small-caps" htmlFor="stage-name">
              Your stage name
            </label>
            <input
              id="stage-name"
              type="text"
              maxLength={24}
              placeholder="Challenger"
              autoComplete="off"
              value={stageName}
              onChange={(e) => setStageName(e.target.value)}
            />
            <small>Optional. Shown on your replays.</small>
          </div>
          {error && <p className="error-line">{error}</p>}
          <div className="sheet-actions">
            <small>You will not see this card again. The rubric stays on the duel screen.</small>
            <button className="ticket" type="submit" disabled={starting}>
              Play it
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
