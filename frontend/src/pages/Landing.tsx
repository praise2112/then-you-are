import { useEffect, useRef, useState, type FormEvent } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, navigate, ThemeToggle } from "../App.tsx";
import { api, type DemoPoints, type OpenDuel, type TemplateView } from "../api.ts";
import { store } from "../store.ts";
import { fullMove, HOUSE } from "./format.ts";

export function Landing() {
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [openDuels, setOpenDuels] = useState<OpenDuel[]>([]);
  useEffect(() => {
    api.templates().then(setTemplates, () => setTemplates(null));
    api.session().then((s) => setOpenDuels(s.open_duels), () => setOpenDuels([]));
  }, []);
  const template = templates?.find((t) => t.featured) ?? templates?.[0] ?? null;

  return (
    <>
      <header className="bar-top">
        <span className="wordmark">Oddstage</span>
        <span className="aside">
          <Link to="/games">Games</Link>
          <Link to="/stage">Watch</Link>
          <Link to="/standings">Standings</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </header>

      {openDuels.map((duel) => (
        <div key={duel.id} className="open-duel">
          <p>
            You have a duel waiting. <b>{duel.title}</b>, {duel.line}.
          </p>
          <Link className="ticket" to={`/m/${duel.id}`}>
            Resume
          </Link>
        </div>
      ))}

      <main className="wrap">
        <section className="hero">
          {template && <Stage key={template.slug} template={template} />}
          {templates && template && <PosterRow templates={templates} current={template.slug} />}
        </section>
        <StageFoot />
      </main>
    </>
  );
}

/** The one line under every game page: who scores the moves and where to read how. */
export function StageFoot() {
  return (
    <p className="stage-foot">
      Every move is scored by an AI judge. <a href="/mockups/methodology.html">How the judging is graded</a>
    </p>
  );
}

/** The game on show first, then the rest of the bill, cut to one row; "All games" is always there. */
export function PosterRow({ templates, current }: { templates: TemplateView[]; current: string }) {
  const shown = [...templates].sort((a, b) => Number(b.slug === current) - Number(a.slug === current));
  return (
    <nav className="poster-row" aria-label="Games">
      <div className="posters">
        {shown.map((t) => (
          <Poster key={t.slug} template={t} current={t.slug === current} />
        ))}
      </div>
      <Link className="poster all-games" to="/games">
        <span className="emblem" aria-hidden="true">
          ☰
        </span>
        <h3>All games</h3>
        <em>{templates.length} on the bill</em>
      </Link>
    </nav>
  );
}

export function Poster({ template, current = false, tagline = false }: { template: TemplateView; current?: boolean; tagline?: boolean }) {
  return (
    <Link
      to={template.featured ? "/" : `/play/${template.slug}`}
      className={`poster${current ? " on" : ""}`}
      aria-current={current ? "page" : undefined}
      style={{ "--game-accent": template.accent } as React.CSSProperties}
    >
      <span className="emblem" aria-hidden="true">
        {template.emblem}
      </span>
      <h3>{template.title}</h3>
      {tagline && <em>{template.tagline}</em>}
    </Link>
  );
}

/** A game's board before a match: the card to answer and the move box, with one worked round folded
 *  beneath, open the first time a visitor meets the game. */
