import { useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, ThemeToggle } from "../App.tsx";
import { api, type DuelRow, type ProfileView, type TemplateView } from "../api.ts";
import { ReplayCard } from "./cards.tsx";
import { prefixOf } from "./format.ts";

const BADGES: { name: string; label: string; glyph: string; meaning: string }[] = [
  { name: "close_call", label: "Close call", glyph: "⚖", meaning: "A ruling too close to end a duel on." },
  { name: "accidental_truth", label: "Accidental truth", glyph: "🎯", meaning: "Your bluff was the real meaning." },
  { name: "near_miss", label: "Near miss", glyph: "◎", meaning: "A bluff a hair from the truth." },
];

const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const LONG_DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });

/** A player's programme: record per game, badges, every duel, best duels. */
export function Profile({ accountId }: { accountId: string }) {
  const [profile, setProfile] = useState<ProfileView | null>(null);
  const [templates, setTemplates] = useState<TemplateView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.profile(accountId).then(setProfile, (e) => setError(e.message));
    api.templates().then(setTemplates, () => setTemplates(null));
  }, [accountId]);

  return (
    <>
      <header className="bar-top">
        <Link className="wordmark" to="/">
          Oddstage
        </Link>
        <span className="round">Programme</span>
        <span className="aside">
          <Link to="/">Home</Link>
          <Link to="/standings">Standings</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </header>

      <main className="wrap programme">
        {error && <p className="page-status">{error}</p>}
        {!profile && !error && <p className="page-status">Finding the programme.</p>}
        {profile && (
          <>
            <Cover profile={profile} />
            <Record profile={profile} />
            <BadgeList profile={profile} />
            <Duels profile={profile} />
            {profile.best.length > 0 && (
              <>
                <h2 className="centered-label small-caps">Best duels</h2>
                <div className="classics">
                  {profile.best.map((replay) => (
                    <ReplayCard key={replay.id} replay={replay} prefix={prefixOf(templates, replay.template_id)} />
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </main>
    </>
  );
}

function Cover({ profile }: { profile: ProfileView }) {
  const rate = profile.played ? Math.round((100 * profile.won) / profile.played) : null;
  const ranked = profile.records.filter((r) => r.rank !== null);
  return (
    <section className="cover">
      <span className="medallion portrait" aria-hidden="true">
        {profile.avatar_url ? <img src={profile.avatar_url} alt="" /> : "🎭"}
      </span>
      <p className="small-caps kicker">Programme</p>
      <h1>{profile.display_name}</h1>
      <p className="since">
        On the bill since {LONG_DAY.format(new Date(profile.since))}.{" "}
        {profile.played === 0
          ? "No duels finished yet."
          : `${profile.played} ${profile.played === 1 ? "duel" : "duels"}, ${profile.won} won${rate === null ? "" : `, a ${rate}% win rate`}.`}
      </p>
      <div className="credits">
        <span className="plaque">
          Streak <b>{profile.streak}</b>
        </span>
        <span className="plaque">
          Best streak <b>{profile.best_streak}</b>
        </span>
        {ranked.map((r) => (
          <span key={r.slug} className="plaque">
            Standing <b>{ordinal(r.rank!)}</b> in {r.title}
          </span>
        ))}
      </div>
    </section>
  );
}

function Record({ profile }: { profile: ProfileView }) {
  if (profile.records.length === 0) return null;
  const total = {
    played: profile.played,
    won: profile.won,
    drawn: profile.records.reduce((n, r) => n + r.drawn, 0),
    best_streak: profile.best_streak,
  };
  const pct = (won: number, played: number) => (played ? `${Math.round((100 * won) / played)}%` : "");
  return (
    <>
      <h2 className="centered-label small-caps">The record</h2>
      <table className="record">
        <thead>
          <tr>
            <th>Game</th>
            <th>Duels</th>
            <th>Won</th>
            <th>Drawn</th>
            <th>Win rate</th>
            <th>Best streak</th>
          </tr>
        </thead>
        <tbody>
          {profile.records.map((r) => (
            <tr key={r.slug}>
              <td>{r.title}</td>
              <td>{r.played}</td>
              <td>
                <b>{r.won}</b>
              </td>
              <td>{r.drawn}</td>
              <td>{pct(r.won, r.played)}</td>
              <td>{r.best_streak}</td>
            </tr>
          ))}
          {profile.records.length > 1 && (
            <tr className="total">
              <td>All games</td>
              <td>{total.played}</td>
              <td>{total.won}</td>
              <td>{total.drawn}</td>
              <td>{pct(total.won, total.played)}</td>
              <td>{total.best_streak}</td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

function BadgeList({ profile }: { profile: ProfileView }) {
  const counts = new Map(profile.badges.map((b) => [b.name, b.count]));
  return (
    <>
      <h2 className="centered-label small-caps">Badges</h2>
      <ul className="badges">
        {BADGES.map((badge) => {
          const count = counts.get(badge.name);
          return (
            <li key={badge.name} className={count ? undefined : "locked"}>
              <span className={count ? "medallion" : "medallion empty"} aria-hidden="true">
                {badge.glyph}
              </span>
              <span className="name">
                {badge.label}
                {count && <small>{count}</small>}
              </span>
              <p className="meaning">
                {badge.meaning}
                {!count && " Not yet."}
              </p>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function Duels({ profile }: { profile: ProfileView }) {
  return (
    <>
      <h2 className="centered-label small-caps">Every duel</h2>
      {profile.duels.length === 0 && (
        <p className="empty-strip">{profile.is_yours ? "No duels yet. The stage is waiting." : "No listed duels yet."}</p>
      )}
      <ol className="duel-list">
        {profile.duels.map((duel) => (
          <DuelLine key={duel.id} duel={duel} mine={profile.is_yours} />
        ))}
      </ol>
    </>
  );
}

function DuelLine({ duel, mine }: { duel: DuelRow; mine: boolean }) {
  const open = duel.status !== "ended" && duel.status !== "abandoned";
  const tone = open || duel.status === "abandoned" ? "closed" : duel.won === null ? "draw" : duel.won ? "won" : "lost";
  return (
    <li>
      <time dateTime={duel.created_at}>{DAY.format(new Date(duel.created_at))}</time>
      <span className="game">
        {duel.title} <small>{open ? "in play" : duel.length}</small>
        {mine && !duel.is_public && duel.status === "ended" && <span className="ribbon quiet">Private</span>}
      </span>
      <span className={`result ${tone}`}>{duel.result}</span>
      {open && mine ? (
        <Link to={`/m/${duel.id}`}>Resume</Link>
      ) : duel.status === "ended" ? (
        <Link to={`/r/${duel.id}`}>Replay</Link>
      ) : (
        <span />
      )}
    </li>
  );
}

function ordinal(n: number): string {
  const rest = n % 100;
  if (rest >= 11 && rest <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}
