export type MessageType = 'update' | 'timeline' | 'screenshot' | 'status' | 'error' | 'complete';

export interface WebSocketMessage {
  type: MessageType;
  executionId: string;
  data: any;
  timestamp: string;
}

class WebSocketManager {
  private clients: Map<string, Set<WebSocket>> = new Map();
  private subscribers: Map<string, Set<(message: WebSocketMessage) => void>> = new Map();

  subscribe(executionId: string, callback: (message: WebSocketMessage) => void): () => void {
    if (!this.subscribers.has(executionId)) {
      this.subscribers.set(executionId, new Set());
    }

    this.subscribers.get(executionId)!.add(callback);

    // Return unsubscribe function
    return () => {
      this.subscribers.get(executionId)?.delete(callback);
    };
  }

  broadcast(message: WebSocketMessage): void {
    const callbacks = this.subscribers.get(message.executionId);
    if (callbacks) {
      callbacks.forEach((callback) => {
        try {
          callback(message);
        } catch (error) {
          console.error('[WebSocket] Callback error:', error);
        }
      });
    }
  }

  registerClient(executionId: string, client: WebSocket): void {
    if (!this.clients.has(executionId)) {
      this.clients.set(executionId, new Set());
    }
    this.clients.get(executionId)!.add(client);
  }

  unregisterClient(executionId: string, client: WebSocket): void {
    this.clients.get(executionId)?.delete(client);
    if (this.clients.get(executionId)?.size === 0) {
      this.clients.delete(executionId);
    }
  }

  broadcastToClients(executionId: string, message: WebSocketMessage): void {
    const clients = this.clients.get(executionId);
    if (clients) {
      clients.forEach((client) => {
        if (client.readyState === WebSocket.OPEN) {
          try {
            client.send(JSON.stringify(message));
          } catch (error) {
            console.error('[WebSocket] Send error:', error);
          }
        }
      });
    }
  }
}

export const wsManager = new WebSocketManager();
