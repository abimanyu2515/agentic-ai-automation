'use client';

import { useState, useRef } from 'react';
import { ExecutionArtifacts } from '@/lib/execution-queue';
import { Download, X, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ArtifactPreviewProps {
  executionId: string;
  artifacts: ExecutionArtifacts;
}

export function ArtifactPreview({ executionId, artifacts }: ArtifactPreviewProps) {
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [videoOpen, setVideoOpen] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const hasScreenshots = artifacts.screenshots.length > 0;
  const hasVideo = !!artifacts.video;
  const hasTrace = !!artifacts.trace;
  const hasReport = !!artifacts.htmlReport;

  if (!hasScreenshots && !hasVideo && !hasTrace && !hasReport) {
    return (
      <div className="text-center py-8 text-gray-500">
        No artifacts available
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Screenshots */}
      {hasScreenshots && (
        <div>
          <h3 className="font-semibold text-gray-900 mb-3">
            Screenshots ({artifacts.screenshots.length})
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {artifacts.screenshots.map((screenshot) => (
              <div
                key={screenshot}
                className="relative group bg-gray-100 rounded-lg overflow-hidden cursor-pointer"
                onClick={() =>
                  setSelectedImage(`/api/artifacts/${executionId}/screenshot/${screenshot}`)
                }
              >
                <img
                  src={`/api/artifacts/${executionId}/screenshot/${screenshot}`}
                  alt={screenshot}
                  className="w-full h-32 object-cover group-hover:opacity-80 transition-opacity"
                />
                <div className="absolute inset-0 bg-black bg-opacity-0 group-hover:bg-opacity-30 transition-all flex items-center justify-center opacity-0 group-hover:opacity-100">
                  <Play className="w-6 h-6 text-white" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Video */}
      {hasVideo && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-gray-900">Video Recording</h3>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setVideoOpen(!videoOpen)}
            >
              {videoOpen ? 'Hide' : 'View'}
            </Button>
          </div>
          {videoOpen && (
            <div className="bg-black rounded-lg overflow-hidden">
              <video
                ref={videoRef}
                src={`/api/artifacts/${executionId}/video`}
                className="w-full max-h-96"
                controls
              />
            </div>
          )}
          <a
            href={`/api/artifacts/${executionId}/video`}
            download
            className="text-blue-600 hover:text-blue-700 text-sm inline-flex items-center gap-2 mt-2"
          >
            <Download className="w-4 h-4" />
            Download Video
          </a>
        </div>
      )}

      {/* Report */}
      {hasReport && (
        <div>
          <h3 className="font-semibold text-gray-900 mb-3">HTML Report</h3>
          <a
            href={`/api/artifacts/${executionId}/report`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Play className="w-4 h-4" />
            Open Report
          </a>
        </div>
      )}

      {/* Trace */}
      {hasTrace && (
        <div>
          <h3 className="font-semibold text-gray-900 mb-3">Playwright Trace</h3>
          <a
            href={`/api/artifacts/${executionId}/trace`}
            download
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            <Download className="w-4 h-4" />
            Download Trace
          </a>
          <p className="text-xs text-gray-600 mt-2">
            View with: npx playwright show-trace artifact.zip
          </p>
        </div>
      )}

      {/* Image Modal */}
      {selectedImage && (
        <div
          className="fixed inset-0 z-50 bg-black bg-opacity-75 flex items-center justify-center p-4"
          onClick={() => setSelectedImage(null)}
        >
          <div
            className="relative max-w-4xl max-h-96 bg-white rounded-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setSelectedImage(null)}
              className="absolute top-2 right-2 bg-gray-900 bg-opacity-75 text-white p-2 rounded-lg hover:bg-opacity-90 z-10"
            >
              <X className="w-4 h-4" />
            </button>
            <img
              src={selectedImage}
              alt="Artifact preview"
              className="w-full h-full object-contain"
            />
          </div>
        </div>
      )}
    </div>
  );
}
