import { useEffect, useState, type ReactNode } from "react";

import { Duel } from "./pages/Duel.tsx";
import { GamesPage } from "./pages/Games.tsx";
import { HowItWasBuiltPage } from "./pages/HowItWasBuilt.tsx";
import { Landing } from "./pages/Landing.tsx";
import { Leaderboard } from "./pages/Leaderboard.tsx";
import { LegalPage, type LegalSlug } from "./pages/Legal.tsx";
import { JoinPage, LobbyPage } from "./pages/Lobby.tsx";
import { GamePage, Play } from "./pages/Play.tsx";
import { Profile } from "./pages/Profile.tsx";
import { ReplayPage } from "./pages/Replay.tsx";
import { StagePage } from "./pages/Stage.tsx";
import { store } from "./store.ts";

export function navigate(path: string) {
  history.pushState(null, "", path);
  dispatchEvent(new PopStateEvent("popstate"));
}

function usePath() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const onChange = () => setPath(location.pathname);
    addEventListener("popstate", onChange);
    return () => removeEventListener("popstate", onChange);
  }, []);
  return path;
}

export function Link({ to, children, ...rest }: { to: string; children: ReactNode; className?: string; style?: React.CSSProperties }) {
  return (
    <a
      href={to}
      {...rest}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey) return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

/** The ink bar on every page: the game's name, then the page's own items as children. */
export function TopBar({ linksHome = true, children }: { linksHome?: boolean; children: ReactNode }) {
  return (
    <header className="bar-top">
      {linksHome ? (
        <Link className="wordmark" to="/">
          Then You Are
        </Link>
      ) : (
        <span className="wordmark">Then You Are</span>
      )}
      {children}
    </header>
  );
}

export function ThemeToggle({ icon = false }: { icon?: boolean }) {
  const [theme, setTheme] = useState(store.theme());
  const label = theme === "dark" ? "Day edition" : "Night edition";
  return (
    <button
      className={icon ? "theme-toggle moon" : "theme-toggle"}
      type="button"
      aria-label={icon ? label : undefined}
      title={icon ? label : undefined}
      onClick={() => {
        const next = theme === "dark" ? "light" : "dark";
        store.setTheme(next);
        setTheme(next);
      }}
    >
      {icon ? (theme === "dark" ? "☀" : "☾") : label}
    </button>
  );
}

/** The line at the foot of every page: the privacy page and the terms. */
function SiteFoot() {
  return (
    <footer className="site-foot">
      <Link to="/privacy">Privacy</Link>
      <Link to="/terms">Terms</Link>
    </footer>
  );
}

export default function App() {
  const path = usePath();
  return (
    <>
      <div className="page">{pageAt(path)}</div>
      <SiteFoot />
    </>
  );
}

function pageAt(path: string) {
  const match = path.match(/^\/m\/([^/]+)$/);
  const replay = path.match(/^\/r\/([^/]+)$/);
  const watch = path.match(/^\/w\/([^/]+)$/);
  const play = path.match(/^\/play(?:\/([^/]+))?\/start$/);
  const game = path.match(/^\/play\/([^/]+)$/);
  const person = path.match(/^\/p\/([^/]+)$/);
  const invite = path.match(/^\/i\/([^/]+)$/);
  if (match) return <Duel matchId={match[1]} key={match[1]} />;
  if (watch) return <Duel matchId={watch[1]} key={`w-${watch[1]}`} spectator />;
  if (replay) return <ReplayPage matchId={replay[1]} key={replay[1]} />;
  if (play) return <Play slug={play[1] ?? "then-i-am"} key={play[1] ?? "then-i-am"} />;
  if (game) return <GamePage slug={game[1]} key={game[1]} />;
  if (person) return <Profile accountId={person[1]} key={person[1]} />;
  if (invite) return <JoinPage code={invite[1]} key={invite[1]} />;
  if (path === "/lobby") return <LobbyPage />;
  if (path === "/stage") return <StagePage />;
  if (path === "/games") return <GamesPage />;
  if (path === "/how-it-was-built") return <HowItWasBuiltPage />;
  if (path === "/privacy" || path === "/terms") return <LegalPage slug={path.slice(1) as LegalSlug} />;
  const standings = path.match(/^\/standings(?:\/([^/]+))?$/);
  if (standings) return <Leaderboard slug={standings[1]} />;
  return <Landing />;
}
