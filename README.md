# AI Boss backend — first deploy

This is the real version of what the dashboard demo was simulating. Three
webhooks (WhatsApp, Email, Call transcript) all write into one task list
that `/tasks` exposes — the dashboard reads from this instead of its own
in-page JavaScript state.

**Tasks now persist in a real database.** Follow step 1.5 below to add
free Postgres on Railway — without it, tasks fall back to a local SQLite
file that may not survive redeploys.

## 1. Get this code onto GitHub

1. Create a new repository on GitHub (e.g. `ai-boss-backend`).
2. Upload these files to it (GitHub's web UI has an "upload files" button —
   no command line needed).

## 1.5. Add Postgres (do this before or right after deploying)

1. In your Railway project, tap **"+ New"** → **"Database"** → **"Add PostgreSQL"**.
2. Railway automatically creates a `DATABASE_URL` variable and makes it
   available to your `web` service — you don't need to copy/paste anything.
3. Redeploy the `web` service (Railway usually does this automatically
   when a new database is linked; if not, tap into the service →
   Deployments → "Redeploy").
4. That's it — tasks now survive restarts permanently.

## 2. Deploy on Railway

1. Sign in to Railway with your GitHub account.
2. "New Project" → "Deploy from GitHub repo" → pick `ai-boss-backend`.
3. Railway detects `Procfile` and `requirements.txt` automatically and
   deploys. You'll get a URL like `https://ai-boss-backend-production.up.railway.app`.
4. In the Railway project → **Variables** tab, add:
   - `WHATSAPP_VERIFY_TOKEN` = any string you make up (e.g. `new18verify2026`)

## 3. Connect WhatsApp (Meta Cloud API)

1. Go to developers.facebook.com → create an app → add the "WhatsApp"
   product.
2. Under WhatsApp → Configuration, set:
   - **Callback URL**: `https://<your-railway-url>/webhooks/whatsapp`
   - **Verify token**: the exact same string you put in
     `WHATSAPP_VERIFY_TOKEN` above.
3. Click "Verify and Save" — Meta calls your `GET /webhooks/whatsapp`
   endpoint automatically; if it fails, double check the token matches
   exactly and the Railway deploy is live.
4. Subscribe to the `messages` field so incoming messages actually get
   sent to your webhook.
5. Send a test WhatsApp message to your business number, then check
   `https://<your-railway-url>/tasks` in Chrome — you should see it
   appear as a routed task.

## 4. Connect Email (SendGrid Inbound Parse — free tier works)

1. In SendGrid: Settings → Inbound Parse → Add Host & URL.
2. Point it at `https://<your-railway-url>/webhooks/email`.
3. You'll need a domain/subdomain you control to receive mail through
   (e.g. `leads.new18travellers.com`) — SendGrid's setup page walks
   through the DNS record needed.

## 5. Connect Calls (Twilio)

1. Buy/use a Twilio number, enable call recording + transcription in the
   Twilio Console.
2. Under the number's Voice webhook, point post-call events (or a
   TwiML `<Record transcribe="true" transcribeCallback="...">`) at
   `https://<your-railway-url>/webhooks/call`.
3. Twilio's docs have the exact TwiML snippet — this is the one piece
   worth doing together live since Twilio's setup UI changes often.

## 6. Open the live dashboard

Once deployed, visit `https://<your-railway-url>/dashboard` — this is
the real dashboard, served directly by the backend, pulling live data
from `/tasks` every 5 seconds. No claude.ai artifact involved, so it can
actually talk to your backend (claude.ai's sandboxed pages can't make
outbound calls to your Railway URL, which is why this lives here instead).

You can approve, reject, reopen, and add notes right from this page —
those actions call `PATCH /tasks/{id}` and update instantly.

## 7. Test without waiting on real accounts

You don't need WhatsApp/Twilio/SendGrid live to test the backend itself.
Open **web.postman.co** and send this:

```
POST https://<your-railway-url>/webhooks/whatsapp
Content-Type: application/json

{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "919876543210",
          "text": { "body": "New inquiry: 4 people, Ladakh, first week of October" }
        }]
      }
    }]
  }]
}
```

Then `GET https://<your-railway-url>/tasks` — you should see the task,
already routed to the Email Agent.

## What's new in this version

- **Database persistence** — see step 1.5 above.
- **Manual task entry** — the dashboard now has a "+ Add task" box at the
  top, so the admin can log a walk-in or phone inquiry directly, and it
  gets routed by the same logic as the webhooks.
- **Sample data button** — "Load sample data" on the dashboard adds 4
  realistic test tasks instantly, no Postman needed.
- **Clear all button** — wipes all tasks, for resetting during testing.
  Asks for confirmation before doing anything.

## What's intentionally not here yet

- **Real sending** — webhooks only *receive* and route. Actually sending
  the WhatsApp/email reply back out is a separate, deliberate next step.
- **Itinerary/invoice/PDF generation** — still lives in the dashboard
  demo; porting it here comes after the basic pipe is proven.
