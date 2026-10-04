import { useCallback, useEffect, useState } from "react";

import { AccountMenu } from "../Account.tsx";
import { Link, navigate, ThemeToggle, TopBar } from "../App.tsx";
import { api, usePresence, type SocketMessage, type TableView, type TemplateView } from "../api.ts";
import { store } from "../store.ts";

/** Every open table, live over the socket, and a quick seat per game. */
export function LobbyPage() {
  const [tables, setTables] = useState<TableView[] | null>(null);
  const [online, setOnline] = useState<number | null>(null);
  const [templates, setTemplates] = useState<TemplateView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.lobby().then(setTables, (e) => setError(e.message));
    api.templates().then(setTemplates, () => setTemplates([]));
  }, []);

  const onMessage = useCallback((message: SocketMessage) => {
    if (message.type === "lobby") setTables(message.tables);
    if (message.type === "online") setOnline(message.count);
  }, []);
  usePresence(onMessage);

  async function sit(action: () => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      navigate(`/m/${await action()}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const name = store.stageName() || undefined;
  return (
    <>
      <TopBar>
        <span className="round">Open tables{online !== null && `, ${online} online`}</span>
        <span className="aside">
          <Link to="/games">Games</Link>
          <AccountMenu />
          <ThemeToggle icon />
        </span>
      </TopBar>
      <main className="wrap lobby">
        <ul className="table-list">
          {tables?.length === 0 && <li className="empty">No open tables. Take a quick seat below and one opens.</li>}
          {tables?.map((t) => (
            <li key={t.id}>
              <span className="emblem" aria-hidden="true">
                {t.emblem}
              </span>
              <span className="what">
                <b>{t.title}</b>
                <small>{t.host_name}&rsquo;s table</small>
              </span>
              <span className="count">
                {t.seats_taken} of {t.seats_wanted}
              </span>
              <button className="ticket" type="button" disabled={busy} onClick={() => sit(async () => (await api.joinTable(t.invite_code, name)).match_id)}>
                Sit down
              </button>
            </li>
          ))}
        </ul>
        <p className="small-caps centered-label">Quick seat</p>
        <div className="poster-grid">
          {templates.map((t) => (
            <button key={t.slug} type="button" className="poster" disabled={busy} onClick={() => sit(async () => (await api.quickMatch(t.slug, 2, name)).match_id)}>
              <span className="emblem" aria-hidden="true">
                {t.emblem}
              </span>
              <h3>{t.title}</h3>
            </button>
          ))}
        </div>
        {error && <p className="hint error">{error}</p>}
      </main>
    </>
  );
}

/** An invite link: take the seat under your stage name, then go to the table. */
export function JoinPage({ code }: { code: string }) {
  const [name, setName] = useState(store.stageName());
  const [error, setError] = useState<string | null>(null);
  const [joining, setJoining] = useState(false);

  // A signed-in player sits down under their account name; a returning guest under theirs.
  useEffect(() => {
    api.session().then(
      (s) => {
        const known = s.account?.display_name ?? (s.stage_name !== "Challenger" ? s.stage_name : "");
        setName((typed) => typed || known);
      },
      () => undefined,
    );
  }, []);

  async function join() {
    setJoining(true);
    setError(null);
    try {
      const { match_id } = await api.joinTable(code, name.trim() || undefined);
      if (name.trim()) store.setStageName(name.trim());
      navigate(`/m/${match_id}`);
    } catch (e) {
      setError((e as Error).message);
      setJoining(false);
    }
  }

  return (
    <>
      <TopBar>
        <span className="round">An invitation</span>
        <ThemeToggle icon />
      </TopBar>
      <main className="wrap">
        <section className="hero">
          <form
            className="torn waiting-room"
            onSubmit={(e) => {
              e.preventDefault();
              void join();
            }}
          >
            <h1 className="board-title">
              Take a seat
              <small>Someone saved you a place at their table.</small>
            </h1>
            <label className="small-caps" htmlFor="stage-name">
              Your stage name
            </label>
            <div className="compose">
              <span className="field">
                <input
                  id="stage-name"
                  type="text"
                  maxLength={24}
                  placeholder="Challenger"
                  autoComplete="off"
                  data-form-type="other"
                  data-lpignore="true"
                  data-1p-ignore=""
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </span>
              <button className="ticket" type="submit" disabled={joining}>
                {joining ? "Sitting down" : "Sit down"}
              </button>
            </div>
            {error && <p className="hint error">{error}</p>}
          </form>
        </section>
      </main>
    </>
  );
}
