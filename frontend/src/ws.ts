/**
 * WebSocket client for JARVIS server communication.
 */

export type MessageHandler = (msg: Record<string, unknown>) => void;
export type ConnectionHandler = (connected: boolean) => void;

export interface JarvisSocket {
  send(data: Record<string, unknown>): void;
  onMessage(handler: MessageHandler): void;
  onConnectionChange(handler: ConnectionHandler): void;
  close(): void;
  isConnected(): boolean;
}

export function createSocket(url: string): JarvisSocket {
  let ws: WebSocket | null = null;
  let handlers: MessageHandler[] = [];
  let connectionHandlers: ConnectionHandler[] = [];
  let reconnectDelay = 1000;
  let closed = false;
  let connected = false;

  function notifyConnection(nextConnected: boolean) {
    connected = nextConnected;
    for (const handler of connectionHandlers) handler(nextConnected);
  }

  function connect() {
    if (closed) return;

    ws = new WebSocket(url);

    ws.onopen = () => {
      notifyConnection(true);
      reconnectDelay = 1000;
      console.log("[ws] connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        for (const h of handlers) h(msg);
      } catch {
        console.warn("[ws] bad message", event.data);
      }
    };

    ws.onclose = () => {
      notifyConnection(false);
      if (!closed) {
        console.log(`[ws] reconnecting in ${reconnectDelay}ms`);
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
      }
    };

    ws.onerror = (err) => {
      console.error("[ws] error", err);
      ws?.close();
    };
  }

  connect();

  return {
    send(data) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
      }
    },
    onMessage(handler) {
      handlers.push(handler);
    },
    onConnectionChange(handler) {
      connectionHandlers.push(handler);
      handler(connected);
    },
    close() {
      closed = true;
      ws?.close();
    },
    isConnected() {
      return connected;
    },
  };
}
