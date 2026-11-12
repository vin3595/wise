import { useEffect, useState, type ReactNode } from "react";
import InstallApp from "./components/InstallApp";
import { Link, Navigate, Route, Routes } from "react-router-dom";
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
  // One menu entry per job. An entry with several pages opens on the first one you may use, and the
  // others show as tabs along the top of the page, so the menu stays short.
  type Page = [string, string, boolean];
  type Entry = { label: string; section: string; pages: Page[] };
  const entries: Entry[] = [
    { label: "Home", section: "Counter", pages: [["/", "Home", can("tenant.settings") || can("menu.override")]] },
    { label: "Orders", section: "Counter", pages: [["/orders", "Orders", can("kot.send")]] },
    { label: "Kitchen display", section: "Counter", pages: [["/kds", "Kitchen display", can("kds.view")]] },
    { label: "Bills", section: "Counter", pages: [["/bills", "Bills", can("billing")]] },
    { label: "Cash", section: "Money", pages: [["/cash", "Cash", can("cash.expected")], ["/day-close", "Day close", can("day.close")]] },
    { label: "Reports", section: "Money", pages: [["/reports", "Reports", can("reports.view")], ["/accounts", "Accounts and Tally", can("reports.view")]] },
    { label: "Stock", section: "Stock and customers", pages: [["/stock", "Stock", can("stock.count")]] },
    { label: "Customers", section: "Stock and customers", pages: [["/customers", "Customers", can("loyalty.manage")]] },
    { label: "Getting started", section: "Setup", pages: [["/start", "Getting started", can("tenant.settings")]] },
    { label: "Menu", section: "Setup", pages: [["/menu", "Items", can("menu.manage")], ["/outlet-menu", "Outlet prices", can("menu.override")],
      ["/tax", "Tax", can("menu.manage")], ["/kitchen", "Kitchen and printers", can("menu.manage") || can("outlets.manage")]] },
    { label: "Outlets and screens", section: "Setup", pages: [["/outlets", "Outlets", can("outlets.manage")], ["/screens", "Screens", can("outlets.manage")],
      ["/screen-ads", "Screen ads", can("ads.manage")]] },
    { label: "Staff", section: "Setup", pages: [["/users", "Staff", can("users.manage")], ["/access", "Who can do what", can("users.manage")]] },
    { label: "Settings", section: "Setup", pages: [["/payments", "Payments", can("tenant.settings")], ["/sms", "SMS", can("tenant.settings")],
      ["/branding", "Branding", can("tenant.settings")]] },
  ];
  const path = location.pathname;
  const onPage = (to: string) => to === "/" ? path === "/" : path === to || path.startsWith(to + "/");
  const visible = entries.map((e) => ({ ...e, pages: e.pages.filter(([, , ok]) => ok) })).filter((e) => e.pages.length > 0);
  const daily = visible.filter((e) => e.section !== "Setup");
  const setupEntries = visible.filter((e) => e.section === "Setup");
  const current = visible.find((e) => e.pages.some(([to]) => onPage(to)));
  const onSetup = current?.section === "Setup";
  const [showSetup, setShowSetup] = useState(() => { try { return localStorage.getItem("wise.nav_setup") === "1"; } catch { return false; } });
  // Phones: the links live in a drawer opened from the top bar; it closes when a page opens.
  const [drawer, setDrawer] = useState(false);
  // Keep the open tab in view when the tab row is wider than a phone.
  useEffect(() => { try { document.querySelector(".page-tabs a.on")?.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch { /* old browsers */ } }, [path]);
  const entryLink = (e: Entry) => (
    <Link key={e.label} to={e.pages[0][0]} className={current?.label === e.label ? "active" : ""}>{e.label}</Link>
  );
  const sections = (list: Entry[]) => {
    const out: [string, Entry[]][] = [];
    for (const e of list) { const g = out.find(([n]) => n === e.section); if (g) g[1].push(e); else out.push([e.section, [e]]); }
    return out;
  };
  return (
    <div className={`shell ${me?.training ? "is-training" : ""}`}>
      {me?.training && <div className="training-bar" role="status">Training: practice only. Nothing here is a real bill, payment, stock or sale.</div>}
      <header className="topbar">
        {visible.length > 1 && (
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
            {sections(visible).map(([name, list]) => (
              <section key={name} className="drawer-group" aria-label={name}>
                {visible.length > 4 && <p className="drawer-head">{name}</p>}
                {list.map(entryLink)}
              </section>
            ))}
            <button className="link drawer-out" onClick={signOut}>Sign out</button>
          </nav>
        </div>
      )}
      <nav className="nav">
        {daily.map(entryLink)}
        {setupEntries.length > 0 && <button className={`nav-more ${showSetup || onSetup ? "open" : ""}`} aria-expanded={showSetup || onSetup}
          onClick={() => { const v = !(showSetup || onSetup); setShowSetup(v); try { localStorage.setItem("wise.nav_setup", v ? "1" : "0"); } catch { /* */ } }}>Setup {showSetup || onSetup ? "▴" : "▾"}</button>}
      </nav>
      {(showSetup || onSetup) && setupEntries.length > 0 && (
        <nav className="nav subnav" aria-label="Setup">{setupEntries.map(entryLink)}</nav>
      )}
      {current && current.pages.length > 1 && (
        <nav className="page-tabs" aria-label={current.label}>
          {current.pages.map(([to, label]) => <Link key={to} to={to} className={onPage(to) ? "on" : ""} aria-current={onPage(to) ? "page" : undefined}>{label}</Link>)}
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
