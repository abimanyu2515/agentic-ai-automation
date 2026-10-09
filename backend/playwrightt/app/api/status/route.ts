import { NextRequest, NextResponse } from 'next/server';
import { executionQueue } from '@/lib/execution-queue';
import * as fs from 'fs/promises';
import * as path from 'path';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  console.log('[API Status GET] Request url:', request.url);
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get('id');
    const projectId = searchParams.get('projectId');

    if (id) {
      // Get single execution
      const execution = executionQueue.getExecution(id);
      if (execution) {
        return NextResponse.json(execution);
      }

      // If not in local queue, try to fetch from database
      try {
        let hostname = new URL(request.url).hostname;
        if (hostname === 'localhost') hostname = '127.0.0.1';
        const dbResponse = await fetch(`http://${hostname}:8000/api/v1/executions/${id}`, { cache: 'no-store' });
        if (dbResponse.ok) {
          const dbEx = await dbResponse.json();
          const getBasename = (p: string | null) => p ? p.split(/[\\/]/).pop() : undefined;
          const screenshotFile = getBasename(dbEx.screenshot_path);

          // Attempt to load timeline from report.html if it exists
          let timeline: any[] = [];
          const nextReportPath = path.join(process.cwd(), 'public', 'artifacts', id, 'report.html');
          try {
            const reportContent = await fs.readFile(nextReportPath, 'utf8');
            const matches = Array.from(reportContent.matchAll(/<span class="step-num">Step (\d+)<\/span>\s*<span class="step-title">([^<]+)<\/span>/g));
            if (matches.length > 0) {
              timeline = matches.map((m: any, idx: number) => ({
                timestamp: new Date().toISOString(),
                time: idx * 2.0,
                event: m[2],
                type: 'info'
              }));
            }
          } catch {}

          if (dbEx.error_message) {
            timeline.push({
              timestamp: new Date().toISOString(),
              time: dbEx.duration_seconds || 1.0,
              event: 'Execution Failure',
              details: dbEx.error_message,
              type: 'error'
            });
          }

          return NextResponse.json({
            metadata: {
              executionId: dbEx.id,
              projectId: dbEx.project_id,
              browser: 'chromium',
              status: dbEx.status, // e.g. 'passed', 'failed', 'error'
              started: dbEx.executed_at,
              finished: dbEx.executed_at,
              duration: dbEx.duration_seconds,
              testCycleId: dbEx.test_cycle_id,
              testCaseIds: [dbEx.test_case_id],
            },
            script: '',
            timeline: timeline,
            artifacts: {
              screenshots: screenshotFile ? [screenshotFile] : [],
              video: getBasename(dbEx.video_path),
              trace: getBasename(dbEx.trace_path),
            },
            error: dbEx.error_message
          });
        }
      } catch (err) {
        console.error('[API Status] Failed to fetch database execution:', err);
      }

      return NextResponse.json(
        { error: 'Execution not found' },
        { status: 404 }
      );
    } else {
      // Fetch historical executions from the database
      let dbExecutions: any[] = [];
      if (projectId) {
        try {
          let hostname = new URL(request.url).hostname;
          if (hostname === 'localhost') hostname = '127.0.0.1';
          const dbResponse = await fetch(`http://${hostname}:8000/api/v1/projects/${projectId}/executions`, { cache: 'no-store' });
          if (dbResponse.ok) {
            const dbData = await dbResponse.json();
            dbExecutions = dbData.map((dbEx: any) => {
              const getBasename = (p: string | null) => p ? p.split(/[\\/]/).pop() : undefined;
              const screenshotFile = getBasename(dbEx.screenshot_path);
              
              return {
                metadata: {
                  executionId: dbEx.id,
                  projectId: projectId,
                  browser: 'chromium',
                  status: dbEx.status,
                  started: dbEx.executed_at,
                  finished: dbEx.executed_at,
                  duration: dbEx.duration_seconds,
                  testCycleId: dbEx.test_cycle_id,
                  testCaseIds: [dbEx.test_case_id],
                },
                script: '', 
                timeline: [],
                artifacts: {
                  screenshots: screenshotFile ? [screenshotFile] : [],
                  video: getBasename(dbEx.video_path),
                  trace: getBasename(dbEx.trace_path),
                },
                error: dbEx.error_message
              };
            });
          }
        } catch (dbErr) {
          console.error('[API Status] Failed to fetch database executions:', dbErr);
        }
      }

      const localExecutions = executionQueue.getAllExecutions();
      const mergedMap = new Map<string, any>();
      
      // Load database executions first
      dbExecutions.forEach(ex => {
        mergedMap.set(ex.metadata.executionId, ex);
      });
      
      // Merge local active runs (overwriting database copies if matching)
      localExecutions.forEach(ex => {
        mergedMap.set(ex.metadata.executionId, ex);
      });
      
      const executions = Array.from(mergedMap.values()).sort((a, b) => 
        new Date(b.metadata.started).getTime() - new Date(a.metadata.started).getTime()
      );

      const queue = executionQueue.getQueue();
      return NextResponse.json({ 
        executions,
        queue,
        current: executionQueue.getCurrentExecution(),
      });
    }
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: errorMsg }, { status: 500 });
  }
}

export async function PUT(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get('id');
    const action = searchParams.get('action');

    if (!id || !action) {
      return NextResponse.json(
        { error: 'Missing id or action' },
        { status: 400 }
      );
    }

    const execution = executionQueue.getExecution(id);
    if (!execution) {
      return NextResponse.json(
        { error: 'Execution not found' },
        { status: 404 }
      );
    }

    switch (action) {
      case 'pause':
        executionQueue.pauseExecution(id);
        break;
      case 'resume':
        executionQueue.resumeExecution(id);
        break;
      case 'stop':
        executionQueue.stopExecution(id);
        break;
      default:
        return NextResponse.json(
          { error: 'Invalid action' },
          { status: 400 }
        );
    }

    return NextResponse.json(execution);
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: errorMsg }, { status: 500 });
  }
}
