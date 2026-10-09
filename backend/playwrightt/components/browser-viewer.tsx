'use client';

import { useState, useEffect, useRef } from 'react';
import { Play, Pause, RotateCcw, FastForward, SkipBack } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface BrowserViewerProps {
  executionId: string;
  isRunning: boolean;
  isPaused: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onRestart?: () => void;
}

export function BrowserViewer({
  executionId,
  isRunning,
  isPaused,
  onPause,
  onResume,
  onRestart,
}: BrowserViewerProps) {
  const [currentScreenshot, setCurrentScreenshot] = useState<string | null>(null);
  const [screenshots, setScreenshots] = useState<string[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(200); // ms per frame

  const playbackIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // 1. Fetch screenshots once on load or status change
  const fetchAllScreenshots = async () => {
    try {
      const response = await fetch(`/api/artifacts/${executionId}`);
      if (!response.ok) return;
      const data = await response.json();
      if (data.allScreenshots && data.allScreenshots.length > 0) {
        setScreenshots(data.allScreenshots);
        // During execution, follow the stream, otherwise don't reset index unless at 0
        if (!isRunning) {
          setCurrentIndex(prev => Math.min(prev, data.allScreenshots.length - 1));
          setCurrentScreenshot(data.allScreenshots[Math.min(currentIndex, data.allScreenshots.length - 1)]);
        }
      }
    } catch (error) {
      console.error('[Browser Viewer] Fetch screenshots error:', error);
    }
  };

  useEffect(() => {
    fetchAllScreenshots();
  }, [executionId, isRunning]);

  // 2. Real-time simulation stream via SSE during execution
  useEffect(() => {
    if (!isRunning) return;

    const eventSource = new EventSource(`/api/stream?id=${executionId}`);

    eventSource.addEventListener('message', (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'screenshot' && message.data && message.data.url) {
          const url = message.data.url;
          setScreenshots((prev) => {
            const next = [...prev, url];
            setCurrentIndex(next.length - 1);
            setCurrentScreenshot(url);
            return next;
          });
        }
      } catch (err) {
        console.error('[Browser Viewer] SSE parse error:', err);
      }
    });

    eventSource.addEventListener('error', () => {
      eventSource.close();
    });

    return () => {
      eventSource.close();
    };
  }, [executionId, isRunning]);

  // 3. Playback simulation runner (Replay Mode)
  useEffect(() => {
    if (isPlaying && !isRunning && screenshots.length > 0) {
      playbackIntervalRef.current = setInterval(() => {
        setCurrentIndex((prevIndex) => {
          const nextIndex = prevIndex + 1;
          if (nextIndex >= screenshots.length) {
            setIsPlaying(false);
            if (playbackIntervalRef.current) clearInterval(playbackIntervalRef.current);
            return prevIndex;
          }
          setCurrentScreenshot(screenshots[nextIndex]);
          return nextIndex;
        });
      }, playbackSpeed);
    } else {
      if (playbackIntervalRef.current) {
        clearInterval(playbackIntervalRef.current);
      }
    }

    return () => {
      if (playbackIntervalRef.current) clearInterval(playbackIntervalRef.current);
    };
  }, [isPlaying, isRunning, screenshots, playbackSpeed]);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const idx = parseInt(e.target.value, 10);
    setCurrentIndex(idx);
    if (screenshots[idx]) {
      setCurrentScreenshot(screenshots[idx]);
    }
    setIsPlaying(false);
  };

  const togglePlay = () => {
    if (currentIndex >= screenshots.length - 1) {
      setCurrentIndex(0);
      setCurrentScreenshot(screenshots[0]);
    }
    setIsPlaying(!isPlaying);
  };

  return (
    <div className="h-full flex flex-col bg-slate-900 rounded-lg overflow-hidden border border-slate-700 shadow-xl">
      {/* Top Header Bar */}
      <div className="bg-slate-800 border-b border-slate-700 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-2.5 h-2.5 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-slate-500'}`} />
          <span className="text-sm font-semibold text-slate-300 font-mono">
            {isRunning ? 'LIVE SIMULATION' : 'SIMULATION REPLAY'} ({executionId.slice(0, 8)})
          </span>
        </div>

        {/* Live Controller (Pause / Resume Server Run) */}
        {isRunning && (
          <div className="flex gap-2">
            {!isPaused ? (
              <Button
                size="sm"
                variant="ghost"
                className="text-slate-300 hover:text-white hover:bg-slate-700"
                onClick={onPause}
                title="Pause Execution"
              >
                <Pause className="w-4 h-4" />
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                className="text-slate-300 hover:text-white hover:bg-slate-700"
                onClick={onResume}
                title="Resume Execution"
              >
                <Play className="w-4 h-4" />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Main Canvas view */}
      <div className="flex-1 bg-slate-950 flex items-center justify-center overflow-auto p-4 relative">
        {isRunning ? (
          currentScreenshot ? (
            <img
              src={currentScreenshot}
              alt="Browser view"
              className="max-w-full max-h-full object-contain rounded border border-slate-800 shadow-md"
            />
          ) : (
            <div className="text-center text-slate-500 space-y-3">
              <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto" />
              <p className="text-sm font-medium">Waiting for simulation stream...</p>
            </div>
          )
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <video
              src={`/api/artifacts/${executionId}/video`}
              controls
              autoPlay
              loop
              className="max-w-full max-h-full rounded border border-slate-800 shadow-2xl"
              style={{ maxHeight: 'calc(100% - 20px)' }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
