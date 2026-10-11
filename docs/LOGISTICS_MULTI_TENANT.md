# Logistics company mode (admin + agents)

## Roles

| Role | Can do |
|------|--------|
| **Admin (head)** | Register company, add agents (name + email + password), see **all** messages / held / activity for the org |
| **Agent** | Sign in, open **WhatsApp** tab, scan QR for their business line, handle customers (same AI + voice flow as before) |

## Setup

1. In `.env` set `BRIDGE_MULTI=true` (default in bridge).
2. Restart backend: `uvicorn app.main:app --host 127.0.0.1 --port 8001`
3. Restart bridge: `cd whatsapp-bridge && npm start`
4. Open `/dashboard` → **Register company** (first time only) or sign in with **email + password**.

## Flow

1. **ABC Logistics** admin registers and signs in.
2. **Team** tab → add agents A, B, C (each gets their own email/password).
3. Each agent signs in → **WhatsApp** tab → **Show QR code** → scan on the phone that owns that WhatsApp Business line.
4. Customer messages go to that agent’s WhatsApp → same webhook → AI auto-reply + voice, scoped to that agent.
5. Admin uses **Messages** / **Held** / **Activity** to monitor everyone (optional `agent=` filter on messages API).

## Notes

- One WhatsApp number per agent session (separate QR / `LocalAuth` folder `agent-{id}`).
- `data/bridge_agents.json` maps each agent to their webhook token (auto-generated).
- Legacy single-password login still works if no email session (`DASHBOARD_PASSWORD`).
- Empty `ALLOWED_CONTACTS` = reply to all customers; set numbers to restrict.
