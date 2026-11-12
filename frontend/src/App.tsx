import { useState, type ReactNode } from "react";
import InstallApp from "./components/InstallApp";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useSession } from "./session";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Home from "./pages/Home";
import Branding from "./pages/Branding";
import Outlets from "./pages/Outlets";
import Users from "./pages/Users";
import MenuEditor from "./pages/MenuEditor";
import MenuImport from "./pages/MenuImport";
import OutletMenu from "./pages/OutletMenu";
import TaxGroups from "./pages/TaxGroups";
import Kds from "./pages/Kds";
import Orders from "./pages/Orders";
import Kitchen from "./pages/Kitchen";
import PrintStation from "./pages/PrintStation";
import EBill from "./pages/EBill";
import Sms from "./pages/Sms";
import CustomerScreen from "./pages/CustomerScreen";
import ScreenAds from "./pages/ScreenAds";
import KitchenScreen from "./pages/KitchenScreen";
import Screens from "./pages/Screens";
import CustomerDisplay from "./pages/CustomerDisplay";
import SelfOrder from "./pages/SelfOrder";
import Bills from "./pages/Bills";
import Inventory from "./pages/Inventory";
import Payments from "./pages/Payments";
import DayClose from "./pages/DayClose";
import Customers from "./pages/Customers";
import Accounts from "./pages/Accounts";
import Access from "./pages/Access";
import Cash from "./pages/Cash";
import Reports from "./pages/Reports";
import GettingStarted from "./pages/GettingStarted";

