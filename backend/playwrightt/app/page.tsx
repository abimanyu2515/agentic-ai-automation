'use client';

import { useState, useCallback, useEffect, Suspense } from 'react';
import Editor from '@monaco-editor/react';
import { Button } from '@/components/ui/button';
import { ExecutionList } from '@/components/execution-list';
import { Play, Code2, Save, Sun, Moon } from 'lucide-react';
import { Execution } from '@/lib/execution-queue';

const SAMPLE_SCRIPT = `// Adactin Hotel Application Test
// This test navigates to the Adactin Hotel app, logs in, searches for a hotel, selects and books the hotel.

// Define configuration constants
const BASE_URL = 'https://adactinhotelapp.com/';
const USERNAME = 'adactin_demo_user';
const PASSWORD = 'demo_password';

// Navigate to the Adactin Hotel Application
await page.goto(BASE_URL, { waitUntil: 'networkidle' });
addLog('Navigated to Adactin Hotel Portal');
addTimelineEvent('Website loaded', 'success');

// Take a screenshot of the initial state
await screenshot('01-initial-state');

// Perform Login
await page.fill('#username', USERNAME);
await page.fill('#password', PASSWORD);
await page.click('#login');
addLog('Filled credentials and clicked Login');
addTimelineEvent('Login submitted', 'success');

// Wait for search form
await page.waitForSelector('#location', { state: 'attached', timeout: 5000 });
await screenshot('02-login-success');

// Search Hotel: Select Sydney location
await page.selectOption('#location', 'Sydney');
addLog('Selected Sydney as Location');
addTimelineEvent('Location selected', 'success');

// Select Hotel Creek
await page.selectOption('#hotels', 'Hotel Creek');
addLog('Selected Hotel Creek');

// Select Standard Room type
await page.selectOption('#room_type', 'Standard');
addLog('Selected Standard Room Type');

// Select 1 Room
await page.selectOption('#room_nos', '1');
addLog('Selected 1 Room');

// Click Search
await page.click('#Submit');
addLog('Submitted Hotel Search');
addTimelineEvent('Search form submitted', 'success');

// Wait for Select Hotel page
await page.waitForSelector('#radiobutton_0', { state: 'attached', timeout: 5000 });
await screenshot('03-search-results');

// Select first hotel and continue
await page.click('#radiobutton_0');
await page.click('#continue');
addLog('Selected hotel and clicked Continue');
addTimelineEvent('Hotel selected', 'success');

// Wait for Book Hotel page
await page.waitForSelector('#first_name', { state: 'attached', timeout: 5000 });
await screenshot('04-book-hotel-page');

// Logout to clean up session
await page.goto('https://adactinhotelapp.com/Logout.php');
addLog('Session cleaned up via logout');
addTimelineEvent('Test completed successfully', 'success');
`;

