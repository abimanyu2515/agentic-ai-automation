'use client';

import { CheckCircle, AlertCircle, Clock, Image, List } from 'lucide-react';

interface TimelineEvent {
  timestamp: string;
  time: number;
  event: string;
  details?: string;
  type: 'info' | 'success' | 'warning' | 'error';
}

interface ResultsSummaryProps {
  status: 'completed' | 'running' | 'failed' | 'stopped';
  duration: number;
  screenshots: string[];
  timeline: TimelineEvent[];
  executionId: string;
}

function mapEventToStep(event: TimelineEvent, index: number, executionId: string, screenshots: string[] = []) {
  const eventName = event.event;
  const timeVal = event.time;
  const evtType = event.type;
  const details = event.details || "";

  // Check if this event represents a screenshot or has a screenshot details
  const screenshotFilename = details.endsWith(".png") ? details : null;
  const screenshotUrl = screenshotFilename 
    ? (screenshotFilename.startsWith('/') || screenshotFilename.startsWith('http')
        ? screenshotFilename
        : `/api/artifacts/${executionId}/screenshots/${screenshotFilename}`)
    : null;

  let title = eventName;
  let action = `Execute test step: '${eventName}'.`;
  let observation = `Timeline logged event of type '${evtType}'.`;
  let result = "Step executed successfully.";

  if (evtType === "error" || eventName.toLowerCase().includes("error")) {
    title = "Execution Failure";
    const detailsLower = details.toLowerCase();
    if (detailsLower.includes("strict mode violation")) {
      action = "Select unique target element on screen";
      observation = "Playwright strict mode violation: The test code attempted to click or interact with an element, but the browser found multiple elements matching that description.";
      result = "The selector is ambiguous. To resolve this, update the test script locator to be more specific, such as using the exact button/link text (e.g. 'Enter Vehicle Data') or targeting its unique ID/attributes (e.g. '#entervehicledata').";
    } else if (detailsLower.includes("element is not an <input>") || detailsLower.includes("locator resolved to <select") || detailsLower.includes("selectoption") || detailsLower.includes("select option")) {
      action = "Select dropdown option value";
      observation = "Playwright tried to type or fill text into a dropdown selection element (<select>) instead of choosing one of its options.";
      result = "A dropdown (<select>) element cannot be filled with text. The automation script must be corrected to use 'selectOption' (e.g. page.selectOption('#make', 'Toyota') or page.locator('#make').selectOption('Toyota')) instead of 'fill'.";
    } else if (detailsLower.includes("timeout") || detailsLower.includes("waiting for locator") || detailsLower.includes("waiting for selector")) {
      action = "Wait for target element to become visible / interactive on page";
      observation = "Playwright timed out waiting for the target element to load or appear on the page (the element remained hidden or was not rendered within the timeout period).";
      result = "Verify if the preceding test steps executed successfully, check if the website response was slow, or confirm if the element's selector is correct.";
    } else if (detailsLower.includes("is hidden") || detailsLower.includes("is not visible") || detailsLower.includes("intercepts pointer events") || detailsLower.includes("disabled")) {
      action = "Interact with target element";
      observation = "The target element was found on the page, but it was hidden, disabled, or blocked/intercepted by another page element (like a modal popup, overlay, or loading spinner).";
      result = "Ensure the target element is fully active and visible, and close any blocking modals or overlays before interacting with it.";
    } else if (detailsLower.includes("net::err") || detailsLower.includes("navigation failed") || detailsLower.includes("page.goto")) {
      action = "Load application landing page";
      observation = "The browser failed to navigate to the target URL. The application server might be down, the hostname might be invalid, or the server is refusing connections.";
      result = "Verify that the target application is running locally or online, and that your local network connection / proxy settings are active.";
    } else if (detailsLower.includes("expect") || detailsLower.includes("assertionerror") || detailsLower.includes("assertion")) {
      action = "Verify expected test condition (Assertion)";
      observation = "The test run successfully completed its actions, but the final validation check failed. The page content or page state did not match the expected assertion criteria.";
      result = "Verify if the application behaved unexpectedly, or if the test assertion value needs to be updated.";
    } else {
      action = "Interact with target page controls / elements.";
      observation = `Playwright runner logged error: ${details}`;
      result = `Error details: ${details || 'Timeout/Assertion failure'}`;
    }
  } else if (screenshotFilename) {
    const filenameOnly = screenshotFilename.includes('/') 
      ? screenshotFilename.split('/').pop() || ''
      : screenshotFilename;
    const fnLower = filenameOnly.toLowerCase();
    
    // Check if the timeline event name is a generic placeholder event
    const eventLower = eventName.toLowerCase();
    const isPlaceholder = eventLower.includes("screenshot captured") || eventLower === "screenshot" || eventLower === "visual state capture";

    title = isPlaceholder ? "Visual State Capture" : eventName;
    action = isPlaceholder ? "Capture screenshot to record browser visual state." : `Execute test step: '${eventName}'.`;
    observation = isPlaceholder ? `Visual state captured in file '${screenshotFilename}'.` : "Browser successfully navigated / interacted. Verified visual state layout.";
    result = isPlaceholder ? "Screenshot image saved on disk." : "Step executed successfully.";
  }

  let finalScreenshotUrl = screenshotUrl;
  if (!finalScreenshotUrl && (evtType === "error" || eventName.toLowerCase().includes("error"))) {
    let errorFilename = screenshots.find(
      s => s.toLowerCase().includes('error') || s.toLowerCase().includes('failed')
    );
    if (!errorFilename && screenshots.length > 0) {
      errorFilename = screenshots[screenshots.length - 1];
    }
    if (errorFilename) {
      finalScreenshotUrl = errorFilename.startsWith('/') || errorFilename.startsWith('http')
        ? errorFilename
        : `/api/artifacts/${executionId}/screenshots/${errorFilename}`;
    }
  }

  return {
    title,
    time: `+${timeVal.toFixed(2)}s`,
    action,
    observation,
    result,
    screenshotUrl: finalScreenshotUrl
  };
}

