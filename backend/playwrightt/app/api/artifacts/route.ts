import { NextRequest, NextResponse } from 'next/server';
import { executionQueue } from '@/lib/execution-queue';
import * as fs from 'fs/promises';
import * as path from 'path';

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const executionId = searchParams.get('id');

    if (!executionId) {
      return NextResponse.json({ error: 'Missing execution ID' }, { status: 400 });
    }

    const execution = executionQueue.getExecution(executionId);
    if (!execution) {
      // If execution not in queue, try to read screenshots from disk
      const screenshotsDir = path.join(
        process.cwd(),
        'public',
        'artifacts',
        executionId,
        'screenshots'
      );
      
      try {
        const files = await fs.readdir(screenshotsDir);
        // Filter for named screenshots (not live-XXXXX.png)
        const namedScreenshots = files
          .filter(f => f.endsWith('.png') && !f.startsWith('live-'))
          .sort();
        
        const allScreenshots = files.filter(f => f.endsWith('.png')).sort();
        const screenshotUrls = namedScreenshots.map(f => `/api/artifacts/${executionId}/screenshot/${f}`);
        const allScreenshotUrls = allScreenshots.map(f => `/api/artifacts/${executionId}/screenshot/${f}`);
        
        return NextResponse.json({
          executionId,
          screenshots: screenshotUrls,
          allScreenshots: allScreenshotUrls,
        });
      } catch (err) {
        return NextResponse.json({ error: 'Execution not found' }, { status: 404 });
      }
    }

    return NextResponse.json({
      executionId,
      artifacts: execution.artifacts,
      metadata: execution.metadata,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}

// GET /api/artifacts/[executionId]/screenshot/[filename]
export async function GET_SCREENSHOT(
  request: NextRequest,
  { params }: { params: { executionId: string; filename: string } }
) {
  try {
    const { executionId, filename } = params;
    const artifactDir = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'screenshots',
      filename
    );

    const fileBuffer = await fs.readFile(artifactDir);
    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=3600',
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: 'File not found' }, { status: 404 });
  }
}

// GET /api/artifacts/[executionId]/report
export async function GET_REPORT(
  request: NextRequest,
  { params }: { params: { executionId: string } }
) {
  try {
    const { executionId } = params;
    const reportPath = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'report.html'
    );

    const fileBuffer = await fs.readFile(reportPath);
    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: 'Report not found' }, { status: 404 });
  }
}

// GET /api/artifacts/[executionId]/trace
export async function GET_TRACE(
  request: NextRequest,
  { params }: { params: { executionId: string } }
) {
  try {
    const { executionId } = params;
    const tracePath = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'trace',
      'trace.zip'
    );

    const fileBuffer = await fs.readFile(tracePath);
    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': `attachment; filename="trace-${executionId}.zip"`,
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: 'Trace not found' }, { status: 404 });
  }
}

// GET /api/artifacts/[executionId]/video
export async function GET_VIDEO(
  request: NextRequest,
  { params }: { params: { executionId: string } }
) {
  try {
    const { executionId } = params;
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
