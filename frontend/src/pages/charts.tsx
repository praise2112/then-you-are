import { useId, type ReactNode } from "react";

type BarRow = { label: string; value: number; ours?: boolean; aside?: string };
type Reference = { label: string; value: number };
type Family = { parents: string[]; counts: number[]; heldBack: number };
type Outcomes = { label: string; stood: number; fail: number; refused: number; ours?: boolean };
type Span = { label: string; from: number; to: number; who: "game" | "flash" | "house" };

const HEADLINE: BarRow[] = [
  { label: "Qwen3.5-0.8B before training", value: 19.0 },
  { label: "Qwen3.5-2B without training", value: 28.4 },
  { label: "Qwen3.5-4B without training", value: 55.1 },
  { label: "The shipped 0.8B", value: 68.5, ours: true },
];

const RL: BarRow[] = [
  { label: "Temperature 1.0, before RL", value: 62.4 },
  { label: "Temperature 1.0, after RL", value: 68.5, ours: true },
  { label: "Temperature 0.7, before RL", value: 68.5 },
  { label: "Temperature 0.7, after RL", value: 68.3 },
];

const BASES: BarRow[] = [
  { label: "Qwen3.5-2B", value: 61.7, aside: "$1.74 a run" },
  { label: "Qwen3-1.7B", value: 63.5, aside: "$1.45 a run" },
  { label: "LFM2.5-1.2B", value: 58.1, aside: "$0.78 a run" },
  { label: "MiniCPM5-1B", value: 47.7, aside: "$1.02 a run" },
  { label: "Qwen3.5-0.8B", value: 55.6, aside: "$1.68 a run", ours: true },
  { label: "Qwen3-0.6B", value: 57.1, aside: "$1.33 a run" },
];

const FILTERS = [
  "Written",
  "1. Loads as a valid game",
  "2. Not a near-copy",
  "3. Top half in the ranking",
  "4. Survives trial matches",
  "5. The judges agree",
];

const FAMILIES: Family[] = [
  { parents: ["Then I Am"], counts: [81, 77, 52, 34, 25, 11], heldBack: 3 },
  { parents: ["Word for Word", "Front Page"], counts: [78, 77, 48, 33, 24, 19], heldBack: 5 },
  { parents: ["Domino", "Alibi"], counts: [78, 74, 41, 27, 8, 5], heldBack: 1 },
];

const POSITIONS = 394;

const PREFERENCE: Outcomes[] = [
  { label: "Before training", stood: 75, fail: 138, refused: 144 },
  { label: "SFT", stood: 214, fail: 142, refused: 29 },
  { label: "DPO", stood: 201, fail: 134, refused: 54 },
  { label: "SimPO", stood: 195, fail: 105, refused: 90 },
  { label: "IPO", stood: 224, fail: 98, refused: 66 },
  { label: "IPO with refused pairs", stood: 233, fail: 143, refused: 14 },
  { label: "Plus SFT loss", stood: 241, fail: 129, refused: 20 },
  { label: "Round two", stood: 267, fail: 116, refused: 9, ours: true },
];

const TURN: Span[] = [
  { label: "The game checks the player's move", from: 0, to: 0, who: "game" },
  { label: "Flash: ruling and host line, about 5 s", from: 0, to: 5, who: "flash" },
  { label: "The 0.8B model replies, about 2 s", from: 5, to: 7, who: "house" },
  { label: "Flash judges that move, about 5 s", from: 7, to: 12, who: "flash" },
];

const pct = (value: number) => `${value.toFixed(1)}%`;

/** A chart's frame: its title (also the accessible name), a one-line source note, then the drawing. */
function Figure({ title, note, children }: { title: string; note: ReactNode; children: (titleId: string) => ReactNode }) {
  const id = useId();
  return (
    <figure className="writeup-chart">
      <p className="writeup-chart-title" id={id}>
        {title}
      </p>
      <p className="writeup-chart-note">{note}</p>
      {children(id)}
    </figure>
  );
}

function Grid({ top, bottom, ticks }: { top: number; bottom: number; ticks: [number, string][] }) {
  return (
    <>
      <g className="grid">
        {ticks.map(([at]) => (
          <line key={at} x1={`${at}%`} x2={`${at}%`} y1={top} y2={bottom} />
        ))}
      </g>
      {ticks.map(([at, text], i) => (
        <text
          key={at}
          className="tick"
          x={`${at}%`}
          y={bottom + 18}
          textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"}
        >
          {text}
        </text>
      ))}
    </>
  );
}

