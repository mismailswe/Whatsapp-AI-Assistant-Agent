// Local API: send messages + multi-agent WhatsApp sessions (QR linking).

const fs = require('fs');
const http = require('http');
const path = require('path');
const { Location } = require('whatsapp-web.js');
const sessions = require('./session_manager');

const CONTACTS_FILE = path.resolve(__dirname, '..', 'contacts.json');
const ROUTES = ['/send-text', '/send-location', '/send-reply', '/sessions/start', '/sessions/reset'];
let started = false;

function findEntry(name) {
  const raw = JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf-8'));
  const wanted = String(name || '').trim().toLowerCase();
  for (const [key, value] of Object.entries(raw)) {
    if (key.trim().toLowerCase() === wanted) return String(value).trim();
  }
  return null;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 20000) req.destroy();
    });
    req.on('end', () => resolve(body));
    req.on('error', reject);
  });
}

function startBridgeApi(legacyClient) {
  if (started) return;

  const token = process.env.BRIDGE_API_TOKEN;
  const port = Number(process.env.BRIDGE_API_PORT || 3001);
  if (!token) {
    console.error('BRIDGE_API_TOKEN is not set - send API disabled.');
    return;
  }
  started = true;
  const multi = (process.env.BRIDGE_MULTI || 'true').toLowerCase() === 'true';

  const server = http.createServer(async (req, res) => {
    const reply = (code, obj) => {
      res.writeHead(code, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(obj));
    };

    if (req.headers['x-bridge-token'] !== token) return reply(401, { error: 'unauthorized' });

    const url = req.url.split('?')[0];

    if (multi && req.method === 'POST' && url === '/sessions/reset') {
      try {
        const data = JSON.parse(await readBody(req));
        const agentId = String(data.agent_id || '0');
        await sessions.destroySession(agentId);
        return reply(200, sessions.sessionStatus(agentId));
      } catch (err) {
        return reply(500, { error: 'reset failed' });
      }
    }

    if (multi && req.method === 'POST' && url === '/sessions/start') {
      try {
        const data = JSON.parse(await readBody(req));
        const agentId = String(data.agent_id || '0');
        const st = sessions.sessionStatus(agentId);
        if (st.state === 'logged_out') {
          await sessions.destroySession(agentId);
        }
        sessions.startSession(agentId);
        return reply(200, sessions.sessionStatus(agentId));
      } catch (err) {
        return reply(500, { error: 'start failed' });
      }
    }

    if (multi && req.method === 'GET' && url.startsWith('/sessions/') && url.endsWith('/status')) {
      const agentId = url.split('/')[2];
      return reply(200, sessions.sessionStatus(agentId));
    }

    if (multi && req.method === 'GET' && url.startsWith('/sessions/') && url.endsWith('/qr')) {
      const agentId = url.split('/')[2];
      return reply(200, sessions.sessionQr(agentId));
    }

    if (req.method !== 'POST' || !ROUTES.includes(url)) return reply(404, { error: 'not found' });

    try {
      const data = JSON.parse(await readBody(req));
      const agentId = data.agent_id != null ? String(data.agent_id) : '0';
      const client =
        multi && agentId !== '0' ? sessions.getClient(agentId) : legacyClient;
      if (!client) return reply(503, { error: 'whatsapp not ready for this agent' });

      if (url === '/send-reply') {
        const chatId = String(data.chat_id || '');
        const text = String(data.text || '').trim();
        if (!/^\d+@(c\.us|lid)$/.test(chatId)) return reply(400, { error: 'bad chat id' });
        if (!text || text.length > 4000) return reply(400, { error: 'bad text' });
        await client.sendMessage(chatId, text);
        console.log('[send] /send-reply');
        return reply(200, { status: 'sent' });
      }

      const entry = findEntry(data.contact);
      if (!entry) return reply(400, { error: 'unknown contact' });

      let chatId = entry;
      if (!entry.includes('@')) {
        const wid = await client.getNumberId(entry.replace(/\D/g, ''));
        if (!wid) return reply(404, { error: 'number is not on WhatsApp' });
        chatId = wid._serialized || wid.$1;
      }

      if (url === '/send-text') {
        const text = String(data.text || '').trim();
        if (!text || text.length > 4000) return reply(400, { error: 'bad text' });
        await client.sendMessage(chatId, text);
      } else {
        const { latitude, longitude } = data;
        if (
          !Number.isFinite(latitude) ||
          !Number.isFinite(longitude) ||
          Math.abs(latitude) > 90 ||
          Math.abs(longitude) > 180
        )
          return reply(400, { error: 'bad coordinates' });

        const mapsUrl = `https://maps.google.com/?q=${latitude},${longitude}`;
        try {
          await client.sendMessage(
            chatId,
            new Location(latitude, longitude, { name: 'My location', url: mapsUrl }),
          );
        } catch (err) {
          console.error('Pin failed, sending map link:', err.message);
          await client.sendMessage(chatId, mapsUrl);
        }
      }
      console.log(`[send] ${url} -> "${data.contact}"`);
      reply(200, { status: 'sent' });
    } catch (err) {
      console.error('send failed:', err.stack || err);
      reply(500, { error: 'send failed' });
    }
  });

  server.listen(port, '127.0.0.1', () => console.log(`Bridge API on 127.0.0.1:${port} (multi=${multi})`));
}

module.exports = { startBridgeApi };
