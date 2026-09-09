const KEYS = {
  theme: "oddstage-theme",
  firstPlayDone: "oddstage-first-play-done",
  stageName: "oddstage-stage-name",
  streak: "oddstage-streak",
  bestStreak: "oddstage-best-streak",
  lastCounted: "oddstage-last-counted-match",
  openingMove: "oddstage-opening-move",
};

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be blocked. The page still works, it just forgets.
  }
}

export const store = {
  theme(): "light" | "dark" {
    const saved = read(KEYS.theme);
    if (saved === "dark" || saved === "light") return saved;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  },
  setTheme(theme: "light" | "dark") {
    write(KEYS.theme, theme);
    document.documentElement.dataset.theme = theme;
  },
  firstPlayDone: () => read(KEYS.firstPlayDone) === "1",
  markFirstPlayDone: () => write(KEYS.firstPlayDone, "1"),
  stageName: () => read(KEYS.stageName) ?? "",
  setStageName: (name: string) => write(KEYS.stageName, name),
  /** A first move sent from the landing, shown as pending until its ruling arrives. */
  setOpeningMove(matchId: string, tail: string) {
    write(KEYS.openingMove, JSON.stringify({ matchId, tail }));
  },
  takeOpeningMove(matchId: string): string | null {
    const raw = read(KEYS.openingMove);
    if (!raw) return null;
    try {
      localStorage.removeItem(KEYS.openingMove);
      const saved = JSON.parse(raw) as { matchId: string; tail: string };
      return saved.matchId === matchId ? saved.tail : null;
    } catch {
      return null;
    }
  },
  streak: () => Number(read(KEYS.streak) ?? 0),
  bestStreak: () => Number(read(KEYS.bestStreak) ?? 0),
  /** Records a finished match once and returns the streak after it. */
  recordResult(matchId: string, won: boolean): { streak: number; best: number } {
    let streak = this.streak();
    let best = this.bestStreak();
    if (read(KEYS.lastCounted) !== matchId) {
      streak = won ? streak + 1 : 0;
      best = Math.max(best, streak);
      write(KEYS.streak, String(streak));
      write(KEYS.bestStreak, String(best));
      write(KEYS.lastCounted, matchId);
    }
    return { streak, best };
  },
};
