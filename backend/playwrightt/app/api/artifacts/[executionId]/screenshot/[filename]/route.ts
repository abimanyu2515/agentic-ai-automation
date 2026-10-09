import { NextRequest, NextResponse } from 'next/server';
import * as fs from 'fs/promises';
import * as path from 'path';

export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ executionId: string; filename: string }> }
) {
  try {
    const { executionId, filename } = await params;
    
    // Construct path to screenshot in public/artifacts
    const screenshotPath = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'screenshots',
      filename
    );

    // Read the image file
    const fileBuffer = await fs.readFile(screenshotPath);
    
    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=3600',
      },
    });
  } catch (error: any) {
    console.error(`[Screenshot API] Error serving ${params}:`, error.message);
    return NextResponse.json(
      { error: 'Screenshot not found', details: error.message },
      { status: 404 }
    );
  }
}
