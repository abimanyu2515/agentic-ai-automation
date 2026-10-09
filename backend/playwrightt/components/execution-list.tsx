'use client';

import { Execution, ExecutionStatus } from '@/lib/execution-queue';
import { Button } from '@/components/ui/button';
import { Play, Pause, X, RotateCw } from 'lucide-react';
import { useRouter } from 'next/navigation';

interface ExecutionListProps {
  executions: Execution[];
  queue: string[];
  current: string | null;
  onStatusChange: (executionId: string, action: string) => void;
  onRerun?: (script: string) => void;
}

function getStatusColor(status: ExecutionStatus): string {
  switch (status) {
    case 'completed':
      return 'bg-green-100 text-green-800 border border-green-300';
    case 'running':
      return 'bg-blue-100 text-blue-800 border border-blue-300';
    case 'paused':
      return 'bg-yellow-100 text-yellow-800 border border-yellow-300';
    case 'failed':
      return 'bg-red-100 text-red-800 border border-red-300';
    case 'stopped':
      return 'bg-gray-100 text-gray-800 border border-gray-300';
    case 'queued':
    default:
      return 'bg-purple-100 text-purple-800 border border-purple-300';
  }
}

function formatDuration(seconds?: number): string {
  if (!seconds) return '-';
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins}m ${secs}s`;
}

export function ExecutionList({
  executions,
  queue,
  current,
  onStatusChange,
  onRerun,
}: ExecutionListProps) {
  const router = useRouter();

  return (
    <div className="bg-card rounded-lg border border-border overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b border-border">
            <tr>
              <th className="px-4 py-3 text-left font-semibold text-foreground">
                Execution ID
              </th>
              <th className="px-4 py-3 text-left font-semibold text-foreground">
                Status
              </th>
              <th className="px-4 py-3 text-left font-semibold text-foreground">
                Duration
              </th>
              <th className="px-4 py-3 text-left font-semibold text-foreground">
                Started
              </th>
              <th className="px-4 py-3 text-right font-semibold text-foreground">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {executions.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                  No executions yet
                </td>
              </tr>
            ) : (
              executions.map((execution) => (
                <tr
                  key={execution?.metadata?.executionId || Math.random().toString()}
                  className="border-b border-border hover:bg-muted/50 cursor-pointer transition-colors"
                  onClick={() => {
                    if (execution?.metadata?.executionId) {
                      router.push(`/execution/${execution.metadata.executionId}`);
                    }
                  }}
                >
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {execution?.metadata?.executionId ? `${execution.metadata.executionId.slice(0, 8)}...` : 'unknown'}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block px-3 py-1 rounded-full text-xs font-medium ${getStatusColor(
                        execution?.metadata?.status || 'queued'
                      )}`}
                    >
                      {execution?.metadata?.status || 'queued'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-foreground">
                    {formatDuration(execution?.metadata?.duration)}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {execution?.metadata?.started ? new Date(execution.metadata.started).toLocaleTimeString() : '-'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div
                      className="flex gap-2 justify-end"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {execution?.metadata?.status === 'running' && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            if (execution?.metadata?.executionId) {
                              onStatusChange(execution.metadata.executionId, 'pause');
                            }
                          }}
                        >
                          <Pause className="w-4 h-4 text-foreground" />
                        </Button>
                      )}
                      {execution?.metadata?.status === 'paused' && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            if (execution?.metadata?.executionId) {
                              onStatusChange(execution.metadata.executionId, 'resume');
                            }
                          }}
                        >
                          <Play className="w-4 h-4 text-foreground" />
                        </Button>
                      )}
                      {(execution?.metadata?.status === 'running' ||
                        execution?.metadata?.status === 'paused' ||
                        execution?.metadata?.status === 'queued') && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            if (execution?.metadata?.executionId) {
                              onStatusChange(execution.metadata.executionId, 'stop');
                            }
                          }}
                        >
                          <X className="w-4 h-4 text-foreground" />
                        </Button>
                      )}
                      {(execution?.metadata?.status === 'completed' ||
                        execution?.metadata?.status === 'failed' ||
                        execution?.metadata?.status === 'stopped') && onRerun && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => onRerun(execution.script)}
                          title="Rerun Execution"
                        >
                          <RotateCw className="w-4 h-4 text-foreground" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
