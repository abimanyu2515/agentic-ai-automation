import { v4 as uuidv4 } from 'uuid';
import * as fs from 'fs';
import * as path from 'path';

export type ExecutionStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'stopped';

export interface TimelineEvent {
  timestamp: string;
  time: number;
  event: string;
  details?: string;
  type: 'info' | 'success' | 'warning' | 'error';
}

export interface ExecutionMetadata {
  executionId: string;
  projectId?: string;
  requirementId?: string;
  scenarioIds?: string[];
  testCaseIds?: string[];
  testCycleId?: string;
  browser: string;
  browserVersion?: string;
  status: ExecutionStatus;
  started: string;
  finished?: string;
  duration?: number;
}

export interface ExecutionArtifacts {
  screenshots: string[];
  video?: string;
  trace?: string;
  htmlReport?: string;
  jsonReport?: string;
  logs: string[];
  console: string[];
}

export interface Execution {
  metadata: ExecutionMetadata;
  script: string;
  timeline: TimelineEvent[];
  artifacts: ExecutionArtifacts;
  error?: string;
}

class ExecutionQueueManager {
  private executions: Map<string, Execution> = new Map();
  private queue: string[] = [];
  private currentExecutionId: string | null = null;
  private maxConcurrent: number = 1;
  private pausedExecutions: Set<string> = new Set();

  private static ACTIVE_FILE = path.join(process.cwd(), 'public', 'artifacts', 'active_executions.json');

  private saveToDisk(executionId: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;
    try {
      const dir = path.join(process.cwd(), 'public', 'artifacts', executionId);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(path.join(dir, 'metadata.json'), JSON.stringify(execution, null, 2), 'utf8');

      // Update the shared active_executions.json file
      let active: Record<string, Execution> = {};
      const activePath = ExecutionQueueManager.ACTIVE_FILE;
      const parentDir = path.dirname(activePath);
      if (!fs.existsSync(parentDir)) {
        fs.mkdirSync(parentDir, { recursive: true });
      }
      if (fs.existsSync(activePath)) {
        try {
          active = JSON.parse(fs.readFileSync(activePath, 'utf8'));
        } catch {}
      }
      active[executionId] = execution;
      
      // Prune old completed executions from active list to keep it fast
      const keys = Object.keys(active);
      if (keys.length > 50) {
        keys.sort((a, b) => new Date(active[b].metadata.started).getTime() - new Date(active[a].metadata.started).getTime());
        const pruned: Record<string, Execution> = {};
        keys.slice(0, 50).forEach(k => {
          pruned[k] = active[k];
        });
        active = pruned;
      }

      fs.writeFileSync(activePath, JSON.stringify(active, null, 2), 'utf8');
    } catch (err) {
      console.error('[ExecutionQueueManager] Failed to write metadata to disk:', err);
    }
  }

  private loadFromDisk(executionId: string): Execution | undefined {
    try {
      const activePath = ExecutionQueueManager.ACTIVE_FILE;
      if (fs.existsSync(activePath)) {
        const content = fs.readFileSync(activePath, 'utf8');
        const active = JSON.parse(content);
        if (active[executionId]) {
          this.executions.set(executionId, active[executionId]);
          return active[executionId];
        }
      }
    } catch {}

    try {
      const filePath = path.join(process.cwd(), 'public', 'artifacts', executionId, 'metadata.json');
      if (fs.existsSync(filePath)) {
        const content = fs.readFileSync(filePath, 'utf8');
        const execution = JSON.parse(content);
        this.executions.set(executionId, execution);
        return execution;
      }
    } catch (err) {
      // ignore
    }
    return undefined;
  }

  createExecution(script: string, metadata?: Partial<ExecutionMetadata>): string {
    const executionId = uuidv4();
    const now = new Date().toISOString();

    const execution: Execution = {
      metadata: {
        executionId,
        projectId: metadata?.projectId,
        requirementId: metadata?.requirementId,
        scenarioIds: metadata?.scenarioIds,
        testCaseIds: metadata?.testCaseIds,
        testCycleId: metadata?.testCycleId,
        browser: metadata?.browser || 'chromium',
        status: 'queued',
        started: now,
      },
      script,
      timeline: [
        {
          timestamp: now,
          time: 0,
          event: 'Execution Queued',
          type: 'info',
        },
      ],
      artifacts: {
        screenshots: [],
        logs: [],
        console: [],
      },
    };

    this.executions.set(executionId, execution);
    this.queue.push(executionId);
    this.saveToDisk(executionId);

    return executionId;
  }

