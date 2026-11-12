import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useSession } from "../session";
import { Button, ErrorText, Field, Input, useAction } from "../ui";

/** First screen on a new device: which restaurant is this? */
export default function Setup() {
  const { chooseRestaurant } = useSession();
  const [id, setId] = useState("");
  const { busy, error, run } = useAction();
  const nav = useNavigate();

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>Welcome</h1>
        <p className="muted">Enter your restaurant's login ID to set up this device.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              await chooseRestaurant(id.trim());
              nav("/login");
            });
          }}
        >
          <Field label="Restaurant login ID">
            <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="e.g. shakewallah" autoFocus
              autoCapitalize="none" autoCorrect="off" required />
          </Field>
          <ErrorText error={error && (error.includes("not found") ? "No restaurant with that login ID" : error)} />
          <Button disabled={busy || !id.trim()}>Continue</Button>
        </form>
        <p className="muted center">New restaurant? <Link to="/signup">Create your account</Link></p>
      </div>
    </div>
  );
}
