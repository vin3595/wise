import { useEffect, useState } from "react";

/** Chrome's install prompt, caught as early as possible (main.tsx) so the button can offer it later. */
type InstallEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };
declare global { interface Window { __wiseInstall?: InstallEvent | null } }

export function catchInstallPrompt() {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    window.__wiseInstall = e as InstallEvent;
    window.dispatchEvent(new Event("wise-installable"));
  });
  window.addEventListener("appinstalled", () => { window.__wiseInstall = null; window.dispatchEvent(new Event("wise-installable")); });
}

const installed = () => window.matchMedia?.("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

/** "Install app" in the top bar: one tap on Android and Windows Chrome; on iPhone it shows the two steps. */
export default function InstallApp({ full = false }: { full?: boolean }) {
  const [ready, setReady] = useState(!!window.__wiseInstall);
  const [iosHelp, setIosHelp] = useState(false);
  useEffect(() => {
    const on = () => setReady(!!window.__wiseInstall);
    window.addEventListener("wise-installable", on);
    return () => window.removeEventListener("wise-installable", on);
  }, []);
  if (installed()) return full ? <p className="muted small">Wise is installed on this device. It opens from its own icon.</p> : null;
  const ios = isIos();
  if (!ready && !ios) {
    return full ? <p className="muted small">Open this page in Chrome or Edge to install it. If you don't see an install option, Wise may already be installed, or the browser menu ⋮ has "Install app" / "Add to Home screen".</p> : null;
  }
  const go = async () => {
    if (ios) { setIosHelp(true); return; }
    const e = window.__wiseInstall; if (!e) return;
    await e.prompt(); await e.userChoice.catch(() => null);
    window.__wiseInstall = null; setReady(false);
  };
  return (
    <>
      <button className={full ? "btn" : "link install-link"} onClick={go}>Install app</button>
      {iosHelp && (
        <div className="modal-back" onClick={() => setIosHelp(false)}>
          <div className="modal" role="dialog" aria-label="Install on iPhone or iPad" onClick={(e) => e.stopPropagation()}>
            <h3>Install on iPhone or iPad</h3>
            <ol className="install-steps">
              <li>Open this page in <strong>Safari</strong>.</li>
              <li>Tap the <strong>Share</strong> button (the square with an arrow).</li>
              <li>Tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.</li>
            </ol>
            <button className="btn" onClick={() => setIosHelp(false)}>Done</button>
          </div>
        </div>
      )}
    </>
  );
}
