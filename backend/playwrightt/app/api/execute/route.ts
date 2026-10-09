import { NextRequest, NextResponse } from 'next/server';
import { executionQueue } from '@/lib/execution-queue';
import { ExecutionEngine } from '@/lib/execution-engine';
import * as path from 'path';

export async function POST(request: NextRequest) {
  try {
    const { script, projectId, requirementId, scenarioIds, testCaseIds, browser, testCycleId } = await request.json();
    console.log(`[API Execute] Received script run request. projectId: "${projectId}", testCaseIds: ${JSON.stringify(testCaseIds)}, browser: "${browser}", testCycleId: "${testCycleId}"`);

    if (!script || typeof script !== 'string') {
      return NextResponse.json(
        { error: 'Script is required' },
        { status: 400 }
      );
    }

    // Create execution in queue with metadata
    const executionId = executionQueue.createExecution(script, {
      projectId,
      requirementId,
      scenarioIds,
      testCaseIds,
      testCycleId,
      browser,
    });

    // Setup artifact directory
    const artifactDir = path.join(process.cwd(), 'public', 'artifacts', executionId);

    // Validate script
    const engine = new ExecutionEngine();
    const validation = await engine.validateScript(script);
    if (!validation.valid) {
      executionQueue.setError(executionId, validation.error || 'Invalid script');
      return NextResponse.json(
        { executionId, error: validation.error },
        { status: 400 }
      );
    }

    // Start execution asynchronously (don't await)
    engine.run({
      executionId,
      script,
      artifactDir,
      recordVideo: true,
      recordTrace: true,
    }).catch((error) => {
      console.error('[API] Execution error:', error);
      executionQueue.setError(executionId, error.message);
    });

    // Return execution metadata immediately
    const execution = executionQueue.getExecution(executionId);
    return NextResponse.json({
      executionId,
      status: 'queued',
      metadata: execution?.metadata,
      timeline: execution?.timeline,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }
}
