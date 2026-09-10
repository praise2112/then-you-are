import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { api, type SessionView } from "./api.ts";

const PROVIDER_NAMES: Record<string, string> = { google: "Google", github: "GitHub", discord: "Discord" };

/** Sign-in menu for guests; name and settings gear for signed-in players. Providers come from the server. */
export function AccountMenu() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(false);
  const menuRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    api.session().then(setSession, () => setSession(null));
  }, []);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    addEventListener("click", close);
    return () => removeEventListener("click", close);
  }, [open]);

  if (!session || session.providers.length === 0) return null;
  const back = encodeURIComponent(location.pathname);

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
            <SettingsSheet session={session} onChange={setSession} onClose={() => setSettings(false)} />,
            document.body,
          )}
      </span>
    );
  }
  return (
    <span className="account" ref={menuRef}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Sign in
      </button>
      {open && (
        <span className="signin-menu" role="menu">
          {session.providers.map((p) => (
            <a key={p} role="menuitem" href={`/auth/${p}/login?next=${back}`}>
              {PROVIDER_NAMES[p] ?? p}
            </a>
          ))}
        </span>
      )}
    </span>
  );
}

function SettingsSheet({
  session,
  onChange,
  onClose,
}: {
  session: SessionView;
  onChange: (session: SessionView) => void;
  onClose: () => void;
}) {
  const account = session.account!;
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
        <p className="who">
          {account.avatar_url && <img src={account.avatar_url} alt="" />}
          <span>Signed in with {PROVIDER_NAMES[account.provider] ?? account.provider}.</span>
        </p>
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
