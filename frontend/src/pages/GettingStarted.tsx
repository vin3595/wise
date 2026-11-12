import { Link } from "react-router-dom";
import { useSession } from "../session";
import { Card } from "../ui";

/** The first-time checklist and where each daily screen is. Lives under Setup so Home stays about today. */
export default function GettingStarted() {
  const { branding } = useSession();
  return (
    <div className="stack">
      <h1>Getting started</h1>
      <Card title="Get your restaurant ready">
        <ol className="linklist numbered">
          <li><Link to="/branding">Upload your logo and pick your colour</Link></li>
          <li><Link to="/outlets">Add your outlets</Link> and choose which order types each one takes</li>
          <li><Link to="/menu/import">Import your menu from Excel</Link> or <Link to="/menu">build it here</Link></li>
          <li><Link to="/kitchen">Set up preparation areas</Link> (Shake counter, Kitchen…) and the customer screen link</li>
          <li><Link to="/users">Add staff and give them PINs</Link></li>
        </ol>
        <p className="muted small">
          Your app address: <strong>{branding?.login_id}</strong> (staff enter this once on each device).
        </p>
      </Card>
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
