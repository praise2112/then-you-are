import { useEffect, useRef, useState, type FormEvent } from "react";

import { Link, navigate, ThemeToggle } from "../App.tsx";
import { api, type MatchSnapshot, type Replay, type ReplaySort, type StageView, type TemplateView } from "../api.ts";
import { Host } from "../Host.tsx";
import { store } from "../store.ts";
import { criterionLabel, formName, fullMove, HOUSE, lastStanding, resultLabel, STANDING } from "./format.ts";

const STILL = matchMedia("(prefers-reduced-motion: reduce)").matches;
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function Landing() {
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [stage, setStage] = useState<StageView | null>(null);
  const [sort, setSort] = useState<ReplaySort>("curated");
  const [lists, setLists] = useState<Partial<Record<ReplaySort, Replay[]>>>({});
  useEffect(() => {
    api.template().then(setTemplate, () => setTemplate(null));
    api.stage().then(setStage, () => setStage(null));
  }, []);
  useEffect(() => {
    if (lists[sort]) return;
    api.replays(sort).then(
      (rows) => setLists((l) => ({ ...l, [sort]: rows })),
      () => setLists((l) => ({ ...l, [sort]: [] })),
    );
  }, [sort, lists]);
  const replays = lists[sort];
  const prefix = template?.move_prefix ?? "";

  return (
    <>
      <header className="bar-top">
        <span className="wordmark">Oddstage</span>
        <span className="round">
          Now playing <b>{template?.title}</b>
        </span>
        <span className="aside">
          <a href="#replays">Replays</a>
          <a href="/mockups/methodology.html">The judging</a>
          <ThemeToggle icon />
        </span>
      </header>

      <main className="wrap">
        <section className="hero">
          <h1 className="peak">
            Your turn.
            <small>{template?.tagline}</small>
          </h1>
          {template && <Stage template={template} />}
        </section>

        <section id="bill">
          <h2 className="centered-label small-caps">On the bill</h2>
          <div className="bill">
            <article className="card now">
              <h3>Then I Am,</h3>
              <em>the escalation duel</em>
              <span className="ribbon">Now playing</span>
              {stage && (
                <p className="stat-line">
                  {stage.duels_played} {stage.duels_played === 1 ? "duel" : "duels"} played
                  {stage.live.length > 0 && ` · ${stage.live.length} on stage now`}
                </p>
              )}
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
            <article className="card dashed">
              <h3>Stage your own game</h3>
              <em>a template, a rubric, a judge</em>
              <span className="ribbon quiet">In rehearsal</span>
            </article>
          </div>
        </section>

        {stage && stage.live.length > 0 && (
          <section id="on-stage">
            <h2 className="centered-label small-caps">On stage now</h2>
            <div className="live-row">
              {stage.live.map((m) => (
                <LiveCard key={m.id} match={m} prefix={prefix} title={template?.title ?? ""} />
              ))}
            </div>
          </section>
        )}

        <p className="fleuron" aria-hidden="true">❧</p>

        <section id="replays">
          <h2 className="centered-label small-caps">Replays</h2>
          <div className="tabs" role="tablist">
            {(["curated", "newest", "longest"] as ReplaySort[]).map((key) => (
              <button
                key={key}
                role="tab"
                type="button"
                aria-selected={sort === key}
                onClick={() => setSort(key)}
              >
                {key === "curated" ? "Curated" : key === "newest" ? "Newest" : "Longest run"}
              </button>
            ))}
          </div>
          <div className="classics">
            {replays === undefined && <p className="empty-strip">Fetching the archive.</p>}
            {replays?.length === 0 && (
              <p className="empty-strip">
                {sort === "curated" ? "No duels curated yet. Yours could be the first." : "No finished duels yet."}
              </p>
            )}
            {replays?.map((replay) => (
              <ReplayCard key={replay.id} replay={replay} prefix={prefix} />
            ))}
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

function ReplayCard({ replay, prefix }: { replay: Replay; prefix: string }) {
  const last = lastStanding(replay.transcript);
  const result = resultLabel(replay);
  const score = replay.end_reason === "move_cap_points" ? ` · ${replay.points_p1} : ${replay.points_p2}` : "";
  return (
    <article className="card">
      <span className="medallion" aria-hidden="true">
        {last?.host?.generated_emoji ?? replay.seed_emoji}
      </span>
      <div>
        <p className="billing" style={{ margin: 0 }}>
          {replay.stage_name} <span className="vs">vs</span> {HOUSE}{" "}
          <span className={`result${result.won ? "" : " ink"}`}>{result.text}</span>
        </p>
        <blockquote>“{last?.move_text ?? formName(replay.seed_token, prefix)}”</blockquote>
        <p className="meta">
          {replay.judged_moves} {replay.judged_moves === 1 ? "move" : "moves"}
          {score}
        </p>
        <Link className="watch" to={`/r/${replay.id}`}>
          Watch the duel
        </Link>
      </div>
    </article>
  );
}

function LiveCard({ match, prefix, title }: { match: MatchSnapshot; prefix: string; title: string }) {
  const forms = [match.seed_token, ...match.transcript.filter((t) => STANDING.has(t.outcome)).map((t) => formName(t.move_text, prefix))];
  const shown = forms.slice(-4);
  return (
    <Link className="card" to={`/w/${match.id}`}>
      <span>
        <span className="dot" aria-hidden="true" />
        Round {match.transcript.length + 1} · {title}
      </span>
      <span className="chainlet">
        {forms.length > shown.length && "… → "}
        {shown.join(" → ")} → …
      </span>
      <span className="scoreline">
        <span className="small-caps">{match.stage_name}</span>
        <b>
          {match.points_p1} : {match.points_p2}
        </b>
        <span className="small-caps">{HOUSE}</span>
      </span>
      <span className="watch">Watch live</span>
    </Link>
  );
}

/** The card a visitor plays from: an opening, the first-move box, and one demo round beneath. */
function Stage({ template }: { template: TemplateView }) {
  const demo = template.demo;
  const prefix = template.move_prefix;
  const [opening] = useState(() => demo.openings[Math.floor(Math.random() * demo.openings.length)]);
  const [tail, setTail] = useState("");
  const [focused, setFocused] = useState(false);
  const [ghost, setGhost] = useState(0);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(STILL ? 7 : 0);
  const [typed, setTyped] = useState(STILL ? demo.moves[0].text.slice(prefix.length) : "");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (tail || focused) return;
    const id = setInterval(() => setGhost((g) => g + 1), 3200);
    return () => clearInterval(id);
  }, [tail, focused]);

  useEffect(() => {
    if (STILL) return;
    let live = true;
    const go = async () => {
      await wait(600);
      if (!live) return;
      setStep(1);
      await wait(900);
      setStep(2);
      await wait(300);
      const text = demo.moves[0].text.slice(prefix.length);
      for (let i = 1; i <= text.length && live; i++) {
        setTyped(text.slice(0, i));
        await wait(38);
      }
      await wait(500);
      setStep(3);
      await wait(400);
      setStep(4);
      await wait(1100);
      setStep(5);
      await wait(500);
      setStep(6);
      await wait(700);
      setStep(7);
    };
    void go();
    return () => {
      live = false;
    };
  }, [demo, prefix]);

  async function play(event: FormEvent) {
    event.preventDefault();
    if (!tail.trim()) {
      inputRef.current?.focus();
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const match = await api.createMatch({
        stageName: store.stageName() || undefined,
        seedToken: opening.token,
        firstMove: fullMove(prefix, tail),
      });
      store.markFirstPlayDone();
      store.setOpeningMove(match.id, tail.trim());
      navigate(`/m/${match.id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(false);
    }
  }

  const winner = demo.moves[1];
  const scorer = winner.actor === "p1" ? "You" : HOUSE;
  const ghostText = opening.examples[ghost % opening.examples.length];
  const showGhost = !tail && !focused;

  return (
    <div className={`torn stage-card${step >= 7 ? " played" : ""}`}>
      <div className="live">
        <p className="opening">
          <span className="medallion" aria-hidden="true">
            {opening.emoji}
          </span>
          <span>
            Your opening: <b>{opening.token}</b>
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
          {showGhost && <span className="eg">an example</span>}
          <button className="ticket" type="submit" disabled={starting}>
            {starting ? "Curtain up" : "Play it"}
          </button>
        </form>
        <p className="hint">
          {template.move_hint} Every move is scored by an AI judge, and the rubric is shown before you type. Or{" "}
          <Link to="/play">skip the box and just play</Link>.
        </p>
        {error && <p className="hint error">{error}</p>}
      </div>

      <p className="small-caps round-head">{step >= 7 ? "That was one round. Now yours." : "How a round goes"}</p>
      <div className="demo">
        {step >= 1 && (
          <p className="opening on">
            <span className="medallion" aria-hidden="true">
              {demo.opening.emoji}
            </span>
            <span>
              The opening: <b>{demo.opening.token}</b>
            </span>
          </p>
        )}
        {step >= 2 && (
          <div className="replay-move on">
            <span>
              <span className={`who${demo.moves[0].actor === "p1" ? " you" : ""}`}>
                {demo.moves[0].actor === "p1" ? "You" : HOUSE}
              </span>
              <i className="prefix">{prefix}</i>
              <span className={step < 3 ? "caret" : undefined}>{typed}</span>
            </span>
            <span className="medallion" aria-hidden="true">
              {demo.moves[0].emoji}
            </span>
          </div>
        )}
        {step >= 3 && <p className="versus on">vs</p>}
        {step >= 4 && (
          <div className="replay-move on">
            <span>
              <span className={`who${winner.actor === "p1" ? " you" : ""}`}>{scorer}</span>
              <i className="prefix">{prefix}</i>
              {winner.text.slice(prefix.length)}
            </span>
            <span className="medallion" aria-hidden="true">
              {winner.emoji}
            </span>
          </div>
        )}
        {step >= 5 && (
          <p className="stamp-row on">
            <span className="stamp thump">Point: {formName(winner.text, prefix)}</span>
          </p>
        )}
        {step >= 6 && (
          <p className="pts on">
            <span className="who">{scorer} scored</span>
            {demo.points.map((p) => (
              <span key={p.name}>
                {criterionLabel(p.name)}{" "}
                <b>
                  {p.earned} of {p.max_points}
                </b>
              </span>
            ))}
          </p>
        )}
        {step >= 7 && <p className="headline on">{demo.headline}</p>}
      </div>
    </div>
  );
}