const PERCENT_TICKS: [number, string][] = [
  [0, "0"],
  [25, "25%"],
  [50, "50%"],
  [75, "75%"],
  [100, "100%"],
];

function ReferenceLine({ reference, bottom }: { reference: Reference; bottom: number }) {
  return (
    <g className="reference">
      <title>{`${reference.label}: ${pct(reference.value)}`}</title>
      <line x1={`${reference.value}%`} x2={`${reference.value}%`} y1="4" y2={bottom} />
      <text x={`${reference.value}%`} dx="-6" y="15" textAnchor="end">
        {reference.label} {pct(reference.value)}
      </text>
    </g>
  );
}

/** Labelled horizontal bars on a 0 to 100% scale, with a reference line. */
function Bars({ titleId, rows, reference }: { titleId: string; rows: BarRow[]; reference: Reference }) {
  const bottom = 26 + rows.length * 52 + 4;
  return (
    <svg height={bottom + 24} role="img" aria-labelledby={titleId}>
      <desc>
        {rows.map((r) => `${r.label} ${pct(r.value)}${r.aside ? `, ${r.aside}` : ""}.`).join(" ")}{" "}
        {`${reference.label} ${pct(reference.value)}.`}
      </desc>
      <Grid top={26} bottom={bottom} ticks={PERCENT_TICKS} />
      {rows.map((row, i) => {
        const top = 26 + i * 52;
        const fill = row.ours ? "ours" : "base";
        return (
          <g key={row.label}>
            <title>{`${row.label}: ${pct(row.value)}`}</title>
            <text className="label" x="0" y={top + 20}>
              {row.label}
              {row.aside && (
                <tspan className="aside" dx="10">
                  {row.aside}
                </tspan>
              )}
            </text>
            <rect className={fill} y={top + 28} width={`${row.value}%`} height="18" rx="4" />
            <rect className={fill} y={top + 28} width="3%" height="18" />
            <text className={row.ours ? "value ours" : "value"} x={`${row.value}%`} dx="8" y={top + 41.5}>
              {pct(row.value)}
            </text>
          </g>
        );
      })}
      <ReferenceLine reference={reference} bottom={bottom} />
    </svg>
  );
}

export function HeadlineChart() {
  return (
    <Figure
      title="Share of moves accepted on games never seen in training"
      note="394 positions from 9 held-back games, every move judged by Flash"
    >
      {(id) => <Bars titleId={id} rows={HEADLINE} reference={{ label: "Flash's own moves", value: 86.5 }} />}
    </Figure>
  );
}

export function BasesChart() {
  return (
    <Figure
      title="Six open models after the same SFT recipe"
      note="Moves that stand on the 394 held-out positions, earlier judge setup, temperature 1.0. Cost is what each run's GPU pod cost."
    >
      {(id) => <Bars titleId={id} rows={BASES} reference={{ label: "Flash", value: 84.0 }} />}
    </Figure>
  );
}

export function RlChart() {
  return (
    <Figure
      title="Before and after RL, on the file the game serves"
      note="Moves that stand on the 394 held-out positions, every move judged by Flash"
    >
      {(id) => <Bars titleId={id} rows={RL} reference={{ label: "Flash's own moves", value: 86.5 }} />}
    </Figure>
  );
}

