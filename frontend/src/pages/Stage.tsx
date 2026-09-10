import { useEffect, useState } from "react";

import { Link, ThemeToggle } from "../App.tsx";
import { api, type Replay, type ReplaySort, type StageView, type TemplateView } from "../api.ts";
import { LiveCard, ReplayCard } from "./cards.tsx";
import { prefixOf } from "./format.ts";

const SORTS: { key: ReplaySort; label: string }[] = [
  { key: "curated", label: "Curated" },
  { key: "newest", label: "Newest" },
  { key: "longest", label: "Longest run this week" },
];

/** Public matches in play and every listed replay. Only duels their players chose to list appear. */
export function StagePage() {
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [stage, setStage] = useState<StageView | null>(null);
  const [sort, setSort] = useState<ReplaySort>("newest");
  const [lists, setLists] = useState<Partial<Record<ReplaySort, Replay[]>>>({});
  useEffect(() => {
    api.templates().then(setTemplates, () => setTemplates(null));
    api.stage().then(setStage, () => setStage(null));
  }, []);
  useEffect(() => {
    if (lists[sort]) return;
    api.replays(sort).then(
      (rows) => setLists((l) => ({ ...l, [sort]: rows })),
      () => setLists((l) => ({ ...l, [sort]: [] })),
    );
  }, [sort, lists]);
  const replays = lists[sort];

  return (
    <>
      <header className="bar-top">
        <Link className="wordmark" to="/">
          Oddstage
        </Link>
        <span className="round">Watch</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <ThemeToggle icon />
        </span>
      </header>

      <main className="wrap">
        <section id="on-stage">
          <h2 className="centered-label small-caps">Live now</h2>
          {stage === null && <p className="empty-strip">Looking in on the house.</p>}
          {stage?.live.length === 0 && (
            <p className="empty-strip">Nobody is playing in public right now. Duels are private unless their player lists them.</p>
          )}
          {stage && stage.live.length > 0 && (
            <div className="live-row">
              {stage.live.map((m) => (
                <LiveCard key={m.id} match={m} prefix={prefixOf(templates, m.template_id)} />
              ))}
            </div>
          )}
        </section>

        <p className="fleuron" aria-hidden="true">❧</p>

        <section id="replays">
          <h2 className="centered-label small-caps">Replays</h2>
          <div className="tabs" role="tablist">
            {SORTS.map(({ key, label }) => (
              <button key={key} role="tab" type="button" aria-selected={sort === key} onClick={() => setSort(key)}>
                {label}
              </button>
            ))}
          </div>
          <div className="classics">
            {replays === undefined && <p className="empty-strip">Fetching the archive.</p>}
            {replays?.length === 0 && (
              <p className="empty-strip">
                {sort === "curated" ? "No duels curated yet." : "No listed duels yet. Yours could be the first."}
              </p>
            )}
            {replays?.map((replay) => (
              <ReplayCard key={replay.id} replay={replay} prefix={prefixOf(templates, replay.template_id)} />
            ))}
          </div>
        </section>
      </main>
    </>
  );
}
