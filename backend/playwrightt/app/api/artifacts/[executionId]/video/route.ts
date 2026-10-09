import { NextRequest, NextResponse } from 'next/server';
import * as fs from 'fs/promises';
import * as path from 'path';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ executionId: string }> }
) {
  try {
    const { executionId } = await params;
    const videoDir = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'video'
    );

    const files = await fs.readdir(videoDir);
    const videoFile = files.find(f => f.endsWith('.webm'));

    if (!videoFile) {
      return NextResponse.json({ error: 'Video not found' }, { status: 404 });
    }

    const videoPath = path.join(videoDir, videoFile);
    const fileBuffer = await fs.readFile(videoPath);

    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'video/webm',
        'Content-Disposition': `inline; filename="${videoFile}"`,
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: 'Video not found' }, { status: 404 });
  }
}
