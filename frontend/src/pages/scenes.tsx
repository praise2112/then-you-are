import { useRef, type CSSProperties } from "react";

import { Figure, ReplayButton } from "./charts.tsx";
import { usePlayOnce } from "./playOnce.ts";

type MatchMove = {
  who: "Flash" | "Luna";
  move: string;
  stands: boolean;
  points: number;
  host: string;
  moveCost: number;
  judgeCost: number;
};
type Try = { move: string; stands: boolean; points: number };

// Ledger corpus-2, match 6YKzlllVo88. Points are the template-weighted totals of Flash's scores.
const MATCH: MatchMove[] = [
  {
    who: "Flash",
    move: "I am a goat, thorn-chewing, four-stomached.",
    stands: true,
    points: 31,
    host: "The thorn brought a point to a chewing contest and the goat brought teeth.",
    moveCost: 4.1868e-5,
    judgeCost: 0.000348432,
  },
  {
    who: "Luna",
    move: "I am a stone wall, goat-blocking, thorn-proof.",
    stands: true,
    points: 26,
    host: "The goat brought four stomachs to a wall that refuses to be lunch. Stone takes the round.",
    moveCost: 5.75e-5,
    judgeCost: 0.001064682,
  },
  {
    who: "Flash",
    move: "I am a wrecker's ball, wall-busting, swinging.",
    stands: true,
    points: 38,
    host: "The wall was goat-proof, not pendulum-proof. The wrecking ball swings through.",
    moveCost: 4.6668e-5,
    judgeCost: 0.000419832,
  },
  {
    who: "Luna",
    move: "I am a pillow, ball-stopping, shock-absorbing.",
    stands: false,
    points: 16,
    host: "The pillow brings a nap to a demolition job, and the wrecking ball will not be stopped. Bring another form.",
    moveCost: 6.14e-5,
    judgeCost: 0.001429266,
  },
];

// Ledger rl-1, position YbMi9b1QMyE/1, in the order the moves were judged.
const TRIES: Try[] = [
  { move: "I am a seagull, barnacle-crushing, wing-beaked.", stands: true, points: 28 },
  { move: "I am a crab, shell-cracking, barnacle-breaking.", stands: true, points: 26 },
  { move: "I am a storm, barnacle-cracking, tide-driven.", stands: true, points: 23 },
  { move: "I am a tide, barnacle-eating, salt-soaking.", stands: false, points: 11 },
  { move: "I am a sea otter, mussel-cracking, barnacle-silencing.", stands: true, points: 23 },
  { move: "I am a tide, barnacle-chewing, tide-pacing.", stands: false, points: 8 },
  { move: "I am a ghost boat, anchoring, barnacle-baiting.", stands: false, points: 13 },
  { move: "I am a gull, barnacle-licking, tide-making.", stands: false, points: 15 },
];

const MATCH_COST = MATCH.reduce((sum, m) => sum + m.moveCost + m.judgeCost, 0);
const reward = (t: Try) => Number(t.stands) + (0.5 * t.points) / 40;
const AVERAGE = TRIES.reduce((sum, t) => sum + reward(t), 0) / TRIES.length;
const REWARD_MAX = 1.5;

const usd = (value: number) => `$${value.toFixed(6)}`;
const signed = (value: number) => `${value > 0 ? "+" : "-"}${Math.abs(value).toFixed(2)}`;
const at = (ms: number) => ({ "--at": `${ms}ms` }) as CSSProperties;

const MOVE_MS = 750;
const JUDGED_MS = 350;
const SAID_MS = 550;
const MATCH_END_MS = (MATCH.length - 1) * MOVE_MS + SAID_MS + 200;

