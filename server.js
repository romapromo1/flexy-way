'use strict';

const express = require('express');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const PROJECT_ROOT = __dirname;
const HTTP_PORT = Number(process.env.PORT) || 3300;
const LOOPBACK_HOST = '0.0.0.0';
const SESSION_PATTERN = /^[^\x00-\x1f\x7f]{1,128}$/;
const PRIZE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

function loadLocalEnv() {
  const envPath = path.join(PROJECT_ROOT, '.env');
  if (!fs.existsSync(envPath)) return;
  const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const separator = trimmed.indexOf('=');
    if (separator <= 0) continue;
    const name = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) value = value.slice(1, -1);
    if (!(name in process.env)) process.env[name] = value;
  }
}

loadLocalEnv();

function staticOptions() {
  return {
    dotfiles: 'deny',
    etag: true,
    fallthrough: false,
    index: false,
    maxAge: 0,
  };
}

function createApp() {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '8kb', strict: true }));

  app.get('/', (_req, res) => res.sendFile(path.join(PROJECT_ROOT, 'index.html')));
  app.get('/index.html', (_req, res) => res.sendFile(path.join(PROJECT_ROOT, 'index.html')));
  app.get(['/controller', '/controller.html'], (_req, res) => {
    res.sendFile(path.join(PROJECT_ROOT, 'controller.html'));
  });
  app.get('/welcome_logo.png', (_req, res) => {
    res.sendFile(path.join(PROJECT_ROOT, 'public', 'welcome_logo.png'));
  });
  app.get('/flexyway.glb', (_req, res) => res.sendFile(path.join(PROJECT_ROOT, 'flexyway.glb')));
  app.use('/good', express.static(path.join(PROJECT_ROOT, 'good'), staticOptions()));
  app.use('/bad', express.static(path.join(PROJECT_ROOT, 'bad'), staticOptions()));

  app.get('/api/health', (_req, res) => {
    res.json({ status: 'ok', relayConfigured: Boolean(process.env.RELAY_URL && process.env.RELAY_HOST_SECRET) });
  });

  app.get('/api/relay-config', (_req, res) => {
    res.set('Cache-Control', 'no-store');
    const relayUrl = String(process.env.RELAY_URL || '').trim();
    const hostSecret = String(process.env.RELAY_HOST_SECRET || '').trim();
    if (!/^wss:\/\//i.test(relayUrl) || hostSecret.length < 24) {
      return res.status(503).json({
        error: 'Relay is not configured. Set RELAY_URL and RELAY_HOST_SECRET in .env.',
      });
    }
    return res.json({
      relayUrl,
      hostSecret,
      displayId: String(process.env.RELAY_DISPLAY_ID || 'FLEXY').trim().toUpperCase(),
      controllerUrl: String(
        process.env.CONTROLLER_PUBLIC_URL ||
        'https://romapromo1.github.io/flexy-way/controller.html'
      ).trim(),
    });
  });

  let activeClaimProcesses = 0;
  app.post('/api/claim-prize', (req, res) => {
    res.set('Cache-Control', 'no-store');
    const prizeCode = String(req.body?.prizeCode || '').trim();
    const sessionId = String(req.body?.sessionId || '').trim();
    if (!PRIZE_PATTERN.test(prizeCode) || !SESSION_PATTERN.test(sessionId)) {
      return res.status(400).json({ error: 'Invalid prizeCode or sessionId' });
    }
    if (activeClaimProcesses >= 2) {
      return res.status(429).json({ error: 'Prize service is busy. Retry shortly.' });
    }

    const pythonPath = path.join(PROJECT_ROOT, 'telegram_bot', '.venv', 'Scripts', 'python.exe');
    if (!fs.existsSync(pythonPath)) {
      return res.status(503).json({ error: 'Telegram prize service is not installed' });
    }

    activeClaimProcesses += 1;
    const child = spawn(
      pythonPath,
      ['-m', 'telegram_bot', 'token', '--prize', prizeCode, '--session', sessionId, '--json'],
      { cwd: PROJECT_ROOT, windowsHide: true }
    );
    let stdout = '';
    let stderr = '';
    let finished = false;
    const finish = (status, payload) => {
      if (finished) return;
      finished = true;
      activeClaimProcesses = Math.max(0, activeClaimProcesses - 1);
      clearTimeout(timeout);
      res.status(status).json(payload);
    };
    const timeout = setTimeout(() => {
      child.kill();
      finish(504, { error: 'Prize service timed out' });
    }, 12_000);

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf8');
      if (stdout.length > 16_384) child.kill();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8');
      if (stderr.length > 16_384) child.kill();
    });
    child.on('error', () => finish(503, { error: 'Prize service is unavailable' }));
    child.on('close', (code) => {
      if (code !== 0) {
        console.warn('Prize token process failed:', stderr.slice(0, 500));
        finish(503, { error: 'Prize link could not be created' });
        return;
      }
      try {
        finish(200, JSON.parse(stdout));
      } catch {
        finish(502, { error: 'Prize service returned an invalid response' });
      }
    });
  });

  app.use((_req, res) => res.status(404).json({ error: 'Not found' }));
  return app;
}

if (require.main === module) {
  const app = createApp();
  app.listen(HTTP_PORT, LOOPBACK_HOST, () => {
    console.log(`Flexy Way local game: http://localhost:${HTTP_PORT}`);
    console.log('The server is bound to this computer only; phones use the public relay.');
  });
}

module.exports = { createApp };
