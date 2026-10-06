import { useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, Loading, ThemeToggle, TopBar } from "../App.tsx";
import { api, type BoardSummary, type BoardView } from "../api.ts";

/** Standings: an index of games, then one board per game. Signed-in players, three finished duels or more. */
export function Leaderboard({ slug }: { slug?: string }) {
  return (
    <>
      <TopBar>
        <span className="round">Wins against the House</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <Link to="/stage">Watch</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </TopBar>
      <main className="wrap standings">{slug ? <Board slug={slug} /> : <BoardIndex />}</main>
    </>
  );
}

function BoardIndex() {
  const [boards, setBoards] = useState<BoardSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.boards().then(setBoards, (e) => setError(e.message));
  }, []);
  return (
    <>
      <p className="centered-label small-caps">Pick a game</p>
      {error && <p className="page-status">{error}</p>}
      {boards === null && !error && <Loading text="Counting the house." strip />}
      <div className="board-index">
        {boards?.map((b) => (
          <Link key={b.slug} className="card game-card" to={`/standings/${b.slug}`} style={{ "--game-accent": b.accent } as React.CSSProperties}>
            <span className="medallion" aria-hidden="true">
              {b.emblem}
            </span>
            <span>
              <h3>{b.title}</h3>
              {b.leader && b.leader.wins > 0 ? (
                <p className="leader">
                  {b.leader.avatar_url && <img src={b.leader.avatar_url} alt="" />}
                  <b>{b.leader.display_name}</b> leads, {b.leader.wins} {b.leader.wins === 1 ? "win" : "wins"}
                </p>
              ) : (
                <p className="leader">{b.ranked > 0 ? "No wins yet." : "Nobody on the board yet."}</p>
              )}
              <p className="meta">
                {b.ranked} {b.ranked === 1 ? "player" : "players"} ranked
              </p>
            </span>
          </Link>
        ))}
      </div>
      <p className="board-foot">Signed-in players only, ranked by wins once they have finished three duels in a game.</p>
    </>
  );
}

function Board({ slug }: { slug: string }) {
  const [board, setBoard] = useState<BoardView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.board(slug).then(setBoard, (e) => setError(e.message));
  }, [slug]);
  if (error) return <p className="page-status">{error}</p>;
  if (!board) return <Loading text="Counting the house." strip />;
  return (
    <section className="board" style={{ "--game-accent": board.accent } as React.CSSProperties}>
      <p className="back">
        <Link to="/standings">All games</Link>
      </p>
      <h2>
        <span className="emblem" aria-hidden="true">
          {board.emblem}
        </span>{" "}
        {board.title}
      </h2>
      {board.standings.length === 0 ? (
        <p className="empty-strip">
          Nobody on the board yet. <Link to={`/play/${board.slug}/start`}>Play three duels signed in</Link> and your name goes up
          first.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Player</th>
              <th>Wins</th>
              <th>Played</th>
            </tr>
          </thead>
          <tbody>
            {board.standings.map((row) => (
              <tr key={row.rank}>
                <td className="rank">{row.rank}</td>
                <td className="player">
                  <Link to={`/p/${row.account_id}`}>
                    {row.avatar_url && <img src={row.avatar_url} alt="" />}
                    {row.display_name}
                  </Link>
                </td>
                <td className="num">{row.wins}</td>
                <td className="num">{row.played}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="board-foot">
        Signed-in players only, ranked by wins once they have finished three duels. Play three signed in and your name
        goes up.
      </p>
    </section>
  );
}