/** One real training match: each move, Flash's ruling and host line, and the match's call log. */
export function MatchScene() {
  const drawing = useRef<HTMLDivElement>(null);
  const { still, start, replay } = usePlayOnce(drawing);
  const calls = MATCH.flatMap((m, i) => [
    { what: `${m.who} writes move ${i + 1}`, cost: m.moveCost, ms: i * MOVE_MS },
    { what: "Flash judges it", cost: m.judgeCost, ms: i * MOVE_MS + JUDGED_MS },
  ]);
  return (
    <Figure
      title="One training match, judged move by move"
      note={
        <>
          Flash and Luna play <i>Then I Am</i>. The match opens with "a thorn", and each move must beat the one before
          it. Under each move are Flash's ruling and the host's line.
        </>
      }
      action={!still && <ReplayButton onClick={replay} />}
    >
      {(id) => (
        <div
          className={start === null ? "writeup-scene paused" : "writeup-scene"}
          ref={drawing}
          role="group"
          aria-labelledby={id}
        >
          <div className="writeup-match" key={start ?? 0}>
            <ol className="writeup-match-moves">
              {MATCH.map((m, i) => {
                const ms = i * MOVE_MS;
                return (
                  <li key={m.move} className="in" style={at(ms)}>
                    <p className="who">{m.who}</p>
                    <p className="move">{m.move}</p>
                    <span className={m.stands ? "verdict stands in" : "verdict in"} style={at(ms + JUDGED_MS)}>
                      {m.stands ? "Stands" : "Fail"}, {m.points} points
                    </span>
                    <p className="line in" style={at(ms + SAID_MS)}>
                      {m.host}
                    </p>
                    <span className={m.stands ? "outcome kept in" : "outcome in"} style={at(ms + SAID_MS)}>
                      {m.stands ? "Kept for training" : "Ends the match"}
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="writeup-match-log">
              <p className="head">Every call, with its cost</p>
              <ol>
                {calls.map((call) => (
                  <li key={call.ms} className="in" style={at(call.ms)}>
                    <span>{call.what}</span>
                    <span className="cost">{usd(call.cost)}</span>
                  </li>
                ))}
              </ol>
              <p className="sum in" style={at(MATCH_END_MS)}>
                <span>This match</span>
                <span className="cost">{usd(MATCH_COST)}</span>
              </p>
            </div>
          </div>
        </div>
      )}
    </Figure>
  );
}

const ROW_MS = 80;
const JUDGE_MS = 900;
const JUDGE_STEP_MS = 150;
const AVERAGE_MS = JUDGE_MS + TRIES.length * JUDGE_STEP_MS + 350;
const SETTLE_MS = AVERAGE_MS + 450;

/** One RL step: eight moves at one position, their rewards against the group's average. */
export function RlScene() {
  const drawing = useRef<HTMLDivElement>(null);
  const { still, start, replay } = usePlayOnce(drawing);
  const along = (value: number) => `${(value / REWARD_MAX) * 100}%`;
  return (
    <Figure
      title="One RL step: eight tries at one position"
      note={
        <>
          The match opens with "a barnacle", and the model has to name something that beats it. During RL it writes 8
          tries at this one position, and Flash judges each one. Reward: 1 if the move stands, plus up to 0.5 for its
          points out of 40.
        </>
      }
      action={!still && <ReplayButton onClick={replay} />}
    >
      {(id) => (
        <div
          className={start === null ? "writeup-scene paused" : "writeup-scene"}
          ref={drawing}
          role="group"
          aria-labelledby={id}
        >
          <div className="writeup-rl" key={start ?? 0}>
            <div className="row head">
              <span className="track">
                <span className="average in" style={{ ...at(AVERAGE_MS), left: along(AVERAGE) }} />
                <span className="average-label in" style={{ ...at(AVERAGE_MS), right: `calc(100% - ${along(AVERAGE)})` }}>
                  Group average {AVERAGE.toFixed(2)}
                </span>
              </span>
            </div>
            <ol>
              {TRIES.map((t, i) => {
                const r = reward(t);
                const above = r > AVERAGE;
                const judged = JUDGE_MS + i * JUDGE_STEP_MS;
                return (
                  <li key={t.move} className={above ? "row in up" : "row in down"} style={at(i * ROW_MS)}>
                    <span className="move in" style={at(SETTLE_MS)}>
                      {t.move}
                    </span>
                    <span className={t.stands ? "verdict stands in" : "verdict in"} style={at(judged)}>
                      {t.stands ? "Stands" : "Fail"}, {t.points}
                    </span>
                    <span className="track">
                      <span className={t.stands ? "fill stands in" : "fill in"} style={{ ...at(judged), width: along(r) }} />
                      <span className="reward in" style={{ ...at(judged), left: along(r) }}>
                        {r.toFixed(2)}
                      </span>
                      <span className="average in" style={{ ...at(AVERAGE_MS + i * 40), left: along(AVERAGE) }} />
                    </span>
                    <span className="change in" style={at(SETTLE_MS)}>
                      {signed(r - AVERAGE)}
                    </span>
                  </li>
                );
              })}
            </ol>
            <p className="writeup-chart-note end in" style={at(SETTLE_MS)}>
              Moves above the group's average become more likely, moves below it less likely.
            </p>
          </div>
        </div>
      )}
    </Figure>
  );
}
