import { useMemo, useState } from "react";

import { Link } from "../App.tsx";
import type { MatchEnded, MatchSnapshot, TemplateView } from "../api.ts";
import { Host } from "../Host.tsx";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import { formName, HOUSE, STANDING } from "./format.ts";

type Props = { snap: MatchSnapshot; ended: MatchEnded; template: TemplateView };

export function MatchEnd({ snap, ended, template }: Props) {
  const won = ended.winner === "p1";
  const onPoints = ended.end_reason === "move_cap_points";
  const { streak, best } = useMemo(() => store.recordResult(snap.id, won), [snap.id, won]);
  const [share, setShare] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const standingTurns = snap.transcript.filter((t) => STANDING.has(t.outcome));
  const CHAIN_SHOWN = 8;
  const hiddenLinks = Math.max(0, standingTurns.length - CHAIN_SHOWN);
  const shownTurns = standingTurns.slice(hiddenLinks);
  const lastTurn = snap.transcript[snap.transcript.length - 1];
  const highlight = snap.transcript.find((t) => t.seq === ended.highlight_seq);
  const stamp = onPoints
    ? won
      ? "Won on points"
      : "Lost on points"
    : ended.end_reason === "resign"
      ? "Resigned"
      : won
        ? "Victory"
        : "Defeat";
  const kicker = onPoints
    ? `${template.title}, ${snap.judged_moves} moves, nobody fell`
    : `${template.title}, a duel concluded`;
  const verdictLine = lastTurn?.host?.quotable_line ?? (won ? "The other side gave up." : "You gave up.");
  const replayUrl = `${location.origin}/r/${snap.id}`;

  const exchanges = useMemo(() => {
    const marks: boolean[] = [];
    const judged = snap.transcript.filter((t) => t.scoring);
    for (let i = 0; i + 1 < judged.length; i += 2) {
      const [a, b] = [judged[i], judged[i + 1]];
      const mine = a.actor === "p1" ? a : b;
      const theirs = a.actor === "p1" ? b : a;
      marks.push((mine.points ?? 0) >= (theirs.points ?? 0));
    }
    return marks;
  }, [snap.transcript]);

  async function copy(kind: "link" | "text") {
    const value = kind === "link" ? replayUrl : ended.share_text;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      setCopied(null);
    }
  }

  return (
    <div className="playbill-page">
      <main className="playbill">
        <span className="wordmark">Oddstage</span>
        <span className="small-caps kicker">{kicker}</span>

        <p style={{ margin: "var(--space-3) 0 0" }}>
          <span className={`stamp huge${won ? "" : " ink"}`}>{stamp}</span>
        </p>

        {onPoints && (
          <>
            <p className="score">
              <b>{ended.points_p1}</b> : {ended.points_p2}
            </p>
            <p className="score-names">
              <span>You</span>
              <span>{HOUSE}</span>
            </p>
          </>
        )}

        <p className="verdict-line">{verdictLine}</p>

        {onPoints && exchanges.length > 0 && (
          <>
            <div
              className="tally"
              role="img"
              aria-label={`Exchange by exchange: you took ${exchanges.filter(Boolean).length} of ${exchanges.length}`}
            >
              {exchanges.map((mine, i) => (
                <i key={i} className={mine ? "you" : undefined}></i>
              ))}
            </div>
            <p className="tally-caption">Each mark is an exchange. Red ones went your way.</p>
          </>
        )}

        {!onPoints && (
          <div className="chain">
            <figure>
              <span className="medallion">{snap.seed_emoji}</span>
              <figcaption>{snap.seed_token}</figcaption>
            </figure>
            {hiddenLinks > 0 && (
              <span style={{ display: "contents" }}>
                <span className="chain-link">→</span>
                <figure>
                  <span className="medallion more">+{hiddenLinks}</span>
                  <figcaption>more forms</figcaption>
                </figure>
              </span>
            )}
            {shownTurns.map((turn, i) => (
              <span key={turn.seq} style={{ display: "contents" }}>
                <span className="chain-link">→</span>
                <figure>
                  <span className={`medallion${i === shownTurns.length - 1 && turn.actor === ended.winner ? " crowned" : ""}`}>
                    {turn.host?.generated_emoji}
                  </span>
                  <figcaption>{formName(turn.move_text, template.move_prefix)}</figcaption>
                </figure>
              </span>
            ))}
          </div>
        )}

        <p className="stats">
          <span>
            Streak <b>{streak}</b>
          </span>
          <span>
            Best streak <b>{best}</b>
          </span>
          <span>
            Moves judged <b>{snap.judged_moves}</b>
          </span>
        </p>

        {highlight && (
          <div className="highlight">
            <p className="small-caps" style={{ margin: 0 }}>
              Highlight move
            </p>
            <blockquote>“{highlight.move_text}”</blockquote>
          </div>
        )}

        <Link className="ticket" to="/play">
          {won ? "Play again" : "Rematch"}
        </Link>

        <div className="after">
          <button className="icon-link" type="button" onClick={() => setShare(true)}>
            <Icon name="share" />
            Share result
          </button>
          <Link className="icon-link" to={`/r/${snap.id}`}>
            <Icon name="play" />
            See the full duel
          </Link>
          <Link className="icon-link" to="/">
            <Icon name="bill" />
            Back to the bill
          </Link>
        </div>

        <div className="host host-corner">
          <Host state="tipping" big worn={!won} />
          {ended.coaching_line ? (
            <p className="host-line coaching">
              <span>What would have won</span>
              {ended.coaching_line}
            </p>
          ) : (
            <p className="host-line">
              {won ? "Take the win and go. The next one will not be so polite." : "The replay is saved. So is the lesson."}
            </p>
          )}
        </div>
      </main>

      {share && (
        <div className="scrim">
          <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="share-title" style={{ maxWidth: "26rem" }}>
            <h2 id="share-title" style={{ margin: 0, fontSize: "1.8rem" }}>
              Share the duel
            </h2>
            <p style={{ margin: "var(--space-1) 0 var(--space-2)", color: "var(--ink-faint)", fontSize: "0.9rem" }}>
              No moves in the text, so nobody gets the answers before they play.
            </p>
            <p className="share-text">
              {ended.share_text.replace(/\s*https?:\S+$/, "")}
              <a href={replayUrl}>{replayUrl}</a>
            </p>
            <div className="sheet-actions">
              <span style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap" }}>
                <button className="icon-link" type="button" onClick={() => copy("link")}>
                  <Icon name="link" />
                  {copied === "link" ? "Link copied" : "Copy link"}
                </button>
                <button className="icon-link" type="button" onClick={() => copy("text")}>
                  <Icon name="quill" />
                  {copied === "text" ? "Text copied" : "Copy text"}
                </button>
                <a
                  className="icon-link"
                  href={`https://x.com/intent/post?text=${encodeURIComponent(ended.share_text)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Icon name="x" />
                  Post to X
                </a>
              </span>
              <button className="quiet-button" type="button" onClick={() => setShare(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
