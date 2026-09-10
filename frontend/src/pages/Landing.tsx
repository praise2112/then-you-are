import { useEffect, useRef, useState, type FormEvent } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, navigate, ThemeToggle } from "../App.tsx";
import { api, type OpenDuel, type Replay, type TemplateView } from "../api.ts";
import { Host } from "../Host.tsx";
import { store } from "../store.ts";
import { ReplayCard } from "./cards.tsx";
import { criterionLabel, fullMove, HOUSE, prefixOf } from "./format.ts";

const STILL = matchMedia("(prefers-reduced-motion: reduce)").matches;
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function Landing() {
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [curated, setCurated] = useState<Replay[] | null>(null);
  const [openDuels, setOpenDuels] = useState<OpenDuel[]>([]);
  useEffect(() => {
    api.templates().then(setTemplates, () => setTemplates(null));
    api.replays("curated").then(setCurated, () => setCurated([]));
    api.session().then((s) => setOpenDuels(s.open_duels), () => setOpenDuels([]));
  }, []);
  const [slug, setSlug] = useState<string | null>(null);
  const template = templates?.find((t) => t.slug === slug) ?? templates?.[0] ?? null;
  const at = templates && template ? templates.indexOf(template) : 0;
  const turnTo = (offset: number) => {
    if (!templates) return;
    setSlug(templates[(at + offset + templates.length) % templates.length].slug);
  };

  return (
    <>
      <header className="bar-top">
        <span className="wordmark">Oddstage</span>
        <span className="round">
          On stage <b>{template?.title}</b>
        </span>
        <span className="aside">
          <Link to="/stage">The stage</Link>
          <Link to="/standings">Standings</Link>
          <a href="/mockups/methodology.html">The judging</a>
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
          <h1 className="peak">
            Your turn.
            <small>{template?.tagline}</small>
          </h1>
          {templates && templates.length > 1 && (
            <nav className="playbill-tabs" aria-label="Games on stage">
              <button type="button" className="turn" aria-label="Previous game" onClick={() => turnTo(-1)}>
                &lsaquo;
              </button>
              {templates.map((t) => (
                <button
                  key={t.slug}
                  type="button"
                  className={t.slug === template?.slug ? "on" : undefined}
                  aria-pressed={t.slug === template?.slug}
                  onClick={() => setSlug(t.slug)}
                >
                  {t.title}
                </button>
              ))}
              <button type="button" className="turn" aria-label="Next game" onClick={() => turnTo(1)}>
                &rsaquo;
              </button>
            </nav>
          )}
          {template && <Stage key={template.slug} template={template} />}
        </section>

        <section id="bill">
          <h2 className="centered-label small-caps">On the bill</h2>
          <div className="bill">
            {templates?.map((t) => (
              <Link key={t.slug} className="card now" to={`/play/${t.slug}`}>
                <h3>{t.title}</h3>
                <em>{t.tagline}</em>
                <p className="premise">{t.premise}</p>
                <span className="ribbon">Now playing</span>
              </Link>
            ))}
            <article className="card dashed">
              <h3>Stage your own game</h3>
              <em>a template, scoring rules, a judge</em>
              <span className="ribbon quiet">In rehearsal</span>
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
            {curated?.map((replay) => (
              <ReplayCard key={replay.id} replay={replay} prefix={prefixOf(templates, replay.template_id)} />
            ))}
          </div>
          <p className="strip-foot">
            <Link to="/stage">Live duels and every listed replay, on the stage</Link>
          </p>
        </section>

        <section className="trust host" id="trust">
          <Host state="idle" />
          <p className="host-line">
            Every move scored by an AI judge. The scoring rules are shown before you type.{" "}
            <a href="/mockups/methodology.html">See how the judging is graded</a>.
          </p>
        </section>
      </main>
    </>
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

  const winner = demo.moves[1];
  const scorer = winner.actor === "p1" ? "You" : HOUSE;
  const ghostText = opening.examples[ghost % opening.examples.length];
  const showGhost = !tail && !focused;
  const showcase = template.mode === "showcase";

  return (
    <div className={`torn stage-card${step >= 7 ? " played" : ""}`}>
      <div className="live">
        <p className="opening">
          <span className="medallion" aria-hidden="true">
            {opening.emoji}
          </span>
          <span>
            {showcase ? "Your word" : "Your opening"}: <b>{opening.token}</b>
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
        <p className="hint">
          {template.move_hint} An AI judge scores every move.{" "}
          <Link to={`/play/${template.slug}`}>Or just play.</Link>
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
              {showcase ? "The word" : "The opening"}: <b>{demo.opening.token}</b>
              {demo.opening.detail && <em className="detail"> {demo.opening.detail}</em>}
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
          <div className={`replay-move on${step >= 5 ? " won" : ""}`}>
            <span>
              <span className={`who${winner.actor === "p1" ? " you" : ""}`}>{scorer}</span>
              <i className="prefix">{prefix}</i>
              {winner.text.slice(prefix.length)}
            </span>
            <span className="medallion" aria-hidden="true">
              {winner.emoji}
            </span>
            {step >= 5 && <span className="stamp thump point">Point</span>}
          </div>
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
        {step >= 7 && !showcase && <p className="headline on">{demo.headline}</p>}
        {step >= 7 && showcase && demo.opening.reveal && (
          <p className="reveal on">
            <span className="medallion" aria-hidden="true">
              {demo.opening.emoji}
            </span>
            <span>
              <b>What {demo.opening.token} really means</b>
              {demo.opening.reveal}
            </span>
          </p>
        )}
      </div>
    </div>
  );
}
