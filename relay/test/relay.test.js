'use strict';

const assert = require('node:assert/strict');
const { afterEach, beforeEach, test } = require('node:test');
const { once } = require('node:events');
const WebSocket = require('ws');
const { createRelayServer } = require('../server');

const HOST_SECRET = 'test-secret-with-at-least-24-characters';
const HOST_ORIGIN = 'http://localhost:3300';
const CONTROLLER_ORIGIN = 'https://romapromo1.github.io';

function openSocket(url, origin) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url, { origin });
    socket.once('open', () => resolve(socket));
    socket.once('error', reject);
  });
}

function nextMessage(socket) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timed out waiting for message')), 1500);
    socket.once('message', (raw) => {
      clearTimeout(timer);
      resolve(JSON.parse(raw.toString('utf8')));
    });
  });
}

let relay;
let wsUrl;
let sockets;

beforeEach(async () => {
  sockets = [];
  relay = createRelayServer({
    hostSecret: HOST_SECRET,
    allowedOrigins: [HOST_ORIGIN, CONTROLLER_ORIGIN],
    pairTtlMs: 2000,
    resumeGraceMs: 80,
  });
  relay.server.listen(0, '127.0.0.1');
  await once(relay.server, 'listening');
  wsUrl = `ws://127.0.0.1:${relay.server.address().port}/ws`;
});

afterEach(async () => {
  for (const socket of sockets) socket.terminate();
  await relay.closeServer();
});

test('pairs exactly one controller and relays validated motion', async () => {
  const host = await openSocket(wsUrl, HOST_ORIGIN);
  sockets.push(host);
  host.send(JSON.stringify({ type: 'host_hello', hostSecret: HOST_SECRET, displayId: 'FLEXY' }));
  const ready = await nextMessage(host);
  assert.equal(ready.type, 'host_ready');
  assert.ok(ready.pairToken);

  const controller = await openSocket(wsUrl, CONTROLLER_ORIGIN);
  sockets.push(controller);
  controller.send(JSON.stringify({ type: 'controller_hello', pairToken: ready.pairToken }));
  const controllerReady = await nextMessage(controller);
  const connected = await nextMessage(host);
  assert.equal(controllerReady.type, 'controller_ready');
  assert.equal(connected.type, 'controller_connected');

  controller.send(JSON.stringify({
    type: 'motion', x: 9, y: -9, sequence: 7, clientTime: 123,
  }));
  const motion = await nextMessage(host);
  assert.deepEqual(motion, {
    type: 'motion', x: 1, y: -1, sequence: 7, clientTime: 123,
  });

  const intruder = await openSocket(wsUrl, CONTROLLER_ORIGIN);
  sockets.push(intruder);
  intruder.send(JSON.stringify({ type: 'controller_hello', pairToken: ready.pairToken }));
  const [code] = await once(intruder, 'close');
  assert.equal(code, 4403);
});

test('relays WebRTC negotiation only between the paired host and controller', async () => {
  const host = await openSocket(wsUrl, HOST_ORIGIN);
  sockets.push(host);
  host.send(JSON.stringify({ type: 'host_hello', hostSecret: HOST_SECRET, displayId: 'FLEXY' }));
  const ready = await nextMessage(host);

  const controller = await openSocket(wsUrl, CONTROLLER_ORIGIN);
  sockets.push(controller);
  controller.send(JSON.stringify({ type: 'controller_hello', pairToken: ready.pairToken }));
  await nextMessage(controller);
  await nextMessage(host);

  host.send(JSON.stringify({ type: 'rtc_offer', sdp: 'test-offer-sdp' }));
  assert.deepEqual(await nextMessage(controller), { type: 'rtc_offer', sdp: 'test-offer-sdp' });

  controller.send(JSON.stringify({ type: 'rtc_answer', sdp: 'test-answer-sdp' }));
  assert.deepEqual(await nextMessage(host), { type: 'rtc_answer', sdp: 'test-answer-sdp' });

  const candidate = {
    candidate: 'candidate:1 1 udp 2122260223 192.0.2.1 5000 typ host',
    sdpMid: '0',
    sdpMLineIndex: 0,
  };
  host.send(JSON.stringify({ type: 'rtc_ice_candidate', candidate }));
  assert.deepEqual(await nextMessage(controller), { type: 'rtc_ice_candidate', candidate });

  controller.send(JSON.stringify({ type: 'rtc_ice_candidate', candidate }));
  assert.deepEqual(await nextMessage(host), { type: 'rtc_ice_candidate', candidate });
});

test('controller can resume during the grace period', async () => {
  const host = await openSocket(wsUrl, HOST_ORIGIN);
  sockets.push(host);
  host.send(JSON.stringify({ type: 'host_hello', hostSecret: HOST_SECRET, displayId: 'FLEXY' }));
  const ready = await nextMessage(host);

  const controller = await openSocket(wsUrl, CONTROLLER_ORIGIN);
  sockets.push(controller);
  controller.send(JSON.stringify({ type: 'controller_hello', pairToken: ready.pairToken }));
  const controllerReady = await nextMessage(controller);
  await nextMessage(host);
  controller.terminate();
  const disconnected = await nextMessage(host);
  assert.equal(disconnected.type, 'controller_disconnected');

  const resumed = await openSocket(wsUrl, CONTROLLER_ORIGIN);
  sockets.push(resumed);
  resumed.send(JSON.stringify({
    type: 'controller_resume',
    sessionId: controllerReady.sessionId,
    resumeToken: controllerReady.resumeToken,
  }));
  const resumedReady = await nextMessage(resumed);
  const reconnected = await nextMessage(host);
  assert.equal(resumedReady.resumed, true);
  assert.equal(reconnected.type, 'controller_reconnected');
});

test('rejects untrusted origins and bad host credentials', async () => {
  const badOrigin = await openSocket(wsUrl, 'https://evil.example');
  sockets.push(badOrigin);
  const [originCode] = await once(badOrigin, 'close');
  assert.equal(originCode, 4403);

  const badHost = await openSocket(wsUrl, HOST_ORIGIN);
  sockets.push(badHost);
  badHost.send(JSON.stringify({ type: 'host_hello', hostSecret: 'wrong', displayId: 'FLEXY' }));
  const [hostCode] = await once(badHost, 'close');
  assert.equal(hostCode, 4403);
});
