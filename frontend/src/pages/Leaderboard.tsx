import { useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, ThemeToggle } from "../App.tsx";
import { api, type BoardView } from "../api.ts";

/** Standings per game for signed-in players with at least three finished duels. */
export function Leaderboard() {
  const [boards, setBoards] = useState<BoardView[] | null>(null);
  useEffect(() => {
    api.leaderboard().then(setBoards, () => setBoards([]));
  }, []);

  return (
    <>
      <header className="bar-top">
        <Link className="wordmark" to="/">
          Oddstage
        </Link>
        <span className="round">Standings</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <Link to="/stage">The stage</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </header>

      <main className="wrap">
        <p className="centered-label small-caps">Signed-in players, three finished duels or more</p>
        {boards === null && <p className="empty-strip">Counting the house.</p>}
        <div className="boards">
          {boards?.map((board) => (
            <section key={board.slug} className="board">
              <h2>{board.title}</h2>
              {board.standings.length === 0 ? (
                <p className="empty-strip">
                  Nobody yet. <Link to={`/play/${board.slug}`}>Play three signed in</Link> and this is yours.
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
                          {row.avatar_url && <img src={row.avatar_url} alt="" />}
                          {row.display_name}
                        </td>
                        <td className="num">{row.wins}</td>
                        <td className="num">{row.played}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          ))}
        </div>
      </main>
    </>
  );
}
