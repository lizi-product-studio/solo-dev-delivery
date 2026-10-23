import { writeFile, mkdir } from 'node:fs/promises';

export async function openPage(width = 1440, height = 1000) {
  const target = await fetch('http://127.0.0.1:9222/json/new?about:blank', { method: 'PUT' }).then(r => r.json());
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  const events = [];
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (!message.id) { events.push(message); return; }
    const task = pending.get(message.id);
    if (!task) return;
    pending.delete(message.id);
    clearTimeout(task.timer);
    message.error ? task.reject(new Error(JSON.stringify(message.error))) : task.resolve(message.result);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + ': ' + JSON.stringify(result.exceptionDetails.exception));
    return result.result.value;
  };
  const until = async expression => {
    for (let attempt = 0; attempt < 80; attempt++) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Condition not reached: ${expression}`);
  };
  const navigate = async (url = 'http://127.0.0.1:8765/') => {
    await send('Page.navigate', { url });
    await until('document.readyState === "complete" && !!document.querySelector(".ticket-card")');
  };
  const screenshot = async name => {
    await evaluate('document.fonts.ready.then(() => new Promise(resolve => setTimeout(resolve, 150)))');
    await mkdir('verification', { recursive: true });
    const result = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(`verification/${name}.png`, Buffer.from(result.data, 'base64'));
  };
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Network.enable');
  await send('Page.bringToFront');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  return { send, evaluate, until, navigate, screenshot, events, close: async () => {
    await fetch(`http://127.0.0.1:9222/json/close/${target.id}`);
    if (socket.readyState !== WebSocket.CLOSED) {
      await new Promise(resolve => { socket.addEventListener('close', resolve, { once: true }); socket.close(); });
    }
  } };
}

if (process.argv.includes('--capture')) {
  const page = await openPage();
  try {
    await page.navigate();
    await page.screenshot('workspace');
    console.log(await page.evaluate('({title: document.title, tickets: document.querySelectorAll(".ticket-card").length, overflow: document.documentElement.scrollWidth > innerWidth})'));
  } finally { await page.close(); }
}
