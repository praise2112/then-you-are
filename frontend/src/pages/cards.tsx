import { Link } from "../App.tsx";
import type { MatchSnapshot, Replay } from "../api.ts";
import { formName, lastStanding, STANDING } from "./format.ts";
import { lengthWords, RESULT_WORDS } from "./labels.ts";

export function ReplayCard({ replay, prefix }: { replay: Replay; prefix: string }) {
  const showcase = replay.mode === "showcase";
  const last = lastStanding(replay.transcript);
  const owner = replay.seats.find((s) => s.kind === "human")?.seat ?? "p1";
  const won = replay.winner === owner;
  const result = RESULT_WORDS[replay.result_kind].short(won);
  const onPoints = replay.result_kind === "points" || replay.result_kind === "draw";
  const score = onPoints ? ` · ${replay.seats.map((s) => s.points).join(" : ")}` : "";
  const revealed = replay.rounds.filter((r) => r.emoji);
  const medallion = showcase ? revealed[revealed.length - 1]?.emoji : (last?.host?.generated_emoji ?? replay.seed_emoji);
  return (
    <article className="card">
      <span className="medallion" aria-hidden="true">
        {medallion}
      </span>
      <div>
        <p className="billing" style={{ margin: 0 }}>
          {replay.seats.map((s, i) => (
            <span key={s.seat}>
              {i > 0 && <span className="vs"> vs </span>}
              {s.display_name}
            </span>
          ))}{" "}
          <span className={`result${won ? "" : " ink"}`}>{result}</span>
        </p>
        <blockquote>
          {showcase ? replay.rounds.map((r) => r.token).join(" · ") : `“${last?.move_text ?? formName(replay.seed_token, prefix)}”`}
        </blockquote>
        <p className="meta">
          {showcase && `${replay.title}, `}
          {lengthWords(showcase, replay.judged_moves, replay.rounds.length)}
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
  const round = showcase ? match.rounds.length : match.round_in_play;
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
        {match.seats.map((s) => (
          <span key={s.seat} className="small-caps">
            {s.display_name} <b>{s.points}</b>
          </span>
        ))}
      </span>
      <span className="watch">Watch live</span>
    </Link>
  );
}
