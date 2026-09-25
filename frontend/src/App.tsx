import { useEffect, useState, type ReactNode } from "react";

import { Duel } from "./pages/Duel.tsx";
import { GamesPage } from "./pages/Games.tsx";
import { Landing } from "./pages/Landing.tsx";
import { Leaderboard } from "./pages/Leaderboard.tsx";
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

export default function App() {
  const path = usePath();
  const match = path.match(/^\/m\/([^/]+)$/);
  const replay = path.match(/^\/r\/([^/]+)$/);
  const watch = path.match(/^\/w\/([^/]+)$/);
  const play = path.match(/^\/play(?:\/([^/]+))?\/start$/);
  const game = path.match(/^\/play\/([^/]+)$/);
  const person = path.match(/^\/p\/([^/]+)$/);
  if (match) return <Duel matchId={match[1]} key={match[1]} />;
  if (watch) return <Duel matchId={watch[1]} key={`w-${watch[1]}`} spectator />;
  if (replay) return <ReplayPage matchId={replay[1]} key={replay[1]} />;
  if (play) return <Play slug={play[1] ?? "then-i-am"} key={play[1] ?? "then-i-am"} />;
  if (game) return <GamePage slug={game[1]} key={game[1]} />;
  if (person) return <Profile accountId={person[1]} key={person[1]} />;
  if (path === "/stage") return <StagePage />;
  if (path === "/games") return <GamesPage />;
  const standings = path.match(/^\/standings(?:\/([^/]+))?$/);
  if (standings) return <Leaderboard slug={standings[1]} />;
  return <Landing />;
}
