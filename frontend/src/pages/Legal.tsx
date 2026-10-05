import type { ReactNode } from "react";

import { Link, ThemeToggle, TopBar } from "../App.tsx";

const UPDATED = "5 October 2026";

export type LegalSlug = "privacy" | "terms";

/** The privacy page and the terms, one plain-text page each. */
export function LegalPage({ slug }: { slug: LegalSlug }) {
  const { title, body } = PAGES[slug];
  return (
    <>
      <TopBar>
        <span className="aside">
          <Link to="/">Home</Link>
          <ThemeToggle icon />
        </span>
      </TopBar>
      <main className="legal">
        <h1>{title}</h1>
        <p className="updated">Last updated {UPDATED}</p>
        {body}
      </main>
    </>
  );
}

const mail = <a href="mailto:hello@thenyouare.com">hello@thenyouare.com</a>;

const PAGES: Record<LegalSlug, { title: string; body: ReactNode }> = {
  privacy: {
    title: "Privacy",
    body: (
      <>
        <p>
          Then You Are keeps only what the game needs to work. There are no ads, no trackers and no analytics, and we
          never collect your email address. This page goes with our <Link to="/terms">terms</Link>.
        </p>

        <h2>What we collect</h2>
        <ul>
          <li>The stage name you choose, your moves and your matches.</li>
          <li>
            If you sign in with Google, GitHub or Discord, only your name and profile picture from that service.
          </li>
          <li>One cookie, so the game remembers you between visits.</li>
          <li>Your IP address, to prevent abuse and cheating.</li>
        </ul>

        <h2>How we use it</h2>
        <p>
          To run the game: saving your matches, scoring your moves, and showing your name on replays and the
          standings. Your moves are never used to train AI models, and we never sell your data.
        </p>

        <h2>How we share it</h2>
        <p>
          Your moves go to the hosting and AI providers that run and score the game, and nowhere else. Some of them
          are outside the European Union. Matches you make public, and your profile, can be seen by anyone.
        </p>

        <h2>How long we keep it</h2>
        <ul>
          <li>Your account and matches, for as long as you keep the account.</li>
          <li>Guest play, for 12 months after your last visit.</li>
          <li>IP addresses, for 14 days.</li>
        </ul>
        <p>
          When you delete your account, your games against the House go with it. In games with other players, your
          moves stay as "Deleted player" so their replays still work.
        </p>

        <h2>Your choices</h2>
        <p>
          You can rename or delete your account at any time from your account settings. For anything else, write to{" "}
          {mail}. You can also contact your data protection authority.
        </p>

        <h2>Changes</h2>
        <p>If this page changes, the date at the top changes with it.</p>
      </>
    ),
  },
  terms: {
    title: "Terms",
    body: (
      <>
        <p>
          Then You Are is free to play. By playing you accept these terms and our{" "}
          <Link to="/privacy">privacy page</Link>.
        </p>

        <h2>Who can play</h2>
        <p>
          Anyone can play as a guest. Signing in needs you to be 15 or older, and one account per person keeps the
          standings fair.
        </p>

        <h2>Play fair</h2>
        <ul>
          <li>No hateful, sexual or threatening moves or names.</li>
          <li>No moves written to trick the judge instead of playing the game.</li>
          <li>No bots or automated play.</li>
        </ul>
        <p>Moves, names or accounts that break these rules may be removed, to keep the game good for everyone.</p>

        <h2>Your moves</h2>
        <p>
          Your moves stay yours. By playing, you let us show them in matches, replays and the standings.
        </p>

        <h2>The game as it is</h2>
        <p>
          We work to keep the game running and fair, but it comes as it is, without guarantees. Scores come from an
          automatic judge and are part of the fun, not a final verdict. We may change or retire games over time.
        </p>

        <h2>Changes</h2>
        <p>If these terms change, the date at the top changes with them.</p>

        <h2>Contact</h2>
        <p>
          Write to {mail}. Then You Are is published by a private individual and hosted by DA International Group Ltd.
          (AlphaVPS), Sofia, Bulgaria. These terms follow French law.
        </p>
      </>
    ),
  },
};
