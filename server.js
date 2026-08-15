const express = require('express');
const http = require('http');
const https = require('https');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');
const { WebSocketServer, WebSocket } = require('ws');
const selfsigned = require('selfsigned');

const app = express();
const httpServer = http.createServer(app);

// Generate self-signed certificate for local HTTPS
const pems = selfsigned.generate([{ name: 'commonName', value: 'localhost' }], { days: 365 });
const httpsServer = https.createServer({ key: pems.private, cert: pems.cert }, app);

// WebSocket servers for both HTTP and HTTPS
const wssHttp = new WebSocketServer({ server: httpServer });
const wssHttps = new WebSocketServer({ server: httpsServer });

const HTTP_PORT = parseInt(process.env.PORT, 10) || 3300;
const HTTPS_PORT = HTTP_PORT + 1;

// Serve static files
app.use(express.static(__dirname));

// Intelligent local network IP detection (skips VPN adapters like Amnezia, Outline, WSL, etc.)
function getLocalIp() {
  const interfaces = os.networkInterfaces();
  let fallbackIp = null;

  for (const name of Object.keys(interfaces)) {
    // Skip virtual/VPN adapters
    if (/amnezia|outline|tap|tun|vethernet|virtual|wsl|bluetooth/i.test(name)) {
      continue;
    }

    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        // Highest priority: standard local Wi-Fi 192.168.x.x
        if (iface.address.startsWith('192.168.')) {
          return iface.address;
        }
        if (!fallbackIp) fallbackIp = iface.address;
      }
    }
  }

  return fallbackIp || '127.0.0.1';
}

const localIp = getLocalIp();
let cloudflareUrl = null;
let pinggyUrl = null;
let cloudflareProcess = null;
let pinggyProcess = null;

// Active rooms map: roomId -> { host: ws, controllers: Set<ws> }
const rooms = new Map();

function handleWsConnection(ws) {
  let userRoom = null;
  let userRole = null;

  ws.on('message', (message) => {
    try {
      const data = JSON.parse(message);

      if (data.type === 'join') {
        const { roomId, role } = data;
        userRoom = roomId;
        userRole = role;

        if (!rooms.has(roomId)) {
          rooms.set(roomId, { host: null, controllers: new Set() });
        }

        const room = rooms.get(roomId);

        if (role === 'host') {
          room.host = ws;
          ws.send(JSON.stringify({ type: 'host_ready', roomId }));
          if (room.controllers.size > 0) {
            ws.send(JSON.stringify({ type: 'controller_connected', count: room.controllers.size }));
          }
        } else if (role === 'controller') {
          room.controllers.add(ws);
          ws.send(JSON.stringify({ type: 'controller_ready', roomId }));
          if (room.host && room.host.readyState === WebSocket.OPEN) {
            room.host.send(JSON.stringify({ type: 'controller_connected', count: room.controllers.size }));
          }
        }
      } else if (data.type === 'motion' || data.type === 'action' || data.type === 'recalibrate') {
        if (userRoom && rooms.has(userRoom)) {
          const room = rooms.get(userRoom);
          if (room.host && room.host.readyState === WebSocket.OPEN) {
            room.host.send(JSON.stringify(data));
          }
        }
      } else if (data.type === 'haptic' || data.type === 'game_state') {
        if (userRoom && rooms.has(userRoom)) {
          const room = rooms.get(userRoom);
          for (const ctrl of room.controllers) {
            if (ctrl.readyState === WebSocket.OPEN) {
              ctrl.send(JSON.stringify(data));
            }
          }
        }
      } else if (data.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong', time: data.time }));
      }
    } catch (e) {
      console.error('WS Error:', e);
    }
  });

  ws.on('close', () => {
    if (userRoom && rooms.has(userRoom)) {
      const room = rooms.get(userRoom);
      if (userRole === 'host') {
        room.host = null;
        for (const ctrl of room.controllers) {
          if (ctrl.readyState === WebSocket.OPEN) {
            ctrl.send(JSON.stringify({ type: 'host_disconnected' }));
          }
        }
      } else if (userRole === 'controller') {
        room.controllers.delete(ws);
        if (room.host && room.host.readyState === WebSocket.OPEN) {
          room.host.send(JSON.stringify({ type: 'controller_disconnected', count: room.controllers.size }));
        }
      }
      if (!room.host && room.controllers.size === 0) {
        rooms.delete(userRoom);
      }
    }
  });
}

wssHttp.on('connection', handleWsConnection);
wssHttps.on('connection', handleWsConnection);

app.use(express.json());

// API endpoint returning all connection options
app.get('/api/info', (req, res) => {
  res.json({
    localIp,
    localUrl: `http://${localIp}:${HTTP_PORT}`,
    localHttpsUrl: `https://${localIp}:${HTTPS_PORT}`,
    pinggyUrl,
    cloudflareUrl,
    preferredUrl: `http://${localIp}:${HTTP_PORT}`
  });
});

