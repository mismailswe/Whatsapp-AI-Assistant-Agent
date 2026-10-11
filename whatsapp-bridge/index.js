// WhatsApp connection layer — multi-agent (logistics) or single legacy session.

const path = require('path');
require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') });

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { startBridgeApi } = require('./bridge_api');

const MULTI = (process.env.BRIDGE_MULTI || 'true').toLowerCase() === 'true';
const ECHO_ONLY = false;

const puppeteerConfig = {
  headless: true,
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run', '--no-zygote'],
};
if (process.env.PUPPETEER_EXECUTABLE_PATH) {
  puppeteerConfig.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
}

if (MULTI) {
  console.log('Multi-agent bridge: agents scan QR from the dashboard after login.');
  startBridgeApi(null);
} else {
  const client = new Client({ authStrategy: new LocalAuth(), puppeteer: puppeteerConfig });
  client.on('qr', (qr) => {
    console.log('Scan QR (WhatsApp > Linked devices):');
    qrcode.generate(qr, { small: true });
  });
  client.on('ready', () => {
    console.log('WhatsApp bridge ready (legacy single session).');
    startBridgeApi(client);
  });
  client.initialize().catch((err) => console.error('Failed to start client:', err.message));
}
