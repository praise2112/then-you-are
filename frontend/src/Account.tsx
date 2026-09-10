import { useEffect, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { api, type SessionView } from "./api.ts";
import { PROVIDER_MARKS } from "./providerMarks.ts";

const PROVIDER_NAMES: Record<string, string> = { google: "Google", github: "GitHub", discord: "Discord" };

/** Sign-in menu for guests; name and settings gear for signed-in players. Providers come from the server. */
/** One button per provider, each a plain link into the redirect flow. */
function ProviderButtons({ providers, verb }: { providers: string[]; verb: string }) {
  const back = encodeURIComponent(location.pathname);
  return (
    <div className="providers">
      {providers.map((p) => (
        <a key={p} className="provider" href={`/auth/${p}/login?next=${back}`}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d={PROVIDER_MARKS[p]} />
          </svg>
          {verb} {PROVIDER_NAMES[p] ?? p}
        </a>
      ))}
    </div>
  );
}

/** What the sign-in callback reports back in the URL after a link attempt. */
type Notice = { kind: "linked" | "taken"; provider: string; name: string } | null;

function takeNotice(): Notice {
  const params = new URLSearchParams(location.search);
  const raw = params.get("account");
  if (!raw) return null;
  params.delete("account");
  const query = params.toString();
  history.replaceState(null, "", location.pathname + (query ? `?${query}` : ""));
  const [kind, provider, ...name] = raw.split(":");
  if (kind !== "linked" && kind !== "taken") return null;
  return { kind, provider, name: name.join(":") };
}

export function AccountMenu() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [open, setOpen] = useState(false);
  const [notice] = useState<Notice>(takeNotice);
  const [settings, setSettings] = useState(notice !== null);
  useEffect(() => {
    api.session().then(setSession, () => setSession(null));
  }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open]);

  if (!session || session.providers.length === 0) return null;

  if (session.account) {
    return (
      <span className="account">
        {session.account.avatar_url && <img src={session.account.avatar_url} alt="" />}
        <button className="name" type="button" title="Change your public name" onClick={() => setSettings(true)}>
          {session.account.display_name}
        </button>
        <button
          type="button"
          onClick={async () => {
            await api.logout();
            setSession(await api.session());
          }}
        >
          Sign out
        </button>
        <button className="gear" type="button" aria-label="Account settings" title="Account settings" onClick={() => setSettings(true)}>
          ⚙
        </button>
        {settings &&
          createPortal(
            <SettingsSheet session={session} notice={notice} onChange={setSession} onClose={() => setSettings(false)} />,
            document.body,
          )}
      </span>
    );
  }
  return (
    <span className="account">
      <button type="button" onClick={() => setOpen(true)}>
        Sign in
      </button>
      {open &&
        createPortal(
          <div className="scrim" onClick={(e) => e.target === e.currentTarget && setOpen(false)}>
            <div className="sheet settings" role="dialog" aria-modal="true" aria-labelledby="signin-title">
              <h2 id="signin-title">Sign in</h2>
              <p className="lede">
                Keep your duels under one name and take a place on the standings. No password: Oddstage only
                receives your name and avatar.
              </p>
              <ProviderButtons providers={session.providers} verb="Continue with" />
              <div className="sheet-actions">
                <button className="quiet-button" type="button" onClick={() => setOpen(false)}>
                  Not now
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </span>
  );
}

function SettingsSheet({
  session,
  notice,
  onChange,
  onClose,
}: {
  session: SessionView;
  notice: Notice;
  onChange: (session: SessionView) => void;
  onClose: () => void;
}) {
  const account = session.account!;
  const label = (p: string) => PROVIDER_NAMES[p] ?? p;
  const unlinked = session.providers.filter((p) => !account.providers.includes(p));
  const [name, setName] = useState(account.display_name);
  const [step, setStep] = useState<"edit" | "confirm" | "saved">("edit");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose]);

  const trimmed = name.trim();
  const changed = trimmed !== "" && trimmed !== account.display_name;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (changed) setStep("confirm");
  }

  async function save() {
    try {
      onChange(await api.updateSession({ stage_name: trimmed }));
      setStep("saved");
    } catch (e) {
      setError((e as Error).message);
      setStep("edit");
    }
  }

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="sheet settings" role="dialog" aria-modal="true" aria-labelledby="settings-title" onSubmit={submit}>
        <h2 id="settings-title">Your account</h2>
        {notice?.kind === "linked" && (
          <p className="notice">
            {label(notice.provider)} is now linked. Signing in with it lands on this account.
          </p>
        )}
        {notice?.kind === "taken" && (
          <p className="notice warn">
            That {label(notice.provider)} account is already its own Oddstage account, <b>{notice.name}</b>. To use
            it, sign out and sign in with {label(notice.provider)}.
          </p>
        )}
        <p className="who">
          {account.avatar_url && <img src={account.avatar_url} alt="" />}
          <span>Signed in with {account.providers.map(label).join(" and ")}.</span>
        </p>
        {unlinked.length > 0 && (
          <div className="link-row">
            <span className="small-caps">Also sign in with</span>
            <ProviderButtons providers={unlinked} verb="Link" />
          </div>
        )}
        <div className="name-field">
          <label className="small-caps" htmlFor="public-name">
            Public name
          </label>
          <input
            id="public-name"
            type="text"
            maxLength={40}
            autoFocus
            autoComplete="off"
            data-form-type="other"
            data-lpignore="true"
            data-1p-ignore=""
            value={name}
            disabled={step !== "edit"}
            onChange={(e) => {
              setName(e.target.value);
              setStep("edit");
            }}
          />
          <small>Shown on the standings, your replays, and to anyone watching you play.</small>
        </div>
        {error && <p className="error-line">{error}</p>}
        {step === "confirm" && (
          <p className="confirm">
            Show up as <b>{trimmed}</b> from now on? Past replays change too.
          </p>
        )}
        {step === "saved" && <p className="confirm saved">Saved. You are {account.display_name} on the standings.</p>}
        <div className="sheet-actions">
          {step === "confirm" ? (
            <>
              <button className="quiet-button" type="button" onClick={() => setStep("edit")}>
                Keep {account.display_name}
              </button>
              <button className="ticket" type="button" onClick={save} autoFocus>
                Yes, change it
              </button>
            </>
          ) : (
            <>
              <button className="quiet-button" type="button" onClick={onClose}>
                {step === "saved" ? "Done" : "Close"}
              </button>
              <button className="ticket" type="submit" disabled={!changed}>
                Save
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
