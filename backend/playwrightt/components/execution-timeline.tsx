'use client';

import { TimelineEvent } from '@/lib/execution-queue';
import { CheckCircle2, AlertCircle, Info, XCircle } from 'lucide-react';

interface ExecutionTimelineProps {
  events: TimelineEvent[];
  isLoading?: boolean;
}

export function ExecutionTimeline({ events, isLoading = false }: ExecutionTimelineProps) {
  const getIcon = (type: string) => {
    switch (type) {
      case 'success':
        return <CheckCircle2 className="w-5 h-5 text-green-600" />;
      case 'error':
        return <XCircle className="w-5 h-5 text-red-600" />;
      case 'warning':
        return <AlertCircle className="w-5 h-5 text-yellow-600" />;
      case 'info':
      default:
        return <Info className="w-5 h-5 text-blue-600" />;
    }
  };

  const getTextColor = (type: string) => {
    switch (type) {
      case 'success':
        return 'text-green-900';
      case 'error':
        return 'text-red-900';
      case 'warning':
        return 'text-yellow-900';
      case 'info':
      default:
        return 'text-blue-900';
    }
  };

  const getBgColor = (type: string) => {
    switch (type) {
      case 'success':
        return 'bg-green-50 border-green-200';
      case 'error':
        return 'bg-red-50 border-red-200';
      case 'warning':
        return 'bg-yellow-50 border-yellow-200';
      case 'info':
      default:
        return 'bg-blue-50 border-blue-200';
    }
  };

  return (
    <div className="space-y-0">
      {events.length === 0 ? (
        <div className="text-center py-8 text-gray-500">
          {isLoading ? 'Waiting for events...' : 'No timeline events yet'}
        </div>
      ) : (
        events.map((event, index) => (
          <div
            key={index}
            className={`flex gap-4 p-4 border-l-4 ${getBgColor(event.type)}`}
          >
            <div className="flex-shrink-0 pt-0.5">
              {getIcon(event.type)}
            </div>
            <div className="flex-grow">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xs text-gray-500">
                  {event.timestamp.split('T')[1].split('.')[0]}
                </span>
                <span className={`font-semibold ${getTextColor(event.type)}`}>
                  {event.event}
                </span>
                <span className="text-xs text-gray-500">
                  (+{event.time.toFixed(2)}s)
                </span>
              </div>
              {event.details && (
                <p className="mt-1 text-sm text-gray-700 break-all">
                  {event.details}
                </p>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
