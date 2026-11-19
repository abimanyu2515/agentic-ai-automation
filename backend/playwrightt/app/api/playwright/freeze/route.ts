import { NextRequest, NextResponse } from 'next/server';
import { forwardPut } from '@/lib/backend-auth';

// Proxies PUT /api/v1/playwright/freeze to FastAPI with the workspace secret,
// added here on the server so it never reaches the browser (spec 0009, AC-9).
export async function PUT(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: 'Request body must be JSON' }, { status: 400 });
  }
  try {
    return await forwardPut('/api/v1/playwright/freeze', body);
  } catch (err) {
    console.error('[Workspace proxy] Backend unreachable for freeze:', err);
    return NextResponse.json({ detail: 'Backend unreachable' }, { status: 502 });
  }
}
