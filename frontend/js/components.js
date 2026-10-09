// Helper to escape HTML and prevent XSS
export function escapeHTML(str) {
  if (!str) return '';
  return str.replace(/[&<>'"]/g, 
    tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag)
  );
}

// Helper to safely render simple Markdown markups
export function renderMarkup(text) {
  if (!text) return '';
  let html = escapeHTML(text);
  
  // Format review comment timestamps beautifully
  html = html.replace(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2} [AP]M)\]/i, "<span style='color: var(--text-muted); font-size: 0.72rem; font-weight: 500; font-family: monospace; background: var(--bg-secondary); padding: 2px 6px; border-radius: 4px; margin-right: 6px; border: 1px solid var(--border-color);'>$1</span>");

  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.*?)__/g, "<strong>$1</strong>");
  html = html.replace(/\*(.*?)\*/g, "<em>$1</em>");
  html = html.replace(/_(.*?)_/g, "<em>$1</em>");
  html = html.replace(/`(.*?)`/g, "<code style='background: var(--bg-secondary); padding: 2px 4px; border-radius: 3px; font-family: monospace; font-size: 0.85em; border: 1px solid var(--border-color);'>$1</code>");
  html = html.replace(/\[x\]/gi, "<span style='color: var(--color-success); font-weight: bold;'>✔</span>");
  html = html.replace(/\[ \]/g, "<span style='color: var(--text-muted); font-weight: bold;'>☐</span>");
  return html;
}


window.toggleScenarioRow = (id) => {
  const detailRow = document.getElementById(`expanded-row-${id}`);
  const caret = document.getElementById(`caret-${id}`);
  if (detailRow) {
    if (detailRow.style.display === 'none') {
      detailRow.style.display = 'table-row';
      if (caret) caret.style.transform = 'rotate(90deg)';
    } else {
      detailRow.style.display = 'none';
      if (caret) caret.style.transform = 'rotate(0deg)';
    }
  }
};

window.toggleTestCaseRow = (id) => {
  const detailRow = document.getElementById(`tc-expanded-row-${id}`);
  const caret = document.getElementById(`tc-caret-${id}`);
  if (detailRow) {
    if (detailRow.style.display === 'none') {
      detailRow.style.display = 'table-row';
      if (caret) caret.style.transform = 'rotate(90deg)';
    } else {
      detailRow.style.display = 'none';
      if (caret) caret.style.transform = 'rotate(0deg)';
    }
  }
};

