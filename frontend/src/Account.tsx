import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { api, ApiError, type SessionView } from "./api.ts";
import { Link } from "./App.tsx";
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
        <Link className="name" to={`/p/${session.account.id}`}>
          {session.account.display_name}
        </Link>
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
  const [listDuels, setListDuels] = useState(session.list_duels);
  const [step, setStep] = useState<"edit" | "confirm" | "saved">("edit");
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (step === "confirm") setStep("edit");
      else onClose();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [onClose, step]);

  const trimmed = name.trim();
  const changes: string[] = [];
  if (trimmed && trimmed !== account.display_name) {
    changes.push(`Public name: ${account.display_name}, now ${trimmed}.`);
  }
  if (listDuels !== session.list_duels) {
    changes.push(`Duels listed on the stage: ${listDuels ? "yes" : "no"}.`);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (changes.length > 0) setStep("confirm");
  }

  async function save() {
    try {
      const next = await api.updateSession({
        stage_name: trimmed !== account.display_name ? trimmed : undefined,
        list_duels: listDuels !== session.list_duels ? listDuels : undefined,
      });
      onChange(next);
      setStep("saved");
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        setNameError(e.message);
        nameRef.current?.focus();
      } else {
        setError((e as Error).message);
      }
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
        {step === "saved" && <p className="notice">Settings saved.</p>}
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
        <div className={nameError ? "name-field refused" : "name-field"}>
          <label className="small-caps" htmlFor="public-name">
            Public name
          </label>
          <input
            ref={nameRef}
            id="public-name"
            type="text"
            aria-invalid={!!nameError}
            maxLength={40}
            autoFocus
            autoComplete="off"
            data-form-type="other"
            data-lpignore="true"
            data-1p-ignore=""
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setNameError(null);
              setStep("edit");
            }}
          />
          <small>{nameError ?? "Shown on the standings, your replays, and to anyone watching you play."}</small>
        </div>
        <label className="choice">
          <input
            type="checkbox"
            checked={listDuels}
            onChange={(e) => {
              setListDuels(e.target.checked);
              setStep("edit");
            }}
          />
          <span>
            List my duels, so others can watch them live and find the replays.
            <small>Your duels stay private and shareable by link either way.</small>
          </span>
        </label>
        {error && <p className="error-line">{error}</p>}
        <div className="sheet-actions">
          <button className="quiet-button" type="button" onClick={onClose}>
            {step === "saved" ? "Done" : "Close"}
          </button>
          <button className="ticket" type="submit" disabled={changes.length === 0}>
            Save
          </button>
        </div>
      </form>
      {step === "confirm" && (
        <div className="scrim" onClick={(e) => e.target === e.currentTarget && setStep("edit")}>
          <div className="sheet settings confirm-sheet" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
            <h2 id="confirm-title">Save these changes?</h2>
            <ul className="changes">
              {changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            <div className="sheet-actions">
              <button className="quiet-button" type="button" onClick={() => setStep("edit")}>
                Cancel
              </button>
              <button className="ticket" type="button" onClick={save} autoFocus>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
