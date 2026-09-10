import { useEffect, useRef, useState } from "react";

import { api, type SessionView } from "./api.ts";

const PROVIDER_NAMES: Record<string, string> = { google: "Google", github: "GitHub", discord: "Discord" };

/** Sign-in menu or the signed-in name, for the top bar. Providers come from the server. */
export function AccountMenu() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [open, setOpen] = useState(false);
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
        <b>{session.account.display_name}</b>
        <button
          type="button"
          onClick={async () => {
            await api.logout();
            setSession(await api.session());
          }}
        >
          Sign out
        </button>
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