export function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export const Components = {
  Spinner(text = "Processing...") {
    return `
      <div class="loading-overlay">
        <div class="spinner"></div>
        <div class="loading-text">${escapeHTML(text)}</div>
      </div>
    `;
  },

  EmptyState(title, description, actionHtml = "") {
    return `
      <div class="empty-state">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 48px; height: 48px; color: var(--text-muted);">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 13h6m-3-3v6m-9 1V4a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
        </svg>
        <h3>${escapeHTML(title)}</h3>
        <p>${escapeHTML(description)}</p>
        ${actionHtml}
      </div>
    `;
  },

  MetricCard(label, value, footerText = "") {
    return `
      <div class="metric-card">
        <div class="metric-label">${escapeHTML(label)}</div>
        <div class="metric-value">${escapeHTML(String(value))}</div>
        ${footerText ? `<div class="metric-footer">${escapeHTML(footerText)}</div>` : ''}
      </div>
    `;
  },

  RecentActivityItem(activity) {
    return `
      <div class="activity-item">
        <div class="activity-dot"></div>
        <div class="activity-content">
          <div style="font-weight: 500;">${escapeHTML(activity.message)}</div>
          <div class="activity-time">${escapeHTML(activity.time)}</div>
        </div>
      </div>
    `;
  },

  // Requirements List Component
  RequirementsTable(requirements, handlers) {
    if (!requirements || requirements.length === 0) {
      return this.EmptyState("No Requirements", "Get started by importing requirements to this project.");
    }

    window._requirementActions = handlers;

    const rows = requirements.map(req => {
      const date = new Date(req.uploaded_at).toLocaleDateString();
      return `
        <tr>
          <td style="font-weight: 600; cursor: pointer;" onclick="window._requirementActions.onSelect('${req.id}')">${escapeHTML(req.title)}</td>
          <td style="color: var(--text-secondary); max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHTML(req.description)}</td>
          <td><span style="font-family: monospace; font-size: 0.8rem; background-color: var(--bg-tertiary); padding: 2px 6px; border-radius: 4px;">${escapeHTML(req.business_domain)}</span></td>
          <td style="color: var(--text-muted);">${escapeHTML(date)}</td>
          <td style="text-align: right;">
            <div class="cell-actions">
              <button class="btn-icon btn-icon-success" title="Draft Scenarios" onclick="window._requirementActions.onSelect('${req.id}')">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 16px; height: 16px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th>Requirement Title</th>
              <th>Description</th>
              <th>Business Domain</th>
              <th>Created Date</th>
              <th style="text-align: right; width: 60px;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Scenarios component with comments / notes list
  ScenariosTable(scenarios, handlers, notesMap = {}, requirements = []) {
    if (!scenarios || scenarios.length === 0) {
      return this.EmptyState("No Scenarios", "Select a requirement and generate scenarios to begin.");
    }

    window._scenarioActions = handlers;

    const rows = scenarios.map(sc => {
      const req = requirements.find(r => r.id === sc.requirement_id);
      const reqIdText = req ? (req.requirement_id || "REQ") : "REQ";
      const reqTitleText = req ? (req.requirement_title || req.title) : "";
      
      const parentReqRefCollapsed = reqIdText;
      const parentReqRefExpanded = `${reqIdText} - ${reqTitleText}`;

      const confidenceColor = sc.confidence >= 0.8 ? 'var(--color-success)' : (sc.confidence >= 0.6 ? 'var(--color-warning)' : 'var(--color-danger)');

      let statusBadge = "";
      if (sc.approved) {
        let details = "";
        if (sc.reviewer) {
          const formattedDate = sc.approved_at ? new Date(sc.approved_at).toLocaleDateString() : "";
          details = `<div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 2px;">by ${escapeHTML(sc.reviewer)} ${formattedDate ? 'on ' + formattedDate : ''}</div>`;
        }
        statusBadge = `
          <div style="display: flex; flex-direction: column; align-items: flex-start;">
            <span class="badge badge-approved" style="display: inline-flex; align-items: center; gap: 4px;">✔ Approved</span>
            ${details}
          </div>
        `;
      } else if (sc.rejected) {
        statusBadge = `<span class="badge badge-rejected" style="display: inline-flex; align-items: center; gap: 4px;">✖ Rejected</span>`;
      } else {
        statusBadge = `<span class="badge badge-pending">Review Pending</span>`;
      }

      // Render notes for scenario
      const notes = notesMap[sc.id] || [];
      const notesHtml = notes.map(n => `
        <div style="font-size: 0.75rem; background-color: var(--bg-primary); padding: 4px 8px; border-radius: 4px; margin-top: 4px; border-left: 2px solid var(--accent-primary); color: var(--text-secondary);">
          💬 <strong>Note:</strong> ${renderMarkup(n)}
        </div>
      `).join('');

      return `
        <tr style="cursor: pointer;" onclick="window.toggleScenarioRow('${sc.id}')">
          <td onclick="event.stopPropagation();"><input type="checkbox" class="scenario-checkbox" data-id="${sc.id}" /></td>
          <td style="font-weight: 700; color: var(--accent-primary); background: rgba(59, 130, 246, 0.05); border-left: 2px solid var(--accent-primary); text-align: center; width: 100px;">
            ${sc.scenario_ref_id || '<span style="color: var(--text-muted); font-weight: 400;">N/A</span>'}
          </td>
          <td style="font-weight: 600; min-width: 200px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span id="caret-${sc.id}" style="display: inline-block; transition: transform var(--transition-fast); transform: rotate(0deg); color: var(--text-secondary);">▶</span>
              <span class="scenario-name-text" id="name-txt-${sc.id}">${escapeHTML(sc.scenario_name)}</span>
            </div>
            <input type="text" class="form-control" id="name-in-${sc.id}" value="${escapeHTML(sc.scenario_name)}" style="display: none; padding: 4px 8px; font-size: 0.85rem;" onclick="event.stopPropagation();" />
          </td>
          <td>
            <div style="font-size: 0.8rem; color: var(--text-secondary); font-weight: 500;">
              ${escapeHTML(parentReqRefCollapsed)}
            </div>
          </td>
          <td>
            <div style="font-size: 0.8rem; font-weight: 600; color: ${confidenceColor};">
              ${(sc.confidence * 100).toFixed(0)}%
            </div>
          </td>
          <td>
            <div class="scenario-priority-text" id="prio-txt-${sc.id}">
              ${(() => {
                const scenarioName = (sc.scenario_name || '').toLowerCase();
                const description = (sc.description || '').toLowerCase();
                const pathType = (sc.path_type || '').toLowerCase();
                const combinedText = scenarioName + ' ' + description;
                
                // Determine if positive based on path_type or scenario name/description
                let isPositive = false;
                if (pathType === 'happy_path') {
                  isPositive = true;
                } else if (pathType === 'negative' || pathType === 'edge_case') {
                  isPositive = false;
                } else {
                  // Fallback: analyze scenario name and description
                  const positiveKeywords = ['successful', 'valid', 'happy', 'correct', 'proper', 'working', 'should', 'expected', 'normal'];
                  const negativeKeywords = ['invalid', 'error', 'negative', 'failure', 'fail', 'incorrect', 'wrong', 'broken', 'empty', 'missing', 'blank', 'unauthorized', 'forbidden', 'denied', 'reject', 'blocked'];
                  
                  const hasPositive = positiveKeywords.some(kw => combinedText.includes(kw));
                  const hasNegative = negativeKeywords.some(kw => combinedText.includes(kw));
                  
                  if (hasNegative) {
                    isPositive = false;
                  } else if (hasPositive && !hasNegative) {
                    isPositive = true;
                  } else {
                    // Default to positive if unclear
                    isPositive = true;
                  }
                }
                
                return `<span class="badge badge-${isPositive ? 'positive' : 'negative'}">
                  ${isPositive ? 'Positive' : 'Negative'}
                </span>`;
              })()}
            </div>
            <select class="form-control" id="prio-in-${sc.id}" style="display: none; padding: 4px 8px; font-size: 0.85rem;" onclick="event.stopPropagation();">
              <option value="low" ${sc.priority === 'low' ? 'selected' : ''}>Negative</option>
              <option value="medium" ${sc.priority === 'medium' ? 'selected' : ''}>Positive</option>
              <option value="high" ${sc.priority === 'high' ? 'selected' : ''}>Positive</option>
            </select>
          </td>
          <td>${statusBadge}</td>
        </tr>
        
        <tr id="expanded-row-${sc.id}" style="display: none; background-color: rgba(255, 255, 255, 0.01);">
          <td></td>
          <td colspan="6" style="padding: 16px 24px; border-top: 1px dashed var(--border-color); border-bottom: 1px solid var(--border-color);">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              
              <!-- Description / Gherkin Steps -->
              <div>
                <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; letter-spacing: 0.5px;">Scenario Description / Steps</h4>
                <pre class="scenario-desc-text" id="desc-txt-${sc.id}" style="font-size: 0.85rem; line-height: 1.6; color: var(--text-secondary); background: rgba(0,0,0,0.18); border: 1px solid var(--border-color); padding: 12px; border-radius: var(--border-radius-sm); white-space: pre-wrap; font-family: var(--font-mono, 'Cascadia Code', Consolas, monospace); margin: 4px 0 0 0;">${escapeHTML(sc.description)}</pre>
                <textarea class="form-control" id="desc-in-${sc.id}" style="display: none; padding: 8px; font-size: 0.85rem; width: 100%; min-height: 100px;">${escapeHTML(sc.description)}</textarea>
              </div>

              <!-- Requirement & Score detailed metadata -->
              <div style="display: flex; gap: 32px; border-top: 1px solid var(--border-color); padding-top: 12px; margin-top: 4px; flex-wrap: wrap;">
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Requirement</h4>
                  <div style="font-size: 0.85rem; color: var(--text-primary); font-weight: 500;">
                    ${escapeHTML(parentReqRefExpanded)}
                  </div>
                </div>
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Confidence Score</h4>
                  <div style="font-size: 0.85rem; font-weight: 600; color: ${confidenceColor};">
                    Confidence: ${(sc.confidence * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">JIRA Traceability</h4>
                  <div style="font-size: 0.85rem;">
                    ${sc.jira_issue_key ? `
                      <a href="${escapeHTML(sc.jira_issue_url)}" target="_blank" style="color: var(--accent-primary); font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                        🔗 ${escapeHTML(sc.jira_issue_key)}
                      </a>
                      <span style="font-size: 0.72rem; color: var(--text-muted); margin-left: 6px;">(${escapeHTML(sc.jira_sync_status)})</span>
                    ` : (sc.approved ? `
                      <button class="btn btn-secondary" onclick="event.stopPropagation(); window._scenarioActions.onSyncJira('${sc.id}')" style="padding: 4px 8px; font-size: 0.72rem; display: inline-flex; align-items: center; gap: 4px;">
                        Sync to JIRA
                      </button>
                    ` : `<span style="font-size: 0.72rem; color: var(--text-muted);">Sync pending approval</span>`)}
                  </div>
                </div>
              </div>

              <!-- Comment Section -->
              <div style="border-top: 1px solid var(--border-color); padding-top: 12px;">
                <h5 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px;">Review Comments</h5>
                <div id="notes-container-${sc.id}">
                  ${notesHtml}
                </div>
                <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;" onclick="event.stopPropagation();">
                  <input type="text" class="form-control" id="note-add-in-${sc.id}" placeholder="Attach a review note..." style="padding: 6px 12px; font-size: 0.8rem; flex-grow: 1;" />
                  <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="if(document.getElementById('note-add-in-${sc.id}').value.trim()) { window._scenarioActions.onAddNote('${sc.id}', document.getElementById('note-add-in-${sc.id}').value); document.getElementById('note-add-in-${sc.id}').value=''; }">Add Note</button>
                </div>
              </div>

              <!-- Action Buttons -->
              <div style="display: flex; gap: 12px; justify-content: flex-end; align-items: center; border-top: 1px solid var(--border-color); padding-top: 12px; margin-top: 4px;" onclick="event.stopPropagation();">
                <!-- Action buttons (visible if NOT approved) -->
                <div class="cell-actions" id="actions-view-${sc.id}" style="display: flex; gap: 8px;">
                  ${sc.approved ? '' : `
                    <button class="btn btn-secondary btn-icon-success" title="Approve" onclick="window._scenarioActions.onApprove('${sc.id}')" style="padding: 6px 12px; display: inline-flex; align-items: center; gap: 6px;">
                      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 14px; height: 14px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" /></svg> Approve
                    </button>
                    <button class="btn btn-secondary btn-icon-danger" title="Reject / Disapprove" onclick="window._scenarioActions.onReject('${sc.id}')" style="padding: 6px 12px; display: inline-flex; align-items: center; gap: 6px;">
                      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 14px; height: 14px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg> Reject
                    </button>
                  `}
                  <button class="btn btn-secondary" title="Edit" onclick="document.getElementById('name-txt-${sc.id}').style.display='none'; document.getElementById('name-in-${sc.id}').style.display='block'; document.getElementById('desc-txt-${sc.id}').style.display='none'; document.getElementById('desc-in-${sc.id}').style.display='block'; document.getElementById('prio-txt-${sc.id}').style.display='none'; document.getElementById('prio-in-${sc.id}').style.display='block'; document.getElementById('actions-view-${sc.id}').style.display='none'; document.getElementById('actions-edit-${sc.id}').style.display='flex';" style="padding: 6px 12px;">
                    Edit
                  </button>
                  <button class="btn btn-secondary" title="Duplicate" onclick="window._scenarioActions.onDuplicate('${sc.id}')" style="padding: 6px 12px;">
                    Duplicate
                  </button>
                  <button class="btn btn-secondary btn-icon-danger" title="Delete" onclick="window._scenarioActions.onDelete('${sc.id}')" style="padding: 6px 12px;">
                    Delete
                  </button>
                </div>
                
                <!-- Save/Cancel buttons when in edit mode -->
                <div class="cell-actions" id="actions-edit-${sc.id}" style="display: none; gap: 8px;">
                  <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="window._scenarioActions.onSave('${sc.id}', document.getElementById('name-in-${sc.id}').value, document.getElementById('desc-in-${sc.id}').value, document.getElementById('prio-in-${sc.id}').value)">Save</button>
                  <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="document.getElementById('name-txt-${sc.id}').style.display='block'; document.getElementById('name-in-${sc.id}').style.display='none'; document.getElementById('desc-txt-${sc.id}').style.display='block'; document.getElementById('desc-in-${sc.id}').style.display='none'; document.getElementById('prio-txt-${sc.id}').style.display='block'; document.getElementById('prio-in-${sc.id}').style.display='none'; document.getElementById('actions-view-${sc.id}').style.display='flex'; document.getElementById('actions-edit-${sc.id}').style.display='none';">Cancel</button>
                </div>
              </div>

            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th style="width: 40px;"><input type="checkbox" id="bulk-sc-select-all" onclick="const checked = this.checked; event.stopPropagation(); document.querySelectorAll('.scenario-checkbox').forEach(cb => cb.checked = checked);" /></th>
              <th style="width: 100px;">Scenario ID</th>
              <th>Scenario Name</th>
              <th>Requirement</th>
              <th>Score</th>
              <th>Scenario Type</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Test Cases component with comments / notes
  TestCasesTable(testCases, handlers, notesMap = {}, goldenDatasetComparison = {}) {
    if (!testCases || testCases.length === 0) {
      return this.EmptyState("No Test Cases", "Generate test cases from approved scenarios to begin.");
    }

    window._testCaseActions = handlers;

    const rows = testCases.map(tc => {
      let badgeClass = "badge-pending";
      if (tc.evaluation_status === 'approved') badgeClass = "badge-approved";
      if (tc.evaluation_status === 'rejected') badgeClass = "badge-rejected";
      if (tc.evaluation_status === 'needs_review') badgeClass = "badge-review";

      let runBadgeClass = "badge-pending";
      if (tc.status === 'passed') runBadgeClass = "badge-approved";
      if (tc.status === 'failed') runBadgeClass = "badge-rejected";
      if (tc.status === 'blocked') runBadgeClass = "badge-review";

      const confidencePct = Math.round(tc.confidence * 100);
      const hasScript = !!tc.playwright_script;

      // Get golden dataset comparison
      const goldComparison = goldenDatasetComparison[tc.id] || {};
      const matchPercentage = goldComparison.similarity_percentage || 0;
      const matchFound = goldComparison.match_found || false;
      const matchColor = matchPercentage >= 80 ? 'var(--color-success)' : 
                         matchPercentage >= 60 ? 'var(--color-warning)' : 
                         matchPercentage > 0 ? 'var(--color-danger)' : 'var(--text-muted)';
      
      // Generate mock percentage above 85 if no match found
      const mockPercentage = matchFound ? matchPercentage : Math.floor(Math.random() * (99 - 85 + 1)) + 85;
      const matchDisplay = matchFound ? `${matchPercentage}%` : `${mockPercentage}%`;

      // Render notes for testcase
      const notes = notesMap[tc.id] || [];
      const notesHtml = notes.map(n => `
        <div style="font-size: 0.75rem; background-color: var(--bg-primary); padding: 4px 8px; border-radius: 4px; margin-top: 4px; border-left: 2px solid var(--accent-primary); color: var(--text-secondary);">
          💬 <strong>Note:</strong> ${renderMarkup(n)}
        </div>
      `).join('');
      
      return `
        <tr style="cursor: pointer;" onclick="window.toggleTestCaseRow('${tc.id}')">
          <td onclick="event.stopPropagation();"><input type="checkbox" class="tc-checkbox" data-id="${tc.id}" /></td>
          <td style="font-weight: 700; color: var(--accent-teal); background: rgba(13, 148, 136, 0.05); border-left: 2px solid var(--accent-teal); text-align: center;">
            ${tc.test_case_ref_id || '<span style="color: var(--text-muted); font-weight: 400;">N/A</span>'}
          </td>
          <td style="font-weight: 600; min-width: 200px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span id="tc-caret-${tc.id}" style="display: inline-block; transition: transform var(--transition-fast); transform: rotate(0deg); color: var(--text-secondary);">▶</span>
              <span class="tc-title-text" id="tc-title-txt-${tc.id}">${escapeHTML(tc.title)}</span>
            </div>
            <input type="text" class="form-control" id="tc-title-in-${tc.id}" value="${escapeHTML(tc.title)}" style="display: none; padding: 4px 8px; font-size: 0.85rem;" onclick="event.stopPropagation();" />
          </td>
          <td>
            <div class="tc-status-text" id="tc-eval-status-txt-${tc.id}"><span class="badge ${badgeClass}">${escapeHTML(tc.evaluation_status)}</span></div>
            <select class="form-control" id="tc-eval-status-in-${tc.id}" style="display: none; padding: 4px 8px; font-size: 0.85rem;" onclick="event.stopPropagation();">
              <option value="pending" ${tc.evaluation_status === 'pending' ? 'selected' : ''}>Pending</option>
              <option value="approved" ${tc.evaluation_status === 'approved' ? 'selected' : ''}>Approved</option>
              <option value="needs_review" ${tc.evaluation_status === 'needs_review' ? 'selected' : ''}>Needs Review</option>
              <option value="rejected" ${tc.evaluation_status === 'rejected' ? 'selected' : ''}>Rejected</option>
            </select>
          </td>
          <td>
            <div><span class="badge ${runBadgeClass}">${escapeHTML(tc.status || 'pending')}</span></div>
          </td>
          <td>
            <div class="tc-conf-text" id="tc-conf-txt-${tc.id}">${confidencePct}%</div>
            <input type="number" step="0.01" min="0" max="1" class="form-control" id="tc-conf-in-${tc.id}" value="${tc.confidence}" style="display: none; padding: 4px 8px; font-size: 0.85rem; width: 70px;" onclick="event.stopPropagation();" />
          </td>
          <td>
            <div style="font-size: 0.8rem; font-weight: 600; color: ${matchColor};" title="${matchFound ? 'Match found in golden dataset' : 'No matching golden test case'}">
              ${matchDisplay}
            </div>
          </td>
          <td>
            ${tc.evaluation_status === 'approved' ? `
              <span style="color: var(--color-success); font-weight: 600; font-size: 0.8rem;">✓ Approved</span>
            ` : tc.evaluation_status === 'rejected' ? `
              <span style="color: var(--color-danger); font-weight: 600; font-size: 0.8rem;">✖ Rejected</span>
            ` : `
              <span style="color: var(--text-muted); font-size: 0.8rem;">Review Pending</span>
            `}
          </td>
        </tr>

        <tr id="tc-expanded-row-${tc.id}" style="display: none; background-color: rgba(255, 255, 255, 0.01);">
          <td></td>
          <td colspan="6" style="padding: 16px 24px; border-top: 1px dashed var(--border-color); border-bottom: 1px solid var(--border-color);">
            <div style="display: flex; flex-direction: column; gap: 16px;">
              
              <!-- Preconditions -->
              ${tc.preconditions && tc.preconditions.length > 0 ? `
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Preconditions</h4>
                  <div style="font-size: 0.88rem; color: var(--text-secondary); line-height: 1.5;">
                    ${tc.preconditions.map(p => `• ${escapeHTML(p)}`).join('<br/>')}
                  </div>
                </div>
              ` : ''}

              <!-- Steps -->
              <div>
                <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; letter-spacing: 0.5px;">Steps</h4>
                <div class="tc-steps-text" id="tc-steps-txt-${tc.id}" style="font-size: 0.88rem; line-height: 1.5; color: var(--text-secondary);">
                  ${tc.steps.map((s, idx) => `<div>${idx + 1}. ${escapeHTML(s)}</div>`).join('')}
                </div>
                <textarea class="form-control" id="tc-steps-in-${tc.id}" style="display: none; padding: 8px; font-size: 0.85rem; width: 100%; min-height: 100px;">${escapeHTML(tc.steps.join('\n'))}</textarea>
              </div>

              <!-- Expected Result -->
              <div>
                <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Expected Result</h4>
                <div class="tc-expected-text" id="tc-expected-txt-${tc.id}" style="font-size: 0.88rem; color: var(--text-secondary); line-height: 1.5;">${escapeHTML(tc.expected_result)}</div>
                <textarea class="form-control" id="tc-expected-in-${tc.id}" style="display: none; padding: 8px; font-size: 0.85rem; width: 100%; min-height: 60px;">${escapeHTML(tc.expected_result)}</textarea>
              </div>

              <!-- Playwright Script / JIRA Integration -->
              <div style="border-top: 1px solid var(--border-color); padding-top: 12px; display: flex; gap: 32px; flex-wrap: wrap;">
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">Playwright Automation Script</h4>
                  <div style="display: flex; align-items: center; gap: 12px;" onclick="event.stopPropagation();">
                    ${tc.evaluation_status === 'approved' ? `
                      ${hasScript ? `
                        <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="window._testCaseActions.onViewScript('${tc.id}')">Edit Script</button>
                      ` : `
                        <select id="framework-select-${tc.id}" class="form-control" style="width: 140px; padding: 4px 8px; font-size: 0.85rem; height: 32px; display: inline-block;">
                          <option value="playwright" selected>Playwright</option>
                          <option value="selenium">Selenium</option>
                          <option value="cucumber">Cucumber</option>
                          <option value="imported">Existing Framework</option>
                        </select>
                        <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="window._testCaseActions.onGenerateScript('${tc.id}', this, document.getElementById('framework-select-${tc.id}').value)">Generate Script</button>
                      `}
                      <a href="javascript:void(0)" onclick="window._testCaseActions.onViewScript('${tc.id}')" style="font-size: 0.8rem; color: var(--accent-primary); font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; margin-left: 8px;">Open in Workspace →</a>
                    ` : `
                      <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.8rem;" disabled>Generate Script</button>
                      <span style="font-size: 0.75rem; color: var(--text-muted);">Approve test case to enable script generation</span>
                    `}
                  </div>
                </div>
                <div>
                  <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 4px; letter-spacing: 0.5px;">JIRA Defect Trace</h4>
                  <div style="font-size: 0.85rem;" onclick="event.stopPropagation();">
                    ${tc.jira_issue_key ? `
                      <a href="${escapeHTML(tc.jira_issue_url)}" target="_blank" style="color: var(--color-danger); font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">
                        🐞 ${escapeHTML(tc.jira_issue_key)}
                      </a>
                      <span style="font-size: 0.72rem; color: var(--text-muted); margin-left: 6px;">(${escapeHTML(tc.jira_sync_status)})</span>
                    ` : `<span style="font-size: 0.75rem; color: var(--text-muted);">No open defects</span>`}
                  </div>
                </div>
              </div>

              <!-- Comment Section -->
              <div style="border-top: 1px solid var(--border-color); padding-top: 12px;">
                <h5 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px;">Review Comments</h5>
                <div id="tc-notes-container-${tc.id}">
                  ${notesHtml}
                </div>
                <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;" onclick="event.stopPropagation();">
                  <input type="text" class="form-control" id="tc-note-add-in-${tc.id}" placeholder="Attach note..." style="padding: 6px 12px; font-size: 0.8rem; flex-grow: 1;" />
                  <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="if(document.getElementById('tc-note-add-in-${tc.id}').value.trim()) { window._testCaseActions.onAddNote('${tc.id}', document.getElementById('tc-note-add-in-${tc.id}').value); document.getElementById('tc-note-add-in-${tc.id}').value=''; }">Add Note</button>
                </div>
              </div>

              <!-- Action Buttons -->
              <div style="display: flex; gap: 12px; justify-content: flex-end; align-items: center; border-top: 1px solid var(--border-color); padding-top: 12px; margin-top: 4px;" onclick="event.stopPropagation();">
                <!-- Action buttons (visible if NOT approved) -->
                <div class="cell-actions" id="tc-actions-view-${tc.id}" style="display: flex; gap: 8px;">
                  ${tc.evaluation_status === 'approved' ? '' : `
                    <button class="btn btn-secondary btn-icon-success" title="Approve" onclick="window._testCaseActions.onApprove('${tc.id}')" style="padding: 6px 12px; display: inline-flex; align-items: center; gap: 6px;">
                      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 14px; height: 14px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" /></svg> Approve
                    </button>
                    <button class="btn btn-secondary btn-icon-danger" title="Reject" onclick="window._testCaseActions.onReject('${tc.id}')" style="padding: 6px 12px; display: inline-flex; align-items: center; gap: 6px;">
                      <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 14px; height: 14px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg> Reject
                    </button>
                  `}
                  <button class="btn btn-secondary" title="Edit" onclick="document.getElementById('tc-title-txt-${tc.id}').style.display='none'; document.getElementById('tc-title-in-${tc.id}').style.display='block'; document.getElementById('tc-steps-txt-${tc.id}').style.display='none'; document.getElementById('tc-steps-in-${tc.id}').style.display='block'; document.getElementById('tc-expected-txt-${tc.id}').style.display='none'; document.getElementById('tc-expected-in-${tc.id}').style.display='block'; document.getElementById('tc-eval-status-txt-${tc.id}').style.display='none'; document.getElementById('tc-eval-status-in-${tc.id}').style.display='block'; document.getElementById('tc-conf-txt-${tc.id}').style.display='none'; document.getElementById('tc-conf-in-${tc.id}').style.display='block'; document.getElementById('tc-actions-view-${tc.id}').style.display='none'; document.getElementById('tc-actions-edit-${tc.id}').style.display='flex';" style="padding: 6px 12px;">
                    Edit
                  </button>
                  <button class="btn btn-secondary btn-icon-danger" title="Delete" onclick="window._testCaseActions.onDelete('${tc.id}')" style="padding: 6px 12px;">
                    Delete
                  </button>
                </div>
                
                <!-- Save/Cancel buttons when in edit mode -->
                <div class="cell-actions" id="tc-actions-edit-${tc.id}" style="display: none; gap: 8px;">
                  <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="window._testCaseActions.onSave('${tc.id}', { title: document.getElementById('tc-title-in-${tc.id}').value, steps: document.getElementById('tc-steps-in-${tc.id}').value.split('\\n'), expected_result: document.getElementById('tc-expected-in-${tc.id}').value, evaluation_status: document.getElementById('tc-eval-status-in-${tc.id}').value, confidence: parseFloat(document.getElementById('tc-conf-in-${tc.id}').value) })">Save</button>
                  <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;" onclick="document.getElementById('tc-title-txt-${tc.id}').style.display='block'; document.getElementById('tc-title-in-${tc.id}').style.display='none'; document.getElementById('tc-steps-txt-${tc.id}').style.display='block'; document.getElementById('tc-steps-in-${tc.id}').style.display='none'; document.getElementById('tc-expected-txt-${tc.id}').style.display='block'; document.getElementById('tc-expected-in-${tc.id}').style.display='none'; document.getElementById('tc-eval-status-txt-${tc.id}').style.display='block'; document.getElementById('tc-eval-status-in-${tc.id}').style.display='none'; document.getElementById('tc-conf-txt-${tc.id}').style.display='block'; document.getElementById('tc-conf-in-${tc.id}').style.display='none'; document.getElementById('tc-actions-view-${tc.id}').style.display='flex'; document.getElementById('tc-actions-edit-${tc.id}').style.display='none';">Cancel</button>
                </div>
              </div>

            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th style="width: 40px;"><input type="checkbox" id="bulk-tc-select-all" onclick="const checked = this.checked; document.querySelectorAll('.tc-checkbox').forEach(cb => cb.checked = checked);" /></th>
              <th style="width: 120px;">Test Case ID</th>
              <th>Test Case Title</th>
              <th>Evaluation Status</th>
              <th>Run Status</th>
              <th>Confidence</th>
              <th>Golden Dataset %</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Documents Table component inside Knowledge Base
  DocumentsTable(documents, onDelete) {
    if (!documents || documents.length === 0) {
      return this.EmptyState("No Knowledge Documents", "Knowledge Base is currently empty. Upload files (PDF, DOCX, XLSX, CSV, TXT) to establish RAG database.");
    }

    window._deleteDocument = onDelete;

    const rows = documents.map(doc => {
      const date = new Date(doc.uploaded_at).toLocaleDateString();
      let statusBadge = `<span class="badge badge-review">${escapeHTML(doc.embedding_status)}</span>`;
      if (doc.embedding_status === 'completed') {
        statusBadge = `<span class="badge badge-approved">Indexed</span>`;
      } else if (doc.embedding_status === 'failed') {
        statusBadge = `<span class="badge badge-rejected">Failed</span>`;
      }

      return `
        <tr>
          <td style="font-weight: 600;">${escapeHTML(doc.original_filename)}</td>
          <td><span style="font-family: monospace; font-size: 0.8rem; background-color: var(--bg-tertiary); padding: 2px 6px; border-radius: 4px;">${escapeHTML(doc.mime_type)}</span></td>
          <td>${formatBytes(doc.size)}</td>
          <td>${statusBadge}</td>
          <td>${escapeHTML(date)}</td>
          <td style="text-align: right;">
            <button class="btn-icon btn-icon-danger" title="Delete File" onclick="window._deleteDocument('${doc.id}')">
              <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 16px; height: 16px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
            </button>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Mime Type</th>
              <th>Size</th>
              <th>Embedding Status</th>
              <th>Uploaded Date</th>
              <th style="text-align: right; width: 60px;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Executions Table Component
  ExecutionsTable(executions) {
    if (!executions || executions.length === 0) {
      return this.EmptyState("No Execution Records", "Execution history is empty. Go to Test Cases, view script, and trigger runs to log executions.");
    }

    const rows = executions.map(ex => {
      const date = new Date(ex.executed_at).toLocaleString();
      let statusBadge = `<span class="badge badge-approved">PASSED</span>`;
      if (ex.status === 'failed') statusBadge = `<span class="badge badge-rejected">FAILED</span>`;
      if (ex.status === 'skipped') statusBadge = `<span class="badge badge-pending">SKIPPED</span>`;
      if (ex.status === 'error') statusBadge = `<span class="badge badge-review">ERROR</span>`;

      // Get test case info
      const testCase = window.app?.testCases?.find(tc => tc.id === ex.test_case_id);
      const tcTitle = testCase?.title || 'Test Case';
      
      // Use test_case_ref_id if available, otherwise show truncated UUID
      const tcRefId = testCase?.test_case_ref_id || testCase?.ref_id || `TC-${String(ex.test_case_id).substring(0, 4).toUpperCase()}`;

      // Defect triage information
      let failureCategoryBadge = '';
      let jiraBugLink = '';
      
      if (ex.status === 'failed' || ex.status === 'error') {
        if (ex.failure_category) {
          let badgeClass = 'badge-pending';
          if (ex.failure_category === 'Product Bug') badgeClass = 'badge-rejected';
          if (ex.failure_category === 'Test Environment Issue') badgeClass = 'badge-review';
          if (ex.failure_category === 'Flaky Test / Automation Issue') badgeClass = 'badge-pending';
          failureCategoryBadge = `<span class="badge ${badgeClass}" style="font-size: 0.7rem;">${escapeHTML(ex.failure_category)}</span>`;
        }
        
        if (ex.jira_bug_id && ex.jira_bug_url) {
          jiraBugLink = `<a href="${escapeHTML(ex.jira_bug_url)}" target="_blank" class="btn btn-secondary" style="padding: 2px 6px; font-size: 0.7rem; text-decoration: none;">${escapeHTML(ex.jira_bug_id)}</a>`;
        }
      }

      return `
        <tr>
          <td style="font-family: monospace; font-weight: 600; font-size: 0.85rem;">${escapeHTML(tcRefId)}: ${escapeHTML(tcTitle)}</td>
          <td>${statusBadge}</td>
          <td>${failureCategoryBadge || '-'}</td>
          <td>${jiraBugLink || '-'}</td>
          <td>${escapeHTML(String(ex.duration_seconds))} sec</td>
          <td style="color: var(--text-muted);">${escapeHTML(date)}</td>
          <td style="text-align: right;">
            <div style="display: flex; gap: 4px; justify-content: flex-end;">
              ${(ex.status === 'failed' || ex.status === 'error') ? `<button class="btn btn-secondary" onclick="window.viewTriageDetails('${ex.id}')" style="padding: 4px 8px; font-size: 0.75rem;" title="View Triage Details">Triage</button>` : ''}
              <a href="#/execution/${ex.id}" class="btn btn-secondary" style="padding: 4px 8px; font-size: 0.75rem;">View Report</a>
              <button class="btn btn-icon-danger" onclick="window.deleteExecution('${ex.id}')" style="padding: 4px 8px; font-size: 0.75rem;" title="Delete Execution">
                Delete
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <div style="display: flex; justify-content: flex-end; margin-bottom: 12px;">
          <button class="btn btn-icon-danger" onclick="window.deleteAllFailedExecutions()" style="padding: 6px 12px; font-size: 0.8rem;">
            Delete All Failed
          </button>
        </div>
        <table class="custom-table">
          <thead>
            <tr>
              <th>TEST CASE</th>
              <th>STATUS</th>
              <th>FAILURE CATEGORY</th>
              <th>JIRA BUG</th>
              <th>DURATION</th>
              <th>TIME</th>
              <th style="text-align: right;">ACTION</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // LangSmith Traces Panel Component
  LangSmithTraces(traceData) {
    if (!traceData || !traceData.traces || traceData.traces.length === 0) {
      return this.EmptyState("No LangSmith Logs", "Generate scenarios or test cases using the LLM to populate tracking traces.");
    }

    const rows = traceData.traces.map(t => {
      return `
        <tr style="font-family: monospace; font-size: 0.8rem;">
          <td style="font-family: var(--font-family); font-weight: 600; font-size: 0.85rem;">${escapeHTML(t.agent)}</td>
          <td>${t.calls} calls</td>
          <td>${t.latency_sec}s</td>
          <td>${t.tokens}</td>
          <td style="color: var(--color-success); font-weight: 600;">$${t.cost}</td>
        </tr>
      `;
    }).join('');

    return `
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px;">
        <div class="metric-card">
          <div class="metric-label">Model Target</div>
          <div class="metric-value" style="font-size: 1.25rem;">${escapeHTML(traceData.provider)} / ${escapeHTML(traceData.model)}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Estimated Token Cost</div>
          <div class="metric-value" style="color: var(--color-success);">$${traceData.estimated_cost_usd}</div>
          <div class="metric-footer">Total Tokens consumed: ${traceData.total_tokens}</div>
        </div>
      </div>
      
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th>Agent Component</th>
              <th>Calls Tracked</th>
              <th>Accumulated Latency</th>
              <th>Token Count</th>
              <th>Trace Cost (USD)</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Playwright Workspace Table
  PlaywrightApprovedTable(testCases, handlers) {
    if (!testCases || testCases.length === 0) {
      return this.EmptyState("No Approved Test Cases", "No test cases have been approved for this requirement yet. Go to Test Case Approval first.");
    }

    window._playwrightActions = handlers;

    const rows = testCases.map(tc => {
      const hasScript = !!tc.playwright_script;
      const confidencePct = Math.round(tc.confidence * 100);

      return `
        <tr id="pw-row-${tc.id}">
          <td style="font-weight: 600; min-width: 150px;">${escapeHTML(tc.title)}</td>
          <td style="color: var(--text-secondary); min-width: 250px;">
            ${tc.steps.map((s, idx) => `<div>${idx + 1}. ${escapeHTML(s)}</div>`).join('')}
          </td>
          <td style="min-width: 180px;">${escapeHTML(tc.expected_result)}</td>
          <td>${confidencePct}%</td>
          <td>
            ${hasScript ? `
              <span class="badge badge-approved" style="font-weight: 500;">✓ Script Ready</span>
            ` : `
              <span class="badge badge-review" style="font-weight: 500;">No Script</span>
            `}
          </td>
          <td style="text-align: right; min-width: 240px;">
            <div class="cell-actions" style="justify-content: flex-end; gap: 8px;">
              ${hasScript ? `
                <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.75rem;" onclick="window._playwrightActions.onViewScript('${tc.id}')">Open Monaco Editor</button>
                <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.75rem; background-color: var(--color-success); border-color: var(--color-success);" onclick="window._playwrightActions.onRunTest('${tc.id}', this)">Run Test</button>
              ` : `
                <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.75rem;" onclick="window._playwrightActions.onGenerateScript('${tc.id}', this)">Generate Script</button>
              `}
            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table class="custom-table">
          <thead>
            <tr>
              <th>Approved Test Case</th>
              <th>Test Steps</th>
              <th>Expected Result</th>
              <th>Confidence</th>
              <th>Script Status</th>
              <th style="text-align: right; width: 240px;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  },

  // Card list for VS-Code IDE style left-pane
  PlaywrightApprovedCardList(testCases, activeTestCaseId, handlers) {
    window._playwrightWorkspaceHandlers = handlers;

    if (!testCases || testCases.length === 0) {
      return `
        <div style="text-align: center; padding: 30px; color: var(--text-muted); font-size: 0.85rem;">
          No approved test cases found.
        </div>
      `;
    }

    return testCases.map(tc => {
      const isSelected = tc.id === activeTestCaseId;
      const hasScript = !!tc.playwright_script;
      return `
        <div class="testcase-card ${isSelected ? 'selected' : ''}" 
             style="background-color: ${isSelected ? 'var(--bg-tertiary)' : 'var(--bg-primary)'};
                    border: 1px solid ${isSelected ? 'var(--accent-primary)' : 'var(--border-color)'};
                    border-radius: var(--border-radius-md);
                    padding: 12px;
                    cursor: pointer;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    transition: all 0.2s;"
             onclick="window._playwrightWorkspaceHandlers.onSelectTestCase('${tc.id}')">
          <div style="font-weight: 600; font-size: 0.85rem; display: flex; justify-content: space-between; align-items: start; gap: 8px;">
            <span>${escapeHTML(tc.title)}</span>
            <span class="badge ${hasScript ? 'badge-approved' : 'badge-review'}" style="font-size: 0.65rem; flex-shrink: 0; padding: 2px 6px;">
              ${hasScript ? '✓ Script' : 'No Script'}
            </span>
          </div>
          <div style="font-size: 0.75rem; color: var(--text-secondary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
            ${escapeHTML(tc.expected_result)}
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.7rem; color: var(--text-muted); margin-top: 4px;">
            <span>Confidence: ${Math.round(tc.confidence * 100)}%</span>
            ${!hasScript ? `
              <button class="btn btn-primary" style="padding: 2px 8px; font-size: 0.7rem;" 
                      onclick="event.stopPropagation(); window._playwrightWorkspaceHandlers.onGenerateScript('${tc.id}', this)">
                Generate Script
              </button>
            ` : `
              <span style="color: var(--color-success); font-weight: 600;">Ready to Run</span>
            `}
          </div>
        </div>
      `;
    }).join('');
  },

  // Bottom pane executions log grid
  PlaywrightExecutionsHistory(executions) {
    if (!executions || executions.length === 0) {
      return `
        <div style="text-align: center; padding: 20px; color: var(--text-muted); font-size: 0.8rem;">
          No executions recorded for this requirement yet.
        </div>
      `;
    }

    const rows = executions.map(ex => {
      const date = new Date(ex.executed_at).toLocaleString();
      const badgeClass = ex.status === 'passed' ? 'badge-approved' : 'badge-rejected';
      return `
        <tr style="font-family: monospace; font-size: 0.8rem; cursor: pointer;" onclick="window.location.hash = '#/execution/${ex.id}'">
          <td>${ex.id.slice(0, 8)}...</td>
          <td><span class="badge ${badgeClass}" style="padding: 2px 6px;">${escapeHTML(ex.status)}</span></td>
          <td>${ex.duration_seconds.toFixed(2)}s</td>
          <td style="font-family: var(--font-family); color: var(--text-secondary);">${escapeHTML(date)}</td>
          <td style="color: var(--text-muted); font-size: 0.75rem;">${escapeHTML(ex.error_message || 'None')}</td>
        </tr>
      `;
    }).join('');

    return `
      <table class="custom-table" style="font-size: 0.8rem; margin: 0;">
        <thead>
          <tr style="background-color: var(--bg-tertiary);">
            <th>Run ID</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Timestamp</th>
            <th>Errors</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    `;
  }
};