export function Stage({ template }: { template: TemplateView }) {
  const demo = template.demo;
  const prefix = template.move_prefix;
  const [at] = useState(() => Math.floor(Math.random() * demo.openings.length));
  const opening = demo.openings[at];
  // The ghost cycles another card's examples so it never answers the card on show.
  const elsewhere = demo.openings[(at + 1) % demo.openings.length];
  const [tail, setTail] = useState("");
  const [focused, setFocused] = useState(false);
  const [ghost, setGhost] = useState(0);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [firstVisit] = useState(() => !store.demoSeen(template.slug));
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    store.markDemoSeen(template.slug);
  }, [template.slug]);

  useEffect(() => {
    if (tail || focused) return;
    const id = setInterval(() => setGhost((g) => g + 1), 3200);
    return () => clearInterval(id);
  }, [tail, focused]);

  async function play(event: FormEvent) {
    event.preventDefault();
    if (!tail.trim()) {
      inputRef.current?.focus();
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const match = await api.createMatch(template.slug, {
        stageName: store.stageName() || undefined,
        seedToken: opening.token,
        firstMove: fullMove(prefix, tail),
      });
      store.markFirstPlayDone(template.slug);
      store.setOpeningMove(match.id, tail.trim());
      navigate(`/m/${match.id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(false);
    }
  }

  const ghostText = elsewhere.examples[ghost % elsewhere.examples.length];
  const showGhost = !tail && !focused;
  const guess = template.mode === "showcase" && demo.opening.reveal ? template.guess : null;
  const moveLedger = (
    <ul className="ledger">
      {demo.moves.map((move) => (
        <li key={move.actor}>
          <span>
            <span className={`who${move.actor === "p1" ? " you" : ""}`}>{move.actor === "p1" ? "You" : HOUSE}</span>
            <i className="prefix">{prefix}</i>
            {move.text.slice(prefix.length)}
          </span>
          {move.points && <b className="pts">+{total(move.points)}</b>}
        </li>
      ))}
    </ul>
  );

  return (
    <div className="torn stage-card">
      <h1 className="board-title">
        {template.title}
        <small>{template.tagline}</small>
      </h1>

      <p className="opening">
        <span className="emblem" aria-hidden="true">
          {opening.emoji}
        </span>
        <span>
          {template.labels.your_opening}: <b>{opening.token}</b>
          {opening.detail && <em className="detail"> {opening.detail}</em>}
        </span>
      </p>
      <form className="compose" onSubmit={play}>
        <span className="prefix">{prefix.trim()}</span>
        <span className="field">
          <input
            ref={inputRef}
            type="text"
            aria-label="Your first move"
            autoComplete="off"
            data-form-type="other"
            data-lpignore="true"
            data-1p-ignore=""
            maxLength={template.max_chars - prefix.length}
            value={tail}
            disabled={starting}
            onChange={(e) => setTail(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
          {showGhost && (
            <span className="ghost" key={ghostText} aria-hidden="true">
              {ghostText}
            </span>
          )}
        </span>
        <button className="ticket" type="submit" disabled={starting}>
          {starting ? "Curtain up" : "Play it"}
        </button>
      </form>
      {error && <p className="hint error">{error}</p>}

      <details className="how-a-round" open={firstVisit}>
        <summary>How a round goes</summary>
        <p className="example-card">
          {template.labels.opening}: <b>{demo.opening.token}</b>
          {demo.opening.detail && <em className="detail"> {demo.opening.detail}</em>}
        </p>
        {guess ? (
          <ol className="round-steps">
            <li>
              <span className="step-no">1</span>
              <div>
                <p className="step-head">You both write one. The judge scores each.</p>
                {moveLedger}
              </div>
            </li>
            <li>
              <span className="step-no">2</span>
              <div>
                <p className="step-head">{guess.prompt}</p>
                <p className="step-rule">
                  Right: +{guess.spot_points} to you. Wrong: +{guess.fool_points} to {HOUSE}.
                </p>
                <ul className="ledger">
                  <li>
                    <span>{demo.moves[1].text}</span>
                    <span className="aside-note">{HOUSE}&rsquo;s fake</span>
                  </li>
                  <li>
                    <span>{demo.opening.reveal}</span>
                    <span className="aside-note">
                      Real, your call <b className="pts">+{guess.spot_points}</b>
                    </span>
                  </li>
                </ul>
              </div>
            </li>
          </ol>
        ) : (
          moveLedger
        )}
      </details>
    </div>
  );
}

function total(points: DemoPoints[]): number {
  return points.reduce((sum, p) => sum + p.earned, 0);
}
