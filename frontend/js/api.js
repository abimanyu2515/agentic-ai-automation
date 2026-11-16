const API_BASE = '/api/v1';
const TEST_CASE_GENERATION_TIMEOUT_MS = 20 * 60 * 1000;
const SCRIPT_GENERATION_POLL_MS = 3000;
const SCRIPT_GENERATION_MAX_WAIT_MS = 60 * 60 * 1000;

let isRefreshing = false;
let refreshSubscribers = [];

function subscribeTokenRefresh(cb) {
  refreshSubscribers.push(cb);
}

function onRefreshed(token) {
  refreshSubscribers.forEach(cb => cb(token));
  refreshSubscribers = [];
}

function handleAuthFailure() {
  sessionStorage.clear();
  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.style.display = 'flex';
  const sidebar = document.getElementById('sidebar-container');
  if (sidebar) sidebar.style.display = 'none';
  const mainNav = document.getElementById('project-select-wrapper');
  if (mainNav) mainNav.style.display = 'none';
  
  // Hide main view panels to lock down the interface
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.remove('active');
  });
}

async function request(url, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };

  const token = sessionStorage.getItem('access_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // If body is FormData, do not set Content-Type header manually
  if (options.body instanceof FormData) {
    delete headers['Content-Type'];
  }

  try {
    const response = await fetch(`${API_BASE}${url}`, {
      ...options,
      headers
    });

    if (response.status === 401 && !url.includes('/auth/login') && !url.includes('/auth/refresh')) {
      const refreshToken = sessionStorage.getItem('refresh_token');
      if (!refreshToken) {
        handleAuthFailure();
        throw new Error("Session expired. Please sign in again.");
      }

      if (!isRefreshing) {
        isRefreshing = true;
        try {
          const refreshResponse = await fetch(`${API_BASE}/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken })
          });

          if (!refreshResponse.ok) {
            throw new Error("Refresh token invalid or expired");
          }

          const data = await refreshResponse.json();
          sessionStorage.setItem('access_token', data.access_token);
          isRefreshing = false;
          // Let the failed request subscribe before notifying the queue.
          setTimeout(() => onRefreshed(data.access_token), 0);
        } catch (err) {
          isRefreshing = false;
          handleAuthFailure();
          setTimeout(() => onRefreshed(null), 0);
          throw new Error("Session expired. Please sign in again.");
        }
      }

      return new Promise((resolve) => {
        subscribeTokenRefresh((newToken) => {
          if (!newToken) {
            resolve(Promise.reject(new Error("Session expired. Please sign in again.")));
            return;
          }
          headers['Authorization'] = `Bearer ${newToken}`;
          resolve(
            fetch(`${API_BASE}${url}`, {
              ...options,
              headers
            }).then(res => res.status === 204 ? null : (options.responseType === 'text' ? res.text() : res.json()))
          );
        });
      });
    }

    if (!response.ok) {
      let errorMsg = `HTTP error! Status: ${response.status}`;
      let detail = null;
      try {
        const errJson = await response.json();
        if (errJson && errJson.detail) {
          detail = errJson.detail;
          errorMsg = typeof detail === 'string' ? detail : (detail.message || errorMsg);
        }
      } catch (e) {}
      const error = new Error(errorMsg);
      error.status = response.status;
      error.detail = detail;
      throw error;
    }

    if (response.status === 204) {
      return null;
    }

    // responseType: 'text' returns the raw body (CSV exports).
    return options.responseType === 'text' ? response.text() : response.json();
  } catch (error) {
    if (error && (error.name === 'AbortError' || error.name === 'TimeoutError')) {
      throw new Error("The request timed out while the backend was processing it. Check the backend logs before retrying, as the operation may still be running.");
    }
    if (error.message.includes("Session expired") || error.message.includes("Not authenticated")) {
      handleAuthFailure();
    }
    throw error;
  }
}

// Exchange the refresh token for a new access token, sharing one refresh
// with any concurrent `request` calls. Resolves to the new token or null.
function refreshAccessToken() {
  const refreshToken = sessionStorage.getItem('refresh_token');
  if (!refreshToken) return Promise.resolve(null);
  const waiter = new Promise(resolve => subscribeTokenRefresh(resolve));
  if (!isRefreshing) {
    isRefreshing = true;
    fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken })
    })
      .then(res => (res.ok ? res.json() : Promise.reject(new Error('Refresh failed'))))
      .then(data => {
        sessionStorage.setItem('access_token', data.access_token);
        isRefreshing = false;
        onRefreshed(data.access_token);
      })
      .catch(() => {
        isRefreshing = false;
        onRefreshed(null);
      });
  }
  return waiter;
}

async function errorFromResponse(response) {
  let message = `HTTP error! Status: ${response.status}`;
  let detail = null;
  try {
    const body = await response.json();
    if (body && body.detail) {
      detail = body.detail;
      message = typeof detail === 'string' ? detail : (detail.message || message);
    }
  } catch (e) {}
  const error = new Error(message);
  error.status = response.status;
  error.detail = detail;
  return error;
}

// POST that answers with server sent events (spec 0005). Uses the same token
// as `request` and refreshes it once on a 401 before the stream starts.
// Calls onEvent(name, data) per event; resolves when the stream ends.
async function streamRequest(path, body, onEvent, signal) {
  const send = token => fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body),
    signal
  });

  let response = await send(sessionStorage.getItem('access_token'));
  if (response.status === 401) {
    const token = await refreshAccessToken();
    if (!token) {
      handleAuthFailure();
      throw new Error('Session expired. Please sign in again.');
    }
    response = await send(token);
  }
  if (!response.ok) throw await errorFromResponse(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const dispatch = block => {
    let name = 'message';
    const data = [];
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // keep alive comment
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return;
    let parsed = null;
    try { parsed = JSON.parse(data.join('\n')); } catch (e) { return; }
    onEvent(name, parsed);
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      dispatch(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}

export const API = {
  // Chat assistant (spec 0005)
  async sendChatMessage(projectId, body, onEvent, signal) {
    return streamRequest(`/projects/${projectId}/chat/messages`, body, onEvent, signal);
  },

  async sendChatHelp(body, onEvent, signal) {
    return streamRequest('/chat/help', body, onEvent, signal);
  },

  async retryChatMessage(projectId, threadId, onEvent, signal) {
    return streamRequest(`/projects/${projectId}/chat/threads/${threadId}/retry`, {}, onEvent, signal);
  },

  async getChatThreads(projectId, cursor = null) {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    return request(`/projects/${projectId}/chat/threads${query}`);
  },

  async getChatMessages(projectId, threadId, beforeSeq = null) {
    const query = beforeSeq ? `?before_seq=${beforeSeq}` : '';
    return request(`/projects/${projectId}/chat/threads/${threadId}/messages${query}`);
  },

  async deleteChatThread(projectId, threadId) {
    return request(`/projects/${projectId}/chat/threads/${threadId}`, { method: 'DELETE' });
  },

  // Only confirm_text is sent: the server runs the params saved with the proposal.
  async confirmChatAction(projectId, messageId, confirmText = null) {
    return request(`/projects/${projectId}/chat/messages/${messageId}/action/confirm`, {
      method: 'POST',
      body: JSON.stringify({ confirm_text: confirmText })
    });
  },

  async cancelChatAction(projectId, messageId) {
    return request(`/projects/${projectId}/chat/messages/${messageId}/action/cancel`, { method: 'POST' });
  },

  async getChatAction(projectId, messageId) {
    return request(`/projects/${projectId}/chat/messages/${messageId}/action`);
  },

  // Projects
  async getProjects() {
    return request('/projects');
  },

  async getProject(id) {
    return request(`/projects/${id}`);
  },

  async createProject(name, description, lineOfBusiness, framework, jiraProjectKey = null, targetUrl) {
    return request('/projects', {
      method: 'POST',
      body: JSON.stringify({
        name,
        description,
        line_of_business: lineOfBusiness,
        framework,
        jira_project_key: jiraProjectKey,
        target_url: targetUrl
      })
    });
  },

  async updateProject(id, name, description, lineOfBusiness, framework = 'playwright', jiraProjectKey = null, targetUrl = null, targetUsername = null, targetPassword = null, isSafeTestEnvironment = undefined) {
    const body = {
      name,
      description,
      line_of_business: lineOfBusiness,
      framework,
      jira_project_key: jiraProjectKey,
      target_url: targetUrl,
      target_username: targetUsername,
      target_password: targetPassword
    };
    if (typeof isSafeTestEnvironment === 'boolean') {
      body.is_safe_test_environment = isSafeTestEnvironment;
    }
    return request(`/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body)
    });
  },

  async deleteProject(id) {
    return request(`/projects/${id}`, {
      method: 'DELETE'
    });
  },

  // External Tools
  async getExternalTools() {
    return request('/external-tools');
  },

  async saveExternalTool(toolConfig) {
    return request('/external-tools', {
      method: 'POST',
      body: JSON.stringify(toolConfig)
    });
  },

  async deleteExternalTool(toolId) {
    return request(`/external-tools/${toolId}`, {
      method: 'DELETE'
    });
  },

  async syncRequirementJira(projectId, requirementId) {
    return request(`/projects/${projectId}/requirements/${requirementId}/sync-jira`, {
      method: 'POST'
    });
  },

  async syncExecutionBug(projectId, executionId) {
    return request(`/projects/${projectId}/executions/${executionId}/sync-bug`, {
      method: 'POST'
    });
  },

  // Requirements
  async getRequirements(projectId) {
    return request(`/projects/${projectId}/requirements`);
  },

  async createRequirement(projectId, title, description, priority, businessDomain, releaseId = null) {
    return request(`/projects/${projectId}/requirements`, {
      method: 'POST',
      body: JSON.stringify({
        title,
        description,
        priority,
        business_domain: businessDomain,
        release_id: releaseId
      })
    });
  },

  // Import Requirements File
  async importRequirements(projectId, fileObj, releaseId = null) {
    const formData = new FormData();
    formData.append('file', fileObj);
    const url = `/projects/${projectId}/requirements/import` + (releaseId ? `?release_id=${releaseId}` : '');
    return request(url, {
      method: 'POST',
      body: formData
    });
  },

  // Import Requirement from JIRA User Story
  async importJiraStory(projectId, issueKey, releaseId = null) {
    const url = `/projects/${projectId}/requirements/import-jira?issue_key=${encodeURIComponent(issueKey)}` + (releaseId ? `&release_id=${releaseId}` : '');
    return request(url, {
      method: 'POST'
    });
  },

  // Scenarios
  async getScenarios(projectId) {
    return request(`/projects/${projectId}/scenarios`);
  },

  async generateScenarios(projectId, requirementId, count, mode = 'append') {
    return request(`/projects/${projectId}/requirements/${requirementId}/generate-scenarios`, {
      method: 'POST',
      body: JSON.stringify({ count, mode }),
      // Allow the scenario agent's single five-minute model request to finish.
      signal: AbortSignal.timeout(360000)
    });
  },

  async updateScenario(projectId, scenarioId, data) {
    return request(`/projects/${projectId}/scenarios/${scenarioId}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    });
  },

  async deleteScenario(projectId, scenarioId) {
    return request(`/projects/${projectId}/scenarios/${scenarioId}`, {
      method: 'DELETE'
    });
  },

  async duplicateScenario(projectId, scenarioId) {
    return request(`/projects/${projectId}/scenarios/duplicate/${scenarioId}`, {
      method: 'POST'
    });
  },

  // Scenario Notes
  async getScenarioNotes(scenarioId) {
    return request(`/scenarios/${scenarioId}/notes`);
  },

  async addScenarioNote(scenarioId, note) {
    return request(`/scenarios/${scenarioId}/notes`, {
      method: 'POST',
      body: JSON.stringify({ note })
    });
  },

  // Test Cases
  async getTestCases(projectId) {
    return request(`/projects/${projectId}/testcases`);
  },

  async validateProjectScripts(projectId, testCaseIds) {
    return request(`/projects/${projectId}/testcases/validate-scripts`, {
      method: 'POST',
      body: JSON.stringify({
        test_case_ids: testCaseIds,
        confirmed_safe_test_run: true
      })
    });
  },

  async getGoldenDatasetComparison(projectId) {
    return request(`/projects/${projectId}/testcases/golden-dataset-comparison`);
  },

  async generateTestCases(projectId, requirementId) {
    return request(`/projects/${projectId}/requirements/${requirementId}/generate-testcases`, {
      method: 'POST',
      signal: AbortSignal.timeout(TEST_CASE_GENERATION_TIMEOUT_MS)
    });
  },

  async generateTestDataTests(projectId, requirementId) {
    return request(`/projects/${projectId}/requirements/${requirementId}/generate-testdata-tests`, {
      method: 'POST',
      signal: AbortSignal.timeout(300000)
    });
  },

  async generateTestDataCases(projectId, requirementId) {
    return request(`/projects/${projectId}/requirements/${requirementId}/generate-testdata-tests?generate_scripts=false`, {
      method: 'POST',
      signal: AbortSignal.timeout(300000)
    });
  },

  async generateTestDataForTestCase(projectId, requirementId, testCaseId) {
    return request(
      `/projects/${projectId}/requirements/${requirementId}/testcases/${testCaseId}/generate-testdata`,
      {
        method: 'POST',
        signal: AbortSignal.timeout(300000)
      }
    );
  },

  async generateTestDataScriptForTestCase(projectId, requirementId, testCaseId) {
    return request(
      `/projects/${projectId}/requirements/${requirementId}/testcases/${testCaseId}/generate-testdata-script`,
      {
        method: 'POST',
        signal: AbortSignal.timeout(300000)
      }
    );
  },

  async updateTestCase(projectId, testCaseId, data) {
    return request(`/projects/${projectId}/testcases/${testCaseId}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    });
  },

  async deleteTestCase(projectId, testCaseId) {
    return request(`/projects/${projectId}/testcases/${testCaseId}`, {
      method: 'DELETE'
    });
  },

  async deleteExecution(projectId, executionId) {
    return request(`/projects/${projectId}/executions/${executionId}`, {
      method: 'DELETE'
    });
  },

  async deleteFailedExecutions(projectId) {
    return request(`/projects/${projectId}/executions/failed`, {
      method: 'DELETE'
    });
  },

  // Test Case Notes
  async getTestCaseNotes(testCaseId) {
    return request(`/testcases/${testCaseId}/notes`);
  },

  async addTestCaseNote(testCaseId, note) {
    return request(`/testcases/${testCaseId}/notes`, {
      method: 'POST',
      body: JSON.stringify({ note })
    });
  },

  // Playwright Code Generation
  async generatePlaywrightScript(projectId, testCaseId, framework) {
    // Generation runs the steps live with many LLM calls and can outlast any
    // HTTP timeout, so the backend runs it as a job and this polls until it
    // finishes. Resolves with the updated test case, as the old call did.
    const options = { method: 'POST' };
    if (framework) {
      options.body = JSON.stringify({ framework });
    }
    let job = await request(
      `/projects/${projectId}/testcases/${testCaseId}/generate-script/jobs`,
      options
    );
    const deadline = Date.now() + SCRIPT_GENERATION_MAX_WAIT_MS;
    let pollFailures = 0;
    while (job.status === 'running') {
      if (Date.now() > deadline) {
        throw new Error("Script generation is still running on the backend after 60 minutes. Check the backend logs; the script is saved to the test case when it finishes.");
      }
      await new Promise(resolve => setTimeout(resolve, SCRIPT_GENERATION_POLL_MS));
      try {
        job = await request(`/projects/${projectId}/script-generation-jobs/${job.job_id}`);
        pollFailures = 0;
      } catch (error) {
        // A lost job (backend restart) or expired session will not recover.
        if (error.status === 404 || /Session expired/.test(error.message)) throw error;
        // Ride out a brief network or server hiccup.
        pollFailures += 1;
        if (pollFailures >= 5) throw error;
      }
    }
    if (job.status === 'failed') {
      const error = new Error(job.error || 'Script generation failed.');
      error.status = job.error_status;
      throw error;
    }
    return job.test_case;
  },

  async saveTestCaseScript(projectId, testCaseId, script) {
    return request(`/projects/${projectId}/testcases/${testCaseId}/script`, {
      method: 'PUT',
      body: JSON.stringify({ script })
    });
  },

  // Playwright Execution Run
  async executeTestCase(projectId, testCaseId) {
    return request(`/projects/${projectId}/testcases/${testCaseId}/execute`, {
      method: 'POST'
    });
  },

  async executeBatch(projectId, testCaseIds) {
    return request(`/projects/${projectId}/batches/execute`, {
      method: 'POST',
      body: JSON.stringify(testCaseIds)
    });
  },

  // Execution Results history
  async getExecutionResults(projectId) {
    return request(`/projects/${projectId}/executions`);
  },

  async getExecutionDetail(projectId, executionId) {
    return request(`/projects/${projectId}/executions/${executionId}`);
  },

  // Knowledge Base documents
  async getDocuments(projectId) {
    return request(`/projects/${projectId}/documents`);
  },

  async createDocument(projectId, docData) {
    return request(`/projects/${projectId}/documents`, {
      method: 'POST',
      body: JSON.stringify(docData)
    });
  },

  async deleteDocument(projectId, documentId) {
    return request(`/projects/${projectId}/documents/${documentId}`, {
      method: 'DELETE'
    });
  },

  // Settings
  async getSettings() {
    return request('/settings');
  },

  async updateSettings(settings) {
    return request('/settings', {
      method: 'PUT',
      body: JSON.stringify(settings)
    });
  },

  // LangSmith Trace Logs
  async getTraces(projectId) {
    return request(`/projects/${projectId}/traces`);
  },

  // Auth & RBAC Administration
  async login(email, password) {
    return request('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password })
    });
  },

  async logout(refreshToken) {
    return request('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken })
    });
  },

  async getUsers() {
    return request('/admin/users');
  },

  async createUser(fullname, email, password, role) {
    return request('/admin/users', {
      method: 'POST',
      body: JSON.stringify({ full_name: fullname, email, password, role })
    });
  },

  async toggleUserStatus(userId, isActive) {
    return request(`/admin/users/${userId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ is_active: isActive })
    });
  },

  async resetUserPassword(userId, password) {
    return request(`/admin/users/${userId}/password`, {
      method: 'PUT',
      body: JSON.stringify({ password })
    });
  },

  async getRoles() {
    return request('/admin/roles');
  },

  async createRole(name) {
    return request('/admin/roles', {
      method: 'POST',
      body: JSON.stringify({ name })
    });
  },

  async getPermissions() {
    return request('/admin/permissions');
  },

  async updatePermissions(roleId, permissionIds) {
    return request('/admin/permissions', {
      method: 'POST',
      body: JSON.stringify({ role_id: roleId, permission_ids: permissionIds })
    });
  },

  async assignProjectUser(projectId, userId, roleId) {
    return request(`/projects/${projectId}/users`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, role_id: roleId })
    });
  },

  async getProjectUsers(projectId) {
    return request(`/projects/${projectId}/users`);
  },

  async getReleases(projectId) {
    return request(`/projects/${projectId}/releases`);
  },

  async createRelease(projectId, name, description, status = "Active", startDate = null, endDate = null) {
    return request(`/projects/${projectId}/releases`, {
      method: 'POST',
      body: JSON.stringify({ name, description, status, start_date: startDate, end_date: endDate })
    });
  },

  async getTestCycles(releaseId) {
    return request(`/releases/${releaseId}/cycles`);
  },

  async createTestCycle(releaseId, name, description, status = "Active") {
    return request(`/releases/${releaseId}/cycles`, {
      method: 'POST',
      body: JSON.stringify({ name, description, status })
    });
  },

  async getProjectCycles(projectId) {
    return request(`/projects/${projectId}/cycles`);
  },

  async getAuditLogs() {
    return request('/admin/audit-logs');
  },

  // Object repository (spec 0004)
  async getObjectRepositoryPermissions(projectId) {
    return request(`/projects/${projectId}/object-repository/permissions`);
  },

  async getObjectPages(projectId, limit = 200, offset = 0) {
    return request(`/projects/${projectId}/object-repository/pages?limit=${limit}&offset=${offset}`);
  },

  async createObjectPage(projectId, payload) {
    return request(`/projects/${projectId}/object-repository/pages`, {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async updateObjectPage(projectId, pageId, payload) {
    return request(`/projects/${projectId}/object-repository/pages/${pageId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload)
    });
  },

  async deleteObjectPage(projectId, pageId) {
    return request(`/projects/${projectId}/object-repository/pages/${pageId}`, { method: 'DELETE' });
  },

  async getObjectElements(projectId, pageId, health = '', limit = 500, offset = 0) {
    const filter = health ? `&health=${encodeURIComponent(health)}` : '';
    return request(`/projects/${projectId}/object-repository/pages/${pageId}/elements?limit=${limit}&offset=${offset}${filter}`);
  },

  async createObjectElement(projectId, pageId, payload) {
    return request(`/projects/${projectId}/object-repository/pages/${pageId}/elements`, {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  },

  async updateObjectElement(projectId, elementId, payload) {
    return request(`/projects/${projectId}/object-repository/elements/${elementId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload)
    });
  },

  async deleteObjectElement(projectId, elementId) {
    return request(`/projects/${projectId}/object-repository/elements/${elementId}`, { method: 'DELETE' });
  },

  async startObjectScan(projectId) {
    return request(`/projects/${projectId}/object-repository/scan`, { method: 'POST' });
  },

  async getObjectScan(projectId, scanId) {
    return request(`/projects/${projectId}/object-repository/scan/${scanId}`);
  },

  // ── Token Utilization ───────────────────────────────────────────────
  // filters: { from, to, project_id, agent, model, role, provider, status, operation }

  async getTokenUsageSummary(filters = {}) {
    return request(`/token-usage/summary${tokenUsageQuery(filters)}`);
  },

  async getTokenUsageTimeseries(filters = {}, bucket = 'day', splitBy = '') {
    return request(`/token-usage/timeseries${tokenUsageQuery(filters, { bucket, split_by: splitBy })}`);
  },

  async getTokenUsageBreakdown(filters = {}, groupBy = 'agent') {
    return request(`/token-usage/breakdown${tokenUsageQuery(filters, { group_by: groupBy })}`);
  },

  async getTokenUsageCalls(filters = {}, page = 1, pageSize = 25, sort = '-created_at') {
    return request(`/token-usage/calls${tokenUsageQuery(filters, { page, page_size: pageSize, sort })}`);
  },

  async getTokenUsageFacets(filters = {}) {
    return request(`/token-usage/facets${tokenUsageQuery(filters)}`);
  },

  async exportTokenUsageCsv(filters = {}) {
    return request(`/token-usage/export.csv${tokenUsageQuery(filters)}`, { responseType: 'text' });
  },

  async getTokenUsagePricing() {
    return request('/token-usage/pricing');
  },

  async getTokenUsageSettings() {
    return request('/token-usage/settings');
  },

  async updateTokenUsageSettings(payload) {
    return request('/token-usage/settings', { method: 'PUT', body: JSON.stringify(payload) });
  }
};

function tokenUsageQuery(filters = {}, extra = {}) {
  const params = new URLSearchParams();
  Object.entries({ ...filters, ...extra }).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, value);
  });
  const query = params.toString();
  return query ? `?${query}` : '';
}
export { API_BASE };
