import { NextRequest } from 'next/server';
import { executionQueue } from '@/lib/execution-queue';
import { wsManager } from '@/lib/websocket-manager';

export function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const executionId = searchParams.get('id');

  if (!executionId) {
    return new Response('Missing execution ID', { status: 400 });
  }

  const execution = executionQueue.getExecution(executionId);
  if (!execution) {
    return new Response('Execution not found', { status: 404 });
  }

  // Create ReadableStream for SSE
  const stream = new ReadableStream({
    start(controller) {
      // Send initial state
      const initialData = `data: ${JSON.stringify(execution)}\n\n`;
      controller.enqueue(new TextEncoder().encode(initialData));

      // Subscribe to updates
      const unsubscribe = wsManager.subscribe(executionId, (message) => {
        const data = `data: ${JSON.stringify(message)}\n\n`;
        controller.enqueue(new TextEncoder().encode(data));
      });

      // Cleanup on close
      const closeHandler = () => {
        unsubscribe();
        controller.close();
      };

      request.signal.addEventListener('abort', closeHandler);
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
