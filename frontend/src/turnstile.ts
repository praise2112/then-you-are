interface Turnstile {
  render(container: HTMLElement, options: Record<string, unknown>): string;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

let script: Promise<Turnstile> | null = null;

function loadTurnstile(): Promise<Turnstile> {
  script ??= new Promise<Turnstile>((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    tag.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("no turnstile")));
    tag.onerror = () => {
      script = null;
      reject(new Error("no turnstile"));
    };
    document.head.append(tag);
  });
  return script;
}

/** A fresh Turnstile token; the widget shows a checkbox only when Cloudflare asks for one. */
export async function humanToken(siteKey: string): Promise<string> {
  const turnstile = await loadTurnstile();
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:50";
  document.body.append(box);
  let widget = "";
  try {
    return await new Promise<string>((resolve, reject) => {
      widget = turnstile.render(box, {
        sitekey: siteKey,
        appearance: "interaction-only",
        callback: resolve,
        "error-callback": () => reject(new Error("turnstile failed")),
      });
    });
  } finally {
    if (widget) turnstile.remove(widget);
    box.remove();
  }
}
