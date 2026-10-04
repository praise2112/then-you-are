import type { ReactNode } from "react";

import { Link, ThemeToggle, TopBar } from "../App.tsx";

const CONTACT = "hello@thenyouare.com";
const UPDATED = "4 October 2026";

export type LegalSlug = "privacy" | "terms" | "legal";

/** The privacy policy, the terms and the legal notice, one plain-text page each. */
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

const mail = <a href={`mailto:${CONTACT}`}>{CONTACT}</a>;

const PAGES: Record<LegalSlug, { title: string; body: ReactNode }> = {
  privacy: {
    title: "Privacy",
    body: (
      <>
        <p>
          Then You Are is a word game run by one person as a hobby project, with no ads and no analytics. This page
          says what the game stores about you, who else sees it, and how to have it deleted. Questions go to {mail}.
        </p>

        <h2>What is stored</h2>
        <ul>
          <li>
            A random session key in a cookie, so the game knows your browser. It lasts a year and is the only cookie,
            apart from a short-lived one while you sign in.
          </li>
          <li>The stage name you type, your moves, guesses and disagree votes, and the matches they belong to.</li>
          <li>
            If you sign in with Google, GitHub or Discord: your name and profile picture from that service, and the id
            it gives your account there. Never your email address.
          </li>
          <li>Server logs of each request, with your IP address, kept for 14 days for security and fixing faults.</li>
        </ul>
        <p>
          Your browser also keeps a few settings in its own storage, like the colour theme and your streak. They stay
          on your device.
        </p>

        <h2>Why</h2>
        <p>
          Everything above is what it takes to run the game you chose to play. The logs are kept because a public
          site has to be able to spot abuse and fix faults.
        </p>

        <h2>Who else sees it</h2>
        <ul>
          <li>AlphaVPS hosts the server and its database in Nuremberg, Germany.</li>
          <li>Google Cloud, in Belgium, runs the House and receives the moves of games played against it.</li>
          <li>
            DeepSeek, in China, judges every move and sometimes stands in for the House. Moves reach it through
            OpenRouter, in the United States. This means your moves leave the European Union.
          </li>
          <li>
            Langfuse, in the European Union, receives the timing, size and errors of those calls, never the moves.
          </li>
          <li>Cloudflare runs the domain and forwards mail sent to {mail}.</li>
          <li>Google, GitHub or Discord learn that you signed in here, if you use them to sign in.</li>
        </ul>
        <p>Your moves are not used to train models.</p>

        <h2>What is public</h2>
        <p>
          Your profile page shows your name, picture and record. A match you list can be watched live and replayed by
          anyone, with every move and stage name in it. Unlisted matches can still be opened by anyone you send the
          link to.
        </p>

        <h2>How long it is kept</h2>
        <ul>
          <li>An account and its matches: until you delete the account.</li>
          <li>A guest, who never signed in: 12 months after the last visit.</li>
          <li>Request logs: 14 days.</li>
        </ul>
        <p>
          When an account or guest is deleted, games against the House go with it. Moves in games with other players
          stay, so their replays still work, but they are shown under "Deleted player" with no name or picture.
        </p>

        <h2>Your rights</h2>
        <p>
          You can change your name in your account settings and delete your account from there too. For a copy of
          your data, or anything else, write to {mail} and you will get an answer within a month. You can also
          complain to the CNIL, France's data protection authority, at <a href="https://www.cnil.fr">cnil.fr</a>.
        </p>

        <h2>Age</h2>
        <p>You must be 15 or older to sign in. Anyone can play as a guest.</p>
      </>
    ),
  },
  terms: {
    title: "Terms",
    body: (
      <>
        <p>
          These terms apply when you play Then You Are. By playing you accept them. Questions go to {mail}.
        </p>

        <h2>The game</h2>
        <p>
          The game is free and comes as it is, with no guarantee that it works, stays online or keeps your matches.
          Scores come from an automatic judge and can be wrong. They are part of the game, not a statement of fact.
        </p>

        <h2>Accounts</h2>
        <p>
          You must be 15 or older to sign in. One account per person. You can delete your account at any time from
          your account settings.
        </p>

        <h2>Playing fair</h2>
        <ul>
          <li>No hateful, sexual or threatening moves, names or stage names.</li>
          <li>No moves meant to break or trick the judge rather than play the game.</li>
          <li>No automated play or attempts to overload the site.</li>
        </ul>
        <p>
          Moves, names and matches that break these rules can be removed, and accounts that keep breaking them can be
          closed, without notice.
        </p>

        <h2>What others can see</h2>
        <p>
          Listed matches are public: anyone can watch and replay them, with every move and stage name in them. The{" "}
          <Link to="/privacy">privacy page</Link> says what else is shown and kept.
        </p>

        <h2>Changes</h2>
        <p>
          These terms can change. The date at the top shows the latest version, and playing after a change means you
          accept it.
        </p>
      </>
    ),
  },
  legal: {
    title: "Legal notice",
    body: (
      <>
        <h2>Publisher</h2>
        <p>
          Then You Are is published by a private individual, not as a business. As French law allows, the
          publisher's identity is held by the host. Contact: {mail}.
        </p>

        <h2>Host</h2>
        <p>
          DA International Group Ltd. (AlphaVPS)
          <br />8 Asen Yordanov Blvd, Sofia, Bulgaria
          <br />
          Company number 202826767
          <br />
          Contact: <a href="https://alphavps.com/clients/submitticket.php">alphavps.com support</a>
        </p>
      </>
    ),
  },
};
