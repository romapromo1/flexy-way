'use strict';

const assert = require('node:assert/strict');
const { after, before, test } = require('node:test');
const { once } = require('node:events');
const { createApp } = require('../server');

let server;
let baseUrl;

before(async () => {
  process.env.RELAY_URL = 'wss://relay.example/ws';
  process.env.RELAY_HOST_SECRET = 'test-secret-with-at-least-24-characters';
  process.env.RELAY_DISPLAY_ID = 'FLEXY';
  server = createApp().listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(() => new Promise((resolve) => server.close(resolve)));

test('serves the game and loopback relay configuration', async () => {
  const game = await fetch(`${baseUrl}/`);
  assert.equal(game.status, 200);
  assert.match(await game.text(), /Flexy Way/);

  const config = await fetch(`${baseUrl}/api/relay-config`);
  assert.equal(config.status, 200);
  assert.equal(config.headers.get('cache-control'), 'no-store');
  const payload = await config.json();
  assert.equal(payload.relayUrl, 'wss://relay.example/ws');
  assert.equal(payload.displayId, 'FLEXY');
});

test('does not expose private project files', async () => {
  const paths = [
    '/.env',
    '/package.json',
    '/Flexy_Way_%D0%BF%D1%80%D0%BE%D0%BC%D0%BE%D0%BA%D0%BE%D0%B4%D1%8B.xlsx',
    '/telegram_bot/data/flexy_way_bot.sqlite3',
    '/telegram_bot/data/local_secret.key',
  ];
  for (const pathname of paths) {
    const response = await fetch(baseUrl + pathname);
    assert.equal(response.status, 404, pathname);
  }
});

test('rejects malformed prize requests before starting Python', async () => {
  const response = await fetch(`${baseUrl}/api/claim-prize`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ prizeCode: '../secret', sessionId: '' }),
  });
  assert.equal(response.status, 400);
});
