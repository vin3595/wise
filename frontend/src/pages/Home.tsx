import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { formatPaise } from "../money";
import { useSession } from "../session";
import { Card } from "../ui";
import CashAlerts from "../components/CashAlerts";
import OwnerAlerts from "../components/OwnerAlerts";

type Today = { bills: number; sales_paise: number; avg_bill_paise: number; cancelled_bills: number; discount_paise: number };

/** Today's numbers first: what the owner opens the app to see. */
function TodayCard() {
  const [t, setT] = useState<Today | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    const load = () => api<Today>("/reports/summary").then((x) => { if (live) { setT(x); setFailed(false); } }).catch(() => live && setFailed(true));
    load();
    const id = setInterval(load, 60000);
    return () => { live = false; clearInterval(id); };
  }, []);
  return (
    <Card title="Today">
      {failed && !t ? <p className="muted">Couldn't load today's numbers. They'll appear when the connection is back.</p>
        : !t ? <p className="muted" role="status">Loading today's sales…</p>
        : t.bills === 0 ? <p className="muted">No bills yet today. Sales show here as soon as the first bill is settled.</p>
        : (
          <div className="today">
            <div><span>Sales</span><strong>{formatPaise(t.sales_paise)}</strong></div>
            <div><span>Bills</span><strong>{t.bills}</strong></div>
            <div><span>Average bill</span><strong>{formatPaise(t.avg_bill_paise)}</strong></div>
            <div><span>Discounts</span><strong>{formatPaise(t.discount_paise)}</strong></div>
            {t.cancelled_bills > 0 && <div><span>Cancelled</span><strong>{t.cancelled_bills}</strong></div>}
          </div>
        )}
      <p className="small"><Link to="/reports">All reports</Link></p>
    </Card>
  );
}

export default function Home() {
  const { me, can, branding } = useSession();
  if (!me) return null;
  const trialDays = me.tenant.trial_ends_at
    ? Math.max(0, Math.ceil((new Date(me.tenant.trial_ends_at).getTime() - Date.now()) / 86400000))
    : null;
  return (
    <div className="stack">
      <h1>Hello, {me.user.name.split(" ")[0]}</h1>
      {me.tenant.plan === "trial" && trialDays !== null && (
        <div className="banner info">Free trial: {trialDays} day{trialDays === 1 ? "" : "s"} left.</div>
      )}
      {me.tenant.status === "read_only" && <div className="banner warn">Subscription expired. The app is read-only.</div>}
      {can("reports.view") && <TodayCard />}
      {can("approve") && <OwnerAlerts />}
      <CashAlerts />
      {can("tenant.settings") && (
        <Card title="Get your restaurant ready">
          <ol className="linklist numbered">
            <li><Link to="/branding">Upload your logo and pick your colour</Link></li>
            <li><Link to="/outlets">Add your outlets</Link></li>
            <li><Link to="/menu/import">Import your menu from Excel</Link> or <Link to="/menu">build it here</Link></li>
            <li><Link to="/kitchen">Set up preparation areas</Link> (Shake counter, Kitchen…) and the customer screen link</li>
            <li><Link to="/users">Add staff and give them PINs</Link></li>
          </ol>
          <p className="muted small">
            Your app address: <strong>{branding?.login_id}</strong> (staff enter this once on each device).
          </p>
        </Card>
      )}
      <Card title="Run service">
        <ul className="linklist">
          <li><Link to="/orders">Orders</Link>: take dine-in, takeaway (with car number) and delivery orders and send KOTs</li>
          <li><Link to="/kds">Kitchen display</Link>: each preparation area sees and updates its own tickets</li>
          <li><Link to="/kitchen">Customer screen</Link>: open the outlet's display link on the counter TV</li>
        </ul>
      </Card>
    </div>
  );
}
