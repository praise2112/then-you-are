import { Link } from "../App.tsx";
import type { MatchSnapshot, Replay } from "../api.ts";
import { formName, HOUSE, lastStanding, resultLabel, STANDING } from "./format.ts";

export function ReplayCard({ replay, prefix }: { replay: Replay; prefix: string }) {
  const last = lastStanding(replay.transcript);
  const result = resultLabel(replay);
  const score = replay.end_reason === "move_cap_points" ? ` · ${replay.points_p1} : ${replay.points_p2}` : "";
  return (
    <article className="card">
      <span className="medallion" aria-hidden="true">
        {last?.host?.generated_emoji ?? replay.seed_emoji}
      </span>
      <div>
        <p className="billing" style={{ margin: 0 }}>
          {replay.stage_name} <span className="vs">vs</span> {HOUSE}{" "}
          <span className={`result${result.won ? "" : " ink"}`}>{result.text}</span>
        </p>
        <blockquote>“{last?.move_text ?? formName(replay.seed_token, prefix)}”</blockquote>
        <p className="meta">
          {replay.judged_moves} {replay.judged_moves === 1 ? "move" : "moves"}
          {score}
        </p>
        <Link className="watch" to={`/r/${replay.id}`}>
          Watch the duel
        </Link>
      </div>
    </article>
  );
}

export function LiveCard({ match, prefix, title }: { match: MatchSnapshot; prefix: string; title: string }) {
  const forms = [match.seed_token, ...match.transcript.filter((t) => STANDING.has(t.outcome)).map((t) => formName(t.move_text, prefix))];
  const shown = forms.slice(-4);
  return (
    <Link className="card" to={`/w/${match.id}`}>
      <span>
        <span className="dot" aria-hidden="true" />
        Round {match.transcript.length + 1} · {title}
      </span>
      <span className="chainlet">
        {forms.length > shown.length && "… → "}
        {shown.join(" → ")} → …
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
