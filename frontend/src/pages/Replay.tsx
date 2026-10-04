import { useEffect, useState, type KeyboardEvent } from "react";

import { Link, ThemeToggle } from "../App.tsx";
import { api, type Replay, type TemplateView, type TurnView } from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import { criterionLabel, formName, groupRounds, roundWinner, STANDING, tableOf, type RoundGroup, type Table } from "./format.ts";
import { Bluff, CallLine, TruthLine, WordCard } from "./rounds.tsx";

type Props = { matchId: string };

function slip(turn: TurnView) {
  if (turn.outcome === "fail") return <span className="stamp ink">Fell</span>;
  if (turn.outcome === "semantic_uncertain") return <span className="stamp ink">Close call</span>;
  return <span className="stamp">Point +{turn.points ?? 0}</span>;
}

export function ReplayPage({ matchId }: Props) {
  const [replay, setReplay] = useState<Replay | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [curateError, setCurateError] = useState<string | null>(null);
  const curatorToken = store.curatorToken();

  useEffect(() => {
    api.replay(matchId).then(
      (r) => {
        setReplay(r);
        api.template(r.template_id).then(setTemplate, (e) => setError(e.message));
      },
      (e) => setError(e.message),
    );
  }, [matchId]);

  if (error) return <p className="page-status">{error}</p>;
  if (!replay || !template) return <p className="page-status">Opening the programme.</p>;

  // A replay names every seat, the viewer's included, so it reads the same to anyone.
  const table = tableOf(replay, true);
  const seatOf = table.seat;
  const billed = (seat: string) => seatOf(seat)?.model ?? table.name(seat);
  const winnerName = replay.winner ? table.name(replay.winner) : "";
  const points = [...replay.seats].sort((a, b) => b.points - a.points).map((s) => s.points).join(" to ");
  const game = replay.seats.length === 2 ? "A duel" : `A ${replay.seats.length}-player game`;
  const lastTurn = replay.transcript[replay.transcript.length - 1];
  const highlight = replay.transcript.find((t) => t.seq === replay.highlight_seq);
  const date = new Date(replay.created_at).toLocaleDateString(undefined, { day: "numeric", month: "long" });
  const showcase = replay.mode === "showcase";
  const finish =
    replay.winner === null
      ? `a draw, ${points}`
      : replay.end_reason === "move_cap_points" || replay.end_reason === "rounds_complete"
        ? `wins on points, ${points}`
        : replay.end_reason === "resign"
          ? "wins by resignation"
          : replay.end_reason === "forfeit"
            ? "wins as the others ran out of turns"
            : `wins by sudden death in ${replay.judged_moves} moves`;
  const rounds = showcase ? groupRounds(replay.rounds, replay.transcript).filter((g) => g.revealed) : [];

  return (
    <>
      <header className="bar-top">
        <Link className="wordmark" to="/">
          Oddstage
        </Link>
        <span className="round">A replay</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <ThemeToggle icon />
        </span>
      </header>

      <main className="program">
        <h1>{replay.seats.map((s) => billed(s.seat)).join(" vs ")}</h1>
        <p className="kicker">
          {game} of {template.title}, replayed {showcase ? "round by round" : "move by move"}
        </p>
        <p className="billing small-caps">
          {replay.seats.map((s, i) => (
            <span key={s.seat}>
              {i > 0 && " against "}
              {billed(s.seat)} <span className={`model${s.kind === "human" ? " human" : ""}`}>{s.kind === "human" ? "human" : "model"}</span>
            </span>
          ))}
          , {date}
        </p>

        {showcase && rounds.length > 0 && <RoundStepper rounds={rounds} table={table} template={template} />}

        {!showcase && template.medallions && <Chain replay={replay} prefix={template.move_prefix} />}

        {!showcase && (
          <article className="entry torn opening-card">
            <div>
              <span className="who">{template.labels.opening}</span>
              <p className="said">{replay.seed_token}</p>
            </div>
            {template.medallions && <span className="medallion">{replay.seed_emoji}</span>}
          </article>
        )}

        {!showcase && replay.transcript.map((turn) => (
          <div key={turn.seq}>
            <article className="entry torn">
              <div>
                <span className={`who tone-${table.tone(turn.actor)}`}>
                  <span>{billed(turn.actor)}</span>
                </span>{" "}
                <span className={`model${seatOf(turn.actor)?.kind === "human" ? " human" : ""}`}>
                  {seatOf(turn.actor)?.kind === "human" ? "human" : (turn.played_by ?? "model")}
                </span>
                <p className="said">{turn.outcome === "forfeit" ? "Lost the turn" : turn.move_text}</p>
              </div>
              {template.medallions && <span className="medallion">{turn.host?.generated_emoji ?? "?"}</span>}
            </article>
            {turn.host && (
              <div className="verdict-slip slip">
                {slip(turn)}
                <em>
                  <b>{criterionLabel(turn.host.because_clause.criterion, template)}:</b> {turn.host.because_clause.text}
                </em>
              </div>
            )}
          </div>
        ))}

        <section className="finale card">
          {highlight && (
            <>
              <p className="small-caps" style={{ color: "var(--vermilion)" }}>
                Highlight move
              </p>
              <blockquote>“{highlight.move_text}”</blockquote>
              <hr className="rule-double" />
            </>
          )}
          <p className="small-caps" style={{ margin: 0 }}>
            Final result
          </p>
          <p className="score">
            {replay.winner !== null && <b>{winnerName}</b>} {finish}
          </p>
          <div className="host" style={{ justifyContent: "center", marginTop: "var(--space-2)" }}>
            <Host state="idle" />
            <p className="host-line">{lastTurn?.host?.quotable_line ?? "A quiet ending."}</p>
          </div>
        </section>

        <p className="play-cta">
          <Link className="ticket" to={`/play/${replay.template_id}`}>
            Play {template.title}
          </Link>
          <span className="cta-hint">{showcase ? "Three fresh words, the same judge." : "A fresh opening, the same judge."}</span>
        </p>

        {replay.is_yours && (
          <label className="choice listing">
            <input
              type="checkbox"
              checked={replay.is_public}
              onChange={(e) => {
                const on = e.target.checked;
                api.setVisibility(matchId, on).then(
                  () => setReplay({ ...replay, is_public: on, is_curated: replay.is_curated && on }),
                  (err) => setCurateError(err.message),
                );
              }}
            />
            <span>
              List this duel on the stage
              <small>{replay.is_public ? "Anyone can find this replay." : "Private. Only people with the link can see it."}</small>
            </span>
          </label>
        )}
        {curatorToken && (
          <p className="curate">
            <button
              className="quiet-button"
              type="button"
              onClick={() =>
                api.curate(matchId, !replay.is_curated, curatorToken).then(
                  () => setReplay({ ...replay, is_curated: !replay.is_curated }),
                  (e) => setCurateError(e.message),
                )
              }
            >
              {replay.is_curated ? "Remove from the curated strip" : "Curate this duel"}
            </button>
            {curateError && <span className="error">{curateError}</span>}
          </p>
        )}

        <p className="centered-label small-caps" style={{ marginTop: "var(--space-3)" }}>
          Share this duel
        </p>
        <div className="share">
          <button
            className="icon-link"
            type="button"
            onClick={() => navigator.clipboard.writeText(location.href).then(() => setCopied(true))}
          >
            <Icon name="link" />
            {copied ? "Link copied" : "Copy link"}
          </button>
          <a
            className="icon-link"
            href={`https://x.com/intent/post?text=${encodeURIComponent(replay.share_text)}`}
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="x" />
            Post to X
          </a>
        </div>
      </main>
    </>
  );
}

