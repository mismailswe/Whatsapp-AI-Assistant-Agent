// One WhatsApp Web session per agent (LocalAuth clientId = agent-{id}).

const fs = require('fs');
const path = require('path');
const axios = require('axios');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

const AGENTS_FILE = path.resolve(__dirname, '..', 'data', 'bridge_agents.json');
const ALLOW_GROUPS = (process.env.ALLOW_GROUPS || 'false').toLowerCase() === 'true';
const CONTACTS_FILE = path.resolve(__dirname, '..', 'contacts.json');

function digitsOnly(s) {
  return String(s || '').replace(/\D/g, '');
}

function sameNumber(a, b) {
  a = digitsOnly(a);
  b = digitsOnly(b);
  if (!a || !b || Math.min(a.length, b.length) < 9) return false;
  return a.endsWith(b) || b.endsWith(a);
}

function loadAgentsConfig() {
  try {
    return JSON.parse(fs.readFileSync(AGENTS_FILE, 'utf8'));
  } catch {
    return { backend_url: process.env.BACKEND_URL, agents: {} };
  }
}

function agentToken(agentId) {
  const cfg = loadAgentsConfig();
  const entry = (cfg.agents || {})[String(agentId)];
  return entry ? entry.token : null;
}

function backendUrl() {
  const cfg = loadAgentsConfig();
  return cfg.backend_url || process.env.BACKEND_URL || 'http://127.0.0.1:8001/webhook';
}

function allowedEntries() {
  const fromEnv = (process.env.ALLOWED_CONTACTS || '')
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean);
  try {
    const raw = JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf8'));
    for (const value of Object.values(raw)) {
      const v = String(value).trim();
      if (v) fromEnv.push(v);
    }
  } catch {
    /* no contacts.json */
  }
  return fromEnv;
}

function matchesAllowlist(from, resolvedDigits) {
  const entries = allowedEntries();
  if (entries.length === 0) return true;
  for (const entry of entries) {
    if (entry.includes('@')) {
      if (from === entry) return true;
      const base = entry.split('@')[0];
      if (from.startsWith(`${base}@`)) return true;
      continue;
    }
    if (sameNumber(resolvedDigits, entry)) return true;
  }
  return false;
}

function puppeteerConfig() {
  const puppeteerConfig = {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-zygote',
    ],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    puppeteerConfig.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  return puppeteerConfig;
}

const sessions = new Map();

function getSession(agentId) {
  const key = String(agentId);
    if (!sessions.has(key)) {
    sessions.set(key, {
      agentId: key,
      client: null,
      qr: null,
      ready: false,
      starting: false,
      lastError: null,
      disconnectReason: null,
    });
  }
  return sessions.get(key);
}

async function checkAllowed(client, msg) {
  const from = msg.from;
  let number = from.split('@')[0];
  if (from.endsWith('@g.us') && !ALLOW_GROUPS) return { allowed: false, number };

  if (from.endsWith('@lid')) {
    try {
      let contact = await msg.getContact();
      if (!contact?.number) contact = await client.getContactById(from);
      if (contact?.number) number = digitsOnly(contact.number);
    } catch (err) {
      console.error('Could not resolve @lid contact:', err.message);
    }
  }
  return { allowed: matchesAllowlist(from, number), number };
}