export function ResultsSummary({
  status,
  duration,
  screenshots,
  timeline,
  executionId,
}: ResultsSummaryProps) {
  const isPassed = status === 'completed';
  const statusColor = isPassed ? 'text-green-600' : 'text-red-600';
  const statusBgColor = isPassed ? 'bg-green-50' : 'bg-red-50';
  const statusBorder = isPassed ? 'border-green-200' : 'border-red-200';

  // Filter out events that represent visual steps or failures
  const stepEvents = (timeline || []).filter(
    (evt) => evt.type === 'error' || (evt.details && evt.details.endsWith('.png'))
  );

  const steps = stepEvents.map((evt, idx) => mapEventToStep(evt, idx, executionId, screenshots));

  return (
    <div className="space-y-6">
      {/* Status Header */}
      <div className={`${statusBgColor} border ${statusBorder} rounded-lg p-6`}>
        <div className="flex items-start gap-4">
          {isPassed ? (
            <CheckCircle className={`w-8 h-8 ${statusColor} flex-shrink-0 mt-1`} />
          ) : (
            <AlertCircle className={`w-8 h-8 ${statusColor} flex-shrink-0 mt-1`} />
          )}
          <div className="flex-1">
            <h2 className={`text-2xl font-bold ${statusColor} mb-1`}>
              {isPassed ? 'PASSED' : 'FAILED'}
            </h2>
            <p className="text-gray-600">Execution {executionId.slice(0, 8)}</p>
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-300 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Clock className="w-5 h-5 text-blue-600" />
            <span className="text-sm font-medium text-gray-600">Duration</span>
          </div>
          <p className="text-2xl font-bold text-gray-900">{(duration || 0).toFixed(2)}s</p>
        </div>

        <div className="bg-white border border-gray-300 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Image className="w-5 h-5 text-blue-600" />
            <span className="text-sm font-medium text-gray-600">Screenshots</span>
          </div>
          <p className="text-2xl font-bold text-gray-900">{screenshots.length}</p>
        </div>

        <div className="bg-white border border-gray-300 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <List className="w-5 h-5 text-blue-600" />
            <span className="text-sm font-medium text-gray-600">Timeline Events</span>
          </div>
          <p className="text-2xl font-bold text-gray-900">{timeline.length}</p>
        </div>
      </div>

      {/* Walkthrough List */}
      <div className="bg-white border border-gray-300 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4 border-b pb-2">
          Step-by-Step Walkthrough
        </h3>
        {steps.length === 0 ? (
          <p className="text-sm text-gray-500">No walkthrough steps generated yet.</p>
        ) : (
          <div className="space-y-6">
            {steps.map((step, idx) => (
              <div key={idx} className="grid grid-cols-1 md:grid-cols-2 gap-6 p-4 border border-gray-200 rounded-lg bg-gray-50/50">
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <span className="bg-blue-600 text-white font-bold text-xs px-2.5 py-1 rounded">
                      STEP {idx + 1}
                    </span>
                    <span className="font-semibold text-gray-800">{step.title}</span>
                    <span className="text-xs text-gray-500 font-mono ml-auto">{step.time}</span>
                  </div>
                  
                  <div className="text-sm space-y-2 border-t pt-2">
                    <div>
                      <span className="text-xs font-bold text-gray-500 uppercase tracking-wider block">Actions Taken</span>
                      <p className="text-gray-700">{step.action}</p>
                    </div>
                    <div>
                      <span className="text-xs font-bold text-gray-500 uppercase tracking-wider block">Observation</span>
                      <p className="text-gray-700">{step.observation}</p>
                    </div>
                    <div>
                      <span className="text-xs font-bold text-gray-500 uppercase tracking-wider block">Result</span>
                      <p className="text-gray-700 font-medium">{step.result}</p>
                    </div>
                  </div>
                </div>
                
                <div className="flex items-center justify-center bg-gray-100 rounded-lg border border-gray-200 aspect-video overflow-hidden">
                  {step.screenshotUrl ? (
                    <img
                      src={step.screenshotUrl}
                      alt={`Step ${idx + 1}`}
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <span className="text-xs text-gray-400">No Screenshot Captured</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Screenshots Gallery */}
      {screenshots.length > 0 && (
        <div className="bg-white border border-gray-300 rounded-lg p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">
            Screenshots Gallery ({screenshots.length})
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {screenshots.map((screenshotUrl, index) => (
              <div
                key={index}
                className="border border-gray-200 rounded-lg overflow-hidden bg-gray-50 hover:shadow-md transition-shadow"
              >
                <div className="aspect-video bg-gray-200 flex items-center justify-center">
                  <img
                    src={screenshotUrl.startsWith('/') || screenshotUrl.startsWith('http') ? screenshotUrl : `/api/artifacts/${executionId}/screenshot/${screenshotUrl}`}
                    alt={`Screenshot ${index + 1}`}
                    className="w-full h-full object-cover"
                  />
                </div>
                <div className="p-2 bg-white">
                  <p className="text-xs text-gray-600">
                    Screenshot {index + 1}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Timeline Summary */}
      {timeline.length > 0 && (
        <div className="bg-white border border-gray-300 rounded-lg p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">
            Full Execution Logs
          </h3>
          <div className="space-y-2">
            {timeline.slice(-8).map((event, index) => (
              <div
                key={index}
                className="flex items-center justify-between py-2 border-b border-gray-200 last:border-0"
              >
                <div className="flex items-center gap-2">
                  <div
                    className={`w-2 h-2 rounded-full ${
                      event.type === 'error'
                        ? 'bg-red-500'
                        : event.type === 'warning'
                        ? 'bg-yellow-500'
                        : 'bg-green-500'
                    }`}
                  />
                  <span className="text-sm text-gray-700">{event.event}</span>
                </div>
                <span className="text-xs text-gray-500 font-mono">
                  +{typeof event.time === 'number' ? event.time.toFixed(2) : event.time}s
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