// API endpoint to generate a secure Telegram claim token for winning prize
app.post('/api/claim-prize', (req, res) => {
  const { prizeCode, sessionId } = req.body || {};
  if (!prizeCode) {
    return res.status(400).json({ error: 'prizeCode is required' });
  }
  const session = sessionId || `session-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const pythonPath = path.join(__dirname, 'telegram_bot', '.venv', 'Scripts', 'python.exe');

  const proc = spawn(pythonPath, ['-m', 'telegram_bot', 'token', '--prize', prizeCode, '--session', session, '--json'], {
    cwd: __dirname
  });

  let stdout = '';
  let stderr = '';
  proc.stdout.on('data', (d) => { stdout += d.toString(); });
  proc.stderr.on('data', (d) => { stderr += d.toString(); });

  proc.on('close', (code) => {
    if (code === 0) {
      try {
        const result = JSON.parse(stdout);
        return res.json(result);
      } catch (e) {
        console.warn('JSON parse error from bot token CLI:', e, stdout);
      }
    }
    console.warn('Bot token generation returned code:', code, stderr);
    // Fallback direct deep link if bot process is unavailable
    res.json({
      session_id: session,
      prize_code: prizeCode,
      deep_link: `https://t.me/flexy_way_prize_bot?start=fw_${prizeCode.toLowerCase().replace(/[^a-z0-9]/g, '_')}`
    });
  });

  proc.on('error', (err) => {
    console.warn('Error spawning python bot token CLI:', err.message);
    res.json({
      session_id: session,
      prize_code: prizeCode,
      deep_link: `https://t.me/flexy_way_prize_bot?start=fw_${prizeCode.toLowerCase().replace(/[^a-z0-9]/g, '_')}`
    });
  });
});

// Controller direct route
app.get('/controller', (req, res) => {
  res.sendFile(path.join(__dirname, 'controller.html'));
});

// Start Pinggy Tunnel
function startPinggyTunnel(port) {
  try {
    pinggyProcess = spawn('ssh', [
      '-o', 'StrictHostKeyChecking=no',
      '-o', 'ServerAliveInterval=30',
      '-p', '443',
      '-R0:localhost:' + port,
      'a.pinggy.io'
    ], {
      stdio: ['ignore', 'pipe', 'pipe']
    });

    const handleOutput = (data) => {
      const str = data.toString();
      const matches = str.match(/https:\/\/[a-zA-Z0-9-.]+(?:pinggy\.link|pinggy\.net|pinggy-free\.link)/g);
      if (matches && matches.length > 0 && !pinggyUrl) {
        pinggyUrl = matches[matches.length - 1];
        console.log(`🌐 Внешний Pinggy туннель: ${pinggyUrl}/controller`);
      }
    };

    pinggyProcess.stdout.on('data', handleOutput);
    pinggyProcess.stderr.on('data', handleOutput);

    pinggyProcess.on('error', (err) => {
      console.warn('Pinggy warning:', err.message);
    });
  } catch (e) {
    console.warn('Failed to start Pinggy tunnel:', e);
  }
}

// Start Cloudflare Quick Tunnel
function startCloudflareTunnel(port) {
  try {
    const isWin = process.platform === 'win32';
    const cmd = isWin ? 'npx.cmd' : 'npx';
    
    cloudflareProcess = spawn(cmd, ['-y', 'cloudflared', 'tunnel', '--url', `http://localhost:${port}`], {
      shell: true,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    const handleOutput = (data) => {
      const str = data.toString();
      const match = str.match(/https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com/);
      if (match && !cloudflareUrl) {
        cloudflareUrl = match[0];
        console.log(`🌐 Внешний Cloudflare туннель: ${cloudflareUrl}/controller`);
      }
    };

    cloudflareProcess.stdout.on('data', handleOutput);
    cloudflareProcess.stderr.on('data', handleOutput);

    cloudflareProcess.on('error', (err) => {
      console.warn('Cloudflare warning:', err.message);
    });
  } catch (e) {
    console.warn('Failed to start Cloudflare tunnel:', e);
  }
}

// Start HTTP and HTTPS listeners
httpServer.listen(HTTP_PORT, () => {
  console.log(`\n==================================================`);
  console.log(`🚀 Flexy Way 3D Game Server запущен!`);
  console.log(`🎮 Экран игры (ПК):       http://localhost:${HTTP_PORT}`);
  console.log(`📱 ПРЯМОЙ WI-FI (Яндекс / Все браузеры):`);
  console.log(`👉 http://${localIp}:${HTTP_PORT}/controller`);
  console.log(`==================================================`);

  httpsServer.listen(HTTPS_PORT, () => {
    console.log(`🔒 Локальный HTTPS:      https://${localIp}:${HTTPS_PORT}/controller`);
    console.log(`==================================================\n`);
  });

  // Start background global tunnels
  startPinggyTunnel(HTTP_PORT);
  startCloudflareTunnel(HTTP_PORT);
});

function cleanup() {
  if (pinggyProcess) {
    try { pinggyProcess.kill(); } catch (e) {}
  }
  if (cloudflareProcess) {
    try { cloudflareProcess.kill(); } catch (e) {}
  }
}

process.on('SIGINT', () => { cleanup(); process.exit(); });
process.on('exit', cleanup);
