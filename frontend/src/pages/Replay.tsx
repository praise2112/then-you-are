import { useEffect, useState } from "react";

import { Link, ThemeToggle } from "../App.tsx";
import { api, type Replay, type TemplateView, type TurnView } from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { criterionLabel, formName } from "./format.ts";

type Props = { matchId: string };

function slip(turn: TurnView, prefix: string) {
  if (turn.outcome === "fail") return <span className="stamp ink">Fell</span>;
  if (turn.outcome === "semantic_uncertain") return <span className="stamp ink">Close call</span>;
  return <span className="stamp">Point: {formName(turn.move_text, prefix)}</span>;
}

export function ReplayPage({ matchId }: Props) {
  const [replay, setReplay] = useState<Replay | null>(null);
  const [template, setTemplate] = useState<TemplateView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.replay(matchId).then(setReplay, (e) => setError(e.message));
    api.template().then(setTemplate, (e) => setError(e.message));
  }, [matchId]);

  if (error) return <p className="page-status">{error}</p>;
  if (!replay || !template) return <p className="page-status">Opening the programme.</p>;

  const who = (turn: TurnView) => (turn.actor === "p1" ? replay.stage_name : replay.opponent_name);
  const winnerName = replay.winner === "p1" ? replay.stage_name : replay.opponent_name;
  const lastTurn = replay.transcript[replay.transcript.length - 1];
  const highlight = replay.transcript.find((t) => t.seq === replay.highlight_seq);
  const date = new Date(replay.created_at).toLocaleDateString(undefined, { day: "numeric", month: "long" });
  const finish =
    replay.end_reason === "move_cap_points"
      ? `${winnerName} wins on points, ${replay.points_p1}:${replay.points_p2}`
      : replay.end_reason === "resign"
        ? `${winnerName} wins by resignation`
        : `${winnerName} wins by sudden death in ${replay.judged_moves} moves`;

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
        <h1>
          {replay.stage_name} vs {replay.opponent_name}
        </h1>
        <p className="kicker">A duel of {template.title}, replayed move by move</p>
        <p className="billing small-caps">
          {replay.stage_name} <span className="model human">human</span> against {replay.opponent_name}{" "}
          <span className="model">model</span>, {date}
        </p>

        <div className="card opening">
          <b>Opening:</b> {replay.seed_token} {replay.seed_emoji}
        </div>

        {replay.transcript.map((turn) => (
          <div key={turn.seq}>
            <article className="entry torn">
              <div>
                <span className="who">{who(turn)}</span>{" "}
                <span className={`model${turn.actor === "p1" ? " human" : ""}`}>
                  {turn.actor === "p1" ? "human" : "model"}
                </span>
                <p className="said">{turn.move_text}</p>
              </div>
              <span className="medallion">{turn.host?.generated_emoji ?? "?"}</span>
            </article>
            {turn.host && (
              <div className="verdict-slip slip">
                {slip(turn, template.move_prefix)}
                <em>
                  <b>{criterionLabel(turn.host.because_clause.criterion)}:</b> {turn.host.because_clause.text}
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
          <p className="score">{finish}</p>
          <div className="host" style={{ justifyContent: "center", marginTop: "var(--space-2)" }}>
            <Host state="idle" />
            <p className="host-line">{lastTurn?.host?.quotable_line ?? "A quiet ending."}</p>
          </div>
        </section>

        <p className="play-cta">
          <Link className="ticket" to="/play">
            Play a duel
          </Link>
          <span className="cta-hint">A fresh opening, the same judge.</span>
        </p>

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
