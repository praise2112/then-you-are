import { Link, Loading, ThemeToggle, TopBar } from "../../App.tsx";
import { tableOf } from "../format.ts";
import { WaitingRoom } from "../seats.tsx";
import { EscalationBoard } from "./EscalationBoard.tsx";
import { ShowcaseBoard } from "./ShowcaseBoard.tsx";
import { useDuel } from "./useDuel.ts";

type Props = { matchId: string; spectator?: boolean };

/** A match page: the waiting room while the table fills, then the board for the game's mode. */
export function Duel({ matchId, spectator = false }: Props) {
  const duel = useDuel(matchId, spectator);
  const { snap, template, error } = duel;

  if (error) return <p className="page-status">{error}</p>;
  if (!snap || !template) return <Loading text="Finding your seat." />;
  if (snap.status === "abandoned") {
    return (
      <p className="page-status">
        {snap.end_reason === "unfilled" ? "Nobody joined this table in time." : "This match closed after a day without a move."}{" "}
        <Link to={`/play/${snap.template_id}`}>Start a fresh one</Link>.
      </p>
    );
  }

  const table = tableOf(snap, spectator);
  if (snap.status === "open") {
    return (
      <>
        <TopBar>
          <span className="round">
            <b>{template.title}</b>, a table for {snap.seats_wanted}
          </span>
          <ThemeToggle />
        </TopBar>
        <main className="wrap">
          <section className="hero">
            <WaitingRoom snap={snap} template={template} table={table} onChange={() => duel.refresh()} />
          </section>
        </main>
      </>
    );
  }

  const Board = snap.mode === "showcase" ? ShowcaseBoard : EscalationBoard;
  return <Board duel={duel} snap={snap} template={template} table={table} />;
}