export function FunnelChart() {
  const most = Math.max(...FAMILIES.map((f) => f.counts[0]));
  return (
    <Figure
      title="Variants left after each filter, per family"
      note="237 variants written from five hand-written games. The last bar's hollow end is the games held back for testing."
    >
      {() => (
        <div className="writeup-funnel">
          {FAMILIES.map((family) => {
            const name = `From ${family.parents.join(" and ")}`;
            const kept = family.counts[family.counts.length - 1];
            return (
              <div key={name}>
                <p className="writeup-funnel-name">
                  From{" "}
                  {family.parents.map((parent, i) => (
                    <span key={parent}>
                      {i > 0 && " and "}
                      <i>{parent}</i>
                    </span>
                  ))}
                </p>
                <svg height={FILTERS.length * 34} role="img" aria-label={name}>
                  <desc>
                    {FILTERS.map((step, i) => `${step}: ${family.counts[i]}.`).join(" ")} {family.heldBack} held back.
                  </desc>
                  {FILTERS.map((step, i) => {
                    const top = i * 34;
                    const count = family.counts[i];
                    const last = i === FILTERS.length - 1;
                    const width = (count / most) * 85;
                    const heldWidth = (family.heldBack / most) * 85;
                    return (
                      <g key={step}>
                        <title>{`${step}: ${count}`}</title>
                        <text className="step" x="0" y={top + 13}>
                          {step}
                        </text>
                        <rect className={last ? "ours" : "base"} y={top + 18} width={`${width}%`} height="10" />
                        {last && (
                          <rect
                            className="held"
                            x={`${width - heldWidth}%`}
                            y={top + 18}
                            width={`${heldWidth}%`}
                            height="10"
                          />
                        )}
                        <text className="value" x={`${width}%`} dx="6" y={top + 27.5}>
                          {last ? `${kept} passed, ${family.heldBack} held back` : count}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            );
          })}
        </div>
      )}
    </Figure>
  );
}

export function TurnChart() {
  const end = Math.max(...TURN.map((s) => s.to));
  const at = (seconds: number) => (seconds / end) * 100;
  const bottom = TURN.length * 46 + 6;
  const ticks: [number, string][] = [0, 2, 4, 6, 8, 10, 12].map((s) => [at(s), s === 0 ? "0" : `${s} s`]);
  return (
    <Figure title="One turn, with times" note="The game's checks are the length limit and repeats. Times are typical, see Serving.">
      {(id) => (
        <svg height={bottom + 24} role="img" aria-labelledby={id}>
          <desc>{TURN.map((s) => `${s.label}.`).join(" ")}</desc>
          <Grid top={0} bottom={bottom} ticks={ticks} />
          {TURN.map((span, i) => {
            const top = i * 46;
            return (
              <g key={span.label}>
                <title>{span.label}</title>
                <text className="label" x="0" y={top + 16}>
                  {span.label}
                </text>
                {span.who === "game" ? (
                  <rect className="mark" y={top + 24} width="4" height="14" />
                ) : (
                  <rect
                    className={span.who === "house" ? "ours" : "base"}
                    x={`${at(span.from)}%`}
                    y={top + 24}
                    width={`${at(span.to - span.from)}%`}
                    height="14"
                  />
                )}
              </g>
            );
          })}
        </svg>
      )}
    </Figure>
  );
}

export function OutcomesChart() {
  const reference = { label: "Flash's own moves", value: 86.5 };
  const bottom = 26 + PREFERENCE.length * 44 + 4;
  const share = (count: number) => (count / POSITIONS) * 100;
  return (
    <Figure
      title="Every Qwen3.5-0.8B model's 394 moves"
      note="Counts after each name: stood · failed by the judge · refused by the game. The small gap left is moves the judge rejected outright."
    >
      {(id) => (
        <>
          <svg height={bottom + 24} role="img" aria-labelledby={id}>
            <desc>
              {PREFERENCE.map(
                (m) => `${m.label}: ${m.stood} stood, ${m.fail} failed by the judge, ${m.refused} refused by the game.`,
              ).join(" ")}
            </desc>
            <Grid top={26} bottom={bottom} ticks={PERCENT_TICKS} />
            {PREFERENCE.map((model, i) => {
              const top = 26 + i * 44;
              const parts: [string, number, string][] = [
                ["ours", model.stood, "stood"],
                ["base", model.fail, "failed by the judge"],
                ["refused", model.refused, "refused by the game"],
              ];
              let x = 0;
              return (
                <g key={model.label}>
                  <title>{`${model.label}: ${pct(share(model.stood))} stood`}</title>
                  <text className={model.ours ? "label ours" : "label"} x="0" y={top + 16}>
                    {model.label}
                    <tspan className="aside" dx="10">
                      {model.stood} · {model.fail} · {model.refused}
                    </tspan>
                  </text>
                  {parts.map(([fill, count, what]) => {
                    const from = x;
                    x += share(count);
                    return (
                      <rect key={what} className={`${fill} split`} x={`${from}%`} y={top + 22} width={`${share(count)}%`} height="14">
                        <title>{`${model.label}: ${count} ${what}`}</title>
                      </rect>
                    );
                  })}
                </g>
              );
            })}
            <ReferenceLine reference={reference} bottom={bottom} />
          </svg>
          <Legend
            items={[
              ["ours", "Stood"],
              ["base", "Failed by the judge"],
              ["refused", "Refused by the game"],
            ]}
          />
        </>
      )}
    </Figure>
  );
}

export function PromptChart() {
  const game = 34;
  const move = 14;
  const rows = [1, 2, 3];
  const panel = (top: number, grows: boolean) =>
    rows.map((moves, r) => {
      const y = top + r * 22;
      const end = game + moves * move;
      return (
        <g key={`${grows}-${moves}`}>
          {grows ? (
            <>
              <rect className={moves === 1 ? "ours split" : "cached"} y={y} width={`${game}%`} height="14" />
              {Array.from({ length: moves }, (_, j) => (
                <rect
                  key={j}
                  className={j === moves - 1 ? "ours split" : "cached"}
                  x={`${game + j * move}%`}
                  y={y}
                  width={`${move}%`}
                  height="14"
                />
              ))}
            </>
          ) : (
            <rect className="ours" y={y} width={`${end}%`} height="14" />
          )}
          <text className="tick" x={`${end}%`} dx="6" y={y + 11}>
            move {moves}
          </text>
        </g>
      );
    });
  return (
    <Figure
      title="The model's prompt, before and after"
      note="Schematic. With one slot per match, each later turn reads about 70 new tokens."
    >
      {(id) => (
        <>
          <svg height="200" role="img" aria-labelledby={id}>
            <desc>
              Before, every move resends one long message and the server reads all of it. After, the conversation grows
              by one move and the server reads only the new part.
            </desc>
            <text className="label" x="0" y="16">
              Before: one message, reread in full
            </text>
            {panel(26, false)}
            <text className="label" x="0" y="122">
              After: a conversation that grows
            </text>
            {panel(132, true)}
          </svg>
          <Legend
            items={[
              ["ours", "Read by the server"],
              ["cached", "Reused from the cache"],
            ]}
          />
        </>
      )}
    </Figure>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return (
    <ul className="writeup-legend">
      {items.map(([fill, text]) => (
        <li key={text}>
          <svg width="14" height="14" aria-hidden="true">
            <rect className={fill} x="1" y="1" width="12" height="12" />
          </svg>
          {text}
        </li>
      ))}
    </ul>
  );
}

type Step = { title: string; lines: string[]; italic?: boolean; href?: string; ours?: boolean };
type Slot = { row: number; col: number; span?: number };
type Layout = { width: number; cols: number; slots: Record<string, Slot> };

const STEPS: Record<string, Step> = {
  games: {
    title: "5 hand-written games",
    lines: ["Then I Am", "Word for Word, Front Page", "Domino, Alibi"],
    italic: true,
  },
  generator: { title: "Generator", lines: ["writes new variants", "of the five games"] },
  filters: { title: "5 filters", lines: ["237 variants written", "35 kept"] },
  training: { title: "31 training games", lines: ["the 5 plus 26 variants"] },
  heldBack: { title: "9 held-back games", lines: ["never used for training"] },
  test: { title: "The test", lines: ["Flash self-play", "394 frozen positions"], href: "#evaluation" },
  matches: { title: "Flash vs Luna", lines: ["3,738 matches", "Flash judges every move", "plus bad-move checks"] },
  examples: { title: "14,941 examples", lines: ["only accepted moves"] },
  sft: { title: "SFT", lines: ["supervised fine-tuning"], href: "#sft" },
  preference: { title: "Preference", lines: ["optimization", "in two rounds"], href: "#preference" },
  rl: { title: "RL", lines: ["GRPO, judge as reward"], href: "#rl" },
  shipped: { title: "The shipped model", lines: ["Qwen3.5-0.8B"], ours: true },
};

const FLOW: [string, string][] = [
  ["games", "generator"],
  ["generator", "filters"],
  ["filters", "training"],
  ["filters", "heldBack"],
  ["heldBack", "test"],
  ["training", "matches"],
  ["matches", "examples"],
  ["examples", "sft"],
  ["sft", "preference"],
  ["preference", "rl"],
  ["rl", "shipped"],
];

const WIDE: Layout = {
  width: 930,
  cols: 5,
  slots: {
    games: { row: 0, col: 0 },
    generator: { row: 0, col: 1 },
    filters: { row: 0, col: 2 },
    training: { row: 0, col: 3 },
    matches: { row: 0, col: 4 },
    heldBack: { row: 1, col: 2 },
    test: { row: 1, col: 3 },
    examples: { row: 1, col: 4 },
    sft: { row: 2, col: 0 },
    preference: { row: 2, col: 1 },
    rl: { row: 2, col: 2 },
    shipped: { row: 2, col: 3 },
  },
};

const NARROW: Layout = {
  width: 340,
  cols: 2,
  slots: {
    games: { row: 0, col: 0, span: 2 },
    generator: { row: 1, col: 0, span: 2 },
    filters: { row: 2, col: 0, span: 2 },
    training: { row: 3, col: 0 },
    heldBack: { row: 3, col: 1 },
    matches: { row: 4, col: 0 },
    test: { row: 4, col: 1 },
    examples: { row: 5, col: 0 },
    sft: { row: 6, col: 0, span: 2 },
    preference: { row: 7, col: 0, span: 2 },
    rl: { row: 8, col: 0, span: 2 },
    shipped: { row: 9, col: 0, span: 2 },
  },
};

/** The data pipeline: games to variants to matches to training stages, as boxes and arrows. */
export function PipelineChart() {
  return (
    <Figure
      title="From five games to the shipped model"
      note="Boxes with a section of their own link to it."
    >
      {(id) => (
        <div className="writeup-pipeline">
          <Pipeline titleId={id} layout={WIDE} className="wide" />
          <Pipeline titleId={id} layout={NARROW} className="narrow" />
        </div>
      )}
    </Figure>
  );
}

function Pipeline({ titleId, layout, className }: { titleId: string; layout: Layout; className: string }) {
  const gap = 20;
  const rowGap = 30;
  const colWidth = (layout.width - gap * (layout.cols - 1)) / layout.cols;
  const rows = Math.max(...Object.values(layout.slots).map((s) => s.row)) + 1;
  const heights = Array.from({ length: rows }, (_, row) =>
    Math.max(...Object.entries(layout.slots).filter(([, s]) => s.row === row).map(([k]) => 30 + STEPS[k].lines.length * 17)),
  );
  const tops: number[] = [];
  let y = 0;
  for (const h of heights) {
    tops.push(y);
    y += h + rowGap;
  }
  const box = (key: string) => {
    const { row, col, span = 1 } = layout.slots[key];
    const x = col * (colWidth + gap);
    return { x, y: tops[row], w: span * colWidth + (span - 1) * gap, h: heights[row], row };
  };
  return (
    <svg className={className} viewBox={`0 0 ${layout.width} ${y - rowGap + 2}`} role="img" aria-labelledby={titleId}>
      <desc>
        Five hand-written games go to a generator, then five filters: 237 variants written, 35 kept. They split into
        31 training games and 9 held-back games. The held-back games become the test, Flash self-play with 394 frozen
        positions. The training games feed 3,738 Flash vs Luna matches, judged by Flash, which give 14,941 training
        examples. Those feed SFT, then two rounds of preference optimization, then RL with GRPO, then the shipped model.
      </desc>
      {FLOW.map(([from, to]) => {
        const a = box(from);
        const b = box(to);
        const bx = b.x + b.w / 2;
        if (a.row === b.row) {
          const end = b.x - 1;
          return <Arrow key={`${from}-${to}`} points={[[a.x + a.w, a.y + a.h / 2], [end, a.y + a.h / 2]]} />;
        }
        const ax = a.x + a.w / 2;
        const mid = a.y + a.h + rowGap / 2;
        const path: [number, number][] =
          Math.abs(ax - bx) < 1
            ? [[ax, a.y + a.h], [bx, b.y - 1]]
            : [[ax, a.y + a.h], [ax, mid], [bx, mid], [bx, b.y - 1]];
        return <Arrow key={`${from}-${to}`} points={path} />;
      })}
      {Object.keys(layout.slots).map((key) => {
        const step = STEPS[key];
        const { x, y: top, w, h } = box(key);
        const body = (
          <g className={step.ours ? "step ours" : "step"}>
            <rect x={x} y={top} width={w} height={h} rx="4" />
            <text className="step-title" x={x + 10} y={top + 21}>
              {step.title}
            </text>
            {step.lines.map((line, i) => (
              <text key={i} className={step.italic ? "step-line italic" : "step-line"} x={x + 10} y={top + 40 + i * 17}>
                {line}
              </text>
            ))}
          </g>
        );
        return step.href ? (
          <a key={key} href={step.href}>
            {body}
          </a>
        ) : (
          <g key={key}>{body}</g>
        );
      })}
    </svg>
  );
}

function Arrow({ points }: { points: [number, number][] }) {
  const [x, y] = points[points.length - 1];
  const [px] = points[points.length - 2];
  const head = px < x ? `${x},${y} ${x - 7},${y - 4} ${x - 7},${y + 4}` : `${x},${y} ${x - 4},${y - 7} ${x + 4},${y - 7}`;
  return (
    <g className="flow">
      <polyline points={points.map((p) => p.join(",")).join(" ")} />
      <polygon points={head} />
    </g>
  );
}
