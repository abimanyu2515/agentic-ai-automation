import { NextRequest, NextResponse } from 'next/server';
import { executionQueue } from '@/lib/execution-queue';
import * as fs from 'fs/promises';
import * as path from 'path';

export const dynamic = 'force-dynamic';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ executionId: string }> }
) {
  console.log("--- GET ARTIFACTS DYNAMIC ROUTE HIT ---");
  try {
    const { executionId } = await params;
    console.log("GET ARTIFACTS executionId:", executionId);

    // First try to get from execution queue
    const execution = executionQueue.getExecution(executionId);
    if (execution) {
      const namedScreenshots = (execution.artifacts.screenshots || []).map(
        f => `/api/artifacts/${executionId}/screenshot/${f}`
      );
      // Retrieve filenames from the timeline events when available
      const timelineScreenshots = (execution.timeline || [])
        .filter(e => e.details && e.details.endsWith('.png'))
        .map(e => `/api/artifacts/${executionId}/screenshot/${e.details}`);

      const allScreenshotUrls = timelineScreenshots.length > 0 
        ? timelineScreenshots 
        : namedScreenshots;

      return NextResponse.json({
        executionId,
        artifacts: execution.artifacts,
        metadata: execution.metadata,
        screenshots: allScreenshotUrls,
        allScreenshots: allScreenshotUrls,
      });
    }

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
      const screenshotUrls = namedScreenshots.map(
        f => `/api/artifacts/${executionId}/screenshot/${f}`
      );
      const allScreenshotUrls = allScreenshots.map(
        f => `/api/artifacts/${executionId}/screenshot/${f}`
      );

      return NextResponse.json({
        executionId,
        screenshots: screenshotUrls.length > 0 ? screenshotUrls : allScreenshotUrls,
        allScreenshots: allScreenshotUrls,
      });
    } catch (err) {
      return NextResponse.json({ error: 'Execution not found' }, { status: 404 });
    }
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
