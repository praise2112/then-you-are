import { useEffect, useState, type ReactNode } from "react";

import { Duel } from "./pages/Duel.tsx";
import { Landing } from "./pages/Landing.tsx";
import { Play } from "./pages/Play.tsx";
import { ReplayPage } from "./pages/Replay.tsx";
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

export function Link({ to, children, ...rest }: { to: string; children: ReactNode; className?: string }) {
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
  if (match) return <Duel matchId={match[1]} key={match[1]} />;
  if (watch) return <Duel matchId={watch[1]} key={`w-${watch[1]}`} spectator />;
  if (replay) return <ReplayPage matchId={replay[1]} key={replay[1]} />;
  if (path === "/play") return <Play />;
  return <Landing />;
}
