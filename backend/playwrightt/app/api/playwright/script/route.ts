import { NextRequest, NextResponse } from 'next/server';
import { forwardPut } from '@/lib/backend-auth';

// Proxies PUT /api/v1/playwright/script to FastAPI with the workspace secret,
// added here on the server so it never reaches the browser (spec 0009, AC-9).
export async function PUT(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: 'Request body must be JSON' }, { status: 400 });
  }
  try {
    return await forwardPut('/api/v1/playwright/script', body);
  } catch (err) {
    console.error('[Workspace proxy] Backend unreachable for script:', err);
    return NextResponse.json({ detail: 'Backend unreachable' }, { status: 502 });
  }
}