function PlaywrightWorkspaceContent() {
  const getBackendUrl = () => {
    if (typeof window !== 'undefined') {
      let hostname = window.location.hostname;
      if (hostname === 'localhost') hostname = '127.0.0.1';
      return `http://${hostname}:8000`;
    }
    return 'http://127.0.0.1:8000';
  };

  const [script, setScript] = useState(SAMPLE_SCRIPT);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [queue, setQueue] = useState<string[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [projectId, setProjectId] = useState<string | null>(null);
  const [testCaseId, setTestCaseId] = useState<string | null>(null);
  const [testCaseTitle, setTestCaseTitle] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [isFrozen, setIsFrozen] = useState(false);
  const [scenario, setScenario] = useState<{ id: string; scenario_name: string; description: string } | null>(null);
  const [uploadTestData, setUploadTestData] = useState(false);
  const [testDataText, setTestDataText] = useState("");
  const [batchTestCases, setBatchTestCases] = useState<{ id: string; title: string; playwright_script: string; is_frozen: boolean }[]>([]);
  const [currentBatchIndex, setCurrentBatchIndex] = useState<number | null>(null);
  const [isBatchRunning, setIsBatchRunning] = useState(false);
  const [testCycleId, setTestCycleId] = useState<string | null>(null);

  const [theme, setTheme] = useState<'dark' | 'light'>('dark');

  useEffect(() => {
    // Default to dark theme to match portal
    document.documentElement.classList.add('dark');
  }, []);

  const toggleTheme = (e: React.MouseEvent<HTMLButtonElement>) => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    
    const changeTheme = () => {
      setTheme(nextTheme);
      if (typeof window !== 'undefined') {
        if (nextTheme === 'dark') {
          document.documentElement.classList.add('dark');
        } else {
          document.documentElement.classList.remove('dark');
        }
      }
    };

    if (!(document as any).startViewTransition) {
      changeTheme();
      return;
    }

    const rect = e.currentTarget.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const endRadius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );

    const transition = (document as any).startViewTransition(() => {
      changeTheme();
    });

    transition.ready.then(() => {
      document.documentElement.animate(
        {
          clipPath: [
            `circle(0px at ${x}px ${y}px)`,
            `circle(${endRadius}px at ${x}px ${y}px)`,
          ],
        },
        {
          duration: 400,
          easing: 'ease-out',
          pseudoElement: '::view-transition-new(root)',
        }
      );
    });
  };

  // Fetch executions
  const fetchExecutions = useCallback(async () => {
    try {
      const url = projectId 
        ? `/api/status?projectId=${projectId}`
        : '/api/status';
      const response = await fetch(url);
      if (!response.ok) {
        console.error('[Dashboard] Status response not ok:', response.status);
        return;
      }
      const text = await response.text();
      try {
        const data = JSON.parse(text);
        setExecutions(data.executions || []);
        setQueue(data.queue || []);
        setCurrent(data.current);
      } catch (jsonErr) {
        console.error('[Dashboard] JSON parse error:', jsonErr, 'Response:', text.substring(0, 100));
      }
    } catch (err) {
      console.error('[Dashboard] Fetch error:', err);
    }
  }, [projectId]);

  // Fetch workspace details from FastAPI on load
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      let pId = params.get('project_id');
      let tcId = params.get('test_case_id');
      let tcIdsParam = params.get('test_case_ids');
      let cycleId = params.get('test_cycle_id');
      
      // Fallback to localStorage if parameters are not present in URL
      if (!pId) {
        pId = localStorage.getItem('project_id');
      } else {
        localStorage.setItem('project_id', pId);
      }
      
      if (!tcId && !tcIdsParam) {
        tcId = localStorage.getItem('test_case_id');
      } else if (tcId) {
        localStorage.setItem('test_case_id', tcId);
      }

      if (cycleId) {
        localStorage.setItem('test_cycle_id', cycleId);
        setTestCycleId(cycleId);
      } else {
        const storedCycle = localStorage.getItem('test_cycle_id');
        if (storedCycle) {
          setTestCycleId(storedCycle);
        }
      }

      const initializeWorkspace = async () => {
        // Step 1: If project ID is still missing, fetch the first project from FastAPI
        if (!pId) {
          try {
            const projectsRes = await fetch(`${getBackendUrl()}/api/v1/projects`);
            if (projectsRes.ok) {
              const projects = await projectsRes.json();
              if (Array.isArray(projects) && projects.length > 0) {
                pId = projects[0].id;
                localStorage.setItem('project_id', pId!);
                setProjectId(pId);
              }
            }
          } catch (err) {
            console.error("Failed to default projects list:", err);
          }
        } else {
          setProjectId(pId);
        }

        // If batch test case IDs are provided
        if (pId && tcIdsParam) {
          try {
            const res = await fetch(`${getBackendUrl()}/api/v1/playwright/workspace?project_id=${pId}&test_case_ids=${tcIdsParam}`);
            if (!res.ok) throw new Error("Batch workspace details not found");
            const data = await res.json();
            
            if (data.test_cases && data.test_cases.length > 0) {
              setBatchTestCases(data.test_cases);
              const firstCase = data.test_cases[0];
              setScript(firstCase.playwright_script || SAMPLE_SCRIPT);
              setTestCaseId(firstCase.id);
              setTestCaseTitle(`Batch Case: ${firstCase.title}`);
              setIsFrozen(firstCase.is_frozen);
            }
          } catch (err) {
            console.error("Error loading batch workspace data:", err);
            setError("Failed to load batch context from the Enterprise Platform.");
          }
          return;
        }

        // Step 2: If we have a project ID but test case ID is still missing, fetch test cases for the project
        if (pId && !tcId) {
          try {
            const testcasesRes = await fetch(`${getBackendUrl()}/api/v1/projects/${pId}/testcases`);
            if (testcasesRes.ok) {
              const testcases = await testcasesRes.json();
              if (Array.isArray(testcases) && testcases.length > 0) {
                tcId = testcases[0].id;
                localStorage.setItem('test_case_id', tcId!);
                setTestCaseId(tcId);
              }
            }
          } catch (err) {
            console.error("Failed to default testcases list:", err);
          }
        } else {
          setTestCaseId(tcId);
        }

        // Step 3: Fetch workspace details if we have both IDs
        if (pId && tcId) {
          try {
            const res = await fetch(`${getBackendUrl()}/api/v1/playwright/workspace?project_id=${pId}&test_case_id=${tcId}`);
            if (!res.ok) throw new Error("Workspace details not found");
            const data = await res.json();
            
            if (data.playwright_script !== null && data.playwright_script !== undefined) {
              setScript(data.playwright_script);
            } else {
              setScript(SAMPLE_SCRIPT);
            }
            if (data.is_frozen !== undefined) {
              setIsFrozen(data.is_frozen);
            }
            if (data.scenario) {
              setScenario(data.scenario);
            }
            setTestCaseTitle(`Test Case: ${tcId.substring(0, 8)}`);
          } catch (err) {
            console.error("Error loading workspace data from FastAPI:", err);
            setError("Failed to load script context from the Enterprise Platform.");
          }
        }
      };

      initializeWorkspace();
    }
  }, []);

  // Load and poll executions history on component mount
  useEffect(() => {
    fetchExecutions();
    const poll = setInterval(fetchExecutions, 2000);
    return () => clearInterval(poll);
  }, [fetchExecutions]);

  const handleFreeze = async () => {
    if (!testCaseId) return;
    try {
      const response = await fetch(`${getBackendUrl()}/api/v1/playwright/freeze`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          test_case_id: testCaseId,
          is_frozen: true
        })
      });
      if (!response.ok) throw new Error("Failed to freeze script");
      setIsFrozen(true);
    } catch (err: any) {
      setError(`Freeze failed: ${err.message}`);
    }
  };

  // Save script back to FastAPI
  const handleSave = async () => {
    if (isFrozen) return;
    if (!projectId || !testCaseId) return;
    setIsSaving(true);
    setSaveSuccess(false);
    setError(null);
    try {
      const response = await fetch(`${getBackendUrl()}/api/v1/playwright/script`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          test_case_id: testCaseId,
          script
        })
      });
      if (!response.ok) {
        throw new Error("Failed to save script back to repository");
      }
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
    } catch (err: any) {
      setError(`Save failed: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const runBatchSequentially = async () => {
    if (batchTestCases.length === 0) return;
    setIsBatchRunning(true);
    setCurrentBatchIndex(0);
    setError(null);

    const activeTestCycleId = testCycleId || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });

    for (let i = 0; i < batchTestCases.length; i++) {
      setCurrentBatchIndex(i);
      
      const activeTc = batchTestCases[i];
      setScript(activeTc.playwright_script || SAMPLE_SCRIPT);
      setTestCaseId(activeTc.id);
      setTestCaseTitle(`Batch Case [${i + 1}/${batchTestCases.length}]: ${activeTc.title}`);
      setIsFrozen(activeTc.is_frozen);

      // Execute this test case using our existing /api/execute flow
      await new Promise<void>(async (resolve) => {
        try {
          const response = await fetch('/api/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              script: activeTc.playwright_script || SAMPLE_SCRIPT,
              projectId: projectId || 'demo-project',
              testCaseIds: [activeTc.id],
              browser: 'chromium',
              testData: uploadTestData ? testDataText : null,
              testCycleId: activeTestCycleId
            }),
          });

          if (!response.ok) {
            const data = await response.json();
            console.error('Execution failed start:', data.error);
            resolve();
            return;
          }

          const data = await response.json();
          const execId = data.executionId;

          // Poll status of this specific run until completed/failed/stopped
          let notFoundCount = 0;
          const interval = setInterval(async () => {
            try {
              const statusRes = await fetch(`/api/status?id=${execId}`);
              if (statusRes.ok) {
                notFoundCount = 0; // reset
                const statusData = await statusRes.json();
                const status = statusData.metadata?.status;
                if (status === 'completed' || status === 'failed' || status === 'stopped' || status === 'passed') {
                  clearInterval(interval);
                  resolve();
                }
              } else {
                notFoundCount++;
                if (notFoundCount > 45) { // 45 seconds of consecutive errors/404s
                  console.error(`[Batch Poller] Timeout waiting for execution ${execId}. Resolving.`);
                  clearInterval(interval);
                  resolve();
                }
              }
            } catch (err) {
              notFoundCount++;
              if (notFoundCount > 45) {
                clearInterval(interval);
                resolve();
              }
            }
          }, 1000);
        } catch (err) {
          console.error('Batch run step failed:', err);
          resolve();
        }
      });
    }

    setIsBatchRunning(false);
    setCurrentBatchIndex(null);
    setTestCaseTitle(`Batch Execution Completed (${batchTestCases.length} runs)`);
    await fetchExecutions();
  };

  // Handle script execution
  const handleExecute = async (overrideScript?: any) => {
    if (batchTestCases.length > 0) {
      await runBatchSequentially();
      return;
    }
    const isOverride = typeof overrideScript === 'string';
    const scriptToRun = isOverride ? overrideScript : script;
    setIsLoading(true);
    setError(null);

    try {
      // Auto-save script back to FastAPI before execution to keep it authoritative (if not frozen)
      if (projectId && testCaseId && !isFrozen && !isOverride) {
        await handleSave();
      }

      const response = await fetch('/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          script: scriptToRun,
          projectId: projectId || 'demo-project',
          testCaseIds: testCaseId ? [testCaseId] : [],
          browser: 'chromium',
          testData: uploadTestData ? testDataText : null,
          testCycleId: testCycleId
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || 'Failed to execute script');
      }

      const data = await response.json();

      if (data.error) {
        setError(data.error);
      }

      await fetchExecutions();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  // Handle status change
  const handleStatusChange = async (executionId: string, action: string) => {
    try {
      const response = await fetch(`/api/status?id=${executionId}&action=${action}`, {
        method: 'PUT',
      });

      if (!response.ok) {
        throw new Error('Failed to update execution');
      }

      await fetchExecutions();
    } catch (err) {
      console.error('[Dashboard] Status change error:', err);
    }
  };

  // Refresh on mount
  useEffect(() => {
    fetchExecutions();
    const interval = setInterval(fetchExecutions, 2000);
    return () => clearInterval(interval);
  }, [fetchExecutions]);

  return (
    <div className="min-h-screen bg-background text-foreground transition-colors duration-300">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8 flex justify-between items-start">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Code2 className="w-8 h-8 text-blue-600" />
              <h1 className="text-4xl font-bold text-foreground">Playwright Workspace</h1>
            </div>
            <p className="text-muted-foreground">
              {testCaseTitle 
                ? `${testCaseTitle} — Persisted to the Enterprise Platform`
                : 'Author, verify, and run browser automation scripts'}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button
              onClick={toggleTheme}
              variant="outline"
              size="icon"
              className="border-border text-foreground hover:bg-muted rounded-full w-10 h-10 flex items-center justify-center cursor-pointer"
              title="Toggle Theme"
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </Button>
            {testCaseId && (
              <Button
                onClick={handleSave}
                disabled={isSaving || isLoading}
                variant="outline"
                className="border-border text-foreground hover:bg-muted"
              >
                <Save className="w-4 h-4 mr-2" />
                {isSaving ? 'Saving...' : 'Save Changes'}
              </Button>
            )}
          </div>
        </div>



        {/* Batch Queue View */}
        {batchTestCases.length > 0 && (
          <div className="mb-6 bg-card border border-border rounded-lg p-5 shadow-lg">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
              <div>
                <h3 className="font-semibold text-xl text-foreground flex items-center gap-2">
                  📦 Batch Queue ({batchTestCases.length} Test Cases)
                </h3>
                <p className="text-muted-foreground text-sm">
                  Executing test cases sequentially within the Playwright Workspace using a shared browser session.
                </p>
              </div>
              <Button
                onClick={runBatchSequentially}
                disabled={isBatchRunning}
                className="bg-green-600 hover:bg-green-700 text-white font-semibold cursor-pointer px-6 py-2 rounded-lg"
              >
                {isBatchRunning ? 'Running Batch Sequential Queue...' : 'Run Selected Batch'}
              </Button>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {batchTestCases.map((tc, idx) => {
                const isActive = currentBatchIndex === idx;
                const isCompleted = currentBatchIndex !== null && idx < currentBatchIndex;
                const isPending = currentBatchIndex === null || idx > currentBatchIndex;
                
                return (
                  <div
                    key={tc.id}
                    className={`p-4 rounded-xl border text-sm flex items-center justify-between cursor-pointer transition-all duration-200 ${
                      isActive
                        ? 'border-blue-500 bg-blue-500/10 shadow-md ring-1 ring-blue-500/30'
                        : isCompleted
                        ? 'border-green-500/30 bg-green-500/5 hover:bg-green-500/10'
                        : 'border-border bg-muted/20 hover:bg-muted/40 hover:border-muted-foreground/30'
                    }`}
                    onClick={() => {
                      if (!isBatchRunning) {
                        setScript(tc.playwright_script || SAMPLE_SCRIPT);
                        setTestCaseId(tc.id);
                        setTestCaseTitle(`Batch Case [${idx+1}/${batchTestCases.length}]: ${tc.title}`);
                        setIsFrozen(tc.is_frozen);
                      }
                    }}
                  >
                    <div className="truncate pr-2">
                      <p className="font-semibold truncate text-foreground">{tc.title}</p>
                      <p className="text-xs text-muted-foreground truncate font-mono mt-0.5">{tc.id.substring(0, 8)}</p>
                    </div>
                    <div>
                      {isActive && <span className="text-xs bg-blue-500/20 text-blue-400 font-semibold px-2.5 py-0.5 rounded-full animate-pulse border border-blue-500/30">Running</span>}
                      {isCompleted && <span className="text-xs bg-green-500/20 text-green-400 font-semibold px-2.5 py-0.5 rounded-full border border-green-500/30">Passed</span>}
                      {isPending && <span className="text-xs bg-muted text-muted-foreground font-semibold px-2.5 py-0.5 rounded-full border border-border">Pending</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left Panel - Script Editor */}
          <div className="lg:col-span-1 space-y-4">
            <div className="bg-card rounded-lg border border-border p-4 shadow-sm">
              <h2 className="text-lg font-semibold mb-3 flex items-center justify-between text-foreground">
                <span className="flex items-center gap-2">
                  <Code2 className="w-5 h-5" />
                  Monaco Editor
                </span>
                {isFrozen && <span className="text-xs bg-red-500/10 border border-red-500/30 text-red-400 px-2 py-0.5 rounded flex items-center gap-1 font-normal">🔒 Locked</span>}
                {!isFrozen && testCaseId && (
                  <Button
                    onClick={handleFreeze}
                    variant="outline"
                    className="h-7 text-xs border-red-500/30 text-red-400 hover:bg-red-500/10"
                  >
                    Freeze Code
                  </Button>
                )}
              </h2>

              {scenario && (
                <div className="p-3 bg-muted/30 border border-border rounded text-sm mb-3">
                  <div className="font-semibold text-foreground mb-1 flex items-center gap-1.5">
                    <span className="text-xs text-muted-foreground uppercase font-normal">Scenario:</span>
                    {scenario.scenario_name}
                  </div>
                  <p className="text-muted-foreground text-xs">{scenario.description}</p>
                </div>
              )}
              
              <div className="border border-border rounded overflow-hidden">
                <Editor
                  height="400px"
                  defaultLanguage="typescript"
                  value={script}
                  onChange={(val) => setScript(val || '')}
                  theme={theme === 'dark' ? 'vs-dark' : 'vs'}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: 'on',
                    automaticLayout: true,
                    tabSize: 2,
                    readOnly: false
                  }}
                />
              </div>

              <div className="mt-4 border-t border-border pt-3">
                <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={uploadTestData}
                    onChange={(e) => setUploadTestData(e.target.checked)}
                    className="rounded border-border bg-card text-blue-600 focus:ring-blue-500 cursor-pointer"
                  />
                  <span>Are you willing to upload test data?</span>
                </label>
                
                {uploadTestData && (
                  <div className="mt-2 space-y-2">
                    <div className="flex items-center gap-2">
                      <input
                        type="file"
                        accept=".json,.csv,.txt"
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) {
                            const reader = new FileReader();
                            reader.onload = (evt) => {
                              setTestDataText(evt.target?.result as string || "");
                            };
                            reader.readAsText(file);
                          }
                        }}
                        className="text-xs text-muted-foreground file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:font-semibold file:bg-blue-600/10 file:text-blue-400 hover:file:bg-blue-600/20 cursor-pointer"
                      />
                    </div>
                    <textarea
                      placeholder="Paste or upload test data (JSON, CSV or plain text) here before execution..."
                      value={testDataText}
                      onChange={(e) => setTestDataText(e.target.value)}
                      className="w-full h-24 p-2 bg-muted/30 border border-border rounded text-xs text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                )}
              </div>

              <div className="flex gap-2 mt-4">
                <Button
                  onClick={handleExecute}
                  disabled={isLoading || isBatchRunning || !script.trim()}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white cursor-pointer"
                >
                  <Play className="w-4 h-4 mr-2" />
                  {isLoading || isBatchRunning ? 'Running...' : batchTestCases.length > 0 ? 'Run Selected Batch' : 'Execute Script'}
                </Button>
              </div>

              {saveSuccess && (
                <div className="mt-4 p-2 bg-green-500/10 border border-green-500/30 rounded text-green-400 text-sm text-center">
                  Changes successfully saved to Enterprise Platform!
                </div>
              )}

              {error && (
                <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded text-red-400 text-sm">
                  {error}
                </div>
              )}
            </div>

            {/* Status Summary */}
            <div className="bg-card rounded-lg border border-border p-4 shadow-sm">
              <h3 className="font-semibold mb-3 text-foreground">Status</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total Executions</span>
                  <span className="font-mono text-foreground">{executions.length}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Queued</span>
                  <span className="font-mono text-purple-600">{queue.length}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Running</span>
                  <span className="font-mono text-blue-600">
                    {current ? '1' : '0'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Completed</span>
                  <span className="font-mono text-green-600">
                    {executions.filter((e) => e?.metadata?.status === 'completed')
                      .length}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Right Panel - Execution List */}
          <div className="lg:col-span-2">
            <div className="bg-card rounded-lg border border-border p-4 shadow-sm">
              <h2 className="text-lg font-semibold mb-4 text-foreground">Recent Executions</h2>
              <ExecutionList
                executions={executions}
                queue={queue}
                current={current}
                onStatusChange={handleStatusChange}
                onRerun={(rerunScript) => {
                  setScript(rerunScript);
                  handleExecute(rerunScript);
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div className="p-8 text-center">Loading Playwright Workspace...</div>}>
      <PlaywrightWorkspaceContent />
    </Suspense>
  );
}
