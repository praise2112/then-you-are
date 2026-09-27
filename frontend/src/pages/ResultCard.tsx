import { useEffect, useMemo, useState } from "react";

import { Link } from "../App.tsx";
import { api, type MatchEnded, type MatchSnapshot } from "../api.ts";
import { Icon } from "../Icons.tsx";
import { store } from "../store.ts";
import type { Table } from "./format.ts";

type Props = { snap: MatchSnapshot; ended: MatchEnded; table: Table };

/** The stamp, the streak and the ways out. Sits where the composer was; the board stays up. */
export function ResultCard({ snap, ended, table }: Props) {
  const won = ended.winner !== null && ended.winner === table.me;
  // Only House duels move a streak; a game against people is an exhibition.
  const ranked = snap.kind === "house";
  const draw = ended.winner === null;
  const onPoints = ended.end_reason === "move_cap_points" || ended.end_reason === "rounds_complete";
  // Only the player's own duel moves their streak; a visitor's copy of the page counts nothing.
  const local = useMemo(
    () =>
      snap.is_yours && ranked ? store.recordResult(snap.id, draw ? null : won) : { streak: store.streak(), best: store.bestStreak() },
    [snap.id, snap.is_yours, ranked, won, draw],
  );
  // A signed-in player's streak comes from the server, so it follows the account across devices.
  const [account, setAccount] = useState<{ streak: number; best: number } | null>(null);
  useEffect(() => {
    api.session().then(
      (s) => s.account && setAccount({ streak: s.account.streak, best: s.account.best_streak }),
      () => undefined,
    );
  }, [snap.id]);
  const { streak, best } = account ?? local;
  const [share, setShare] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [listed, setListed] = useState(snap.is_public);
  const [listError, setListError] = useState<string | null>(null);
  const replayUrl = `${location.origin}/r/${snap.id}`;

  const loser = snap.seats.length === 2 ? "Defeat" : `${ended.winner ? table.name(ended.winner) : "Nobody"} won`;
  const stamp = draw
    ? "A draw"
    : onPoints
      ? won
        ? "Won on points"
        : snap.seats.length === 2
          ? "Lost on points"
          : loser
      : ended.end_reason === "resign" && !won
        ? "Resigned"
        : ended.end_reason === "forfeit" && !won
          ? "Out of turns"
          : won
            ? "Victory"
            : loser;

  function setListing(on: boolean) {
    api.setVisibility(snap.id, on).then(
      () => setListed(on),
      (e) => setListError((e as Error).message),
    );
  }

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
    <>
      <div className="result-card">
        <span className={`stamp${won ? "" : " ink"}`}>{stamp}</span>
        {snap.is_yours && ranked && (
          <p className="stats">
            <span>
              Streak<b>{streak}</b>
            </span>
            <span>
              Best<b>{best}</b>
            </span>
          </p>
        )}
        <Link className="ticket" to={ranked ? `/play/${snap.template_id}/start` : `/play/${snap.template_id}`}>
          {ranked ? "Next duel" : "Play again"}
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
            Home
          </Link>
        </div>
        {snap.is_yours && (
          <label className="choice listing">
            <input type="checkbox" checked={listed} onChange={(e) => setListing(e.target.checked)} />
            <span>
              List this duel on the stage
              {listError && <small className="error">{listError}</small>}
            </span>
          </label>
        )}
      </div>

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
    </>
  );
}