/** One round at a time, with arrows, the left and right keys, and a #round-N link into the page. */
function RoundStepper({ rounds, table, template }: { rounds: RoundGroup[]; table: Table; template: TemplateView }) {
  const fromHash = Number(location.hash.match(/^#round-(\d+)$/)?.[1]);
  const [at, setAt] = useState(fromHash >= 1 && fromHash <= rounds.length ? fromHash - 1 : 0);
  const group = rounds[at];
  const { round, turns } = group;
  const go = (next: number) => {
    if (next < 0 || next >= rounds.length) return;
    setAt(next);
    history.replaceState(null, "", `#round-${next + 1}`);
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "ArrowLeft") go(at - 1);
    if (event.key === "ArrowRight") go(at + 1);
  };
  return (
    <section className="round-result stepper" tabIndex={0} onKeyDown={onKey} aria-label={`Round ${at + 1} of ${rounds.length}`}>
      <p className="stepper-bar">
        <button type="button" className="turn" aria-label="Previous round" disabled={at === 0} onClick={() => go(at - 1)}>
          &lsaquo;
        </button>
        <span className="small-caps">
          Round {at + 1} of {rounds.length}
        </span>
        <button
          type="button"
          className="turn"
          aria-label="Next round"
          disabled={at === rounds.length - 1}
          onClick={() => go(at + 1)}
        >
          &rsaquo;
        </button>
      </p>
      <WordCard round={round} />
      <div className="bluffs">
        {turns.map((turn) => (
          <Bluff
            key={turn.seq}
            turn={turn}
            round={round}
            who={table.name(turn.actor)}
            tone={table.tone(turn.actor)}
            ai={table.seat(turn.actor)?.kind === "model"}
            won={roundWinner(group) === turn.actor}
            template={template}
          />
        ))}
      </div>
      <TruthLine round={round} />
      {template.guess && <CallLine group={group} table={table} />}
      <p className="stepper-dots" aria-hidden="true">
        {rounds.map((r, i) => (
          <button key={r.round.round_n} type="button" className={i === at ? "on" : undefined} onClick={() => go(i)} tabIndex={-1} />
        ))}
      </p>
    </section>
  );
}

const CHAIN_SHOWN = 8;

/** Every form that stood, as a row of medallions, the winner's last one crowned. */
function Chain({ replay, prefix }: { replay: Replay; prefix: string }) {
  const standing = replay.transcript.filter((t) => STANDING.has(t.outcome));
  const hidden = Math.max(0, standing.length - CHAIN_SHOWN);
  const shown = standing.slice(hidden);
  return (
    <div className="chain">
      <figure>
        <span className="medallion">{replay.seed_emoji}</span>
        <figcaption>{replay.seed_token}</figcaption>
      </figure>
      {hidden > 0 && (
        <span style={{ display: "contents" }}>
          <span className="chain-link">→</span>
          <figure>
            <span className="medallion more">+{hidden}</span>
            <figcaption>more moves</figcaption>
          </figure>
        </span>
      )}
      {shown.map((turn, i) => (
        <span key={turn.seq} style={{ display: "contents" }}>
          <span className="chain-link">→</span>
          <figure>
            <span className={`medallion${i === shown.length - 1 && turn.actor === replay.winner ? " crowned" : ""}`}>
              {turn.host?.generated_emoji}
            </span>
            <figcaption>{formName(turn.move_text, prefix)}</figcaption>
          </figure>
        </span>
      ))}
    </div>
  );
}
