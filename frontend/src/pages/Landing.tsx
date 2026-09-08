import { useEffect, useState } from "react";

import { Link, ThemeToggle } from "../App.tsx";
import { api, type Replay, type TemplateView } from "../api.ts";
import { Host } from "../Host.tsx";
import { formName, lastStanding } from "./format.ts";

export function Landing() {
  const [curated, setCurated] = useState<Replay[] | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  useEffect(() => {
    api.curated().then(setCurated, () => setCurated([]));
    api.template().then(setTemplate, () => setTemplate(null));
  }, []);
  const prefix = template?.move_prefix ?? "";

  return (
    <>
      <header className="masthead">
        <span className="wordmark">Oddstage</span>
        <nav>
          <a href="#bill">Games</a>
          <a href="#replays">Replays</a>
          <a href="/mockups/methodology.html">How the judging works</a>
        </nav>
        <ThemeToggle />
      </header>

      <main className="wrap">
        <section className="hero">
          <div>
            <p className="small-caps kicker">Then I Am, the escalation duel</p>
            <div className="chain hero-chain">
              <figure>
                <span className="medallion" aria-hidden="true">🪨</span>
                <figcaption>a rock</figcaption>
              </figure>
              <span className="chain-link" aria-hidden="true">→</span>
              <figure>
                <span className="medallion" aria-hidden="true">🔨</span>
                <figcaption>the hammer</figcaption>
              </figure>
              <span className="chain-link" aria-hidden="true">→</span>
              <figure>
                <span className="medallion" aria-hidden="true">🔩</span>
                <figcaption>the rust</figcaption>
              </figure>
              <span className="chain-link" aria-hidden="true">→</span>
              <figure>
                <span className="medallion empty" aria-hidden="true"></span>
                <figcaption className="next">you</figcaption>
              </figure>
            </div>
            <h1 className="peak">Your turn.</h1>
            <p className="sub">A duel of words. Become something that beats what came before.</p>
            <Link className="ticket" to="/play">
              Play now
            </Link>
          </div>

          <div className="torn replay-panel">
            <p className="small-caps round-head">How a round goes</p>
            <div className="replay-move">
              <span>I am the hammer, two kilos, rock-splitting.</span>
              <span className="medallion" aria-hidden="true">🔨</span>
            </div>
            <p className="versus">vs</p>
            <div className="replay-move">
              <span>I am the rust, patient, steel-eating.</span>
              <span className="medallion" aria-hidden="true">🔩</span>
            </div>
            <p style={{ textAlign: "right", margin: "var(--space-3) 0 0" }}>
              <span className="stamp">Point: the rust</span>
            </p>
            <p className="replay-caption">An example exchange. Real matches are below.</p>
          </div>
        </section>

        <p className="fleuron" aria-hidden="true">❧</p>

        <section id="bill">
          <h2 className="centered-label small-caps">On the bill</h2>
          <div className="bill">
            <article className="card now">
              <h3>Then I Am,</h3>
              <em>the escalation duel</em>
              <span className="ribbon">Now playing</span>
            </article>
            <article className="card">
              <h3>Verse vs Verse,</h3>
              <em>a rap battle</em>
              <span className="ribbon quiet">Coming soon</span>
            </article>
            <article className="card">
              <h3>Seventeen Syllables,</h3>
              <em>a haiku duel</em>
              <span className="ribbon quiet">Coming soon</span>
            </article>
            <article className="card">
              <h3>The Floor is Yours,</h3>
              <em>a debate</em>
              <span className="ribbon quiet">Coming soon</span>
            </article>
          </div>
        </section>

        <p className="fleuron" aria-hidden="true">❧</p>

        <section id="replays">
          <h2 className="centered-label small-caps">Great duels, replayed</h2>
          <div className="classics">
            {curated === null && <p className="empty-strip">Fetching the archive.</p>}
            {curated?.length === 0 && (
              <p className="empty-strip">No duels curated yet. Yours could be the first.</p>
            )}
            {curated?.map((replay) => {
              const last = lastStanding(replay.transcript);
              return (
                <article className="card" key={replay.id}>
                  <span className="medallion" aria-hidden="true">
                    {last?.host?.generated_emoji ?? replay.seed_emoji}
                  </span>
                  <div>
                    <p className="billing" style={{ margin: 0 }}>
                      {replay.stage_name} <span className="vs">vs</span> {replay.opponent_name}
                    </p>
                    <blockquote>“{last?.move_text ?? formName(replay.seed_token, prefix)}”</blockquote>
                    <Link className="watch" to={`/r/${replay.id}`}>
                      Watch the duel
                    </Link>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="trust host" id="trust">
          <Host state="idle" />
          <p className="host-line">
            Every move scored by an AI judge. The rubric is shown before you type.{" "}
            <a href="/mockups/methodology.html">See how the judging is graded</a>.
          </p>
        </section>
      </main>
    </>
  );
}
