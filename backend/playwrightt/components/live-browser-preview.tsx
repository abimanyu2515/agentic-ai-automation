'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { RefreshCw, X } from 'lucide-react';

export function LiveBrowserPreview() {
  const [url, setUrl] = useState('https://sampleapp.tricentis.com/101/app.php');
  const [displayUrl, setDisplayUrl] = useState(url);
  const [isLoading, setIsLoading] = useState(false);
  const [iframeKey, setIframeKey] = useState(0);

  const handleNavigate = () => {
    if (url.trim()) {
      setIsLoading(true);
      setDisplayUrl(url);
      setIframeKey(prev => prev + 1);
      // Simulate loading - iframe will call onLoad
      setTimeout(() => setIsLoading(false), 2000);
    }
  };

  const handleRefresh = () => {
    setIframeKey(prev => prev + 1);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleNavigate();
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg border border-gray-700 overflow-hidden">
      {/* Browser toolbar */}
      <div className="flex items-center gap-2 p-3 bg-gray-800 border-b border-gray-700">
        <Button
          onClick={handleRefresh}
          size="sm"
          variant="ghost"
          className="text-gray-400 hover:text-white"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </Button>

        <div className="flex-1 flex gap-2">
          <Input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Enter website URL..."
            className="bg-gray-700 border-gray-600 text-white text-sm"
          />
          <Button
            onClick={handleNavigate}
            size="sm"
            className="bg-blue-600 hover:bg-blue-700 text-white"
          >
            Go
          </Button>
        </div>

        <div className="text-xs text-gray-500 flex-shrink-0">
          {isLoading ? 'Loading...' : displayUrl}
        </div>
      </div>

      {/* Browser content */}
      <div className="flex-1 bg-white relative overflow-hidden">
        {displayUrl && (
          <iframe
            key={iframeKey}
            src={displayUrl}
            className="w-full h-full border-0"
            title="Live Browser Preview"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-modals allow-presentation"
            onLoad={() => setIsLoading(false)}
          />
        )}
      </div>
    </div>
  );
}
