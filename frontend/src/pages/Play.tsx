import { useEffect, useRef, useState, type FormEvent } from "react";

import { navigate, ThemeToggle } from "../App.tsx";
import { api, ApiError, type TemplateView } from "../api.ts";
import { store } from "../store.ts";
import { HOUSE } from "./format.ts";

/** Entry to a new match: the first-play card once per game, then straight into a duel. */
export function Play({ slug }: { slug: string }) {
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [stageName, setStageName] = useState(store.stageName());
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const [starting, setStarting] = useState(false);
  const [listDuels, setListDuels] = useState(false);
  const [firstPlay] = useState(() => !store.firstPlayDone(slug));

  async function start(name: string) {
    setStarting(true);
    try {
      const match = await api.createMatch(slug, { stageName: name || undefined });
      navigate(`/m/${match.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The stage door is stuck.");
      setStarting(false);
    }
  }

  useEffect(() => {
    api.template(slug).then(setTemplate, () => setError("The backend is not answering."));
  }, [slug]);

  useEffect(() => {
    if (firstPlay) return;
    api.createMatch(slug, { stageName: store.stageName() || undefined }).then(
      (match) => navigate(`/m/${match.id}`),
      (e) => setError(e instanceof Error ? e.message : "The stage door is stuck."),
    );
  }, [firstPlay, slug]);

  if (!firstPlay || !template) {
    return <p className="page-status">{error ?? "Raising the curtain."}</p>;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const name = stageName.trim().slice(0, 24);
    setError(null);
    try {
      await api.updateSession({ stage_name: name || undefined, list_duels: listDuels });
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setNameError(e.message);
        nameRef.current?.focus();
      } else {
        setError(e instanceof Error ? e.message : "The stage door is stuck.");
      }
      return;
    }
    store.setStageName(name);
    store.markFirstPlayDone(slug);
    void start(name);
  }

  return (
    <>
      <div className="backdrop" style={{ padding: "var(--space-3)" }}>
        <header className="bar-top">
          <span className="wordmark">Oddstage</span>
          <span className="round">Round 1</span>
          <ThemeToggle icon />
        </header>
      </div>
      <div className="scrim">
        <form className="sheet first-play" role="dialog" aria-modal="true" aria-labelledby="fp-title" onSubmit={submit}>
          <h2 id="fp-title">{template.title}</h2>
          {template.mode === "showcase" ? <ShowcaseRules template={template} /> : <EscalationRules template={template} />}
          <div className={nameError ? "name-field refused" : "name-field"}>
            <label className="small-caps" htmlFor="stage-name">
              Your stage name
            </label>
            <input
              ref={nameRef}
              id="stage-name"
              type="text"
              aria-invalid={!!nameError}
              maxLength={24}
              placeholder="Challenger"
              autoComplete="off"
              data-form-type="other"
              data-lpignore="true"
              data-1p-ignore=""
              value={stageName}
              onChange={(e) => {
                setStageName(e.target.value);
                setNameError(null);
              }}
            />
            <small>{nameError ?? "Optional. Shown on your replays."}</small>
          </div>
          <label className="choice">
            <input type="checkbox" checked={listDuels} onChange={(e) => setListDuels(e.target.checked)} />
            <span>
              List my duels, so others can watch them live and find the replays.
              <small>Off by default. Your duels stay private and shareable by link either way.</small>
            </span>
          </label>
          {error && <p className="error-line">{error}</p>}
          <div className="sheet-actions">
            <small>You will not see this card again. The scoring rules stay on the duel screen.</small>
            <button className="ticket" type="submit" disabled={starting}>
              Play it
            </button>
          </div>
        </form>
      </div>
    </>
  );
}

function EscalationRules({ template }: { template: TemplateView }) {
  return (
    <>
      <p className="kicker">{template.tagline}</p>
      <ul className="rules-text">
        {template.rules.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
      <div className="word-card entry-card">
        <p className="headword">{template.demo.opening.token}</p>
        <p className="example">
          You might write: <span>{template.move_example}</span>
        </p>
      </div>
      <p className="rules-foot">
        One move that fails ends the match. {template.move_budget} moves with nobody falling goes to points. A muddled
        move comes back to you, no harm done. Under {template.max_chars} characters a move.
      </p>
    </>
  );
}

function ShowcaseRules({ template }: { template: TemplateView }) {
  const demo = template.demo.opening;
  const sample = template.demo.moves[0].text;
  return (
    <>
      <p className="kicker">{template.tagline}</p>
      <ul className="rules-text">
        {template.rules.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
      <div className="word-card entry-card">
        <p className="headword">{demo.token}</p>
        {demo.detail && <p className="detail">{demo.detail}</p>}
        <p className="example">
          You might write: <span>{sample}</span>
        </p>
        {demo.reveal && (
          <p className="reveal-line">
            <b>What {demo.token} really means:</b> {demo.reveal}.
          </p>
        )}
      </div>
      <p className="rules-foot">
        {HOUSE} answers the same card, hidden until the Judge has scored you both. Under {template.max_chars} characters
        a move.
      </p>
    </>
  );
}
