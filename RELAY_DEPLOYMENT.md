# Flexy Way: GitHub Pages controller + Render relay

## Runtime layout

- The festival PC runs `server.js`, the Three.js game, the Telegram bot, SQLite, and Excel.
- GitHub Pages serves only `controller.html` over HTTPS.
- Render runs the stateless web process in `relay/`. It never receives prize data, phone numbers, promo codes, or Telegram credentials.
- Both the PC and phone open outbound WebSocket connections to `wss://<render-service>/ws`; no inbound festival-PC port is exposed.

## Pairing protocol

1. The local game authenticates to Render with `RELAY_HOST_SECRET`.
2. Render issues a random, single-use pairing token with a five-minute TTL.
3. The game encodes the Render URL, session ID, and pairing token into the GitHub Pages controller QR.
4. The first controller consumes the token and receives a short-lived in-memory resume token.
5. A disconnected phone gets 15 seconds to resume; otherwise Render rotates the QR automatically.
6. `release_controller` ends the lease when the game returns to the start screen.

Only one Render instance must be used because active sessions are intentionally held in memory. The persistent business state remains local.

## Render settings

The repository includes `render.yaml`. Equivalent manual settings are:

- Runtime: Node
- Root directory: `relay`
- Build command: `npm ci`
- Start command: `npm start`
- Health check path: `/health`
- Environment variable `RELAY_HOST_SECRET`: a random value of at least 32 characters
- Environment variable `ALLOWED_ORIGINS`:
  `https://romapromo1.github.io,http://localhost:3300,http://127.0.0.1:3300`

Do not put `RELAY_HOST_SECRET` in GitHub. Store the same value in the local root `.env` and in Render.

## Local `.env`

```dotenv
RELAY_URL=wss://your-render-service.onrender.com/ws
RELAY_HOST_SECRET=<same secret as Render>
RELAY_DISPLAY_ID=FLEXY
CONTROLLER_PUBLIC_URL=https://romapromo1.github.io/flexy-way/controller.html
```

## Verification

```powershell
npm test
npm run relay:test
npm run bot:test
node --check server.js
node --check relay/server.js
```

The local health endpoint is `http://localhost:3300/api/health`; the Render endpoint is `https://<service>/health`.
