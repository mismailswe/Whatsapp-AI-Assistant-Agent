# WhatsApp Customer Service Agent
### AI + human approval for logistics operations

**Built for a logistics company** to make customer-service chats faster, consistent, and safer — without leaving WhatsApp.

---

## 1. The problem

Logistics teams live in WhatsApp: booking updates, delivery ETAs, “can we schedule a call?”, photos of parcels, voice notes from drivers and customers.

Doing that by hand means:

- Slow first replies
- Inconsistent tone across agents
- Risky auto-sends (promises, payments, private details)
- No single view for the operations head

---

## 2. The product

A **multi-agent WhatsApp desk**:

1. **Company admin** signs in and creates customer-service agents (name, email, password).
2. Each **agent** links their own business WhatsApp by scanning a QR code.
3. Incoming chats are answered by **AI + a human hold queue**.
4. The **head** sees all messages, held replies, and activity across the team.

Voice notes, photos, and text all go through the same pipeline.

---

## 3. How a reply is decided

The model writes a short reply **and** two flags. Code — not the model — decides whether to send:

```
send automatically  =  reply is not empty
                    AND grounded
                    AND not a decision only a human can make
```

| If… | Then… |
|---|---|
| Small talk, location, schedule facts | Auto-reply on WhatsApp |
| Meeting, payment, private / unsure | **Hold** for the agent to edit, send, or dismiss |

Doubt defaults to **hold**. The customer never gets a guessed promise.

---

## 4. What the dashboard does

| Tab | Role |
|---|---|
| **Chat** | Internal assistant (Mika) — draft or send, list today’s messages |
| **Held** | Approvals with a reason (“needs you to answer”) |
| **Messages** | Full in/out log by contact and day |
| **Activity** | Audit trail of auto-replies and holds |
| **Team** | Admin adds agents |
| **WhatsApp** | Agent scans QR and links the business line |

Auto-reply and direct-send can be paused globally.

---

## 5. Architecture (one sentence)

WhatsApp ↔ **Node bridge** (`whatsapp-web.js`) ↔ **FastAPI** webhook ↔ **LangGraph** agent ↔ **SQLite**.

```
Customer  →  WhatsApp Web session (per agent)
          →  FastAPI /webhook
          →  Whisper / BLIP (if voice or photo)
          →  LangGraph + OpenAI (reply + flags)
          →  Send  or  Held queue
          →  Dashboard for the team
```

---

## 6. Technology

| Layer | Stack |
|---|---|
| API & dashboard | **FastAPI**, Uvicorn, Pydantic |
| Agent orchestration | **LangGraph** + **LangChain** |
| Language model | **OpenAI** (chat + optional TTS) |
| Memory & logs | **SQLite** |
| Voice in | **faster-whisper** (local STT) |
| Photos | **Hugging Face Transformers** + **PyTorch** + **BLIP** captions |
| WhatsApp | **whatsapp-web.js** (multi-session QR linking) |
| Auth | Company admin + agents (email/password), scoped history |

Language matching: **English and Urdu** (including Roman Urdu). Voice in → voice out when they send a voice note.

---

## 7. Why this fits logistics CS

- **First response in seconds** for routine questions
- **Humans stay in the loop** for bookings, payments, and commitments
- **One ops view** for the company head
- **Each agent owns a WhatsApp line** — no shared QR chaos
- **Grounded answers** from company notes and schedule, not invented facts

---

## 8. Closing

Not “reply to everything.”  
**Reply like the company would — and hold the rest.**
