import { Link } from "../App.tsx";
import type { MatchSnapshot, Replay } from "../api.ts";
import { formName, HOUSE, lastStanding, resultLabel, STANDING } from "./format.ts";

export function ReplayCard({ replay, prefix }: { replay: Replay; prefix: string }) {
  const showcase = replay.mode === "showcase";
  const last = lastStanding(replay.transcript);
  const result = resultLabel(replay);
  const onPoints = replay.end_reason === "move_cap_points" || replay.end_reason === "rounds_complete";
  const score = onPoints ? ` · ${replay.points_p1} : ${replay.points_p2}` : "";
  const revealed = replay.rounds.filter((r) => r.emoji);
  const medallion = showcase ? revealed[revealed.length - 1]?.emoji : (last?.host?.generated_emoji ?? replay.seed_emoji);
  return (
    <article className="card">
      <span className="medallion" aria-hidden="true">
        {medallion}
      </span>
      <div>
        <p className="billing" style={{ margin: 0 }}>
          {replay.stage_name} <span className="vs">vs</span> {HOUSE}{" "}
          <span className={`result${result.won ? "" : " ink"}`}>{result.text}</span>
        </p>
        <blockquote>
          {showcase ? replay.rounds.map((r) => r.token).join(" · ") : `“${last?.move_text ?? formName(replay.seed_token, prefix)}”`}
        </blockquote>
        <p className="meta">
          {showcase
            ? `${replay.title}, ${replay.rounds.length} ${replay.rounds.length === 1 ? "round" : "rounds"}`
            : `${replay.judged_moves} ${replay.judged_moves === 1 ? "move" : "moves"}`}
          {score}
        </p>
        <Link className="watch" to={`/r/${replay.id}`}>
          Watch the duel
        </Link>
      </div>
    </article>
  );
}

export function LiveCard({ match, prefix }: { match: MatchSnapshot; prefix: string }) {
  const showcase = match.mode === "showcase";
  const forms = [match.seed_token, ...match.transcript.filter((t) => STANDING.has(t.outcome)).map((t) => formName(t.move_text, prefix))];
  const shown = forms.slice(-4);
  const round = showcase ? match.rounds.length : match.transcript.length + 1;
  return (
    <Link className="card" to={`/w/${match.id}`}>
      <span>
        <span className="dot" aria-hidden="true" />
        Round {round} · {match.title}
      </span>
      <span className="chainlet">
        {showcase ? (
          match.rounds.map((r) => r.token).join(" · ")
        ) : (
          <>
            {forms.length > shown.length && "… → "}
            {shown.join(" → ")} → …
          </>
        )}
      </span>
      <span className="scoreline">
        <span className="small-caps">{match.stage_name}</span>
        <b>
          {match.points_p1} : {match.points_p2}
        </b>
        <span className="small-caps">{HOUSE}</span>
      </span>
      <span className="watch">Watch live</span>
    </Link>
  );
}
