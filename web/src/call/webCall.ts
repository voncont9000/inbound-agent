/**
 * Browser audio client for the hosted web-call server (`voice/web_call_server.py`).
 * Mirrors `voice/inbound_voice/static/web_call.html`: 16 kHz PCM16 mono up, 24 kHz PCM16 down,
 * `{"type":"clear"}` text frames stop queued playback.
 */
export type WebCallEvents = {
  onConnected: () => void;
  onEnded: (error?: string) => void;
};

export interface WebCallSession {
  start(): Promise<void>;
  setMuted(muted: boolean): void;
  hangUp(): void;
}

export type WebCallSessionFactory = (url: string, events: WebCallEvents) => WebCallSession;

/** Turns the test-call page URL (`…/call/?call_token=x`) into its WebSocket URL (`wss://…/call/ws?call_token=x`). */
export function webCallSocketUrl(pageUrl: string): string {
  const page = new URL(pageUrl);
  const socket = new URL("ws", page);
  socket.protocol = page.protocol === "https:" ? "wss:" : "ws:";
  for (const key of ["token", "call_token"]) {
    const value = page.searchParams.get(key);
    if (value) socket.searchParams.set(key, value);
  }
  return socket.toString();
}

const workletSource = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = []; this.position = 0; this.step = sampleRate / 16000; this.chunk = []; }
  process(inputs) {
    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;
    const length = channels[0].length;
    for (let index = 0; index < length; index++) {
      let value = 0;
      for (const channel of channels) value += channel[index] || 0;
      this.buffer.push(value / channels.length);
    }
    while (this.position + 1 < this.buffer.length) {
      const base = Math.floor(this.position);
      const fraction = this.position - base;
      this.chunk.push(this.buffer[base] * (1 - fraction) + this.buffer[base + 1] * fraction);
      this.position += this.step;
      if (this.chunk.length === 320) {
        const pcm = new Int16Array(320);
        for (let index = 0; index < 320; index++) {
          const sample = Math.max(-1, Math.min(1, this.chunk[index]));
          pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
        }
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
        this.chunk = [];
      }
    }
    const consumed = Math.min(Math.floor(this.position), this.buffer.length - 1);
    if (consumed > 0) { this.buffer.splice(0, consumed); this.position -= consumed; }
    return true;
  }
}
registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
`;

export function describeMediaError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone blocked. Allow the microphone for this site (icon next to the address bar) or open the call in a new tab.";
  }
  if (name === "NotFoundError") return "No microphone found.";
  return `Call error: ${error instanceof Error ? error.message : String(error)}`;
}

export class BrowserWebCallSession implements WebCallSession {
  private socket: WebSocket | null = null;
  private microphone: MediaStream | null = null;
  private captureContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private worklet: AudioWorkletNode | null = null;
  private scheduled = new Set<AudioBufferSourceNode>();
  private playCursor = 0;
  private muted = false;
  private finished = false;

  constructor(private readonly socketUrl: string, private readonly events: WebCallEvents) {}

  async start(): Promise<void> {
    try {
      this.microphone = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.captureContext = new AudioContext();
      this.playbackContext = new AudioContext({ sampleRate: 24000 });
      const workletUrl = URL.createObjectURL(new Blob([workletSource], { type: "text/javascript" }));
      try {
        await this.captureContext.audioWorklet.addModule(workletUrl);
      } finally {
        URL.revokeObjectURL(workletUrl);
      }
      await this.captureContext.resume();
      await this.playbackContext.resume();
      this.openSocket();
    } catch (error) {
      await this.finish(describeMediaError(error));
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.microphone?.getAudioTracks().forEach((track) => { track.enabled = !muted; });
  }

  hangUp(): void {
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) this.socket.close(1000, "Call ended");
    else void this.finish();
  }

  private openSocket() {
    const socket = new WebSocket(this.socketUrl);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    let failed = "";
    socket.onopen = () => {
      const capture = this.captureContext!;
      const input = capture.createMediaStreamSource(this.microphone!);
      this.worklet = new AudioWorkletNode(capture, "pcm-capture-processor");
      const silent = capture.createGain();
      silent.gain.value = 0;
      this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (!this.muted && socket.readyState === WebSocket.OPEN) socket.send(event.data);
      };
      input.connect(this.worklet);
      this.worklet.connect(silent);
      silent.connect(capture.destination);
      this.events.onConnected();
    };
    socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      if (typeof event.data === "string") {
        try {
          if ((JSON.parse(event.data) as { type?: string }).type === "clear") this.stopPlayback();
        } catch { /* ignore non-JSON text frames */ }
        return;
      }
      this.playPcm(event.data);
    };
    socket.onerror = () => {
      failed = "Call error: the call server could not be reached.";
      socket.close();
    };
    socket.onclose = (event) => {
      if (!failed && event.code !== 1000) failed = `Call error: connection closed (${event.code})${event.reason ? `: ${event.reason}` : ""}`;
      void this.finish(failed || undefined);
    };
  }

  private playPcm(buffer: ArrayBuffer) {
    const context = this.playbackContext;
    if (!context || buffer.byteLength < 2) return;
    const view = new DataView(buffer);
    const samples = new Float32Array(Math.floor(buffer.byteLength / 2));
    for (let index = 0; index < samples.length; index++) samples[index] = view.getInt16(index * 2, true) / 32768;
    const audio = context.createBuffer(1, samples.length, 24000);
    audio.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = audio;
    source.connect(context.destination);
    this.scheduled.add(source);
    source.onended = () => this.scheduled.delete(source);
    const startAt = Math.max(this.playCursor, context.currentTime);
    source.start(startAt);
    this.playCursor = startAt + audio.duration;
  }

  private stopPlayback() {
    for (const source of this.scheduled) {
      try { source.stop(); } catch { /* already stopped */ }
    }
    this.scheduled.clear();
    this.playCursor = this.playbackContext?.currentTime ?? 0;
  }

  private async finish(error?: string) {
    if (this.finished) return;
    this.finished = true;
    this.microphone?.getTracks().forEach((track) => track.stop());
    this.microphone = null;
    if (this.worklet) {
      this.worklet.port.onmessage = null;
      this.worklet.disconnect();
      this.worklet = null;
    }
    this.stopPlayback();
    for (const context of [this.captureContext, this.playbackContext]) {
      if (context && context.state !== "closed") await context.close().catch(() => undefined);
    }
    this.captureContext = null;
    this.playbackContext = null;
    this.events.onEnded(error);
  }
}

export const createBrowserWebCallSession: WebCallSessionFactory = (url, events) => new BrowserWebCallSession(webCallSocketUrl(url), events);
