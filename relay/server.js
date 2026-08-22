'use strict';

const crypto = require('crypto');
const http = require('http');
const express = require('express');
const { WebSocket, WebSocketServer } = require('ws');

const OPEN = WebSocket.OPEN;
const DISPLAY_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,128}$/;
const DEFAULT_ORIGINS = [
  'https://romapromo1.github.io',
  'http://localhost:3300',
  'http://127.0.0.1:3300',
];

function randomToken(bytes = 24) {
  return crypto.randomBytes(bytes).toString('base64url');
}

function safeEqual(left, right) {
  const a = Buffer.from(String(left || ''), 'utf8');
  const b = Buffer.from(String(right || ''), 'utf8');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function clamp(value, min, max) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : 0;
}

function sanitizeRtcCandidate(value) {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const candidate = String(value.candidate || '');
  const sdpMid = value.sdpMid == null ? null : String(value.sdpMid).slice(0, 64);
  const sdpMLineIndex = value.sdpMLineIndex == null
    ? null
    : Math.max(0, Math.min(32, Math.trunc(Number(value.sdpMLineIndex) || 0)));
  if (!candidate || candidate.length > 2048) return undefined;
  return { candidate, sdpMid, sdpMLineIndex };
}

function send(socket, payload) {
  if (socket && socket.readyState === OPEN) {
    socket.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function close(socket, code, reason) {
  if (socket && (socket.readyState === OPEN || socket.readyState === WebSocket.CONNECTING)) {
    socket.close(code, reason);
  }
}

class SlidingRateLimit {
  constructor(limit, windowMs) {
    this.limit = limit;
    this.windowMs = windowMs;
    this.timestamps = [];
  }

  allow(now = Date.now()) {
    const cutoff = now - this.windowMs;
    while (this.timestamps.length && this.timestamps[0] <= cutoff) this.timestamps.shift();
    if (this.timestamps.length >= this.limit) return false;
    this.timestamps.push(now);
    return true;
  }
}

class RelayHub {
  constructor(options) {
    this.hostSecret = String(options.hostSecret || '');
    this.allowedOrigins = new Set(options.allowedOrigins || DEFAULT_ORIGINS);
    this.pairTtlMs = options.pairTtlMs || 5 * 60 * 1000;
    this.resumeGraceMs = options.resumeGraceMs || 15 * 1000;
    this.sessions = new Map();
    this.pairingIndex = new Map();
  }

  isOriginAllowed(origin) {
    return Boolean(origin) && this.allowedOrigins.has(origin);
  }

  createSession(displayId) {
    const session = {
      id: randomToken(12),
      displayId,
      host: null,
      controller: null,
      controllerResumeToken: null,
      controllerResumeTimer: null,
      pairToken: null,
      pairExpiresAt: 0,
    };
    this.sessions.set(displayId, session);
    return session;
  }

  rotatePairing(session) {
    if (session.pairToken) this.pairingIndex.delete(session.pairToken);
    session.pairToken = randomToken();
    session.pairExpiresAt = Date.now() + this.pairTtlMs;
    this.pairingIndex.set(session.pairToken, session);
    return {
      type: 'pair_updated',
      sessionId: session.id,
      displayId: session.displayId,
      pairToken: session.pairToken,
      pairExpiresAt: session.pairExpiresAt,
    };
  }

  clearController(session, { rotate = true } = {}) {
    if (session.controllerResumeTimer) clearTimeout(session.controllerResumeTimer);
    session.controllerResumeTimer = null;
    session.controller = null;
    session.controllerResumeToken = null;
    if (rotate && session.host && session.host.readyState === OPEN) {
      send(session.host, this.rotatePairing(session));
    }
  }

  releaseController(session, reason = 'next_guest') {
    const controller = session.controller;
    send(controller, { type: 'session_ended', reason });
    this.clearController(session, { rotate: true });
    close(controller, 4000, 'Session ended');
    send(session.host, { type: 'controller_released', reason });
  }

  attach(socket, request) {
    const origin = request.headers.origin;
    if (!this.isOriginAllowed(origin)) {
      close(socket, 4403, 'Origin is not allowed');
      return;
    }

    const state = {
      role: null,
      session: null,
      motionLimit: new SlidingRateLimit(70, 1000),
      rtcSignalLimit: new SlidingRateLimit(120, 60_000),
      actionLimit: new SlidingRateLimit(20, 60_000),
    };
    const helloTimer = setTimeout(() => close(socket, 4408, 'Handshake timeout'), 5000);

    socket.on('message', (raw) => {
      let data;
      try {
        data = JSON.parse(raw.toString('utf8'));
      } catch {
        close(socket, 4400, 'Invalid JSON');
        return;
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) return;

      if (!state.role) {
        if (data.type === 'host_hello') {
          this.handleHostHello(socket, state, data, helloTimer);
        } else if (data.type === 'controller_hello') {
          this.handleControllerHello(socket, state, data, helloTimer);
        } else if (data.type === 'controller_resume') {
          this.handleControllerResume(socket, state, data, helloTimer);
        } else {
          close(socket, 4401, 'Handshake required');
        }
        return;
      }

      if (state.role === 'host') this.handleHostMessage(socket, state, data);
      if (state.role === 'controller') this.handleControllerMessage(socket, state, data);
    });

    socket.on('close', () => {
      clearTimeout(helloTimer);
      this.handleClose(socket, state);
    });
  }

  handleHostHello(socket, state, data, helloTimer) {
    const displayId = String(data.displayId || '').trim().toUpperCase();
    if (!DISPLAY_PATTERN.test(displayId) || !safeEqual(data.hostSecret, this.hostSecret)) {
      close(socket, 4403, 'Host authentication failed');
      return;
    }

    clearTimeout(helloTimer);
    const session = this.sessions.get(displayId) || this.createSession(displayId);
    if (session.host && session.host !== socket) close(session.host, 4001, 'Host replaced');
    session.host = socket;
    state.role = 'host';
    state.session = session;

    let pairing = null;
    if (!session.controller) {
      if (!session.pairToken || session.pairExpiresAt <= Date.now()) {
        pairing = this.rotatePairing(session);
      } else {
        pairing = {
          pairToken: session.pairToken,
          pairExpiresAt: session.pairExpiresAt,
        };
      }
    }
    send(socket, {
      type: 'host_ready',
      sessionId: session.id,
      displayId,
      controllerConnected: Boolean(session.controller),
      pairToken: pairing ? pairing.pairToken : null,
      pairExpiresAt: pairing ? pairing.pairExpiresAt : null,
    });
  }

  handleControllerHello(socket, state, data, helloTimer) {
    const token = String(data.pairToken || '');
    if (!TOKEN_PATTERN.test(token)) {
      close(socket, 4403, 'Invalid pairing token');
      return;
    }
    const session = this.pairingIndex.get(token);
    if (!session || !safeEqual(session.pairToken, token) || session.pairExpiresAt <= Date.now()) {
      close(socket, 4403, 'Pairing token expired');
      return;
    }
    if (!session.host || session.host.readyState !== OPEN) {
      close(socket, 4404, 'Game screen is offline');
      return;
    }
    if (session.controller || session.controllerResumeTimer) {
      close(socket, 4409, 'Another guest is already playing');
      return;
    }

    clearTimeout(helloTimer);
    this.pairingIndex.delete(token);
    session.pairToken = null;
    session.pairExpiresAt = 0;
    session.controller = socket;
    session.controllerResumeToken = randomToken();
    state.role = 'controller';
    state.session = session;
    send(socket, {
      type: 'controller_ready',
      sessionId: session.id,
      displayId: session.displayId,
      resumeToken: session.controllerResumeToken,
    });
    send(session.host, { type: 'controller_connected', sessionId: session.id });
  }

  handleControllerResume(socket, state, data, helloTimer) {
    const session = [...this.sessions.values()].find((item) => item.id === data.sessionId);
    if (
      !session ||
      !session.controllerResumeTimer ||
      !safeEqual(data.resumeToken, session.controllerResumeToken) ||
      !session.host ||
      session.host.readyState !== OPEN
    ) {
      close(socket, 4403, 'Resume session expired');
      return;
    }

    clearTimeout(helloTimer);
    clearTimeout(session.controllerResumeTimer);
    session.controllerResumeTimer = null;
    session.controller = socket;
    state.role = 'controller';
    state.session = session;
    send(socket, {
      type: 'controller_ready',
      sessionId: session.id,
      displayId: session.displayId,
      resumeToken: session.controllerResumeToken,
      resumed: true,
    });
    send(session.host, { type: 'controller_reconnected', sessionId: session.id });
  }

  handleHostMessage(socket, state, data) {
    const session = state.session;
    if (!session || session.host !== socket) return;

    if (data.type === 'rotate_pairing' && !session.controller && !session.controllerResumeTimer) {
      send(socket, this.rotatePairing(session));
      return;
    }
    if (data.type === 'release_controller') {
      this.releaseController(session, 'next_guest');
      return;
    }
    if (data.type === 'haptic') {
      const pattern = ['coin', 'gameover', 'connect'].includes(data.pattern) ? data.pattern : 'connect';
      send(session.controller, { type: 'haptic', pattern });
      return;
    }
    if (data.type === 'game_state') {
      send(session.controller, {
        type: 'game_state',
        state: String(data.state || 'start').slice(0, 24),
        score: Math.max(0, Math.min(9999, Math.trunc(Number(data.score) || 0))),
        timeLeft: Math.max(0, Math.min(3600, Math.trunc(Number(data.timeLeft) || 0))),
      });
      return;
    }
    if (data.type === 'rtc_offer' && state.rtcSignalLimit.allow()) {
      const sdp = String(data.sdp || '');
      if (sdp && sdp.length <= 12_000) send(session.controller, { type: 'rtc_offer', sdp });
      return;
    }
    if (data.type === 'rtc_ice_candidate' && state.rtcSignalLimit.allow()) {
      const candidate = sanitizeRtcCandidate(data.candidate);
      if (candidate !== undefined) send(session.controller, { type: 'rtc_ice_candidate', candidate });
    }
  }

  handleControllerMessage(socket, state, data) {
    const session = state.session;
    if (!session || session.controller !== socket || !session.host) return;

    if (data.type === 'motion') {
      if (!state.motionLimit.allow()) return;
      send(session.host, {
        type: 'motion',
        x: clamp(data.x, -1, 1),
        y: clamp(data.y, -1, 1),
        sequence: Math.max(0, Math.trunc(Number(data.sequence) || 0)),
        clientTime: Math.max(0, Math.trunc(Number(data.clientTime) || 0)),
      });
      return;
    }
    if (data.type === 'rtc_answer' && state.rtcSignalLimit.allow()) {
      const sdp = String(data.sdp || '');
      if (sdp && sdp.length <= 12_000) send(session.host, { type: 'rtc_answer', sdp });
      return;
    }
    if (data.type === 'rtc_ice_candidate' && state.rtcSignalLimit.allow()) {
      const candidate = sanitizeRtcCandidate(data.candidate);
      if (candidate !== undefined) send(session.host, { type: 'rtc_ice_candidate', candidate });
      return;
    }
    if (data.type === 'action' && state.actionLimit.allow()) {
      const action = ['restart', 'recalibrate'].includes(data.action) ? data.action : null;
      if (action) send(session.host, { type: 'action', action });
    }
  }

  handleClose(socket, state) {
    const session = state.session;
    if (!session) return;

    if (state.role === 'host' && session.host === socket) {
      session.host = null;
      send(session.controller, { type: 'host_offline' });
      close(session.controller, 4004, 'Game screen offline');
      this.clearController(session, { rotate: false });
      if (session.pairToken) this.pairingIndex.delete(session.pairToken);
      session.pairToken = null;
      session.pairExpiresAt = 0;
      return;
    }

    if (state.role === 'controller' && session.controller === socket) {
      session.controller = null;
      send(session.host, {
        type: 'controller_disconnected',
        reconnectGraceMs: this.resumeGraceMs,
      });
      session.controllerResumeTimer = setTimeout(() => {
        session.controllerResumeTimer = null;
        session.controllerResumeToken = null;
        send(session.host, this.rotatePairing(session));
      }, this.resumeGraceMs);
    }
  }

  shutdown() {
    for (const session of this.sessions.values()) {
      if (session.controllerResumeTimer) clearTimeout(session.controllerResumeTimer);
      close(session.host, 1001, 'Server shutdown');
      close(session.controller, 1001, 'Server shutdown');
    }
    this.sessions.clear();
    this.pairingIndex.clear();
  }
}

function createRelayServer(options = {}) {
  const hostSecret = options.hostSecret || process.env.RELAY_HOST_SECRET;
  if (!hostSecret || String(hostSecret).length < 24) {
    throw new Error('RELAY_HOST_SECRET must contain at least 24 characters');
  }
  const allowedOrigins = options.allowedOrigins || String(process.env.ALLOWED_ORIGINS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  const hub = new RelayHub({
    hostSecret,
    allowedOrigins: allowedOrigins.length ? allowedOrigins : DEFAULT_ORIGINS,
    pairTtlMs: options.pairTtlMs,
    resumeGraceMs: options.resumeGraceMs,
  });

  const app = express();
  app.disable('x-powered-by');
  app.get('/', (_req, res) => res.json({ service: 'flexy-way-relay', status: 'ok' }));
  app.get('/health', (_req, res) => res.json({ status: 'ok', sessions: hub.sessions.size }));

  const server = http.createServer(app);
  const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 16_384 });
  wss.on('connection', (socket, request) => hub.attach(socket, request));

  const closeServer = () => new Promise((resolve) => {
    hub.shutdown();
    wss.close(() => server.close(resolve));
  });
  return { app, server, wss, hub, closeServer };
}

if (require.main === module) {
  const port = Number(process.env.PORT) || 10000;
  const { server } = createRelayServer();
  server.listen(port, '0.0.0.0', () => {
    console.log(`Flexy Way relay listening on port ${port}`);
  });
}

module.exports = { createRelayServer, RelayHub };
