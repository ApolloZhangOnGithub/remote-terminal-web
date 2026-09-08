// mic_relay — bridge browser microphone to ear_recorder.py
// Browser → WebSocket (port 7690) → this relay → TCP (port 7691) → ear_recorder
const net = require('net');
const { WebSocketServer } = require('ws');

const WS_PORT = 7690;
const TCP_PORT = 7691;

const tcpClients = new Set();

// TCP server: ear_recorder.py connects here to receive PCM audio
const tcpServer = net.createServer(sock => {
  tcpClients.add(sock);
  sock.on('close', () => tcpClients.delete(sock));
  sock.on('error', () => { tcpClients.delete(sock); sock.destroy(); });
});
tcpServer.listen(TCP_PORT, '127.0.0.1');

// WebSocket server: browser sends PCM audio here
const wss = new WebSocketServer({ port: WS_PORT, host: '127.0.0.1' });
wss.on('connection', ws => {
  ws.on('message', data => {
    if (!Buffer.isBuffer(data) || tcpClients.size === 0) return;
    for (const sock of tcpClients) {
      try { sock.write(data); } catch { tcpClients.delete(sock); }
    }
  });
});

process.stdout.write(`mic_relay: ws=${WS_PORT} tcp=${TCP_PORT}\n`);
