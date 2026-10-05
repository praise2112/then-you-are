import { useEffect, useState } from "react";
import { api } from "./api.ts";

/** Whether enough players are ranked for the standings to be linked from the menus. */
export function useStandingsShown(): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    api.rankedPlayers().then((n) => setShown(n >= 3), () => setShown(false));
  }, []);
  return shown;
}