  getExecution(executionId: string): Execution | undefined {
    let exec = this.executions.get(executionId);
    if (!exec) {
      exec = this.loadFromDisk(executionId);
    }
    return exec;
  }

  getAllExecutions(): Execution[] {
    try {
      const activePath = ExecutionQueueManager.ACTIVE_FILE;
      if (fs.existsSync(activePath)) {
        try {
          const content = fs.readFileSync(activePath, 'utf8');
          const active = JSON.parse(content);
          Object.keys(active).forEach(key => {
            this.executions.set(key, active[key]);
          });
        } catch {}
      }
    } catch (err) {
      console.error('[ExecutionQueueManager] Failed to read active executions from disk:', err);
    }
    return Array.from(this.executions.values());
  }

  getQueue(): string[] {
    return this.queue;
  }

  addTimelineEvent(
    executionId: string,
    event: string,
    type: 'info' | 'success' | 'warning' | 'error' = 'info',
    details?: string
  ): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    const started = new Date(execution.metadata.started).getTime();
    const now = new Date().getTime();
    const elapsed = (now - started) / 1000;

    execution.timeline.push({
      timestamp: new Date().toISOString(),
      time: elapsed,
      event,
      details,
      type,
    });

    this.saveToDisk(executionId);
  }

  setStatus(executionId: string, status: ExecutionStatus): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.metadata.status = status;

    if (status === 'completed' || status === 'failed' || status === 'stopped') {
      execution.metadata.finished = new Date().toISOString();
      const started = new Date(execution.metadata.started).getTime();
      const finished = new Date(execution.metadata.finished).getTime();
      execution.metadata.duration = (finished - started) / 1000;
    }

    this.addTimelineEvent(executionId, `Status: ${status}`, 'info');
  }

  pauseExecution(executionId: string): void {
    const execution = this.executions.get(executionId);
    if (!execution || execution.metadata.status !== 'running') return;

    this.pausedExecutions.add(executionId);
    this.setStatus(executionId, 'paused');
  }

  resumeExecution(executionId: string): void {
    const execution = this.executions.get(executionId);
    if (!execution || execution.metadata.status !== 'paused') return;

    this.pausedExecutions.delete(executionId);
    this.setStatus(executionId, 'running');
  }

  stopExecution(executionId: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    this.pausedExecutions.delete(executionId);
    this.setStatus(executionId, 'stopped');
  }

  addScreenshot(executionId: string, screenshotPath: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.artifacts.screenshots.push(screenshotPath);
    this.addTimelineEvent(executionId, 'Screenshot Captured', 'success', screenshotPath);
  }

  setArtifact(
    executionId: string,
    key: 'video' | 'trace' | 'htmlReport' | 'jsonReport',
    value: string
  ): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.artifacts[key] = value;
    this.addTimelineEvent(executionId, `${key} Generated`, 'success');
  }

  setBrowserVersion(executionId: string, version: string): void {
    const execution = this.executions.get(executionId);
    if (execution) {
      execution.metadata.browserVersion = version;
      this.saveToDisk(executionId);
    }
  }

  addLog(executionId: string, log: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.artifacts.logs.push(log);
    this.saveToDisk(executionId);
  }

  addConsole(executionId: string, message: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.artifacts.console.push(message);
    this.saveToDisk(executionId);
  }

  setError(executionId: string, error: string): void {
    const execution = this.executions.get(executionId);
    if (!execution) return;

    execution.error = error;
    this.addTimelineEvent(executionId, 'Error Occurred', 'error', error);
    this.setStatus(executionId, 'failed');
  }

  startNextExecution(): string | null {
    if (this.currentExecutionId) return null;
    if (this.queue.length === 0) return null;

    const executionId = this.queue.shift();
    if (!executionId) return null;

    const execution = this.executions.get(executionId);
    if (execution) {
      this.currentExecutionId = executionId;
      this.setStatus(executionId, 'running');
      this.addTimelineEvent(executionId, 'Browser Started', 'success');
    }

    return executionId;
  }

  startExecution(executionId: string): void {
    const execution = this.executions.get(executionId);
    if (execution) {
      this.currentExecutionId = executionId;
      this.setStatus(executionId, 'running');
      const idx = this.queue.indexOf(executionId);
      if (idx > -1) {
        this.queue.splice(idx, 1);
      }
    }
  }

  completeExecution(executionId: string): void {
    if (this.currentExecutionId === executionId) {
      this.currentExecutionId = null;
    }
    this.setStatus(executionId, 'completed');
  }

  getCurrentExecution(): string | null {
    return this.currentExecutionId;
  }
}

// Global singleton instance
export const executionQueue = new ExecutionQueueManager();