function Shell({ children }: { children: ReactNode }) {
  const { branding, me, can, signOut } = useSession();
  // [path, label, allowed, group]. The group decides the heading the link sits under in the phone menu.
  const links: [string, string, boolean, string][] = [
    ["/", "Home", can("tenant.settings") || can("menu.override"), "Counter"],
    ["/orders", "Orders", can("kot.send"), "Counter"],
    ["/kds", "Kitchen display", can("kds.view"), "Counter"],
    ["/bills", "Bills", can("billing"), "Counter"],
    ["/cash", "Cash", can("cash.expected"), "Money"],
    ["/day-close", "Day close", can("day.close"), "Money"],
    ["/accounts", "Accounts", can("reports.view"), "Money"],
    ["/reports", "Reports", can("reports.view"), "Business"],
    ["/stock", "Stock", can("stock.count"), "Business"],
    ["/customers", "Customers", can("loyalty.manage"), "Business"],
  ];
  // Set-up pages live on a second row, opened with "Setup", so the daily screens stay one tap away.
  const setup: [string, string, boolean, string][] = [
    ["/start", "Getting started", can("tenant.settings"), "Start here"],
    ["/menu", "Menu", can("menu.manage"), "Menu and tax"],
    ["/outlet-menu", "Outlet menu", can("menu.override"), "Menu and tax"],
    ["/tax", "Tax", can("menu.manage"), "Menu and tax"],
    ["/kitchen", "Kitchen setup", can("menu.manage") || can("outlets.manage"), "Menu and tax"],
    ["/outlets", "Outlets", can("outlets.manage"), "Outlets and screens"],
    ["/screens", "Screens", can("outlets.manage"), "Outlets and screens"],
    ["/screen-ads", "Screen ads", can("ads.manage"), "Outlets and screens"],
    ["/users", "Staff", can("users.manage"), "People"],
    ["/access", "Who can do what", can("users.manage"), "People"],
    ["/payments", "Payments", can("tenant.settings"), "Business details"],
    ["/sms", "SMS", can("tenant.settings"), "Business details"],
    ["/branding", "Branding", can("tenant.settings"), "Business details"],
  ];
  const setupLinks = setup.filter(([, , show]) => show);
  const [showSetup, setShowSetup] = useState(() => { try { return localStorage.getItem("wise.nav_setup") === "1"; } catch { return false; } });
  const onSetup = setupLinks.some(([to]) => location.pathname === to || location.pathname.startsWith(to + "/"));
  // Phones: the links live in a drawer opened from the top bar; it closes when a page opens.
  const [drawer, setDrawer] = useState(false);
  const shown = links.filter(([, , show]) => show);
  // Setup in the phone menu stays folded unless you're already on a setup page.
  const [drawerSetup, setDrawerSetup] = useState(false);
  const setupOpen = drawerSetup || onSetup;
  const grouped = (list: [string, string, boolean, string][]) => {
    const out: [string, [string, string, boolean, string][]][] = [];
    for (const l of list) { const g = out.find(([name]) => name === l[3]); if (g) g[1].push(l); else out.push([l[3], [l]]); }
    return out;
  };
  return (
    <div className={`shell ${me?.training ? "is-training" : ""}`}>
      {me?.training && <div className="training-bar" role="status">Training: practice only. Nothing here is a real bill, payment, stock or sale.</div>}
      <header className="topbar">
        {(shown.length > 1 || setupLinks.length > 0) && (
          <button className="menu-btn" aria-expanded={drawer} aria-controls="app-drawer" onClick={() => setDrawer(!drawer)}>
            <span aria-hidden>{drawer ? "✕" : "☰"}</span><span className="sr-only">{drawer ? "Close menu" : "Open menu"}</span>
          </button>
        )}
        <div className="brand-mark">
          {branding?.logo_url ? <img src={branding.logo_url} alt="" /> : <span className="logo-fallback">{(branding?.app_name || "W")[0]}</span>}
          <strong>{branding?.app_name || "Wise"}</strong>
        </div>
        <div className="topbar-right">
          <InstallApp />
          <span className="muted who">{me?.user.name} · {me?.user.role}</span>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
      </header>
      {drawer && (
        <div className="drawer-back" onClick={() => setDrawer(false)} onKeyDown={(e) => { if (e.key === "Escape") setDrawer(false); }}>
          <nav id="app-drawer" className="drawer" aria-label="Menu"
            onClick={(e) => { e.stopPropagation(); if ((e.target as HTMLElement).closest("a")) setDrawer(false); }}>
            <p className="drawer-who">{me?.user.name} · {me?.user.role}</p>
            {grouped(shown).map(([name, items]) => (
              <section key={name} className="drawer-group" aria-label={name}>
                {shown.length > 4 && <p className="drawer-head">{name}</p>}
                {items.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
              </section>
            ))}
            {setupLinks.length > 0 && (
              <section className="drawer-group drawer-setup">
                <button className="drawer-fold" aria-expanded={setupOpen} aria-controls="drawer-setup-links"
                  onClick={() => setDrawerSetup(!setupOpen)}>
                  <span>Setup</span><span className="muted">{setupOpen ? "Hide" : `${setupLinks.length} pages`}</span>
                  <span aria-hidden>{setupOpen ? "▴" : "▾"}</span>
                </button>
                {setupOpen && (
                  <div id="drawer-setup-links">
                    {grouped(setupLinks).map(([name, items]) => (
                      <div key={name} className="drawer-sub">
                        <p className="drawer-subhead">{name}</p>
                        {items.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
            <button className="link drawer-out" onClick={signOut}>Sign out</button>
          </nav>
        </div>
      )}
      <nav className="nav">
        {shown.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
        ))}
        {setupLinks.length > 0 && <button className={`nav-more ${showSetup || onSetup ? "open" : ""}`} aria-expanded={showSetup || onSetup}
          onClick={() => { const v = !(showSetup || onSetup); setShowSetup(v); try { localStorage.setItem("wise.nav_setup", v ? "1" : "0"); } catch { /* */ } }}>Setup {showSetup || onSetup ? "▴" : "▾"}</button>}
      </nav>
      {(showSetup || onSetup) && setupLinks.length > 0 && (
        <nav className="nav subnav" aria-label="Setup">
          {setupLinks.map(([to, label]) => <NavLink key={to} to={to}>{label}</NavLink>)}
        </nav>
      )}
      <main className="main">{children}</main>
      {branding?.show_powered_by !== false && <footer className="powered">Powered by Wise</footer>}
    </div>
  );
}

/** Where each role lands after signing in. */
function landing(can: (p: string) => boolean): string {
  if (can("tenant.settings") || can("menu.override")) return "/";
  if (can("kot.send")) return "/orders";
  return "/kds";
}

export default function App() {
  const { loading, loginId, me, can } = useSession();
  // The customer TV screen needs no restaurant setup or login.
  const display = location.pathname.match(/^\/display\/([^/]+)/);
  if (display) return <CustomerDisplay displayKey={decodeURIComponent(display[1])} />;
  // The second screen of a dual-screen POS, facing the customer (opened from Orders on the same computer).
  if (location.pathname === "/customer-screen") return <CustomerScreen />;
  // The customer's e-bill from the SMS link.
  const ebill = location.pathname.match(/^\/b\/([^/]+)/);
  if (ebill) return <EBill shareKey={decodeURIComponent(ebill[1])} />;
  // KOT print station on the PC with the USB printer.
  const station = location.pathname.match(/^\/print\/([^/]+)/);
  if (station) return <PrintStation printKey={decodeURIComponent(station[1])} />;
  // Customer self-ordering: QR at the table, or the kiosk touch screen.
  const self = location.pathname.match(/^\/(q|kiosk)\/([^/]+)/);
  if (self) return <SelfOrder key={self[0]} mode={self[1] === "q" ? "qr" : "kiosk"} outletKey={decodeURIComponent(self[2])} />;
  // Kitchen display from its link: signs itself in, no PIN (Setup > Screens).
  const kitchen = location.pathname.match(/^\/kitchen\/([^/]+)/);
  if (kitchen) return <KitchenScreen kitchenKey={decodeURIComponent(kitchen[1])} />;
  if (loading) return <div className="splash">Loading…</div>;

  if (!me) {
    return (
      <Routes>
        <Route path="/signup" element={<Signup />} />
        <Route path="/login" element={loginId ? <Login /> : <Navigate to="/setup" replace />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="*" element={<Navigate to={loginId ? "/login" : "/setup"} replace />} />
      </Routes>
    );
  }

  return (
    <Shell>
      <Routes>
        <Route path="/" element={landing(can) === "/" ? <Home /> : <Navigate to={landing(can)} replace />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/kds" element={<Kds />} />
        <Route path="/kitchen" element={<Kitchen />} />
        <Route path="/bills" element={<Bills />} />
        <Route path="/stock" element={<Inventory />} />
        <Route path="/day-close" element={<DayClose />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/access" element={<Access />} />
        <Route path="/cash" element={<Cash />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/menu" element={<MenuEditor />} />
        <Route path="/menu/import" element={<MenuImport />} />
        <Route path="/outlet-menu" element={<OutletMenu />} />
        <Route path="/tax" element={<TaxGroups />} />
        <Route path="/outlets" element={<Outlets />} />
        <Route path="/users" element={<Users />} />
        <Route path="/branding" element={<Branding />} />
        <Route path="/start" element={<GettingStarted />} />
        <Route path="/sms" element={<Sms />} />
        <Route path="/payments" element={<Payments />} />
        <Route path="/screen-ads" element={<ScreenAds />} />
        <Route path="/screens" element={<Screens />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
