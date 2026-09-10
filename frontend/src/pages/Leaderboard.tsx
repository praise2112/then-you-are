import { useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, navigate, ThemeToggle } from "../App.tsx";
import { api, type BoardView, type TemplateView } from "../api.ts";

/** Standings for one game at a time: signed-in players with at least three finished duels. */
export function Leaderboard({ slug }: { slug?: string }) {
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [boards, setBoards] = useState<Record<string, BoardView>>({});
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.templates().then(setTemplates, () => setError("The backend is not answering."));
  }, []);
  const current = templates?.find((t) => t.slug === slug) ?? templates?.[0] ?? null;
  const currentSlug = current?.slug;
  useEffect(() => {
    if (!currentSlug) return;
    api.board(currentSlug).then((b) => setBoards((all) => ({ ...all, [b.slug]: b })), (e) => setError(e.message));
  }, [currentSlug]);
  const board = currentSlug ? boards[currentSlug] : undefined;

  return (
    <>
      <header className="bar-top">
        <Link className="wordmark" to="/">
          Oddstage
        </Link>
        <span className="round">Standings</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <Link to="/stage">Watch</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </header>

      <main className="wrap standings">
        {templates && templates.length > 1 && (
          <nav className="playbill-tabs" aria-label="Games">
            {templates.map((t) => (
              <button
                key={t.slug}
                type="button"
                className={t.slug === currentSlug ? "on" : undefined}
                aria-pressed={t.slug === currentSlug}
                onClick={() => navigate(`/standings/${t.slug}`)}
              >
                {t.title}
              </button>
            ))}
          </nav>
        )}
        {error && <p className="page-status">{error}</p>}
        {current && (
          <section className="board">
            <h2>{current.title}</h2>
            {!board && !error && <p className="empty-strip">Counting the house.</p>}
            {board?.standings.length === 0 && (
              <p className="empty-strip">
                Nobody on the board yet. <Link to={`/play/${current.slug}`}>Play three duels signed in</Link> and your
                name goes up first.
              </p>
            )}
            {board && board.standings.length > 0 && (
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
              Signed-in players only, ranked by wins once they have finished three duels. Play three signed in and your
              name goes up.
            </p>
          </section>
        )}
      </main>
    </>
  );
}
