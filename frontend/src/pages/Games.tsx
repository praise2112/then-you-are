import { useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, Loading, ThemeToggle, TopBar } from "../App.tsx";
import { useStandingsShown } from "../standings.ts";
import { api, type TemplateView } from "../api.ts";
import { Poster } from "./Landing.tsx";

/** Every game on the bill, and the slot where games players stage will go. */
export function GamesPage() {
  const standingsShown = useStandingsShown();
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.templates().then(setTemplates, () => setError("The backend is not answering."));
  }, []);

  return (
    <>
      <TopBar>
        <span className="aside">
          <Link to="/stage">Watch</Link>
          {standingsShown && <Link to="/standings">Standings</Link>}
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </TopBar>
      <main className="wrap games">
        <h1 className="peak">All games</h1>
        {error && <p className="page-status">{error}</p>}
        {!templates && !error && <Loading text="Fetching the bill." />}
        {templates && (
          <div className="poster-grid">
            {templates.map((t) => (
              <Poster key={t.slug} template={t} tagline />
            ))}
            <span className="poster stage-own">
              <span className="emblem" aria-hidden="true">
                ✎
              </span>
              <h3>Stage your own game</h3>
              <em>A template, scoring rules, a judge. In rehearsal.</em>
            </span>
          </div>
        )}
      </main>
    </>
  );
}
