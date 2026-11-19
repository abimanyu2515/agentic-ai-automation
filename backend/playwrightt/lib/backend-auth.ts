// Server side only (spec 0009). The workspace proves who it is to FastAPI
// with a shared secret, read from .env.local. Never import this from a
// client component: the secret must not reach the browser bundle.

export const WORKSPACE_SECRET_HEADER = 'X-Webhook-Secret';

/** Where the server routes forward to. The browser side getBackendUrl() cannot be used here. */
export function backendUrl(): string {
  return (process.env.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
}

/** The secret header, or no header when WORKSPACE_WEBHOOK_SECRET is unset (FastAPI then answers 503). */
export function workspaceSecretHeaders(): Record<string, string> {
  const secret = process.env.WORKSPACE_WEBHOOK_SECRET;
  return secret ? { [WORKSPACE_SECRET_HEADER]: secret } : {};
}

/** Forward a JSON PUT to FastAPI with the workspace secret, passing status and body back. */
export async function forwardPut(path: string, body: unknown): Promise<Response> {
  const response = await fetch(`${backendUrl()}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...workspaceSecretHeaders() },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  return new Response(text, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('Content-Type') || 'application/json' },
  });
}