async function handleMessage(agentId, client, msg) {
  try {
    const preview = (msg.body || '').slice(0, 40);
    console.log(`[agent ${agentId} INCOMING] from=${msg.from} fromMe=${msg.fromMe} type=${msg.type} "${preview}"`);
    if (msg.fromMe) {
      console.log(`[agent ${agentId}] ignored fromMe (use another phone to test incoming)`);
      return;
    }
    const { allowed, number } = await checkAllowed(client, msg);
    if (!allowed) {
      console.log(`[agent ${agentId} BLOCKED] ${msg.from}`);
      return;
    }
    const payload = {
      from: msg.from,
      number,
      body: msg.body || '',
      type: 'text',
      media_base64: null,
      mimetype: null,
    };
    if (msg.hasMedia && (msg.type === 'ptt' || msg.type === 'audio' || msg.type === 'image')) {
      try {
        const media = await msg.downloadMedia();
        if (media?.data) {
          payload.media_base64 = media.data;
          payload.mimetype = media.mimetype;
          payload.type = msg.type === 'image' ? 'image' : 'voice';
        }
      } catch (err) {
        console.error('downloadMedia failed:', err.message);
        if (!payload.body) return;
      }
    }
    const token = agentToken(agentId);
    if (!token) {
      console.error(`[agent ${agentId}] no bridge token in bridge_agents.json`);
      return;
    }
    const { data } = await axios.post(backendUrl(), payload, {
      timeout: 180000,
      headers: { 'x-bridge-token': token },
    });
    if (data.action === 'send') {
      if (data.reply_audio_base64) {
        const mime = data.reply_audio_mimetype || 'audio/ogg; codecs=opus';
        const voiceMedia = new MessageMedia(mime, data.reply_audio_base64, 'voice.ogg');
        await client.sendMessage(msg.from, voiceMedia, { sendAudioAsVoice: true });
      } else if (data.reply_text) {
        await client.sendMessage(msg.from, data.reply_text);
      }
    }
  } catch (err) {
    console.error(`[agent ${agentId}] message error:`, err.stack || err);
  }
}

function wireClient(session) {
  const { client, agentId } = session;
  client.on('qr', (qr) => {
    session.qr = qr;
    session.ready = false;
    console.log(`[agent ${agentId}] QR updated — scan in dashboard`);
  });
  client.on('authenticated', () => {
    session.lastError = null;
    console.log(`[agent ${agentId}] authenticated`);
  });
  client.on('ready', () => {
    session.ready = true;
    session.qr = null;
    console.log(`[agent ${agentId}] WhatsApp ready`);
  });
  client.on('auth_failure', (msg) => {
    session.lastError = String(msg);
    session.ready = false;
  });
  client.on('disconnected', (reason) => {
    session.ready = false;
    session.qr = null;
    session.disconnectReason = String(reason);
    console.error(`[agent ${agentId}] disconnected:`, reason);
    const c = session.client;
    session.client = null;
    if (c) {
      c.destroy().catch(() => {});
    }
  });
  client.on('message', (msg) => handleMessage(agentId, client, msg));
}

async function destroySession(agentId) {
  const session = getSession(agentId);
  session.ready = false;
  session.qr = null;
  session.starting = false;
  if (session.client) {
    try {
      await session.client.destroy();
    } catch {
      /* already dead */
    }
    session.client = null;
  }
}

function startSession(agentId) {
  const session = getSession(agentId);
  if (session.ready && session.client) {
    return session;
  }
  if (session.starting) {
    return session;
  }
  if (session.client && !session.ready) {
    return session;
  }
  session.starting = true;
  session.lastError = null;
  session.disconnectReason = null;
  const client = new Client({
    authStrategy: new LocalAuth({ clientId: `agent-${agentId}` }),
    puppeteer: puppeteerConfig(),
  });
  session.client = client;
  wireClient(session);
  client
    .initialize()
    .catch((err) => {
      session.lastError = err.message;
      session.client = null;
      console.error(`[agent ${agentId}] init failed:`, err.message);
    })
    .finally(() => {
      session.starting = false;
    });
  return session;
}

function sessionStatus(agentId) {
  const session = getSession(agentId);
  let state = 'idle';
  if (session.ready) state = 'ready';
  else if (session.disconnectReason) state = 'logged_out';
  else if (session.qr) state = 'qr';
  else if (session.starting) state = 'starting';
  return {
    agent_id: String(agentId),
    ready: session.ready,
    starting: session.starting,
    has_qr: Boolean(session.qr),
    error: session.lastError,
    disconnect_reason: session.disconnectReason,
    state,
  };
}

function sessionQr(agentId) {
  const session = getSession(agentId);
  return { qr: session.qr, ready: session.ready };
}

function getClient(agentId) {
  const session = getSession(agentId);
  return session.ready ? session.client : null;
}

module.exports = {
  startSession,
  destroySession,
  sessionStatus,
  sessionQr,
  getClient,
  getSession,
};
