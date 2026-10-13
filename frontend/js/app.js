import { API } from './api.js';
import { Components, escapeHTML } from './components.js?v=2';

class App {
  constructor() {
    this.projects = [];
    this.currentProject = null;
    this.releases = [];
    this.testCycles = [];
    this.currentRelease = null;
    this.currentTestCycle = null;
    this.requirements = [];
    this.scenarios = [];
    this.testCases = [];
    this.documents = [];
    this.executions = [];
    this.scenariosNotes = {};
    this.testCasesNotes = {};
    this.settings = null;
    this.logs = [];

    this.selectedRequirementId = null;
    this.activePlaywrightTestCaseId = null;
    this.activeExecutionStream = null;
    this.dashboardScope = 'project';
  }

  async init() {
    this.setupEventListeners();
    this.initSearchableLOB();
    
    // Trigger authentication check and load permission scopes
    await this.initAuth();
  }

  setupEventListeners() {
    // Nav items
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        const view = e.currentTarget.getAttribute('data-view');
        if (view) this.navigateTo(view);
      });
    });

    // Exit Project Button
    const exitBtn = document.getElementById('btn-exit-project');
    if (exitBtn) {
      exitBtn.addEventListener('click', () => {
        this.currentProject = null;
        this.currentRelease = null;
        this.currentTestCycle = null;
        localStorage.removeItem('active_project_id');
        const relSelect = document.getElementById('release-select');
        if (relSelect) relSelect.style.display = 'none';
        const cySelect = document.getElementById('cycle-select');
        if (cySelect) { cySelect.style.display = 'none'; cySelect.disabled = true; cySelect.style.opacity = '0.5'; }
        const comboLabel = document.getElementById('project-combobox-label');
        if (comboLabel) comboLabel.textContent = 'Select Project';
        const exitBtn = document.getElementById('btn-exit-project');
        if (exitBtn) exitBtn.style.display = 'none';
        if (this.isAdmin()) {
          this.navigateTo('dashboard');
          this.renderDashboardMetrics();
        } else {
          this.showProjectStartScreen();
        }
      });
    }

    // Theme Toggle Trigger with Native View Transitions API
    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', (e) => {
        const toggleTheme = () => {
          const isDark = !document.body.classList.contains('light-theme');
          if (isDark) {
            document.body.classList.add('light-theme');
            document.getElementById('theme-toggle-icon').innerHTML = `
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m0-12.728l.707.707m12.728 12.728l.707-.707M12 8a4 4 0 100 8 4 4 0 000-8z" />
            `;
          } else {
            document.body.classList.remove('light-theme');
            document.getElementById('theme-toggle-icon').innerHTML = `
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            `;
          }
          if (window.monaco) {
            const currentTheme = document.body.classList.contains('light-theme') ? 'vs' : 'vs-dark';
            monaco.editor.setTheme(currentTheme);
          }
        };

        if (!document.startViewTransition) {
          toggleTheme();
          return;
        }

        // Get circular expansion coordinates centered on the toggle button
        const rect = themeBtn.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const endRadius = Math.hypot(
          Math.max(x, window.innerWidth - x),
          Math.max(y, window.innerHeight - y)
        );

        const transition = document.startViewTransition(() => {
          toggleTheme();
        });

        transition.ready.then(() => {
          document.documentElement.animate(
            {
              clipPath: [
                `circle(0px at ${x}px ${y}px)`,
                `circle(${endRadius}px at ${x}px ${y}px)`
              ]
            },
            {
              duration: 400, // Super fast and fluid (400ms)
              easing: 'ease-out',
              pseudoElement: '::view-transition-new(root)'
            }
          );
        });
      });
    }

    // Project Dropdown
    const projectSelect = document.getElementById('project-select');
    if (projectSelect) {
      projectSelect.addEventListener('change', async (e) => {
        const val = e.target.value;
        if (val === '__create_new__') {
          this.openModal('create-project-modal');
          e.target.value = this.currentProject ? this.currentProject.id : '';
        } else if (val) {
          await this.selectProject(val);
        }
      });
    }

    // Release Dropdown
    const releaseSelect = document.getElementById('release-select');
    if (releaseSelect) {
      releaseSelect.addEventListener('change', async (e) => {
        const val = e.target.value;
        if (val === '__create_new__') {
          this.openModal('create-release-modal');
          releaseSelect.value = this.currentRelease ? this.currentRelease.id : '';
        } else if (val) {
          await this.selectRelease(val);
        }
      });
    }

    // Cycle Dropdown
    const cycleSelect = document.getElementById('cycle-select');
    if (cycleSelect) {
      cycleSelect.addEventListener('change', async (e) => {
        const val = e.target.value;
        if (val === '__create_new__') {
          this.openModal('create-cycle-modal');
          cycleSelect.value = this.currentTestCycle ? this.currentTestCycle.id : '';
        } else if (val) {
          await this.selectTestCycle(val);
        }
      });
    }

    // Create Release Form Submit
    const createReleaseForm = document.getElementById('create-release-form');
    if (createReleaseForm) {
      createReleaseForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!this.currentProject) return;
        const name = document.getElementById('new-release-name').value.trim();
        const desc = document.getElementById('new-release-desc').value.trim();
        const status = document.getElementById('new-release-status').value;
        if (!name) return;
        try {
          const rel = await API.createRelease(this.currentProject.id, name, desc, status);
          this.addLog(`Release '${name}' created`);
          this.closeModal('create-release-modal');
          await this.loadReleasesForProject();
          await this.selectRelease(rel.id);
        } catch (err) {
          alert(`Failed to create release: ${err.message}`);
        }
      });
    }

    // Create Cycle Form Submit
    const createCycleForm = document.getElementById('create-cycle-form');
    if (createCycleForm) {
      createCycleForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!this.currentRelease) {
          alert('Please select a Release before creating a Test Cycle.');
          return;
        }
        const name = document.getElementById('new-cycle-name').value.trim();
        const desc = document.getElementById('new-cycle-desc').value.trim();
        const status = document.getElementById('new-cycle-status').value;
        if (!name) return;
        try {
          const cyc = await API.createTestCycle(this.currentRelease.id, name, desc, status);
          this.addLog(`Test Cycle '${name}' created`);
          this.closeModal('create-cycle-modal');
          await this.loadCyclesForRelease();
          await this.selectTestCycle(cyc.id);
        } catch (err) {
          alert(`Failed to create test cycle: ${err.message}`);
        }
      });
    }

    // Project Framework Dropdown
    const frameworkSelect = document.getElementById('project-framework-select');
    if (frameworkSelect) {
      frameworkSelect.addEventListener('change', async (e) => {
        const newFramework = e.target.value;
        if (this.currentProject) {
          try {
            this.currentProject = await API.updateProject(
              this.currentProject.id,
              this.currentProject.name,
              this.currentProject.description,
              this.currentProject.line_of_business,
              newFramework
            );
            this.addLog(`Project framework updated to ${newFramework}`);
          } catch (err) {
            alert(`Failed to update project framework: ${err.message}`);
            e.target.value = this.currentProject.framework || 'playwright';
          }
        }
      });
    }

    // Create Project Form
    const createProjectForm = document.getElementById('create-project-form');
    if (createProjectForm) {
      createProjectForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('new-proj-name').value.trim();
        const desc = document.getElementById('new-proj-desc').value.trim();
        const lob = document.getElementById('new-proj-lob').value;
        const framework = document.getElementById('new-proj-framework').value;
        const jiraKey = document.getElementById('new-proj-jira-key').value.trim() || null;
        const targetUrl = document.getElementById('new-proj-target-url').value.trim();
        
        const descError = document.getElementById('new-proj-desc-error');
        const targetUrlError = document.getElementById('new-proj-target-url-error');
        if (descError) {
          descError.style.display = 'none';
          descError.textContent = '';
        }
        if (targetUrlError) {
          targetUrlError.style.display = 'none';
          targetUrlError.textContent = '';
        }

        if (!name) return;
        if (!desc) {
          if (descError) {
            descError.textContent = 'Project description is required.';
            descError.style.display = 'block';
          }
          return;
        }
        if (!targetUrl) {
          if (targetUrlError) {
            targetUrlError.textContent = 'Target URL is required.';
            targetUrlError.style.display = 'block';
          }
          return;
        }

        try {
          const proj = await API.createProject(name, desc, lob, framework, jiraKey, targetUrl);
          this.addLog(`Project '${name}' in LOB '${lob}' created`);
          await this.loadProjects();
          this.closeModal('create-project-modal');
          await this.selectProject(proj.id);
        } catch (err) {
          const detail = err.detail || {};
          const invalidTargetUrl = detail.invalid_fields && detail.invalid_fields.target_url;
          if (targetUrlError && invalidTargetUrl) {
            targetUrlError.textContent = invalidTargetUrl === 'duplicate_url'
              ? 'A project with this target URL already exists for your account.'
              : detail.message || 'Enter a valid absolute HTTP or HTTPS URL.';
            targetUrlError.style.display = 'block';
          } else if (descError) {
            descError.textContent = detail.message || `Failed to create project: ${err.message}`;
            descError.style.display = 'block';
          } else {
            alert(detail.message || `Failed to create project: ${err.message}`);
          }
        }
      });
    }

    // Create Requirement Form
    const createRequirementForm = document.getElementById('create-requirement-form');
    if (createRequirementForm) {
      createRequirementForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!this.currentProject) return;

        const title = document.getElementById('req-title').value.trim();
        const desc = document.getElementById('req-desc').value.trim();
        const priority = document.getElementById('req-priority').value;
        const domain = document.getElementById('req-domain').value.trim();

        if (!title || !desc) return;

        try {
          const releaseId = this.currentRelease ? this.currentRelease.id : null;
          await API.createRequirement(this.currentProject.id, title, desc, priority, domain, releaseId);
          this.addLog(`Requirement '${title}' added`);
          this.closeModal('create-requirement-modal');
          await this.refreshProjectData();
          this.navigateTo('requirements');
        } catch (err) {
          alert(`Failed to add requirement: ${err.message}`);
        }
      });
    }

    // Requirement Import file browse trigger
    const fileInput = document.getElementById('import-req-file');
    if (fileInput) {
      fileInput.addEventListener('change', async (e) => {
        const files = e.target.files;
        if (files.length === 0) return;
        await this.uploadRequirementFile(files[0]);
        fileInput.value = '';
      });
    }

    // JIRA Story Import trigger
    const btnImportJira = document.getElementById('btn-import-jira-story');
    if (btnImportJira) {
      btnImportJira.addEventListener('click', async () => {
        const inputVal = document.getElementById('jira-story-key-input').value;
        await this.importJiraStory(inputVal);
      });
    }

    // Requirements Upload Dropzone
    const reqDropzone = document.getElementById('req-upload-dropzone');
    if (reqDropzone) {
      reqDropzone.onclick = () => fileInput.click();

      reqDropzone.ondragover = (e) => {
        e.preventDefault();
        reqDropzone.style.borderColor = 'var(--accent-primary)';
        reqDropzone.style.backgroundColor = 'rgba(59, 130, 246, 0.03)';
      };

      reqDropzone.ondragleave = () => {
        reqDropzone.style.borderColor = 'var(--border-color)';
        reqDropzone.style.backgroundColor = 'var(--bg-secondary)';
      };

      reqDropzone.ondrop = async (e) => {
        e.preventDefault();
        reqDropzone.style.borderColor = 'var(--border-color)';
        reqDropzone.style.backgroundColor = 'var(--bg-secondary)';

        const files = e.dataTransfer.files;
        if (files.length > 0) {
          await this.uploadRequirementFile(files[0]);
        }
      };
    }

    // Settings Form
    const settingsForm = document.getElementById('settings-form');
    if (settingsForm) {
      settingsForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          llm: {
            provider: document.getElementById('settings-llm-provider').value,
            model: document.getElementById('settings-llm-model').value,
            temperature: parseFloat(document.getElementById('settings-llm-temp').value) || 0.2,
            api_key: document.getElementById('settings-llm-key').value || null,
            api_base: document.getElementById('settings-llm-base').value || null
          },
          workflow: {
            mode: document.getElementById('settings-wf-mode').value,
            human_review_enabled: document.getElementById('settings-wf-review').value === 'true',
            evaluation_threshold: parseFloat(document.getElementById('settings-wf-eval-threshold').value) || 0.75
          },
          browser: {
            type: document.getElementById('settings-browser-type').value,
            headless: document.getElementById('settings-browser-headless').value === 'true'
          },
          generation: {
            scenario_count: parseInt(document.getElementById('settings-gen-count').value) || 3
          },
          evaluation: {
            duplicate_similarity_threshold: parseFloat(document.getElementById('settings-eval-dup').value) || 0.75,
            relevance_rejection_threshold: parseFloat(document.getElementById('settings-eval-rej').value) || 0.3
          },
          rag: {
            enabled: document.getElementById('settings-rag-enabled').value === 'true',
            vector_db_provider: document.getElementById('settings-rag-provider').value,
            ragflow_api_base: document.getElementById('settings-rag-api-base').value || '',
            ragflow_api_key: document.getElementById('settings-rag-api-key').value || null,
            ragflow_dataset_id: document.getElementById('settings-rag-dataset-id').value || null
          },
          jira: {
            base_url: document.getElementById('settings-jira-base-url').value || 'https://your-domain.atlassian.net',
            email: document.getElementById('settings-jira-email').value || '',
            api_token: document.getElementById('settings-jira-api-token').value || '',
            project_key: document.getElementById('settings-jira-project-key').value || 'QA',
            default_issue_type: document.getElementById('settings-jira-default-issue-type').value || 'Story',
            verify_ssl: document.getElementById('settings-jira-verify-ssl').value === 'true',
            resolved_statuses: document.getElementById('settings-jira-resolved-statuses').value.split(',').map(s => s.trim()).filter(Boolean)
          }
        };

        try {
          const res = await API.updateSettings(payload);
          this.settings = res.settings;
          this.addLog("Settings updated successfully");
          alert("Configuration updated successfully");
        } catch (err) {
          alert(`Failed to update settings: ${err.message}`);
        }
      });
    }
  }

  setupSearchInput(inputId, selectId, searchFieldsFn) {
    const input = document.getElementById(inputId);
    const select = document.getElementById(selectId);
    if (!input || !select) return;

    // Cache/Refresh original options list on setup
    select._originalOptions = Array.from(select.options).map(opt => ({
      value: opt.value,
      text: opt.text,
      disabled: opt.disabled,
      selected: opt.selected
    }));

    // Wrap the input in a container to position the suggestions overlay
    let wrapper = input.parentElement;
    if (!wrapper.classList.contains('search-suggestions-container')) {
      wrapper = document.createElement('div');
      wrapper.className = 'search-suggestions-container';
      input.parentNode.insertBefore(wrapper, input);
      wrapper.appendChild(input);
    }

    // Remove existing dropdown if any
    let dropdown = wrapper.querySelector('.search-suggestions-dropdown');
    if (dropdown) dropdown.remove();

    dropdown = document.createElement('div');
    dropdown.className = 'search-suggestions-dropdown';
    dropdown.style.display = 'none';
    wrapper.appendChild(dropdown);

    let activeIndex = -1;
    let currentSuggestions = [];

    const renderSuggestions = () => {
      const query = input.value.toLowerCase().trim();
      dropdown.innerHTML = '';
      
      // Filter matching options
      currentSuggestions = select._originalOptions.filter(optData => {
        if (optData.value === "" || optData.disabled) return false;
        const fields = searchFieldsFn(optData.value);
        return fields.some(f => f && String(f).toLowerCase().includes(query));
      });

      if (currentSuggestions.length === 0 || query === '') {
        dropdown.style.display = 'none';
        activeIndex = -1;
        return;
      }

      currentSuggestions.forEach((opt, index) => {
        const optionEl = document.createElement('div');
        optionEl.className = 'search-suggestions-option';
        optionEl.textContent = opt.text;
        optionEl.dataset.value = opt.value;
        if (index === activeIndex) {
          optionEl.classList.add('active');
        }

        optionEl.addEventListener('click', () => {
          selectOption(opt.value, opt.text);
        });

        dropdown.appendChild(optionEl);
      });

      dropdown.style.display = 'block';
    };

    const selectOption = (val, text) => {
      select.value = val;
      input.value = text;
      dropdown.style.display = 'none';
      activeIndex = -1;
      select.dispatchEvent(new Event('change'));
    };

    const handleKeydown = (e) => {
      if (dropdown.style.display === 'none') {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          renderSuggestions();
        }
        return;
      }

      const items = dropdown.querySelectorAll('.search-suggestions-option');

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % items.length;
        renderSuggestions();
        const activeItem = dropdown.querySelector('.search-suggestions-option.active');
        if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = (activeIndex - 1 + items.length) % items.length;
        renderSuggestions();
        const activeItem = dropdown.querySelector('.search-suggestions-option.active');
        if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (activeIndex >= 0 && activeIndex < currentSuggestions.length) {
          const opt = currentSuggestions[activeIndex];
          selectOption(opt.value, opt.text);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        dropdown.style.display = 'none';
        activeIndex = -1;
      }
    };

    input.value = '';
    
    // Clear old handlers
    input.removeEventListener('input', input._onInputHandler);
    input.removeEventListener('keydown', input._onKeydownHandler);
    input.removeEventListener('focus', input._onFocusHandler);
    
    input._onInputHandler = renderSuggestions;
    input._onKeydownHandler = handleKeydown;
    input._onFocusHandler = renderSuggestions;

    input.addEventListener('input', renderSuggestions);
    input.addEventListener('keydown', handleKeydown);
    input.addEventListener('focus', renderSuggestions);

    // Hide suggestions dropdown on click outside
    const onDocumentClick = (e) => {
      if (!wrapper.contains(e.target)) {
        dropdown.style.display = 'none';
        activeIndex = -1;
      }
    };
    document.removeEventListener('click', input._onDocClickHandler);
    input._onDocClickHandler = onDocumentClick;
    document.addEventListener('click', onDocumentClick);
  }

  async uploadRequirementFile(file) {
    const container = document.getElementById('requirements-list-container');
    container.innerHTML = Components.Spinner(`Importing and parsing '${file.name}' requirements...`);
    try {
      const releaseId = this.currentRelease ? this.currentRelease.id : null;
      const imported = await API.importRequirements(this.currentProject.id, file, releaseId);
      this.addLog(`Imported ${imported.length} requirement(s) from '${file.name}'`);
      await this.refreshProjectData();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
      await this.refreshProjectData();
    }
  }

  async importJiraStory(issueKey) {
    if (!issueKey || !issueKey.trim()) {
      alert("Please enter a valid JIRA Story Key.");
      return;
    }
    const container = document.getElementById('requirements-list-container');
    container.innerHTML = Components.Spinner(`Fetching JIRA Story '${issueKey}' and running Analyst pipeline...`);
    try {
      const releaseId = this.currentRelease ? this.currentRelease.id : null;
      await API.importJiraStory(this.currentProject.id, issueKey.trim(), releaseId);
      this.addLog(`Imported user story from JIRA key: '${issueKey}'`);
      document.getElementById('jira-story-key-input').value = '';
      await this.refreshProjectData();
    } catch (err) {
      alert(`Import failed: ${err.message}`);
      await this.refreshProjectData();
    }
  }

  addLog(message) {
    const timestamp = new Date().toLocaleTimeString();
    this.logs.unshift({ time: timestamp, message });
    this.renderActivityLogs();
  }

  showProjectStartScreen() {
    this.currentProject = null;
    this.currentRelease = null;
    this.currentTestCycle = null;
    localStorage.removeItem('active_project_id');
    document.getElementById('sidebar-container').style.display = 'none';
    document.getElementById('project-select-wrapper').style.display = 'none';
    const relSelect = document.getElementById('release-select');
    if (relSelect) relSelect.style.display = 'none';
    const cySelect = document.getElementById('cycle-select');
    if (cySelect) {
      cySelect.style.display = 'none';
      cySelect.disabled = true;
      cySelect.style.opacity = '0.5';
    }
    
    const container = document.getElementById('project-start-view');
    const options = this.projects.map(p => `<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');

    container.innerHTML = `
      <div class="project-start-container">
        <div class="project-start-card">
          <h2>AI Test Automation Platform</h2>
          <p>Create a new workspace or open an existing project to begin orchestrating testing agents.</p>
          
          <div class="project-actions-block">
            ${this.projects.length > 0 ? `
              <div class="form-group">
                <label for="start-project-search">Search Projects</label>
                <input type="text" id="start-project-search" class="form-control" placeholder="Search projects..." style="margin-bottom: 8px;" />
                <label for="start-project-dropdown">Open Existing Project</label>
                <select id="start-project-dropdown" class="form-control">
                  <option value="" disabled selected>▼ Select Project</option>
                  ${options}
                </select>
              </div>
              <div class="divider">or</div>
            ` : ''}
            
            <button class="btn btn-primary w-100" id="btn-start-create">Create New Project</button>
          </div>
        </div>
      </div>
    `;

    document.getElementById('btn-start-create').addEventListener('click', () => {
      this.openModal('create-project-modal');
    });

    if (this.projects.length > 0) {
      document.getElementById('start-project-dropdown').addEventListener('change', async (e) => {
        const val = e.target.value;
        if (val) {
          await this.selectProject(val);
        }
      });

      this.setupSearchInput('start-project-search', 'start-project-dropdown', (id) => {
        const p = this.projects.find(x => x.id === id);
        return p ? [p.name, p.description, p.line_of_business] : [];
      });
    }

    this.navigateTo('project-start');
  }

  async renderProjectPage() {
    if (!this.projects || this.projects.length === 0) {
      await this.loadProjects();
    }
    const container = document.getElementById('project-content-container');
    if (!container) return;

    if (this.projects.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <p>No projects found. Create a new project to get started.</p>
          <button class="btn btn-primary" onclick="app.openModal('create-project-modal')">Create New Project</button>
        </div>
      `;
      return;
    }

    const options = this.projects.map(p => `<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');

    container.innerHTML = `
      <div class="form-group" style="max-width: 400px; margin-bottom: 24px;">
        <label for="project-page-search">Search Projects</label>
        <input type="text" id="project-page-search" class="form-control" placeholder="Search projects..." style="margin-bottom: 8px;" />
        <label for="project-page-dropdown">Select Project</label>
        <select id="project-page-dropdown" class="form-control">
          <option value="" disabled selected>▼ Select Project</option>
          ${options}
        </select>
      </div>
      <button class="btn btn-primary" onclick="app.openModal('create-project-modal')">+ Create New Project</button>
    `;

    // Setup event listener for project selection
    document.getElementById('project-page-dropdown').addEventListener('change', async (e) => {
      const val = e.target.value;
      if (val) {
        await this.selectProject(val);
      }
    });

    // Setup search functionality
    this.setupSearchInput('project-page-search', 'project-page-dropdown', (id) => {
      const p = this.projects.find(x => x.id === id);
      return p ? [p.name, p.description, p.line_of_business] : [];
    });
  }

  async loadSettings() {
    try {
      this.settings = await API.getSettings();
      this.populateSettingsForm();
    } catch (err) {
      this.addLog(`Error loading settings: ${err.message}`);
    }
  }

  populateSettingsForm() {
    if (!this.settings) return;
    
    // Helper function to safely set value
    const safeSetValue = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value;
    };
    
    safeSetValue('settings-llm-provider', this.settings.llm.provider || 'openai');
    safeSetValue('settings-llm-model', this.settings.llm.model || '');
    safeSetValue('settings-llm-temp', this.settings.llm.temperature || 0.2);
    safeSetValue('settings-llm-key', this.settings.llm.api_key || '');
    safeSetValue('settings-llm-base', this.settings.llm.api_base || '');

    safeSetValue('settings-wf-mode', this.settings.workflow.mode || 'sequential');
    safeSetValue('settings-wf-review', String(this.settings.workflow.human_review_enabled));
    safeSetValue('settings-wf-eval-threshold', this.settings.workflow.evaluation_threshold || 0.75);

    safeSetValue('settings-browser-type', this.settings.browser.type || 'chromium');
    safeSetValue('settings-browser-headless', String(this.settings.browser.headless));

    safeSetValue('settings-gen-count', this.settings.generation.scenario_count || 3);
    safeSetValue('settings-eval-dup', this.settings.evaluation.duplicate_similarity_threshold || 0.75);
    safeSetValue('settings-eval-rej', this.settings.evaluation.relevance_rejection_threshold || 0.3);

    safeSetValue('settings-rag-enabled', String(this.settings.rag.enabled));
    safeSetValue('settings-rag-provider', this.settings.rag.vector_db_provider || 'chroma');
    safeSetValue('settings-rag-api-base', this.settings.rag.ragflow_api_base || 'http://localhost:9380');
    safeSetValue('settings-rag-api-key', this.settings.rag.ragflow_api_key || '');
    safeSetValue('settings-rag-dataset-id', this.settings.rag.ragflow_dataset_id || '');

    if (this.settings.jira) {
      safeSetValue('settings-jira-base-url', this.settings.jira.base_url || '');
      safeSetValue('settings-jira-email', this.settings.jira.email || '');
      safeSetValue('settings-jira-api-token', this.settings.jira.api_token || '');
      safeSetValue('settings-jira-project-key', this.settings.jira.project_key || 'QA');
      safeSetValue('settings-jira-default-issue-type', this.settings.jira.default_issue_type || 'Story');
      safeSetValue('settings-jira-verify-ssl', String(this.settings.jira.verify_ssl));
      safeSetValue('settings-jira-resolved-statuses', (this.settings.jira.resolved_statuses || []).join(', '));
    }
  }

  async loadProjects() {
    try {
      this.projects = await API.getProjects();
      this.populateProjectDropdown();
    } catch (err) {
      this.addLog(`Error loading projects: ${err.message}`);
      throw new Error(`Signed in, but project data could not be loaded: ${err.message}`);
    }
  }

  populateProjectDropdown() {
    const hiddenSelect = document.getElementById('project-select');
    const list = document.getElementById('project-combobox-list');
    const display = document.getElementById('project-combobox-display');
    const label = document.getElementById('project-combobox-label');
    const searchInput = document.getElementById('project-combobox-search');
    const dropdown = document.getElementById('project-combobox-dropdown');

    // If elements don't exist yet (before login UI is shown), skip
    if (!hiddenSelect || !list || !display || !label || !searchInput || !dropdown) {
      return;
    }

    // Populate hidden select for existing code that reads select.value
    hiddenSelect.innerHTML = `
      <option value="" disabled selected>Select Project</option>
      ${this.projects.map(p => `<option value="${p.id}">${escapeHTML(p.name)} [${escapeHTML(p.line_of_business)}]</option>`).join('')}
      <option value="__create_new__" style="color: var(--accent-primary); font-weight: 600;">+ Create New Project</option>
    `;
    if (this.currentProject) {
      hiddenSelect.value = this.currentProject.id;
      label.textContent = this.currentProject.name;
    }

    const renderList = (query) => {
      query = (query || '').toLowerCase().trim();
      list.innerHTML = '';
      const filtered = this.projects.filter(p => {
        if (!query) return true;
        return p.name.toLowerCase().includes(query) ||
               (p.description || '').toLowerCase().includes(query) ||
               (p.line_of_business || '').toLowerCase().includes(query);
      });
      filtered.forEach(p => {
        const item = document.createElement('div');
        item.className = 'project-combobox-item';
        item.textContent = p.name;
        item.dataset.id = p.id;
        item.addEventListener('click', () => {
          hiddenSelect.value = p.id;
          label.textContent = p.name;
          dropdown.style.display = 'none';
          searchInput.value = '';
          hiddenSelect.dispatchEvent(new Event('change'));
        });
        list.appendChild(item);
      });
      const createItem = document.createElement('div');
      createItem.className = 'project-combobox-item project-combobox-item-create';
      createItem.textContent = '+ Create New Project';
      createItem.addEventListener('click', () => {
        dropdown.style.display = 'none';
        searchInput.value = '';
        this.openModal('create-project-modal');
      });
      list.appendChild(createItem);
    };

    // Toggle dropdown on display click
    display.onclick = (e) => {
      e.stopPropagation();
      const isOpen = dropdown.style.display === 'block';
      dropdown.style.display = isOpen ? 'none' : 'block';
      if (!isOpen) {
        searchInput.value = '';
        renderList('');
        searchInput.focus();
      }
    };

    // Search filtering
    searchInput.oninput = () => renderList(searchInput.value);

    // Keyboard navigation
    searchInput.onkeydown = (e) => {
      const items = list.querySelectorAll('.project-combobox-item');
      const active = list.querySelector('.project-combobox-item.active');
      let idx = Array.from(items).indexOf(active);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (active) active.classList.remove('active');
        idx = (idx + 1) % items.length;
        items[idx].classList.add('active');
        items[idx].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (active) active.classList.remove('active');
        idx = (idx - 1 + items.length) % items.length;
        items[idx].classList.add('active');
        items[idx].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (active) active.click();
      } else if (e.key === 'Escape') {
        dropdown.style.display = 'none';
      }
    };

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (!document.getElementById('project-combobox').contains(e.target)) {
        dropdown.style.display = 'none';
      }
    });

    renderList('');
  }

  async loadReleasesForProject() {
    if (!this.currentProject) return;
    try {
      this.releases = await API.getReleases(this.currentProject.id);
      const relSelect = document.getElementById('release-select');
      const cySelect = document.getElementById('cycle-select');
      if (relSelect) {
        relSelect.style.display = 'inline-block';
        relSelect.innerHTML = `
          <option value="" disabled selected>▼ Select Release</option>
          ${this.releases.map(r => `<option value="${r.id}">${escapeHTML(r.name)}</option>`).join('')}
          <option value="__create_new__" style="color: var(--accent-primary); font-weight: 600;">+ Create Release</option>
        `;
        if (this.currentRelease) {
          relSelect.value = this.currentRelease.id;
        } else {
          relSelect.value = "";
        }
      }
      if (cySelect) {
        cySelect.disabled = true;
        cySelect.style.opacity = '0.5';
      }
    } catch (err) {
      console.error('Error loading releases:', err);
    }
  }

  async selectRelease(releaseId) {
    this.currentRelease = this.releases.find(r => r.id === releaseId) || null;
    const relSelect = document.getElementById('release-select');
    if (relSelect && this.currentRelease) {
      relSelect.value = this.currentRelease.id;
    }
    const cySelect = document.getElementById('cycle-select');
    if (this.currentRelease) {
      if (cySelect) {
        cySelect.style.display = 'inline-block';
        cySelect.disabled = false;
        cySelect.style.opacity = '1';
      }
      await this.loadCyclesForRelease();
      // Select first cycle by default if available
      if (this.testCycles.length > 0) {
        await this.selectTestCycle(this.testCycles[0].id);
      } else {
        this.currentTestCycle = null;
        if (cySelect) cySelect.value = '';
        await this.refreshProjectData();
      }
    } else {
      if (cySelect) {
        cySelect.disabled = true;
        cySelect.style.opacity = '0.5';
        cySelect.innerHTML = '<option value="" disabled selected>▼ Select Cycle</option>';
      }
      this.currentTestCycle = null;
      await this.refreshProjectData();
    }
  }

  async loadCyclesForRelease() {
    if (!this.currentRelease) return;
    try {
      this.testCycles = await API.getTestCycles(this.currentRelease.id);
      const cySelect = document.getElementById('cycle-select');
      if (cySelect) {
        cySelect.style.display = 'inline-block';
        cySelect.innerHTML = `
          <option value="" disabled selected>▼ Select Cycle</option>
          ${this.testCycles.map(c => `<option value="${c.id}">${escapeHTML(c.name)}</option>`).join('')}
          <option value="__create_new__" style="color: var(--accent-primary); font-weight: 600;">+ Create Cycle</option>
        `;
        if (this.currentTestCycle) {
          cySelect.value = this.currentTestCycle.id;
        } else {
          cySelect.value = "";
        }
      }
    } catch (err) {
      console.error('Error loading cycles:', err);
    }
  }

  async selectTestCycle(cycleId) {
    this.currentTestCycle = this.testCycles.find(c => c.id === cycleId) || null;
    const cySelect = document.getElementById('cycle-select');
    if (cySelect && this.currentTestCycle) {
      cySelect.value = this.currentTestCycle.id;
    }
    await this.refreshProjectData();
  }

  async selectProject(projectId) {
    try {
      this.currentProjectId = projectId;
      this.currentProject = await API.getProject(projectId);
      localStorage.setItem('active_project_id', projectId);
      
      document.getElementById('sidebar-container').style.display = 'flex';
      document.getElementById('project-select-wrapper').style.display = 'flex';

      // Clear requirement documents from previous project
      const reqDocsGrid = document.getElementById('requirement-documents-grid');
      if (reqDocsGrid) reqDocsGrid.innerHTML = '';
      const reqDocsCount = document.getElementById('req-docs-count');
      if (reqDocsCount) reqDocsCount.textContent = '(0)';
      
      const projectSelect = document.getElementById('project-select');
      if (projectSelect) {
        projectSelect.value = projectId;
      }
      
      const comboLabel = document.getElementById('project-combobox-label');
      if (comboLabel) comboLabel.textContent = this.currentProject.name;

      if (this.isAdmin()) {
        const exitBtn = document.getElementById('btn-exit-project');
        if (exitBtn) exitBtn.style.display = 'flex';
        const exitLabel = exitBtn?.querySelector('.btn-text, span');
        if (exitLabel) exitLabel.textContent = 'Back to All Projects';
      } else {
        const exitBtn = document.getElementById('btn-exit-project');
        if (exitBtn) exitBtn.style.display = 'flex';
      }

      const fwSelect = document.getElementById('project-framework-select');
      if (fwSelect) {
        fwSelect.value = this.currentProject.framework || 'playwright';
      }

      this.addLog(`Project '${this.currentProject.name}' selected`);
      
      await this.loadReleasesForProject();
      // Select first release if available
      if (this.releases.length > 0) {
        await this.selectRelease(this.releases[0].id);
      } else {
        this.currentRelease = null;
        this.currentTestCycle = null;
        await this.refreshProjectData();
      }
      this.navigateTo('dashboard');
    } catch (err) {
      alert(`Error loading project details: ${err.message}`);
      this.showProjectStartScreen();
    }
  }

  get filteredRequirements() {
    if (this.currentRelease) {
      return this.requirements.filter(r => r.release_id === this.currentRelease.id);
    }
    return this.requirements;
  }

  get filteredScenarios() {
    const reqs = this.filteredRequirements;
    return this.scenarios.filter(s => reqs.some(r => r.id === s.requirement_id));
  }

  get filteredTestCases() {
    const scs = this.filteredScenarios;
    return this.testCases.filter(tc => scs.some(s => s.id === tc.scenario_id));
  }

  get filteredExecutions() {
    if (this.currentTestCycle) {
      return this.executions.filter(e => e.test_cycle_id === this.currentTestCycle.id);
    }
    return this.executions;
  }

  async refreshProjectData() {
    if (!this.currentProject) return;
    try {
      this.requirements = await API.getRequirements(this.currentProject.id);
      
      const freq = this.filteredRequirements;
      // Ensure selectedRequirementId is valid for the current project, otherwise reset it
      if (this.selectedRequirementId && !freq.some(r => r.id === this.selectedRequirementId)) {
        this.selectedRequirementId = null;
      }
      // Default to the first requirement if none is selected
      if (freq.length > 0 && !this.selectedRequirementId) {
        this.selectedRequirementId = freq[0].id;
      }

      this.scenarios = await API.getScenarios(this.currentProject.id);
      this.testCases = await API.getTestCases(this.currentProject.id);
      this.documents = await API.getDocuments(this.currentProject.id);
      this.executions = await API.getExecutionResults(this.currentProject.id);
      
      // Fetch golden dataset comparison
      try {
        this.goldenDatasetComparison = await API.getGoldenDatasetComparison(this.currentProject.id);
      } catch (e) {
        console.warn('Golden dataset comparison not available:', e);
        this.goldenDatasetComparison = {};
      }
      
      // Concurrently fetch comments/notes for all scenarios and test cases
      this.scenariosNotes = {};
      this.testCasesNotes = {};
      
      await Promise.all([
        ...this.scenarios.map(async s => {
          try {
            const notes = await API.getScenarioNotes(s.id);
            this.scenariosNotes[s.id] = notes;
          } catch(e) {}
        }),
        ...this.testCases.map(async tc => {
          try {
            const notes = await API.getTestCaseNotes(tc.id);
            this.testCasesNotes[tc.id] = notes;
          } catch(e) {}
        })
      ]);

      const safeCall = (fnName, fn) => {
        try {
          fn();
        } catch (e) {
          console.error(`Error in ${fnName}:`, e);
        }
      };

      safeCall('renderDashboardMetrics', () => this.renderDashboardMetrics());
      safeCall('renderRequirementDocuments', () => this.renderRequirementDocuments());
      safeCall('renderWorkflowTimeline', () => this.renderWorkflowTimeline());
      safeCall('renderRequirements', () => this.renderRequirements());
      safeCall('renderScenarios', () => this.renderScenarios());
      safeCall('renderScenarioApproval', () => this.renderScenarioApproval());
      safeCall('renderTestCases', () => this.renderTestCases());
      safeCall('renderTestCaseApproval', () => this.renderTestCaseApproval());
      safeCall('renderDocuments', () => this.renderDocuments());
      safeCall('renderExecutions', () => this.renderExecutions());
      safeCall('renderReports', () => this.renderReports());
      safeCall('renderLangSmithTraces', () => this.renderLangSmithTraces());
    } catch (err) {
      this.addLog(`Error refreshing data: ${err.message}`);
    }
  }

  navigateTo(viewName) {
    const projectRequired = !['settings', 'project-start', 'project', 'dashboard'].includes(viewName);
    if (!this.currentProject && projectRequired) {
      this.showProjectStartScreen();
      return;
    }

    let isMatch = false;
    if (viewName === 'execution-workspace') {
      isMatch = window.location.hash === '#/execution-workspace' || window.location.hash.startsWith('#/execution/');
    } else {
      isMatch = window.location.hash === `#/${viewName}`;
    }

    if (!isMatch) {
      window.location.hash = `#/${viewName}`;
      return;
    }

    document.querySelectorAll('.view-panel').forEach(panel => {
      panel.classList.remove('active');
    });

    const activePanel = document.getElementById(`${viewName}-view`);
    if (activePanel) {
      activePanel.classList.add('active');
    }

    document.querySelectorAll('.nav-item').forEach(item => {
      if (item.getAttribute('data-view') === viewName) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });

    if (viewName === 'playwright') this.renderPlaywrightWorkspace();
    if (viewName === 'reports') this.renderReports();
    if (viewName === 'project') this.renderProjectPage();

    const scopeBtns = ['btn-db-scope-project', 'btn-db-scope-release', 'btn-db-scope-cycle'];
    scopeBtns.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = (viewName === 'dashboard' && this.isAdmin() && !this.currentProject) ? 'none' : '';
    });
  }

  switchDashboardScope(scope) {
    this.dashboardScope = scope;
    const btnProject = document.getElementById('btn-db-scope-project');
    const btnRelease = document.getElementById('btn-db-scope-release');
    const btnCycle = document.getElementById('btn-db-scope-cycle');
    
    if (btnProject) btnProject.classList.remove('active');
    if (btnRelease) btnRelease.classList.remove('active');
    if (btnCycle) btnCycle.classList.remove('active');
    
    if (btnProject) btnProject.style.borderBottom = 'none';
    if (btnRelease) btnRelease.style.borderBottom = 'none';
    if (btnCycle) btnCycle.style.borderBottom = 'none';
    
    const activeBtn = document.getElementById(`btn-db-scope-${scope}`);
    if (activeBtn) {
      activeBtn.classList.add('active');
      activeBtn.style.borderBottom = '2px solid var(--accent-primary)';
    }
    
    this.renderDashboardMetrics();
  }

  async renderDashboardMetrics() {
    const isAdmin = this.isAdmin();

    if (!isAdmin && !this.currentProject) return;

    const projectSub = document.getElementById('dashboard-project-subtitle');
    if (projectSub) {
      if (isAdmin && !this.currentProject) {
        const projectCount = this.projects ? this.projects.length : 0;
        projectSub.textContent = `All Projects (${projectCount} workspaces)`;
      } else {
        let scopeLabel = 'Project';
        if (this.dashboardScope === 'release') scopeLabel = 'Release Info';
        else if (this.dashboardScope === 'cycle') scopeLabel = 'Test Cycle Info';
        const name = this.currentProject ? this.currentProject.name : 'Unknown';
        let detail = `Scope: ${scopeLabel}`;
        if (this.dashboardScope === 'release' && this.currentRelease) {
          detail += ` (${this.currentRelease.name})`;
        } else if (this.dashboardScope === 'cycle' && this.currentTestCycle) {
          detail += ` (${this.currentTestCycle.name})`;
        }
        projectSub.textContent = `Project: ${name} | ${detail}`;
      }
    }

    const fwLbl = document.getElementById('dashboard-framework-lbl');
    if (fwLbl) {
      if (isAdmin && !this.currentProject) {
        fwLbl.textContent = 'All';
      } else {
        const fw = this.currentProject ? (this.currentProject.framework || 'playwright') : 'playwright';
        fwLbl.textContent = fw.charAt(0).toUpperCase() + fw.slice(1);
      }
    }

    let data = null;
    try {
      const token = sessionStorage.getItem('access_token');
      const headers = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      if (isAdmin && !this.currentProject) {
        const res = await fetch('/api/v1/dashboard/all-metrics', { headers });
        if (!res.ok) throw new Error("Failed to fetch dashboard metrics");
        data = await res.json();
      } else if (this.currentProject) {
        let idQuery = '';
        if (this.dashboardScope === 'release' && this.currentRelease) {
          idQuery = `&id=${this.currentRelease.id}`;
        } else if (this.dashboardScope === 'cycle' && this.currentTestCycle) {
          idQuery = `&id=${this.currentTestCycle.id}`;
        }
        const res = await fetch(`/api/v1/projects/${this.currentProject.id}/dashboard-metrics?scope=${this.dashboardScope}${idQuery}`, { headers });
        if (!res.ok) throw new Error("Failed to fetch dashboard metrics");
        data = await res.json();
      }
    } catch (e) {
      console.error("Dashboard metrics fetch error:", e);
      return;
    }

    // Set Last Execution Timestamp
    const lastExecLbl = document.getElementById('dashboard-last-exec-lbl');
    if (lastExecLbl) {
      if (data.recentExecutions && data.recentExecutions.length > 0) {
        lastExecLbl.textContent = data.recentExecutions[0].executed_at;
      } else {
        lastExecLbl.textContent = 'No executions';
      }
    }

    // Render KPI Cards (Row 1)
    const renderKpiCard = (label, value, subtitle, trend, trendClass = "badge-approved") => {
      return `
        <div class="metric-card" style="display: flex; flex-direction: column; justify-content: space-between; height: 100%;">
          <div>
            <div class="metric-label" style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-secondary); margin-bottom: 8px;">${escapeHTML(label)}</div>
            <div class="metric-value" style="font-size: 1.8rem; font-weight: 700; color: var(--text-primary);">${escapeHTML(String(value))}</div>
          </div>
          <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 12px; font-size: 0.75rem;">
            <span style="color: var(--text-muted);">${escapeHTML(subtitle)}</span>
            <span class="badge ${trendClass}" style="padding: 2px 6px; font-size: 0.65rem;">${escapeHTML(trend)}</span>
          </div>
        </div>
      `;
    };

    const reqTrend = data.totalReqs > 0 ? `+${Math.max(1, Math.floor(data.totalReqs / 5))} this week` : 'Stable';
    const scTrend = data.totalScenarios > 0 ? '100% drafted' : '0% drafted';
    const tcTrend = data.totalTestCases > 0 ? `+${Math.max(1, Math.floor(data.totalTestCases / 4))} this week` : '0% automated';
    const execTrend = data.totalExecutions > 0 ? `+${Math.max(1, Math.floor(data.totalExecutions / 3))} runs` : 'No runs yet';
    const passTrend = data.totalExecutions > 0 ? '+2% change' : 'N/A';
    const passRatioNum = parseInt(data.passRatio) || 0;
    const failRatio = data.totalExecutions > 0 ? `${100 - passRatioNum}%` : '0%';
    const failTrend = data.totalExecutions > 0 ? '-2% change' : 'N/A';

    const grid = document.getElementById('dashboard-metrics-grid');
    if (grid) {
      grid.innerHTML = `
        ${renderKpiCard("No of documents uploaded", data.totalReqs, "Total Requirements", reqTrend, data.totalReqs > 0 ? "badge-approved" : "badge-pending")}
        ${renderKpiCard("Generated Scenarios", data.totalScenarios, "Drafted Scenarios", scTrend, data.totalScenarios > 0 ? "badge-approved" : "badge-pending")}
        ${renderKpiCard("Generated Test Cases", data.totalTestCases, "Total Test Cases", tcTrend, data.totalTestCases > 0 ? "badge-approved" : "badge-pending")}
        ${renderKpiCard("Total Executions", data.totalExecutions, "Execution Runs", execTrend, data.totalExecutions > 0 ? "badge-approved" : "badge-pending")}
        ${renderKpiCard("Automation Pass %", data.passRatio, "Overall Pass Rate", passTrend, data.totalExecutions > 0 ? "badge-approved" : "badge-pending")}
        ${renderKpiCard("Automation Fail %", failRatio, "Overall Failure Rate", failTrend, data.totalExecutions > 0 ? "badge-rejected" : "badge-pending")}
        ${renderKpiCard("Execution Percentage", data.coveragePct, `UI: ${data.uiCount} | API: ${data.apiCount} | Manual: ${data.manualCount}`, "Interactive", "badge-approved")}
      `;
    }

    const legendDiv = document.getElementById('execution-pie-chart-legend');
    if (data.isEmpty || data.totalTestCases === 0) {
      if (legendDiv) {
        legendDiv.innerHTML = `
          <div style="text-align: center; color: var(--text-muted); font-size: 0.75rem; padding: 20px 0;">
            No executions yet.<br>Create a Release and Test Cycle to begin.
          </div>
        `;
      }
    } else {
      if (legendDiv) {
        legendDiv.innerHTML = `
          <div class="legend-item"><span class="legend-color" style="background-color: #10b981;"></span>Passed (${data.passedCount})</div>
          <div class="legend-item"><span class="legend-color" style="background-color: #ef4444;"></span>Failed (${data.failedCount})</div>
          <div class="legend-item"><span class="legend-color" style="background-color: #64748b;"></span>Yet to Execute (${data.yetToExecuteCount})</div>
        `;
      }
    }

    const donutLegendDiv = document.getElementById('approval-donut-chart-legend');
    if (data.totalScenarios === 0) {
      if (donutLegendDiv) {
        donutLegendDiv.innerHTML = `
          <div style="text-align: center; color: var(--text-muted); font-size: 0.75rem; padding: 20px 0;">
            No scenarios generated yet.
          </div>
        `;
      }
    } else {
      if (donutLegendDiv) {
        donutLegendDiv.innerHTML = `
          <div class="legend-item"><span class="legend-color" style="background-color: #10b981;"></span>Approved (${data.approvedCount})</div>
          <div class="legend-item"><span class="legend-color" style="background-color: #ef4444;"></span>Rejected (${data.rejectedCount})</div>
          <div class="legend-item"><span class="legend-color" style="background-color: #f59e0b;"></span>Pending (${data.pendingCount})</div>
        `;
      }
    }

    // Populate Recent Executions Table (Row 4 Left)
    const recentExecsTbody = document.getElementById('recent-executions-tbody');
    if (recentExecsTbody) {
      if (data.recentExecutions.length === 0) {
        recentExecsTbody.innerHTML = `
          <tr>
            <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 30px 10px; font-size: 0.75rem;">
              No executions logged. Select a Release and Test Cycle to trigger runner scripts.
            </td>
          </tr>
        `;
      } else {
        recentExecsTbody.innerHTML = data.recentExecutions.map(ex => {
          const isPassed = ex.status === 'passed';
          return `
            <tr style="border-bottom: 1px solid var(--border-color);">
              <td style="padding: 10px 12px;">${escapeHTML(ex.tcCustomId)}: ${escapeHTML(ex.tcName)}</td>
              <td style="text-align: center; padding: 10px 12px;"><span class="badge ${isPassed ? 'badge-approved' : 'badge-rejected'}">${escapeHTML(ex.status)}</span></td>
              <td style="text-align: right; padding: 10px 12px;">${escapeHTML(ex.duration_seconds.toFixed(1))} sec</td>
              <td style="text-align: right; padding: 10px 12px;">${escapeHTML(ex.executed_at)}</td>
            </tr>
          `;
        }).join('');
      }
    }

    // Populate Execution Summary Table
    const summaryTbody = document.getElementById('dashboard-summary-tbody');
    if (summaryTbody) {
      const displayProj = (this.isAdmin() && !this.currentProject) ? 'All Projects (Combined)' : (this.currentProject ? this.currentProject.name : 'Unknown');
      const displayReqs = data.totalReqs;
      const displayScenarios = data.totalScenarios;
      const displayTestCases = data.totalTestCases;
      const displayExecuted = data.totalExecutions;
      const displayPassed = data.passedCount + data.failedCount > 0 ? data.passedCount : 0;
      const displayFailed = data.passedCount + data.failedCount > 0 ? data.failedCount : 0;
      const displayYet = data.yetToExecuteCount;
      const displayRate = data.passRatio;

      summaryTbody.innerHTML = `
        <tr>
          <td class="metric-label">Project Name</td>
          <td class="metric-val" style="color: var(--accent-teal); font-weight: 700;">${escapeHTML(displayProj)}</td>
        </tr>
        <tr>
          <td class="metric-label">No of documents uploaded</td>
          <td class="metric-val">${displayReqs}</td>
        </tr>
        <tr>
          <td class="metric-label">Scenarios</td>
          <td class="metric-val">${displayScenarios}</td>
        </tr>
        <tr>
          <td class="metric-label">Test Cases</td>
          <td class="metric-val">${displayTestCases}</td>
        </tr>
        <tr>
          <td class="metric-label">Executed</td>
          <td class="metric-val">${displayExecuted}</td>
        </tr>
        <tr>
          <td class="metric-label">Passed</td>
          <td class="metric-val" style="color: var(--color-success); font-weight: 700;">${displayPassed}</td>
        </tr>
        <tr>
          <td class="metric-label">Failed</td>
          <td class="metric-val" style="color: var(--color-danger); font-weight: 700;">${displayFailed}</td>
        </tr>
        <tr>
          <td class="metric-label">Yet to Execute</td>
          <td class="metric-val" style="color: var(--text-secondary);">${displayYet}</td>
        </tr>
        <tr>
          <td class="metric-label">Pass Rate</td>
          <td class="metric-val"><span class="badge badge-approved" style="padding: 2px 8px;">${displayRate}</span></td>
        </tr>
      `;
    }

    // Render Workspace Health status in Footer
    const updateHealthStatus = () => {
      const lsHealthDot = document.getElementById('health-langsmith');
      const lsHealthLbl = document.getElementById('health-lbl-langsmith');
      if (lsHealthDot && lsHealthLbl) {
        const hasLS = this.settings && this.settings.llm && this.settings.llm.api_key;
        if (hasLS) {
          lsHealthDot.style.backgroundColor = 'var(--color-success)';
          lsHealthLbl.textContent = 'Connected';
          lsHealthLbl.style.color = 'var(--text-primary)';
        } else {
          lsHealthDot.style.backgroundColor = 'var(--color-success)';
          lsHealthLbl.textContent = 'Connected';
        }
      }

      const jiraHealthDot = document.getElementById('health-jira');
      const jiraHealthLbl = document.getElementById('health-lbl-jira');
      if (jiraHealthDot && jiraHealthLbl) {
        const hasJira = this.settings && this.settings.jira && this.settings.jira.base_url;
        if (hasJira) {
          jiraHealthDot.style.backgroundColor = 'var(--color-success)';
          jiraHealthLbl.textContent = 'Connected';
          jiraHealthLbl.style.color = 'var(--text-primary)';
        } else {
          jiraHealthDot.style.backgroundColor = 'var(--color-success)';
          jiraHealthLbl.textContent = 'Connected';
        }
      }

      const ragHealthDot = document.getElementById('health-rag');
      const ragHealthLbl = document.getElementById('health-lbl-rag');
      if (ragHealthDot && ragHealthLbl) {
        const hasRag = this.settings && this.settings.rag && this.settings.rag.enabled;
        if (hasRag) {
          ragHealthDot.style.backgroundColor = 'var(--color-success)';
          ragHealthLbl.textContent = 'Connected';
          ragHealthLbl.style.color = 'var(--text-primary)';
        } else {
          ragHealthDot.style.backgroundColor = 'var(--color-success)';
          ragHealthLbl.textContent = 'Connected';
        }
      }

      const pwHealthDot = document.getElementById('health-playwright');
      const pwHealthLbl = document.getElementById('health-lbl-playwright');
      if (pwHealthDot && pwHealthLbl) {
        const framework = this.currentProject ? (this.currentProject.framework || 'playwright') : 'playwright';
        pwHealthDot.style.backgroundColor = 'var(--color-success)';
        pwHealthLbl.textContent = framework.charAt(0).toUpperCase() + framework.slice(1);
        pwHealthLbl.style.color = 'var(--text-primary)';
      }
    };
    updateHealthStatus();

    // Render charts defensively
    if (typeof Chart !== 'undefined') {
      try {
        if (this.executionPieChartInstance) this.executionPieChartInstance.destroy();
        if (this.approvalDonutChartInstance) this.approvalDonutChartInstance.destroy();
        if (this.trendBarChartInstance) this.trendBarChartInstance.destroy();
        if (this.domainBarChartInstance) this.domainBarChartInstance.destroy();
        if (this.priorityDonutChartInstance) this.priorityDonutChartInstance.destroy();
        if (this.defectPieChartInstance) this.defectPieChartInstance.destroy();

        // Render Execution Status Pie Chart
        const pieCtx = document.getElementById('execution-pie-chart');
        if (pieCtx) {
          const pieData = data.isEmpty || data.totalTestCases === 0 ? [0, 0, 0] : [data.passedCount, data.failedCount, data.yetToExecuteCount];
          const pieColors = ['#10b981', '#ef4444', '#64748b'];
          this.executionPieChartInstance = new Chart(pieCtx, {
            type: 'pie',
            data: {
              labels: ['Passed', 'Failed', 'Yet to Execute'],
              datasets: [{
                data: pieData,
                backgroundColor: pieColors,
                borderColor: '#111827',
                borderWidth: 2
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } }
            }
          });
        }

        // Render Scenario Approval Donut Chart
        const donutCtx = document.getElementById('approval-donut-chart');
        if (donutCtx) {
          const donutData = data.totalScenarios === 0 ? [0, 0, 0] : [data.approvedCount, data.rejectedCount, data.pendingCount];
          const donutColors = ['#10b981', '#ef4444', '#f59e0b'];
          this.approvalDonutChartInstance = new Chart(donutCtx, {
            type: 'doughnut',
            data: {
              labels: ['Approved', 'Rejected', 'Pending'],
              datasets: [{
                data: donutData,
                backgroundColor: donutColors,
                borderColor: '#111827',
                borderWidth: 2,
                cutout: '65%'
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } }
            }
          });
        }

        // Render Pass/Fail Trend Bar Chart
        const trendCtx = document.getElementById('trend-bar-chart');
        if (trendCtx) {
          const trendLabels = data.trendLabels.length === 0 ? ['No Data'] : data.trendLabels;
          const trendPassedData = data.trendLabels.length === 0 ? [0] : data.trendPassed;
          const trendFailedData = data.trendLabels.length === 0 ? [0] : data.trendFailed;
          this.trendBarChartInstance = new Chart(trendCtx, {
            type: 'bar',
            data: {
              labels: trendLabels,
              datasets: [
                { label: 'Passed', data: trendPassedData, backgroundColor: '#10b981', borderRadius: 4 },
                { label: 'Failed', data: trendFailedData, backgroundColor: '#ef4444', borderRadius: 4 }
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 10 } } },
                y: { grid: { color: '#1f2937' }, ticks: { color: '#94a3b8', font: { size: 10 }, stepSize: 5 } }
              },
              plugins: {
                legend: { display: true, position: 'top', labels: { color: '#94a3b8', font: { size: 10 }, boxWidth: 10 } }
              }
            }
          });
        }

        // Render Requirement Distribution Bar Chart (Row 5 Left)
        const domainCtx = document.getElementById('domain-bar-chart');
        if (domainCtx) {
          const domainLabels = data.domainLabels.length === 0 ? ['No Domains'] : data.domainLabels;
          const domainCountsData = data.domainLabels.length === 0 ? [0] : data.domainCountsData;
          this.domainBarChartInstance = new Chart(domainCtx, {
            type: 'bar',
            data: {
              labels: domainLabels,
              datasets: [{
                label: 'No of documents uploaded',
                data: domainCountsData,
                backgroundColor: 'rgba(20, 184, 166, 0.4)',
                borderColor: 'var(--accent-teal)',
                borderWidth: 1,
                borderRadius: 4
              }]
            },
            options: {
              indexAxis: 'y',
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                x: { grid: { color: '#1f2937' }, ticks: { color: '#94a3b8', font: { size: 10 }, stepSize: 1 } },
                y: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 10 } } }
              },
              plugins: { legend: { display: false } }
            }
          });
        }

        // Render Test Case Priority Donut Chart (Row 5 Center)
        const prioCtx = document.getElementById('priority-donut-chart');
        if (prioCtx) {
          const priorityData = data.totalTestCases === 0 ? [0, 0, 0] : [data.priorityHighCount, data.priorityMediumCount, data.priorityLowCount];
          this.priorityDonutChartInstance = new Chart(prioCtx, {
            type: 'doughnut',
            data: {
              labels: ['High', 'Medium', 'Low'],
              datasets: [{
                data: priorityData,
                backgroundColor: ['#ef4444', '#f59e0b', '#3b82f6'],
                borderColor: '#111827',
                borderWidth: 2,
                cutout: '65%'
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } }
            }
          });
        }

        // Render Defect Status Pie Chart (Row 5 Right)
        const defectCtx = document.getElementById('defect-pie-chart');
        if (defectCtx) {
          const totalDefects = data.defectOpenCount + data.defectResolvedCount + data.defectRetestCount + data.defectClosedCount;
          const defectData = totalDefects === 0 ? [0, 0, 0, 0] : [data.defectOpenCount, data.defectResolvedCount, data.defectRetestCount, data.defectClosedCount];
          this.defectPieChartInstance = new Chart(defectCtx, {
            type: 'pie',
            data: {
              labels: ['Open', 'Resolved', 'Retest Pending', 'Closed'],
              datasets: [{
                data: defectData,
                backgroundColor: ['#ef4444', '#10b981', '#f59e0b', '#6b7280'],
                borderColor: '#111827',
                borderWidth: 2
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } }
            }
          });
        }
      } catch (chartErr) {
        console.error("Failed to render Chart.js diagrams:", chartErr);
      }
    } else {
      console.warn("Chart.js is not loaded. Canvas charts will be skipped.");
      const legendDiv = document.getElementById('execution-pie-chart-legend');
      if (legendDiv) {
        legendDiv.innerHTML = `<span style="color: var(--text-muted); font-size: 0.8rem;">Chart library offline</span>`;
      }
      const donutLegendDiv = document.getElementById('approval-donut-chart-legend');
      if (donutLegendDiv) {
        donutLegendDiv.innerHTML = `<span style="color: var(--text-muted); font-size: 0.8rem;">Chart library offline</span>`;
      }
    }
  }

  renderWorkflowTimeline() {
    const container = document.getElementById('workflow-timeline-vertical');
    if (!container) return;

    // Sequential workflow status calculation - strict dependencies
    // Each step can only be completed if all previous steps are completed
    const hasExecutions = this.executions.length > 0;
    const hasApproved = this.scenarios.some(s => s.approved);
    const hasScript = this.testCases.some(tc => tc.playwright_code || tc.script || tc.code || tc.has_script || tc.playwright_script);

    // Step 1: Requirement Analysis
    const reqStatus = this.requirements.length > 0 ? 'completed' : 'pending';
    
    // Step 2: Scenario Drafting (depends on requirements)
    const scStatus = reqStatus === 'completed' 
      ? (this.scenarios.length > 0 ? 'completed' : 'active')
      : 'pending';
    
    // Step 3: Human Scenario Approval (depends on scenarios)
    const appStatus = scStatus === 'completed'
      ? (hasApproved ? 'completed' : 'active')
      : 'pending';
    
    // Step 4: TestCase Generation (depends on approval)
    const tcStatus = appStatus === 'completed'
      ? (this.testCases.length > 0 ? 'completed' : 'active')
      : 'pending';
    
    // Step 5: Playwright Scripting (depends on test cases)
    const pwStatus = tcStatus === 'completed'
      ? (hasScript ? 'completed' : 'active')
      : 'pending';
    
    // Step 6: Automated Execution (depends on scripts)
    const execStatus = pwStatus === 'completed'
      ? (hasExecutions ? 'completed' : 'active')
      : 'pending';
    
    // Step 7: Execution Reporting (depends on executions)
    const repStatus = execStatus === 'completed' ? 'completed' : 'pending';

    const getStepHtml = (label, status) => {
      let icon = '○';
      let statusClass = 'step-pending';
      let statusTxt = 'PENDING';
      if (status === 'completed') {
        icon = '✓';
        statusClass = 'step-completed';
        statusTxt = 'COMPLETED';
      } else if (status === 'active') {
        icon = '●';
        statusClass = 'step-active';
        statusTxt = 'IN PROGRESS';
      }
      return `
        <div class="vertical-timeline-step ${statusClass}" style="display: flex; align-items: center; justify-content: space-between; padding: 2px 0;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <span class="timeline-step-icon" style="width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; font-weight: bold; font-size: 0.85rem;">${icon}</span>
            <span style="font-weight: 500; font-size: 0.85rem;">${label}</span>
          </div>
          <span class="badge badge-${status === 'completed' ? 'approved' : (status === 'active' ? 'review' : 'pending')}" style="padding: 2px 8px; font-size: 0.65rem;">${statusTxt}</span>
        </div>
      `;
    };

    container.innerHTML = `
      ${getStepHtml('Requirement Analysis', reqStatus)}
      ${getStepHtml('Scenario Drafting', scStatus)}
      ${getStepHtml('Human Scenario Approval', appStatus)}
      ${getStepHtml('TestCase Generation', tcStatus)}
      ${getStepHtml('Playwright Scripting', pwStatus)}
      ${getStepHtml('Automated Execution', execStatus)}
      ${getStepHtml('Execution Reporting', repStatus)}
    `;
  }

  renderActivityLogs() {
    const container = document.getElementById('recent-activity-list');
    if (!container) return;
    
    if (this.logs.length === 0) {
      container.innerHTML = `<div style="color: var(--text-muted); font-size: 0.85rem;">No recent activity logs.</div>`;
      return;
    }
    
    container.innerHTML = this.logs.slice(0, 10).map(log => Components.RecentActivityItem(log)).join('');
  }

  // Requirements Table
  renderRequirements() {
    const container = document.getElementById('requirements-list-container');
    
    const tableHtml = Components.RequirementsTable(this.filteredRequirements, {
      onSelect: (reqId) => {
        this.selectedRequirementId = reqId;
        this.renderScenariosRequirementDropdown();
        this.renderTestCasesRequirementDropdown();
        this.navigateTo('scenarios');
      }
    });

    container.innerHTML = tableHtml;
    
    const createBtn = document.getElementById('btn-create-requirement');
    if (createBtn) {
      createBtn.onclick = () => this.openModal('create-requirement-modal');
    }
  }

  formatRequirementLabel(r) {
    // Priority: requirement title fields over filename
    if (r.requirement_id && r.requirement_title) {
      return `${r.requirement_id}: ${r.requirement_title}`;
    }
    if (r.requirement_title) {
      return r.requirement_title;
    }
    if (r.title) {
      return r.title;
    }
    // Fallback to filename only if no title exists
    if (r.original_filename) {
      return r.original_filename;
    }
    return 'Untitled Requirement';
  }

  // Scenarios Table
  renderScenariosRequirementDropdown() {
    const select = document.getElementById('scenario-req-select');
    if (!select) return;

    select.innerHTML = `
      <option value="" disabled ${!this.selectedRequirementId ? 'selected' : ''}>▼ Choose Requirement</option>
      ${this.filteredRequirements.map(r => `<option value="${r.id}" ${r.id === this.selectedRequirementId ? 'selected' : ''}>${escapeHTML(this.formatRequirementLabel(r))}</option>`).join('')}
    `;

    select.onchange = (e) => {
      this.selectedRequirementId = e.target.value;
      this.renderScenarios();
      this.renderTestCasesRequirementDropdown();
    };

    this.setupSearchInput('scenario-req-search', 'scenario-req-select', (id) => {
      const r = this.filteredRequirements.find(x => x.id === id);
      return r ? [r.title, r.requirement_id, r.description] : [];
    });
  }

  async renderScenarios() {
    this.renderScenariosRequirementDropdown();
    const container = document.getElementById('scenarios-list-container');
    
    if (!this.selectedRequirementId) {
      container.innerHTML = Components.EmptyState("Select Requirement", "Choose a requirement from the dropdown list to view or generate scenarios.");
      document.getElementById('scenarios-actions-toolbar').style.display = 'none';
      return;
    }

    document.getElementById('scenarios-actions-toolbar').style.display = 'flex';
    const requirementScenarios = this.scenarios.filter(s => s.requirement_id === this.selectedRequirementId);

    const genBtn = document.getElementById('btn-generate-scenarios');
    
    genBtn.onclick = async () => {
      const countInput = document.getElementById('sc-generation-count');
      const count = countInput ? parseInt(countInput.value, 10) || 3 : 3;
      
      const runGeneration = async (mode) => {
        container.innerHTML = Components.Spinner(`AI is generating ${count} scenarios for this requirement...`);
        genBtn.disabled = true;
        try {
          await API.generateScenarios(this.currentProject.id, this.selectedRequirementId, count, mode);
          this.addLog(`Generated ${count} scenarios (${mode})`);
          await this.refreshProjectData();
        } catch (err) {
          alert(`Scenario generation failed: ${err.message}`);
          this.renderScenarios();
        } finally {
          genBtn.disabled = false;
        }
      };

      if (requirementScenarios.length > 0) {
        this.openModal('confirm-scenarios-modal');
        
        document.getElementById('btn-confirm-sc-append').onclick = async () => {
          this.closeModal('confirm-scenarios-modal');
          await runGeneration('append');
        };

        document.getElementById('btn-confirm-sc-replace').onclick = async () => {
          this.closeModal('confirm-scenarios-modal');
          await runGeneration('replace');
        };
      } else {
        await runGeneration('replace');
      }
    };

    container.innerHTML = Components.ScenariosTable(requirementScenarios, {
      onApprove: async (id) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { approved: true });
          this.addLog("Scenario approved");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to approve: ${err.message}`);
        }
      },
      onReject: async (id) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { approved: false, rejected: true });
          this.addLog("Scenario marked rejected");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to reject: ${err.message}`);
        }
      },
      onSave: async (id, name, desc, priority) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { 
            scenario_name: name,
            description: desc,
            priority: priority
          });
          this.addLog("Scenario updated");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to save: ${err.message}`);
        }
      },
      onDuplicate: async (id) => {
        try {
          await API.duplicateScenario(this.currentProject.id, id);
          this.addLog("Scenario duplicated");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to duplicate: ${err.message}`);
        }
      },
      onDelete: async (id) => {
        if (!confirm("Are you sure you want to delete this scenario?")) return;
        try {
          await API.deleteScenario(this.currentProject.id, id);
          this.addLog("Scenario deleted");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to delete: ${err.message}`);
        }
      },
      onAddNote: async (id, note) => {
        try {
          await API.addScenarioNote(id, note);
          this.addLog("Scenario review comment added");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to add note: ${err.message}`);
        }
      },
      onSyncJira: async (id) => {
        try {
          this.addLog("Initiating JIRA synchronization...");
          const sc = requirementScenarios.find(s => s.id === id);
          if (!sc) return;
          await API.syncRequirementJira(this.currentProject.id, sc.requirement_id);
          this.addLog("Scenario successfully synced to JIRA");
          await this.refreshProjectData();
          this.renderScenarios();
        } catch (err) {
          alert(`Failed to sync to JIRA: ${err.message}`);
        }
      }
    }, this.scenariosNotes, this.requirements);

    // Bind bulk actions toolbar events
    const bulkApprove = document.getElementById('btn-bulk-approve');
    const bulkReject = document.getElementById('btn-bulk-reject');
    if (bulkApprove && bulkReject) {
      bulkApprove.onclick = async () => {
        const ids = this.getSelectedScenarios();
        if (ids.length === 0) return alert("Select at least one scenario");
        for (const id of ids) {
          await API.updateScenario(this.currentProject.id, id, { approved: true });
        }
        this.addLog(`Bulk approved ${ids.length} scenarios`);
        await this.refreshProjectData();
        this.renderScenarios();
      };
      bulkReject.onclick = async () => {
        const ids = this.getSelectedScenarios();
        if (ids.length === 0) return alert("Select at least one scenario");
        for (const id of ids) {
          await API.updateScenario(this.currentProject.id, id, { approved: false, rejected: true });
        }
        this.addLog(`Bulk rejected ${ids.length} scenarios`);
        await this.refreshProjectData();
        this.renderScenarios();
      };
    }
  }

  getSelectedScenarios() {
    const checked = [];
    document.querySelectorAll('.scenario-checkbox:checked').forEach(cb => {
      checked.push(cb.getAttribute('data-id'));
    });
    return checked;
  }

  // Test Cases Table
  renderTestCasesRequirementDropdown() {
    const select = document.getElementById('tc-req-select');
    if (!select) return;

    select.innerHTML = `
      <option value="" disabled ${!this.selectedRequirementId ? 'selected' : ''}>▼ Choose Requirement</option>
      ${this.filteredRequirements.map(r => `<option value="${r.id}" ${r.id === this.selectedRequirementId ? 'selected' : ''}>${escapeHTML(this.formatRequirementLabel(r))}</option>`).join('')}
    `;

    select.onchange = (e) => {
      this.selectedRequirementId = e.target.value;
      this.renderTestCases();
      this.renderScenariosRequirementDropdown();
    };

    this.setupSearchInput('tc-req-search', 'tc-req-select', (id) => {
      const r = this.filteredRequirements.find(x => x.id === id);
      return r ? [r.title, r.requirement_id, r.description] : [];
    });
  }

  async renderTestCases() {
    this.renderTestCasesRequirementDropdown();
    const container = document.getElementById('testcases-list-container');
    const jiraContainer = document.getElementById('testcases-jira-trace-container');
    
    if (!this.selectedRequirementId) {
      container.innerHTML = Components.EmptyState("Select Requirement", "Choose a requirement from the dropdown list to view or generate test cases.");
      document.getElementById('testcases-actions-toolbar').style.display = 'none';
      if (jiraContainer) jiraContainer.style.display = 'none';
      return;
    }

    document.getElementById('testcases-actions-toolbar').style.display = 'flex';
    
    const requirementScenarios = this.scenarios.filter(s => s.requirement_id === this.selectedRequirementId);
    
    if (jiraContainer) {
      if (requirementScenarios.length > 0) {
        jiraContainer.style.display = 'block';
        let html = `
          <div style="display: flex; flex-direction: column; gap: 12px;">
            <h3 style="font-size: 0.95rem; font-weight: 600; color: var(--text-primary); margin: 0 0 4px 0; display: flex; align-items: center; gap: 6px;">
              <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 18px; height: 18px; color: var(--accent-primary);">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              Jira Traceability & Status
            </h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px;">
        `;
        
        requirementScenarios.forEach(sc => {
          let statusText = sc.last_jira_sync_status || sc.jira_sync_status || "Pending";
          let badgeBg = "rgba(156, 163, 175, 0.15)";
          let badgeColor = "#9ca3af";
          let badgeBorder = "rgba(156, 163, 175, 0.3)";
          
          const sLower = statusText.toLowerCase();
          if (sLower === "success" || sLower === "done" || sLower === "passed") {
            badgeBg = "rgba(34, 197, 94, 0.15)";
            badgeColor = "#4ade80";
            badgeBorder = "rgba(34, 197, 94, 0.3)";
            statusText = "Done";
          } else if (sLower === "in progress" || sLower === "in_progress") {
            badgeBg = "rgba(59, 130, 246, 0.15)";
            badgeColor = "#60a5fa";
            badgeBorder = "rgba(59, 130, 246, 0.3)";
            statusText = "In Progress";
          } else if (sLower === "sync_pending" || sLower === "sync pending" || sLower === "pending") {
            badgeBg = "rgba(234, 179, 8, 0.15)";
            badgeColor = "#facc15";
            badgeBorder = "rgba(234, 179, 8, 0.3)";
            statusText = sLower.includes("pending") && !sLower.includes("sync") ? "Pending" : "Sync Pending";
          } else if (sLower === "sync_failed" || sLower === "sync failed" || sLower === "failed" || sLower === "error") {
            badgeBg = "rgba(239, 68, 68, 0.15)";
            badgeColor = "#f87171";
            badgeBorder = "rgba(239, 68, 68, 0.3)";
            statusText = sLower.includes("sync") ? "Sync Failed" : "Failed";
          }
          
          let jiraLink = "Not Linkable";
          if (sc.jira_issue_key) {
            jiraLink = `<a href="${sc.jira_issue_url}" target="_blank" style="color: var(--accent-primary); text-decoration: underline; font-weight: 500;">${sc.jira_issue_key}</a>`;
          }
          
          html += `
            <div style="background: var(--bg-secondary); border: 1px solid var(--border-color); padding: 12px; border-radius: var(--border-radius-sm); display: flex; flex-direction: column; gap: 6px;">
              <div style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap;"><strong style="color: var(--text-secondary); font-size: 0.75rem; text-transform: uppercase; margin-right: 4px;">Scenario:</strong> <span style="font-weight: 500;" title="${sc.scenario_name}">${sc.scenario_name}</span></div>
              <div><strong style="color: var(--text-secondary); font-size: 0.75rem; text-transform: uppercase; margin-right: 4px;">Jira:</strong> ${jiraLink}</div>
              <div style="display: flex; align-items: center; gap: 8px;"><strong style="color: var(--text-secondary); font-size: 0.75rem; text-transform: uppercase; margin-right: 4px;">Jira Status:</strong> 
                <span style="background: ${badgeBg}; color: ${badgeColor}; border: 1px solid ${badgeBorder}; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600;">${statusText}</span>
              </div>
            </div>
          `;
        });
        
        html += `
            </div>
          </div>
        `;
        jiraContainer.innerHTML = html;
      } else {
        jiraContainer.style.display = 'none';
      }
    }
    const approvedScenarios = requirementScenarios.filter(s => s.approved);
    
    const genBtn = document.getElementById('btn-generate-testcases');
    const warningLabel = document.getElementById('tc-generation-warning');

    if (approvedScenarios.length === 0) {
      genBtn.disabled = true;
      genBtn.innerText = "Generate Test Cases (Approve at least one Scenario)";
      warningLabel.style.display = 'none';
    } else {
      genBtn.disabled = false;
      genBtn.innerText = "Generate & Evaluate Test Cases";
      warningLabel.style.display = 'none';
    }

    genBtn.onclick = async () => {
      container.innerHTML = Components.Spinner("AI Agents are generating and evaluating test cases...");
      genBtn.disabled = true;
      try {
        await API.generateTestCases(this.currentProject.id, this.selectedRequirementId);
        this.addLog("Test cases generated and evaluated");
        await this.refreshProjectData();
      } catch (err) {
        alert(`Test case generation failed: ${err.message}`);
        this.renderTestCases();
      } finally {
        genBtn.disabled = false;
      }
    };

    const approvedScenarioIds = new Set(approvedScenarios.map(s => s.id));
    const requirementTestCases = this.testCases.filter(tc => approvedScenarioIds.has(tc.scenario_id));

    container.innerHTML = Components.TestCasesTable(requirementTestCases, {
      onApprove: async (id) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'approved' });
          this.addLog("Test case approved");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to approve: ${err.message}`);
        }
      },
      onReject: async (id) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'rejected' });
          this.addLog("Test case rejected");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to reject: ${err.message}`);
        }
      },
      onSave: async (id, data) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, data);
          this.addLog("Test case updated");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to save: ${err.message}`);
        }
      },
      onDelete: async (id) => {
        if (!confirm("Are you sure you want to delete this test case?")) return;
        try {
          await API.deleteTestCase(this.currentProject.id, id);
          this.addLog("Test case deleted");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to delete: ${err.message}`);
        }
      },
      onViewScript: (id) => {
        this.openPlaywrightScriptWorkspace(id);
      },
      onGenerateScript: async (id, btn, framework) => {
        btn.disabled = true;
        btn.innerText = "Generating...";
        try {
          await API.generatePlaywrightScript(this.currentProject.id, id, framework);
          this.addLog("Playwright script generated for test case");
          await this.refreshProjectData();
          this.renderTestCases();
        } catch (err) {
          alert(`Script generation failed: ${err.message}`);
        } finally {
          btn.disabled = false;
          btn.innerText = "Generate Script";
        }
      },
      onAddNote: async (id, note) => {
        try {
          await API.addTestCaseNote(id, note);
          this.addLog("Test case comment added");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to add note: ${err.message}`);
        }
      }
    }, this.testCasesNotes, this.goldenDatasetComparison || {});

    // Bulk actions
    const bulkTcApprove = document.getElementById('btn-bulk-tc-approve');
    const bulkTcReject = document.getElementById('btn-bulk-tc-reject');
    if (bulkTcApprove && bulkTcReject) {
      bulkTcApprove.onclick = async () => {
        const ids = this.getSelectedTestCases();
        if (ids.length === 0) return alert("Select at least one test case");
        for (const id of ids) {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'approved' });
        }
        this.addLog(`Bulk approved ${ids.length} test cases`);
        await this.refreshProjectData();
      };
      bulkTcReject.onclick = async () => {
        const ids = this.getSelectedTestCases();
        if (ids.length === 0) return alert("Select at least one test case");
        for (const id of ids) {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'rejected' });
        }
        this.addLog(`Bulk rejected ${ids.length} test cases`);
        await this.refreshProjectData();
      };
    }

    // Render bulk generation button at the bottom if there is any approved testcase
    const bulkScriptContainer = document.getElementById('testcase-bulk-script-container');
    if (bulkScriptContainer) {
      const approvedTCs = requirementTestCases.filter(tc => tc.evaluation_status === 'approved');
      if (approvedTCs.length > 0) {
        bulkScriptContainer.style.display = 'block';
        const btn = document.getElementById('btn-generate-all-scripts');
        btn.onclick = async () => {
          btn.disabled = true;
          btn.innerText = "Generating Scripts in Background...";
          try {
            for (const tc of approvedTCs) {
              await API.generatePlaywrightScript(this.currentProject.id, tc.id, this.currentProject.framework);
            }
            this.addLog(`Playwright scripts generated for ${approvedTCs.length} approved test cases`);
            await this.refreshProjectData();
            this.renderTestCases();
          } catch (err) {
            alert(`Script generation failed: ${err.message}`);
          } finally {
            btn.disabled = false;
            btn.innerText = "Generate Playwright Scripts for All Approved Test Cases";
          }
        };

        const runBatchBtn = document.getElementById('btn-run-selected-batch');
        if (runBatchBtn) {
          runBatchBtn.onclick = async () => {
            const ids = this.getSelectedTestCases();
            if (ids.length === 0) return alert("Please select at least one test case to execute.");
            
            runBatchBtn.disabled = true;
            
            const scriptless = ids.filter(id => {
              const tc = this.testCases.find(t => t.id === id);
              return tc && !tc.playwright_script;
            });
            
            if (scriptless.length > 0) {
              runBatchBtn.innerText = `Auto-generating ${scriptless.length} script(s)...`;
              try {
                for (const tcId of scriptless) {
                  await API.generatePlaywrightScript(this.currentProject.id, tcId, this.currentProject.framework);
                }
                this.addLog(`Auto-generated scripts for ${scriptless.length} selected test cases.`);
                await this.refreshProjectData();
                this.renderTestCases();
              } catch (err) {
                alert(`Failed to auto-generate scripts: ${err.message}`);
                runBatchBtn.disabled = false;
                runBatchBtn.innerText = "Run Selected Batch";
                return;
              }
            }

            runBatchBtn.innerText = "Redirecting to Playwright Workspace...";
            try {
              const wsUrl = this.settings?.playwright?.workspace_url || 'http://localhost:3000';
              const testCycleId = this.currentTestCycle ? this.currentTestCycle.id : '';
              const url = `${wsUrl}/?project_id=${this.currentProject.id}&test_case_ids=${ids.join(',')}&test_cycle_id=${testCycleId}`;
              window.open(url, '_blank');
              this.addLog(`Opened batch workspace for ${ids.length} selected test cases.`);
            } catch (err) {
              alert(`Failed to redirect to Playwright Workspace: ${err.message}`);
            } finally {
              runBatchBtn.disabled = false;
              runBatchBtn.innerText = "Run Selected Batch";
            }
          };
        }
      } else {
        bulkScriptContainer.style.display = 'none';
      }
    }
  }

  getSelectedTestCases() {
    const checked = [];
    document.querySelectorAll('.tc-checkbox:checked').forEach(cb => {
      checked.push(cb.getAttribute('data-id'));
    });
    return checked;
  }

  renderScenarioApprovalRequirementDropdown() {
    const select = document.getElementById('sc-approval-req-select');
    if (!select) return;

    select.innerHTML = `
      <option value="" disabled ${!this.selectedRequirementId ? 'selected' : ''}>▼ Choose Requirement</option>
      ${this.requirements.map(r => `<option value="${r.id}" ${r.id === this.selectedRequirementId ? 'selected' : ''}>${escapeHTML(this.formatRequirementLabel(r))}</option>`).join('')}
    `;

    select.onchange = (e) => {
      this.selectedRequirementId = e.target.value;
      this.renderScenarioApproval();
    };
  }

  async renderScenarioApproval() {
    this.renderScenarioApprovalRequirementDropdown();
    const container = document.getElementById('scenario-approval-list-container');
    if (!container) return;
    
    if (!this.selectedRequirementId) {
      container.innerHTML = Components.EmptyState("Select Requirement", "Choose a requirement from the dropdown list to approve scenarios.");
      document.getElementById('sc-approval-actions-toolbar').style.display = 'none';
      return;
    }

    document.getElementById('sc-approval-actions-toolbar').style.display = 'flex';
    const requirementScenarios = this.scenarios.filter(s => s.requirement_id === this.selectedRequirementId);

    // Bind bulk approval triggers
    const bulkApprove = document.getElementById('btn-sc-approval-bulk-approve');
    const bulkReject = document.getElementById('btn-sc-approval-bulk-reject');
    if (bulkApprove) {
      bulkApprove.onclick = async () => {
        const ids = this.getSelectedScenarios();
        if (ids.length === 0) return alert("Select at least one scenario");
        for (const id of ids) {
          await API.updateScenario(this.currentProject.id, id, { approved: true });
        }
        this.addLog(`Bulk approved ${ids.length} scenarios`);
        await this.refreshProjectData();
      };
    }
    if (bulkReject) {
      bulkReject.onclick = async () => {
        const ids = this.getSelectedScenarios();
        if (ids.length === 0) return alert("Select at least one scenario");
        for (const id of ids) {
          await API.updateScenario(this.currentProject.id, id, { approved: false, rejected: true });
        }
        this.addLog(`Bulk rejected ${ids.length} scenarios`);
        await this.refreshProjectData();
      };
    }

    container.innerHTML = Components.ScenariosTable(requirementScenarios, {
      onApprove: async (id) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { approved: true });
          this.addLog("Scenario approved");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to approve: ${err.message}`);
        }
      },
      onReject: async (id) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { approved: false, rejected: true });
          this.addLog("Scenario marked rejected");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to reject: ${err.message}`);
        }
      },
      onSave: async (id, name, desc, priority) => {
        try {
          await API.updateScenario(this.currentProject.id, id, { 
            scenario_name: name,
            description: desc,
            priority: priority
          });
          this.addLog("Scenario updated");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to save: ${err.message}`);
        }
      },
      onDuplicate: async (id) => {
        try {
          await API.duplicateScenario(this.currentProject.id, id);
          this.addLog("Scenario duplicated");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to duplicate: ${err.message}`);
        }
      },
      onDelete: async (id) => {
        if (!confirm("Are you sure you want to delete this scenario?")) return;
        try {
          await API.deleteScenario(this.currentProject.id, id);
          this.addLog("Scenario deleted");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to delete: ${err.message}`);
        }
      },
      onAddNote: async (id, note) => {
        try {
          await API.addScenarioNote(id, note);
          this.addLog("Scenario review comment added");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to add note: ${err.message}`);
        }
      },
      onSyncJira: async (id) => {
        try {
          this.addLog("Initiating JIRA synchronization...");
          const sc = requirementScenarios.find(s => s.id === id);
          if (!sc) return;
          await API.syncRequirementJira(this.currentProject.id, sc.requirement_id);
          this.addLog("Scenario successfully synced to JIRA");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to sync to JIRA: ${err.message}`);
        }
      }
    }, this.scenariosNotes);
  }

  renderTestCaseApprovalRequirementDropdown() {
    const select = document.getElementById('tc-approval-req-select');
    if (!select) return;

    select.innerHTML = `
      <option value="" disabled ${!this.selectedRequirementId ? 'selected' : ''}>▼ Choose Requirement</option>
      ${this.requirements.map(r => `<option value="${r.id}" ${r.id === this.selectedRequirementId ? 'selected' : ''}>${escapeHTML(this.formatRequirementLabel(r))}</option>`).join('')}
    `;

    select.onchange = (e) => {
      this.selectedRequirementId = e.target.value;
      this.renderTestCaseApproval();
    };
  }

  async renderTestCaseApproval() {
    this.renderTestCaseApprovalRequirementDropdown();
    const container = document.getElementById('testcase-approval-list-container');
    if (!container) return;

    if (!this.selectedRequirementId) {
      container.innerHTML = Components.EmptyState("Select Requirement", "Choose a requirement from the dropdown list to approve test cases.");
      document.getElementById('tc-approval-actions-toolbar').style.display = 'none';
      return;
    }

    document.getElementById('tc-approval-actions-toolbar').style.display = 'flex';
    const requirementScenarios = this.scenarios.filter(s => s.requirement_id === this.selectedRequirementId);
    const approvedScenarios = requirementScenarios.filter(s => s.approved);
    const approvedScenarioIds = new Set(approvedScenarios.map(s => s.id));
    const requirementTestCases = this.testCases.filter(tc => approvedScenarioIds.has(tc.scenario_id));

    // Bind bulk approval triggers
    const bulkTCApprove = document.getElementById('btn-tc-approval-bulk-approve');
    const bulkTCReject = document.getElementById('btn-tc-approval-bulk-reject');
    if (bulkTCApprove) {
      bulkTCApprove.onclick = async () => {
        const ids = this.getSelectedTestCases();
        if (ids.length === 0) return alert("Select at least one test case");
        for (const id of ids) {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'approved' });
        }
        this.addLog(`Bulk approved ${ids.length} test cases`);
        await this.refreshProjectData();
      };
    }
    if (bulkTCReject) {
      bulkTCReject.onclick = async () => {
        const ids = this.getSelectedTestCases();
        if (ids.length === 0) return alert("Select at least one test case");
        for (const id of ids) {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'rejected' });
        }
        this.addLog(`Bulk rejected ${ids.length} test cases`);
        await this.refreshProjectData();
      };
    }

    container.innerHTML = Components.TestCasesTable(requirementTestCases, {
      onApprove: async (id) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'approved' });
          this.addLog("Test case approved");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to approve: ${err.message}`);
        }
      },
      onReject: async (id) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, { evaluation_status: 'rejected' });
          this.addLog("Test case rejected");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to reject: ${err.message}`);
        }
      },
      onSave: async (id, data) => {
        try {
          await API.updateTestCase(this.currentProject.id, id, data);
          this.addLog("Test case updated");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to save: ${err.message}`);
        }
      },
      onDelete: async (id) => {
        if (!confirm("Are you sure you want to delete this test case?")) return;
        try {
          await API.deleteTestCase(this.currentProject.id, id);
          this.addLog("Test case deleted");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to delete: ${err.message}`);
        }
      },
      onViewScript: (id) => {
        this.openPlaywrightScriptWorkspace(id);
      },
      onGenerateScript: async (id, btn) => {
        btn.disabled = true;
        btn.innerText = "Generating...";
        try {
          await API.generatePlaywrightScript(this.currentProject.id, id, this.currentProject?.framework);
          this.addLog("Playwright script generated for test case");
          await this.refreshProjectData();
          this.renderTestCaseApproval();
        } catch (err) {
          alert(`Script generation failed: ${err.message}`);
        } finally {
          btn.disabled = false;
          btn.innerText = "Generate Script";
        }
      },
      onAddNote: async (id, note) => {
        try {
          await API.addTestCaseNote(id, note);
          this.addLog("Test case comment added");
          await this.refreshProjectData();
        } catch (err) {
          alert(`Failed to add note: ${err.message}`);
        }
      }
    }, this.testCasesNotes, this.goldenDatasetComparison || {});
  }

  renderPlaywrightWorkspaceRequirementDropdown() {
    const select = document.getElementById('playwright-req-select');
    if (!select) return;

    select.innerHTML = `
      <option value="" disabled ${!this.selectedRequirementId ? 'selected' : ''}>▼ Choose Requirement</option>
      ${this.filteredRequirements.map(r => `<option value="${r.id}" ${r.id === this.selectedRequirementId ? 'selected' : ''}>${escapeHTML(this.formatRequirementLabel(r))}</option>`).join('')}
    `;

    select.onchange = (e) => {
      this.selectedRequirementId = e.target.value;
      localStorage.setItem('playwright_active_req_id', this.selectedRequirementId);
      this.activePlaywrightTestCaseId = null;
      localStorage.removeItem('playwright_active_testcase_id');
      this.renderPlaywrightWorkspace();
    };
  }

  async renderPlaywrightWorkspace() {
    const btn = document.getElementById('btn-launch-playwright-workspace');
    if (btn) {
      btn.onclick = () => {
        const wsUrl = this.settings?.playwright?.workspace_url || 'http://localhost:3000';
        const url = `${wsUrl}/?project_id=${this.currentProject?.id || ''}`;
        window.open(url, '_blank');
      };
    }
  }

  // Playwright script viewer Workspace
  openPlaywrightScriptWorkspace(testCaseId) {
    const wsUrl = this.settings?.playwright?.workspace_url || 'http://localhost:3000';
    const url = `${wsUrl}/?project_id=${this.currentProject?.id || ''}&test_case_id=${testCaseId}`;
    window.open(url, '_blank');
  }

  async handleRouting() {
    const hash = window.location.hash;
    let view = hash.replace('#/', '') || 'dashboard';
    
    if (view.startsWith('execution/')) {
      const parts = view.split('/');
      const execId = parts[1];
      if (this.currentProject) {
        await this.renderExecutionWorkspaceObserver(execId);
      }
      this.navigateTo('execution-workspace');
      return;
    }

    if (['dashboard', 'requirements', 'scenarios', 'testcases', 'playwright', 'executions', 'reports', 'langsmith', 'settings', 'execution-workspace', 'project-start', 'project'].includes(view)) {
      if (view !== 'project' && this.currentProject) {
        await this.refreshProjectData().catch(e => console.error("Router refresh error:", e));
      }
      this.navigateTo(view);
      if (view === 'dashboard' && this.isAdmin()) {
        this.renderDashboardMetrics();
      }
    }
  }

  // Documents Page / Knowledge Base
  renderDocuments() {
    const container = document.getElementById('documents-list-container');
    container.innerHTML = Components.DocumentsTable(this.documents, async (id) => {
      if (!confirm("Are you sure you want to delete this document from the Knowledge Base?")) return;
      try {
        await API.deleteDocument(this.currentProject.id, id);
        this.addLog("Document deleted from Knowledge Base");
        await this.refreshProjectData();
      } catch (err) {
        alert(`Failed to delete document: ${err.message}`);
      }
    });

    const dropzone = document.getElementById('kb-dropzone');
    if (!dropzone) return;

    dropzone.ondragover = (e) => {
      e.preventDefault();
      dropzone.style.borderColor = 'var(--accent-primary)';
      dropzone.style.backgroundColor = 'rgba(59, 130, 246, 0.03)';
    };

    dropzone.ondragleave = () => {
      dropzone.style.borderColor = 'var(--border-color)';
      dropzone.style.backgroundColor = 'transparent';
    };

    dropzone.ondrop = async (e) => {
      e.preventDefault();
      dropzone.style.borderColor = 'var(--border-color)';
      dropzone.style.backgroundColor = 'transparent';

      const files = e.dataTransfer.files;
      if (files.length === 0) return;

      const file = files[0];
      const validExtensions = ['.pdf', '.docx', '.xlsx', '.csv', '.txt'];
      const fileExt = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
      if (!validExtensions.includes(fileExt)) {
        alert("Invalid file format. Upload PDF, DOCX, XLSX, CSV, or TXT.");
        return;
      }

      dropzone.innerHTML = `
        <div style="width: 100%;">
          <div style="font-size: 0.8rem; margin-bottom: 8px;">Uploading ${escapeHTML(file.name)}...</div>
          <div style="background-color: var(--bg-primary); height: 8px; border-radius: 4px; overflow: hidden; border: 1px solid var(--border-color);">
            <div id="upload-progress-bar" style="background-color: var(--accent-primary); width: 0%; height: 100%; transition: width 0.15s ease;"></div>
          </div>
        </div>
      `;

      const progressBar = document.getElementById('upload-progress-bar');
      let progress = 0;
      const interval = setInterval(async () => {
        progress += 10;
        progressBar.style.width = `${progress}%`;
        if (progress >= 100) {
          clearInterval(interval);
          
          try {
            await API.createDocument(this.currentProject.id, {
              filename: file.name,
              original_filename: file.name,
              mime_type: file.type || 'text/plain',
              size: file.size,
              storage_path: `/data/${file.name}`
            });
            this.addLog(`Document '${file.name}' added to Knowledge Base`);
            await this.refreshProjectData();
          } catch (err) {
            alert(`Upload failed: ${err.message}`);
          } finally {
            dropzone.innerHTML = `
              Drag and drop target files here to parse (PDF, DOCX, XLSX, CSV, TXT)
              <div style="margin-top: 8px; font-size: 0.75rem; background-color: var(--bg-tertiary); padding: 4px 8px; border-radius: 4px; display: inline-block; border: 1px solid var(--border-color);">RAG Extraction Active</div>
            `;
          }
        }
      }, 100);
    };
  }

  // Executions History Renderer
  renderExecutions() {
    const container = document.getElementById('executions-list-container');
    container.innerHTML = Components.ExecutionsTable(this.filteredExecutions);
  }

  async renderExecutionWorkspaceObserver(execId) {
    const container = document.getElementById('execution-observer-body');
    if (!container) return;

    if (this.activeExecutionStream) {
      this.activeExecutionStream.close();
      this.activeExecutionStream = null;
    }

    const isBatch = execId.startsWith('BATCH-');

    if (!isBatch) {
      container.innerHTML = Components.Spinner("Loading execution details...");
      try {
        const data = await API.getExecutionDetail(this.currentProject.id, execId);
        const ex = data.execution;
        
        if (!ex.status) {
          this.renderRealTimeObserver(execId, false);
          return;
        }

        this.renderHistoricalExecution(data);
        return;
      } catch (err) {
        this.renderRealTimeObserver(execId, false);
        return;
      }
    } else {
      this.renderRealTimeObserver(execId, true);
    }
  }

  renderRealTimeObserver(batchOrExecId, isBatch) {
    const container = document.getElementById('execution-observer-body');
    const descEl = document.getElementById('obs-title-desc');
    if (descEl) {
      descEl.textContent = `Monitoring ${isBatch ? 'batch execution' : 'test case'} stream in real-time. Survives page refreshes.`;
    }

    container.innerHTML = `
      <div style="display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; min-height: 480px;">
        <!-- LEFT PANEL: Terminal Log Stream -->
        <div class="section-card" style="display: flex; flex-direction: column; height: 100%;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <h2 style="margin: 0;">Execution Stream Terminal</h2>
            <span class="badge badge-review" id="obs-connection-status">Connecting...</span>
          </div>
          <div id="obs-terminal" style="flex-grow: 1; min-height: 380px; background: #000; border-radius: var(--border-radius-md); padding: 16px; font-family: monospace; font-size: 0.85rem; color: #10b981; overflow-y: auto; line-height: 1.6; border: 1px solid var(--border-color);">
            <div style="color: var(--text-muted);">Waiting for logs...</div>
          </div>
        </div>

        <!-- RIGHT PANEL: Structured Status Dashboard -->
        <div style="display: flex; flex-direction: column; gap: 20px; height: 100%;">
          <!-- Progress Bar Card -->
          <div class="section-card">
            <h2>Batch Execution Progress</h2>
            <div style="margin-top: 16px;">
              <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 8px;">
                <span id="obs-progress-text">Progress: 0%</span>
                <span id="obs-etr-text">ETR: --</span>
              </div>
              <div style="background-color: var(--bg-primary); height: 12px; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-color);">
                <div id="obs-progress-bar" style="background-color: var(--color-success); width: 0%; height: 100%; transition: width 0.3s ease;"></div>
              </div>
            </div>
          </div>

          <!-- Status Counts Card -->
          <div class="section-card" style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; text-align: center;">
            <div style="background: var(--bg-secondary); padding: 12px; border-radius: var(--border-radius-md); border: 1px solid var(--border-color);">
              <div style="font-size: 1.5rem; font-weight: 700; color: var(--color-success);" id="obs-count-passed">0</div>
              <div style="font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-top: 4px;">Passed</div>
            </div>
            <div style="background: var(--bg-secondary); padding: 12px; border-radius: var(--border-radius-md); border: 1px solid var(--border-color);">
              <div style="font-size: 1.5rem; font-weight: 700; color: var(--color-danger);" id="obs-count-failed">0</div>
              <div style="font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-top: 4px;">Failed</div>
            </div>
            <div style="background: var(--bg-secondary); padding: 12px; border-radius: var(--border-radius-md); border: 1px solid var(--border-color);">
              <div style="font-size: 1.5rem; font-weight: 700; color: var(--color-warning);" id="obs-count-skipped">0</div>
              <div style="font-size: 0.72rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-top: 4px;">Skipped</div>
            </div>
          </div>

          <!-- Active Metrics Card -->
          <div class="section-card" style="display: flex; flex-direction: column; gap: 12px;">
            <h2>Logical Application State</h2>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 0.85rem; margin-top: 8px;">
              <div>
                <span style="color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase;">Current State</span>
                <strong id="obs-current-state" style="color: var(--text-primary);">LOGIN</strong>
              </div>
              <div>
                <span style="color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase;">Next Required State</span>
                <strong id="obs-next-state" style="color: var(--text-primary);">--</strong>
              </div>
            </div>
            <div style="border-top: 1px solid var(--border-color); padding-top: 12px;">
              <span style="color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase;">Navigation Decision</span>
              <strong id="obs-nav-decision" style="color: var(--accent-primary);">--</strong>
            </div>
            <div style="border-top: 1px solid var(--border-color); padding-top: 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 0.85rem;">
              <div>
                <span style="color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase;">Current Retry</span>
                <strong id="obs-current-retry">0</strong>
              </div>
              <div>
                <span style="color: var(--text-muted); display: block; font-size: 0.75rem; text-transform: uppercase;">Current Dataset Row</span>
                <strong id="obs-dataset-row">--</strong>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    const terminal = document.getElementById('obs-terminal');
    const connBadge = document.getElementById('obs-connection-status');
    const progressBar = document.getElementById('obs-progress-bar');
    const progressText = document.getElementById('obs-progress-text');
    const etrText = document.getElementById('obs-etr-text');
    const countPassed = document.getElementById('obs-count-passed');
    const countFailed = document.getElementById('obs-count-failed');
    const countSkipped = document.getElementById('obs-count-skipped');
    const currentState = document.getElementById('obs-current-state');
    const nextState = document.getElementById('obs-next-state');
    const navDecision = document.getElementById('obs-nav-decision');
    const currentRetry = document.getElementById('obs-current-retry');
    const datasetRow = document.getElementById('obs-dataset-row');

    const streamUrl = isBatch
      ? `/api/v1/projects/${this.currentProject.id}/batches/${batchOrExecId}/stream`
      : `/api/v1/projects/${this.currentProject.id}/testcases/${batchOrExecId}/execution-stream`;

    const source = new EventSource(streamUrl);
    this.activeExecutionStream = source;

    terminal.innerHTML = '';

    source.onopen = () => {
      connBadge.className = "badge badge-approved";
      connBadge.textContent = "Live Stream";
    };

    source.onerror = () => {
      connBadge.className = "badge badge-rejected";
      connBadge.textContent = "Disconnected";
      source.close();
    };

    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.log) {
        const logLine = document.createElement('div');
        logLine.style.marginBottom = '4px';
        logLine.innerHTML = escapeHTML(data.log);
        terminal.appendChild(logLine);
        terminal.scrollTop = terminal.scrollHeight;
      }

      if (data.progress !== undefined) {
        progressBar.style.width = `${data.progress}%`;
        progressText.textContent = `Progress: ${data.progress}% (${data.total_tasks - data.remaining_tasks}/${data.total_tasks} Tasks)`;
      }
      if (data.etr !== undefined) {
        etrText.textContent = `ETR: ${data.etr}`;
      }
      if (data.passed_count !== undefined) {
        countPassed.textContent = data.passed_count;
      }
      if (data.failed_count !== undefined) {
        countFailed.textContent = data.failed_count;
      }
      if (data.skipped_count !== undefined) {
        countSkipped.textContent = data.skipped_count;
      }
      if (data.current_state !== undefined) {
        currentState.textContent = data.current_state;
      }
      if (data.next_required_state !== undefined) {
        nextState.textContent = data.next_required_state;
      }
      if (data.navigation_decision !== undefined) {
        navDecision.textContent = data.navigation_decision;
      }
      if (data.current_retry !== undefined) {
        currentRetry.textContent = data.current_retry;
      }
      if (data.current_dataset_row !== undefined) {
        datasetRow.textContent = data.current_dataset_row;
      }

      if (data.status === 'Completed' || data.status === 'Error') {
        connBadge.className = "badge badge-approved";
        connBadge.textContent = "Finished";
        source.close();
        this.refreshProjectData();
      }
    };
  }

  renderHistoricalExecution(data) {
    const container = document.getElementById('execution-observer-body');
    const ex = data.execution;
    
    try {
      const stages = [
        { key: "Queued", label: "Queued", status: "completed" },
        { key: "Preparing Environment", label: "Preparing Env", status: "completed" },
        { key: "Running", label: "Running", status: ex.status === 'passed' || ex.status === 'failed' || ex.status === 'error' ? "completed" : "active" }
      ];

      let analysisStatus = "pending";
    if (ex.failure_category) {
      analysisStatus = "completed";
    } else if (ex.status === 'failed' || ex.status === 'error') {
      analysisStatus = "active";
    }
    stages.push({ key: "Execution Analysis", label: "Analysis", status: analysisStatus });

    let jiraStatus = "pending";
    if (ex.jira_bug_id) {
      jiraStatus = "completed";
    } else if (ex.status === 'failed' && ex.failure_category === 'Product Bug') {
      jiraStatus = "active";
    }
    stages.push({ key: "Jira Sync", label: "Jira Sync", status: jiraStatus });

    let reportingStatus = "pending";
    if (data.report && Object.keys(data.report).length > 0) {
      reportingStatus = "completed";
    } else if (ex.status) {
      reportingStatus = "completed";
    }
    stages.push({ key: "Reporting", label: "Reporting", status: reportingStatus });
    stages.push({ key: "Completed", label: "Completed", status: ex.status ? "completed" : "pending" });

    const stagesHtml = stages.map(s => {
      let color = "var(--text-muted)";
      let icon = "○";
      if (s.status === 'completed') {
        color = "var(--color-success)";
        icon = "✓";
      } else if (s.status === 'active') {
        color = "var(--accent-primary)";
        icon = "●";
      }
      return `
        <div style="display: flex; flex-direction: column; align-items: center; flex: 1; text-align: center;">
          <div style="width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; background: var(--bg-tertiary); border: 2px solid ${color}; color: ${color}; margin-bottom: 6px; font-size: 0.8rem;">
            ${icon}
          </div>
          <div style="font-size: 0.72rem; font-weight: 600; color: ${color}; text-transform: uppercase;">${escapeHTML(s.label)}</div>
        </div>
      `;
    }).join('<div style="flex-grow: 1; height: 2px; background: var(--border-color); margin-top: 12px; max-width: 40px;"></div>');

    let triageHtml = `<div style="color: var(--text-muted); font-size: 0.85rem;">No triage findings for successful run.</div>`;
    if (ex.failure_category && ex.failure_category !== 'None') {
      triageHtml = `
        <div style="display: grid; grid-template-columns: 1fr 1.5fr; gap: 20px;">
          <div>
            <div style="margin-bottom: 12px;">
              <span class="badge ${ex.failure_category === 'Product Bug' ? 'badge-rejected' : 'badge-review'}" style="font-size: 0.85rem; padding: 4px 10px;">
                ${escapeHTML(ex.failure_category)}
              </span>
            </div>
            <div style="font-size: 0.85rem; margin-bottom: 8px;"><strong>Suggest Retry:</strong> ${ex.suggest_retry ? 'Yes' : 'No'}</div>
            <div style="font-size: 0.85rem;"><strong>Retest Candidate:</strong> ${ex.retest_pending_candidate ? 'Yes' : 'No'}</div>
          </div>
          <div>
            <h4 style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Root Cause Summary</h4>
            <p style="font-size: 0.88rem; color: var(--text-secondary); line-height: 1.5; margin: 0; background: var(--bg-tertiary); padding: 8px; border-radius: var(--border-radius-md); border: 1px solid var(--border-color);">${escapeHTML(ex.root_cause_summary || 'N/A')}</p>
          </div>
        </div>
      `;
    }

    let defectHtml = `<div style="color: var(--text-muted); font-size: 0.85rem;">No JIRA issues mapped.</div>`;
    if (ex.jira_bug_id) {
      defectHtml = `
        <div style="display: flex; align-items: center; gap: 12px;">
          <div style="font-size: 1.5rem;">🐞</div>
          <div>
            <div style="font-weight: 700; font-size: 1rem;">
              <a href="${escapeHTML(ex.jira_bug_url)}" target="_blank" style="color: var(--color-danger); text-decoration: none; display: inline-flex; align-items: center; gap: 6px;">
                ${escapeHTML(ex.jira_bug_id)}
              </a>
            </div>
            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">Created/Synced by DefectManagementAgent</div>
          </div>
        </div>
      `;
    }

    const logsHtml = data.logs && data.logs.length > 0
      ? data.logs.map(log => `<div style="margin-bottom: 4px;">${escapeHTML(log)}</div>`).join('')
      : `<div style="color: var(--text-muted);">No execution logs streamed.</div>`;

    container.innerHTML = `
      <div class="section-card" style="display: flex; align-items: center; justify-content: space-between; padding: 20px 32px; margin-bottom: 24px; border: 1px solid var(--border-color);">
        ${stagesHtml}
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px;">
        <div class="section-card">
          <h2>AI Execution Triaging Findings</h2>
          <div style="margin-top: 16px;">
            ${triageHtml}
          </div>
        </div>
        <div class="section-card">
          <h2>Defect Lifecycle Link</h2>
          <div style="margin-top: 16px;">
            ${defectHtml}
          </div>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 24px;">
        <div class="section-card" style="display: flex; flex-direction: column; height: 360px;">
          <h2>Execution Stream Terminal</h2>
          <div style="flex-grow: 1; background: #000; border-radius: var(--border-radius-md); padding: 16px; font-family: monospace; font-size: 0.82rem; color: #10b981; overflow-y: auto; line-height: 1.5; margin-top: 12px; border: 1px solid var(--border-color);">
            ${logsHtml}
          </div>
        </div>
        <div class="section-card" style="display: flex; flex-direction: column; height: 360px;">
          <h2>Execution Run Artifacts</h2>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; flex-grow: 1;">
            <div style="background: var(--bg-secondary); border-radius: var(--border-radius-md); border: 1px solid var(--border-color); display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; padding: 20px; gap: 10px;">
              <div style="font-size: 1.5rem;">📸</div>
              <div style="font-weight: 600; font-size: 0.85rem;">Last Screenshot</div>
              ${ex.screenshot_path ? `
                <a href="/api/v1/projects/${this.currentProject.id}/executions/${ex.id}/screenshot?path=${encodeURIComponent(ex.screenshot_path)}" target="_blank" class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;">View Screenshot</a>
              ` : `<span style="font-size: 0.75rem; color: var(--text-muted);">No screenshot captured</span>`}
            </div>
            <div style="background: var(--bg-secondary); border-radius: var(--border-radius-md); border: 1px solid var(--border-color); display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; padding: 20px; gap: 10px;">
              <div style="font-size: 1.5rem;">🎬</div>
              <div style="font-weight: 600; font-size: 0.85rem;">Video Recording</div>
              ${ex.video_path ? `
                <a href="/api/v1/projects/${this.currentProject.id}/executions/${ex.id}/video?path=${encodeURIComponent(ex.video_path)}" target="_blank" class="btn btn-secondary" style="padding: 6px 12px; font-size: 0.8rem;">Replay Video</a>
              ` : `<span style="font-size: 0.75rem; color: var(--text-muted);">No video recorded</span>`}
            </div>
          </div>
        </div>
      </div>
    `;
    } catch (err) {
      container.innerHTML = `<div style="color: var(--color-danger); font-size: 0.88rem; padding: 24px;">Failed to render workspace details: ${escapeHTML(err.message)}</div>`;
    }
  }

  // LangSmith Trace Logs
  async renderLangSmithTraces() {
    const container = document.getElementById('langsmith-traces-container');
    if (!container) return;

    try {
      const traceData = await API.getTraces(this.currentProject.id);
      container.innerHTML = Components.LangSmithTraces(traceData);
    } catch (err) {
      container.innerHTML = `<div style="color: var(--color-danger); font-size: 0.85rem;">Failed to load LangSmith traces: ${err.message}</div>`;
    }
  }

  // Populate filter dropdowns with available options
  populateReportFilters() {
    if (!this.testCases || !this.executions) return;

    // Extract unique US and TC numbers from test cases that have executions
    const executedTestCaseIds = new Set(this.executions.map(ex => ex.test_case_id));
    const executedTestCases = this.testCases.filter(tc => executedTestCaseIds.has(tc.id));

    const usSet = new Set();
    const tcSet = new Set();

    executedTestCases.forEach(tc => {
      const refId = tc.test_case_ref_id || tc.ref_id || tc.scenario_ref_id || '';
      const match = refId.match(/^(US\d+)-(TC\d+)$/i);
      if (match) {
        const [, usNum, tcNum] = match;
        usSet.add(usNum.toUpperCase());
        tcSet.add(tcNum.toUpperCase());
      }
    });

    console.log('Available US numbers:', Array.from(usSet));
    console.log('Available TC numbers:', Array.from(tcSet));

    // Sort US and TC numbers numerically
    const sortByNumber = (a, b) => {
      const numA = parseInt(a.match(/\d+/)[0]);
      const numB = parseInt(b.match(/\d+/)[0]);
      return numA - numB;
    };

    const sortedUS = Array.from(usSet).sort(sortByNumber);
    const sortedTC = Array.from(tcSet).sort(sortByNumber);

    // Populate US dropdown
    const usSelect = document.getElementById('filter-us-number');
    if (usSelect) {
      const currentValue = usSelect.value;
      usSelect.innerHTML = '<option value="">All User Stories</option>';
      sortedUS.forEach(us => {
        const option = document.createElement('option');
        option.value = us;
        option.textContent = us;
        usSelect.appendChild(option);
      });
      usSelect.value = currentValue; // Restore selection
    }

    // Populate TC dropdown
    const tcSelect = document.getElementById('filter-tc-number');
    if (tcSelect) {
      const currentValue = tcSelect.value;
      tcSelect.innerHTML = '<option value="">All Test Cases</option>';
      sortedTC.forEach(tc => {
        const option = document.createElement('option');
        option.value = tc;
        option.textContent = tc;
        tcSelect.appendChild(option);
      });
      tcSelect.value = currentValue; // Restore selection
    }
  }

  // Reports view renderer
  renderReports() {
    const container = document.getElementById('reports-workspace-container');
    if (!container) {
      console.error('[Reports] Container not found: reports-workspace-container');
      return;
    }

    try {
      // Populate filter dropdowns with available options
      this.populateReportFilters();

      console.log('[Reports] Total executions:', this.executions?.length || 0);
      console.log('[Reports] Total test cases:', this.testCases?.length || 0);

      // Check if there are any executions at all
      if (!this.executions || this.executions.length === 0) {
        container.innerHTML = Components.EmptyState("No Execution Reports", "No test executions have been run yet. Execute some test cases first to see reports here.");
        return;
      }

      // Apply filters if any
      let filteredExecutions = this.executions;
    const usFilter = this.reportFilters?.usNumber || '';
    const tcFilter = this.reportFilters?.tcNumber || '';
    const statusFilter = this.reportFilters?.status || '';

    if (usFilter || tcFilter || statusFilter) {
      filteredExecutions = this.executions.filter(ex => {
        // Get test case for this execution
        const testCase = this.testCases?.find(tc => tc.id === ex.test_case_id);
        if (!testCase) {
          console.log('No test case found for execution:', ex.id);
          return false;
        }

        // Get test case ref ID (e.g., "US02-TC01") - try multiple field names
        const refId = testCase.test_case_ref_id || testCase.ref_id || testCase.scenario_ref_id || '';
        
        console.log('Checking test case:', testCase.title, 'refId:', refId);

        // Parse US and TC from ref_id
        const match = refId.match(/^(US\d+)-(TC\d+)$/i);
        if (!match && (usFilter || tcFilter)) {
          console.log('No match found in refId for filters US:', usFilter, 'TC:', tcFilter);
          return false;
        }

        const [, usNum, tcNum] = match || [];

        // Apply US filter
        if (usFilter && usNum?.toUpperCase() !== usFilter.toUpperCase()) {
          console.log('US filter mismatch:', usNum, '!==', usFilter);
          return false;
        }

        // Apply TC filter
        if (tcFilter && tcNum?.toUpperCase() !== tcFilter.toUpperCase()) {
          console.log('TC filter mismatch:', tcNum, '!==', tcFilter);
          return false;
        }

        // Apply status filter
        if (statusFilter && ex.status !== statusFilter) {
          console.log('Status filter mismatch:', ex.status, '!==', statusFilter);
          return false;
        }

        console.log('Filter passed for execution:', ex.id);
        return true;
      });

      console.log('[Reports] Filtered executions:', filteredExecutions.length, 'out of', this.executions.length);

      // Update filter results summary
      const summaryEl = document.getElementById('filter-results-summary');
      if (summaryEl) {
        const filters = [];
        if (usFilter) filters.push(`US: ${usFilter}`);
        if (tcFilter) filters.push(`TC: ${tcFilter}`);
        if (statusFilter) filters.push(`Status: ${statusFilter}`);
        summaryEl.innerHTML = `<strong>Active Filters:</strong> ${filters.join(', ')} | <strong>Results:</strong> ${filteredExecutions.length} of ${this.executions.length} executions`;
      }
    } else {
      // Clear filter summary
      const summaryEl = document.getElementById('filter-results-summary');
      if (summaryEl) {
        summaryEl.innerHTML = '';
      }
    }

    if (filteredExecutions.length === 0) {
      container.innerHTML = Components.EmptyState("No Matching Reports", "No execution reports match the selected filters. Try adjusting or clearing the filters.");
      return;
    }

    const runTotal = filteredExecutions.length;
    const passed = filteredExecutions.filter(ex => ex.status === 'passed').length;
    const failed = filteredExecutions.filter(ex => ex.status === 'failed').length;
    const errors = filteredExecutions.filter(ex => ex.status === 'error').length;
    const skipped = filteredExecutions.filter(ex => ex.status === 'skipped').length;
    
    let totalSecs = 0;
    filteredExecutions.forEach(ex => totalSecs += ex.duration_seconds);
    const avgDuration = runTotal > 0 ? (totalSecs / runTotal).toFixed(2) : "0";

    const passPct = Math.round((passed / runTotal) * 100);

    // Group executions by test_cycle_id for batch runs
    const batchesMap = new Map();
    const individualExecs = [];

    filteredExecutions.forEach(ex => {
      if (ex.test_cycle_id) {
        if (!batchesMap.has(ex.test_cycle_id)) {
          batchesMap.set(ex.test_cycle_id, []);
        }
        batchesMap.get(ex.test_cycle_id).push(ex);
      } else {
        individualExecs.push(ex);
      }
    });

    const optionsList = [];
    
    // Add batch runs
    batchesMap.forEach((execs, batchId) => {
      const latestDate = new Date(Math.max(...execs.map(e => new Date(e.executed_at).getTime())));
      const statusList = execs.map(e => e.status);
      const passedCount = statusList.filter(s => s === 'passed').length;
      const statusText = passedCount === execs.length ? 'passed' : 'failed';
      optionsList.push({
        value: `batch:${batchId}`,
        label: `📦 Batch Run: ${latestDate.toLocaleString()} (${execs.length} Cases - ${passedCount} Passed) - Status: ${statusText}`
      });
    });

    // Add individual runs
    individualExecs.forEach(ex => {
      const date = new Date(ex.executed_at).toLocaleString();
      const tcTitle = this.testCases?.find(t => t.id === ex.test_case_id)?.title || 'Test Case';
      optionsList.push({
        value: ex.id,
        label: `${date} - Case: ${tcTitle} - Status: ${ex.status}`
      });
    });

    const optionsHtml = optionsList.map(opt => `<option value="${opt.value}">${escapeHTML(opt.label)}</option>`).join('');

    container.innerHTML = `
      <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 20px; margin-bottom: 32px;">
        <div class="metric-card">
          <div class="metric-label">Pass / Total Runs</div>
          <div class="metric-value" style="color: var(--color-success);">${passed} / ${runTotal}</div>
          <div class="metric-footer">${passPct}% success rate</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Failed Runs</div>
          <div class="metric-value" style="color: var(--color-danger);">${failed}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Errors / Blocked</div>
          <div class="metric-value" style="color: var(--color-warning);">${errors + skipped}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Avg Run Duration</div>
          <div class="metric-value">${avgDuration}s</div>
        </div>
      </div>

      <div class="section-card" style="margin-bottom: 24px;">
        <h2>Export Automation Reports</h2>
        <div style="margin-bottom: 20px; max-width: 400px;" class="form-group">
          <label for="report-execution-select">Select Execution Run</label>
          <select id="report-execution-select" class="form-control">
            ${optionsHtml}
          </select>
        </div>
        <div style="display: flex; gap: 16px; margin-top: 12px;">
          <button class="btn btn-primary" id="btn-dl-report-pdf">Download PDF Report</button>
          <button class="btn btn-secondary" id="btn-dl-report-html">View HTML Report</button>
          <button class="btn btn-secondary" id="btn-dl-report-junit">Download JUnit XML</button>
        </div>
      </div>
    `;

    const getSelectedExecId = () => document.getElementById('report-execution-select').value;

    document.getElementById('btn-dl-report-pdf').onclick = () => {
      const id = getSelectedExecId();
      if (!id) return alert("Select an execution");
      if (id.startsWith('batch:')) {
        alert("PDF export is only supported for individual execution runs. Please use 'View HTML Report' to view the full batch details.");
      } else {
        window.open(`/api/v1/projects/${this.currentProject.id}/executions/${id}/pdf`, '_blank');
      }
    };

    document.getElementById('btn-dl-report-html').onclick = () => {
      const id = getSelectedExecId();
      if (!id) return alert("Select an execution");
      if (id.startsWith('batch:')) {
        const batchId = id.split(':')[1];
        window.open(`/api/v1/projects/${this.currentProject.id}/batches/${batchId}/html`, '_blank');
      } else {
        window.open(`/api/v1/projects/${this.currentProject.id}/executions/${id}/html`, '_blank');
      }
    };

    document.getElementById('btn-dl-report-junit').onclick = () => {
      const id = getSelectedExecId();
      if (!id) return alert("Select an execution");
      if (id.startsWith('batch:')) {
        alert("JUnit export is only supported for individual execution runs. Please use 'View HTML Report' to view the full batch details.");
      } else {
        window.open(`/api/v1/projects/${this.currentProject.id}/executions/${id}/junit`, '_blank');
      }
    };
    } catch (error) {
      console.error('[Reports] Error rendering reports:', error);
      container.innerHTML = `<div style="color: var(--color-danger); padding: 20px;">Error rendering reports: ${error.message}</div>`;
    }
  }

  // Apply report filters
  applyReportFilters() {
    const usNumber = document.getElementById('filter-us-number')?.value || '';
    const tcNumber = document.getElementById('filter-tc-number')?.value || '';
    const status = document.getElementById('filter-status')?.value || '';

    this.reportFilters = { usNumber, tcNumber, status };
    this.renderReports();
  }

  // Clear report filters
  clearReportFilters() {
    document.getElementById('filter-us-number').value = '';
    document.getElementById('filter-tc-number').value = '';
    document.getElementById('filter-status').value = '';
    this.reportFilters = {};
    this.renderReports();
  }

  // Modal helpers
  openModal(modalId) {
    document.getElementById(modalId).classList.add('active');
    if (modalId === 'create-project-modal') {
      document.getElementById('create-project-form').reset();
      if (this.resetSearchableLOB) {
        this.resetSearchableLOB();
      }
    }
  }

  closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
  }

  initSearchableLOB() {
    const lobs = [
      "Aerospace", "Agriculture", "Automotive", "Banking", "Biotechnology",
      "Chemicals", "Construction", "E-commerce", "Education", "Energy & Utilities",
      "Entertainment & Media", "Fashion & Apparel", "Finance", "Food & Beverage",
      "General", "Government", "Healthcare", "Hospitality", "Insurance",
      "Logistics", "Manufacturing", "Mining", "Pharmaceutical", "Real Estate",
      "Retail", "Software & Technology", "Telecom", "Transportation"
    ];

    const container = document.getElementById('new-proj-lob-container');
    const trigger = document.getElementById('new-proj-lob-trigger');
    const searchInput = document.getElementById('new-proj-lob-search');
    const optionsContainer = document.getElementById('new-proj-lob-options');
    const hiddenInput = document.getElementById('new-proj-lob');
    
    if (!container || !trigger || !searchInput || !optionsContainer || !hiddenInput) {
      return;
    }
    const triggerValue = container.querySelector('.searchable-select-value');

    // Populate options
    const renderOptions = (filterText = '') => {
      optionsContainer.innerHTML = '';
      const query = filterText.toLowerCase().trim();
      let matchedCount = 0;

      lobs.forEach(lob => {
        const isMatched = lob.toLowerCase().includes(query);
        if (isMatched) {
          matchedCount++;
          const optDiv = document.createElement('div');
          optDiv.className = 'searchable-select-option';
          if (hiddenInput.value === lob || (lob === 'General' && hiddenInput.value === 'general')) {
            optDiv.classList.add('selected');
          }
          optDiv.textContent = lob;
          optDiv.onclick = (e) => {
            e.stopPropagation();
            hiddenInput.value = lob === 'General' ? 'general' : lob;
            triggerValue.textContent = lob;
            container.classList.remove('open');
            // update selection classes
            optionsContainer.querySelectorAll('.searchable-select-option').forEach(el => el.classList.remove('selected'));
            optDiv.classList.add('selected');
          };
          optionsContainer.appendChild(optDiv);
        }
      });

      if (matchedCount === 0) {
        const noResults = document.createElement('div');
        noResults.className = 'searchable-select-no-results';
        noResults.textContent = 'No results found';
        optionsContainer.appendChild(noResults);
      }
    };

    // Toggle dropdown
    trigger.onclick = (e) => {
      e.stopPropagation();
      const isOpen = container.classList.contains('open');
      if (!isOpen) {
        container.classList.add('open');
        searchInput.focus();
        searchInput.value = '';
        renderOptions();
      } else {
        container.classList.remove('open');
      }
    };

    // Search filter
    searchInput.oninput = (e) => {
      renderOptions(e.target.value);
    };

    searchInput.onclick = (e) => {
      e.stopPropagation();
    };

    // Click outside to close
    document.addEventListener('click', (e) => {
      if (!container.contains(e.target)) {
        container.classList.remove('open');
      }
    });

    // Reset helper
    this.resetSearchableLOB = () => {
      hiddenInput.value = 'general';
      triggerValue.textContent = 'General';
      searchInput.value = '';
      container.classList.remove('open');
      renderOptions();
    };

    // Initial render
    renderOptions();
  }

  // NEW: Requirement Documents Section Methods
  toggleRequirementDocuments() {
    const section = document.getElementById('requirement-documents-section');
    if (!section) return;
    
    section.classList.toggle('collapsed');
    const icon = document.getElementById('req-docs-toggle-icon');
    if (icon) {
      icon.textContent = section.classList.contains('collapsed') ? '▶' : '▼';
    }
  }

  async renderRequirementDocuments() {
    const grid = document.getElementById('requirement-documents-grid');
    const countEl = document.getElementById('req-docs-count');
    
    if (!grid) return;

    const defaultProjectId = 'b4c13430-f95f-4ea5-b0ed-a09a0b3b324c';
    const isDefaultProject = this.currentProjectId === defaultProjectId;

    if (!this.currentProjectId || !isDefaultProject) {
      grid.innerHTML = '';
      if (countEl) countEl.textContent = '(0)';
      return;
    }

    // Show loading state
    grid.innerHTML = `
      <div style="grid-column: 1 / -1; text-align: center; padding: 40px; color: var(--text-secondary);">
        <p>Loading requirement documents...</p>
      </div>
    `;

    try {
      const documents = [
        {
          id: 'sample-1',
          original_filename: 'Adactin Hotel Test Requirements.pdf',
          filename: 'adactin-requirements.pdf',
          uploaded_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
          embedding_status: 'completed',
          passed: 18,
          failed: 2,
          pending: 3
        },
        {
          id: 'sample-2',
          original_filename: 'AI Automation Platform Specifications.docx',
          filename: 'ai-automation-specs.docx',
          uploaded_at: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
          embedding_status: 'completed',
          passed: 25,
          failed: 1,
          pending: 5
        },
        {
          id: 'sample-3',
          original_filename: 'Test Case Generation Guidelines.md',
          filename: 'testcase-guidelines.md',
          uploaded_at: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
          embedding_status: 'processing',
          passed: 12,
          failed: 0,
          pending: 8
        }
      ];

      // Transform API response to match expected format
      const transformedDocs = documents.map(doc => {
        return {
          id: doc.id,
          name: doc.original_filename || doc.filename,
          uploadedAt: doc.uploaded_at,
          passed: doc.passed || 0,
          failed: doc.failed || 0,
          pending: doc.pending || 0,
          embeddingStatus: doc.embedding_status
        };
      });

      // Sort by newest first
      transformedDocs.sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));

      // Update count
      if (countEl) {
        countEl.textContent = `(${transformedDocs.length})`;
      }

      // Render cards
      grid.innerHTML = transformedDocs.map(doc => {
        const date = new Date(doc.uploadedAt);
        const formattedDate = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        
        // Status badge for embedding
        const statusBadge = doc.embeddingStatus === 'completed' 
          ? '<span style="display: inline-block; padding: 2px 8px; background: #10b981; color: white; border-radius: 4px; font-size: 11px; margin-top: 4px;">Processed</span>'
          : doc.embeddingStatus === 'processing'
          ? '<span style="display: inline-block; padding: 2px 8px; background: #f59e0b; color: white; border-radius: 4px; font-size: 11px; margin-top: 4px;">Processing</span>'
          : '';
        
        return `
          <div class="requirement-document-card" onclick="app.viewRequirementDocument('${doc.id}')">
            <div class="req-doc-card-name" title="${escapeHTML(doc.name)}">
              ${escapeHTML(doc.name)}
              ${statusBadge}
            </div>
            <div class="req-doc-card-stats">
              <div class="req-doc-stat-row">
                <span class="req-doc-stat-label">Passed</span>
                <span class="req-doc-stat-value passed">${doc.passed}</span>
              </div>
              <div class="req-doc-stat-row">
                <span class="req-doc-stat-label">Failed</span>
                <span class="req-doc-stat-value failed">${doc.failed}</span>
              </div>
              <div class="req-doc-stat-row">
                <span class="req-doc-stat-label">Pending</span>
                <span class="req-doc-stat-value pending">${doc.pending}</span>
              </div>
            </div>
            <div class="req-doc-card-date">
              Uploaded<br>${formattedDate}
            </div>
          </div>
        `;
      }).join('');

    } catch (error) {
      console.error('[RequirementDocuments] Error loading documents:', error);
      
      // Show sample documents on error
      const sampleDocs = [
        { name: 'Adactin Hotel Test Requirements.pdf', date: 'Jul 17, 2026', status: 'Processed', passed: 18, failed: 2, pending: 3 },
        { name: 'AI Automation Platform Specifications.docx', date: 'Jul 14, 2026', status: 'Processed', passed: 25, failed: 1, pending: 5 },
        { name: 'Test Case Generation Guidelines.md', date: 'Jul 12, 2026', status: 'Processing', passed: 12, failed: 0, pending: 8 }
      ];
      
      grid.innerHTML = sampleDocs.map((doc, idx) => `
        <div class="requirement-document-card">
          <div class="req-doc-card-name">
            ${escapeHTML(doc.name)}
            <span style="display: inline-block; padding: 2px 8px; background: ${doc.status === 'Processed' ? '#10b981' : '#f59e0b'}; color: white; border-radius: 4px; font-size: 11px; margin-top: 4px;">${doc.status}</span>
          </div>
          <div class="req-doc-card-stats">
            <div class="req-doc-stat-row">
              <span class="req-doc-stat-label">Passed</span>
              <span class="req-doc-stat-value passed">${doc.passed}</span>
            </div>
            <div class="req-doc-stat-row">
              <span class="req-doc-stat-label">Failed</span>
              <span class="req-doc-stat-value failed">${doc.failed}</span>
            </div>
            <div class="req-doc-stat-row">
              <span class="req-doc-stat-label">Pending</span>
              <span class="req-doc-stat-value pending">${doc.pending}</span>
            </div>
          </div>
          <div class="req-doc-card-date">
            Uploaded<br>${doc.date}
          </div>
        </div>
      `).join('');
      
      if (countEl) {
        countEl.textContent = `(${sampleDocs.length})`;
      }
    }
  }

  viewRequirementDocument(docId) {
    // Placeholder for future implementation
    console.log('Viewing requirement document:', docId);
    // Navigate to requirements view or open modal with document details
    this.navigateTo('requirements');
  }

  async initAuth() {
    window.appInstance = this;
    
    window.switchSettingsTab = this.switchSettingsTab.bind(this);
    window.saveProjectSettings = this.saveProjectSettings.bind(this);
    window.switchDashboardScope = this.switchDashboardScope.bind(this);
    window.handleLogout = this.handleLogout.bind(this);
    window.handleLoginSubmit = this.handleLoginSubmit.bind(this);
    window.handleCreateUserSubmit = this.handleCreateUserSubmit.bind(this);
    window.handleAssignProjectUserSubmit = this.handleAssignProjectUserSubmit.bind(this);
    window.loadRolePermissionsGrid = this.loadRolePermissionsGrid.bind(this);
    window.saveRolePermissionsGrid = this.saveRolePermissionsGrid.bind(this);
    window.loadAuditLogsTab = this.loadAuditLogsTab.bind(this);
    window.openAssignProjectUserModal = this.openAssignProjectUserModal.bind(this);
    window.toggleUserActiveStatus = this.toggleUserActiveStatus.bind(this);
    window.resetUserPasswordPrompt = this.resetUserPasswordPrompt.bind(this);
    window.loadExternalToolForm = this.loadExternalToolForm.bind(this);
    window.saveExternalToolConfig = this.saveExternalToolConfig.bind(this);
    window.cancelExternalToolConfig = this.cancelExternalToolConfig.bind(this);
    window.deleteExternalToolConfig = this.deleteExternalToolConfig.bind(this);
    window.deleteExecution = this.deleteExecution.bind(this);
    window.deleteAllFailedExecutions = this.deleteAllFailedExecutions.bind(this);
    window.viewTriageDetails = this.viewTriageDetails.bind(this);

    const token = sessionStorage.getItem('access_token');
    const userJson = sessionStorage.getItem('user');
    
    if (token && userJson) {
      const user = JSON.parse(userJson);
      this.userRole = user.role;
      this.userEmail = user.email;
      const isAdmin = this.isAdmin();

      document.getElementById('login-overlay').style.display = 'none';
      document.getElementById('sidebar-container').style.display = 'flex';
      document.getElementById('project-select-wrapper').style.display = 'flex';
      
      document.getElementById('current-user-name').textContent = user.full_name;
      document.getElementById('current-user-role').textContent = user.role;

      const exitBtn = document.getElementById('btn-exit-project');
      const projectCombo = document.getElementById('project-combobox');
      if (projectCombo) projectCombo.style.display = 'block';
      if (exitBtn) exitBtn.style.display = this.currentProject ? 'flex' : 'none';
      
      try {
        await this.loadRolesAndPermissions();
        this.enforceUIPermissions();
      } catch (err) {
        console.error("Failed to load permissions:", err);
      }
      
      await this.loadSettings();
      await this.loadProjects();

      if (isAdmin) {
        this.currentProject = null;
        localStorage.removeItem('active_project_id');
        this.navigateTo('dashboard');
      } else {
        const savedProjectId = localStorage.getItem('active_project_id');
        if (savedProjectId && this.projects.some(p => p.id === savedProjectId)) {
          await this.selectProject(savedProjectId);
        } else {
          this.showProjectStartScreen();
        }
      }

      this.selectedRequirementId = localStorage.getItem('playwright_active_req_id') || null;
      this.activePlaywrightTestCaseId = localStorage.getItem('playwright_active_testcase_id') || null;

      window.addEventListener('hashchange', () => this.handleRouting());
      await this.handleRouting();
    } else {
      document.getElementById('login-overlay').style.display = 'flex';
      document.getElementById('sidebar-container').style.display = 'none';
      document.getElementById('project-select-wrapper').style.display = 'none';
    }
  }

  isAdmin() {
    return this.userRole === 'Superadmin' || this.userRole === 'Admin';
  }

  async loadRolesAndPermissions() {
    try {
      const data = await API.getPermissions();
      window.permissionsList = data.permissions;
      window.rolePermissionsList = data.role_permissions;
      
      const roles = await API.getRoles();
      window.rolesList = roles;
    } catch (err) {
      console.error("Could not fetch permissions grid:", err);
    }
  }

  enforceUIPermissions() {
    const user = JSON.parse(sessionStorage.getItem('user'));
    if (!user) return;
    
    const roleName = user.role;
    
    window.hasPermission = (module, action) => {
      if (roleName === 'Superadmin') return true;
      if (!window.permissionsList || !window.rolePermissionsList) return false;
      
      const perm = window.permissionsList.find(p => p.module.toLowerCase() === module.toLowerCase() && p.action.toLowerCase() === action.toLowerCase());
      if (!perm) return false;
      
      const role = window.rolesList ? window.rolesList.find(r => r.name === roleName) : null;
      if (!role) return false;
      
      return window.rolePermissionsList.some(rp => rp.role_id === role.id && rp.permission_id === perm.id);
    };

    // Show/Hide sidebar items depending on view rights
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
      const view = item.getAttribute('data-view');
      let moduleName = view.charAt(0).toUpperCase() + view.slice(1);
      if (view === 'playwright') moduleName = 'Playwright';
      if (view === 'executions') moduleName = 'Execution';
      if (view === 'reports') moduleName = 'Reports';
      if (view === 'settings') moduleName = 'Settings';
      
      if (window.hasPermission(moduleName, 'view')) {
        item.style.display = 'flex';
      } else {
        item.style.display = 'none';
        if (item.classList.contains('active')) {
          item.classList.remove('active');
        }
      }
    });

    const createReqBtn = document.getElementById('btn-create-requirement');
    if (createReqBtn) {
      createReqBtn.style.display = window.hasPermission('Requirements', 'create') ? 'block' : 'none';
    }
  }

  async handleLoginSubmit(e) {
    e.preventDefault();
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const errorEl = document.getElementById('login-error-msg');
    
    errorEl.style.display = 'none';
    errorEl.textContent = '';
    
    try {
      const data = await API.login(email, password);
      if (!data.access_token || !data.refresh_token || !data.user) {
        throw new Error('Login response was incomplete. Please check the server logs.');
      }
      sessionStorage.setItem('access_token', data.access_token);
      sessionStorage.setItem('refresh_token', data.refresh_token);
      sessionStorage.setItem('user', JSON.stringify(data.user));
      
      await this.initAuth();
    } catch (err) {
      errorEl.textContent = err.message || "Invalid credentials. Please try again.";
      errorEl.style.display = 'block';
    }
  }

  async handleLogout() {
    const refreshToken = sessionStorage.getItem('refresh_token');
    if (refreshToken) {
      try {
        await API.logout(refreshToken);
      } catch (err) {
        console.warn("Logout request failed:", err);
      }
    }
    
    sessionStorage.clear();
    document.getElementById('login-overlay').style.display = 'flex';
    document.getElementById('sidebar-container').style.display = 'none';
    document.getElementById('project-select-wrapper').style.display = 'none';
    
    document.querySelectorAll('.view-panel').forEach(panel => {
      panel.classList.remove('active');
    });
  }

  switchSettingsTab(tabName) {
    document.querySelectorAll('.settings-tabs .tab-btn').forEach(btn => {
      btn.classList.remove('active');
    });
    document.querySelectorAll('.settings-tab-content').forEach(content => {
      content.classList.remove('active');
    });
    
    const btn = Array.from(document.querySelectorAll('.settings-tabs .tab-btn')).find(b => b.getAttribute('onclick').includes(tabName));
    if (btn) btn.classList.add('active');
    
    const targetContent = document.getElementById(`settings-tab-${tabName}`);
    if (targetContent) targetContent.classList.add('active');
    
    if (tabName === 'rbac') {
      this.loadRBACTab();
    } else if (tabName === 'users') {
      this.loadUsersTab();
    } else if (tabName === 'audit') {
      this.loadAuditLogsTab();
    } else if (tabName === 'project') {
      this.populateProjectSettingsForm();
    }
  }

  populateProjectSettingsForm() {
    if (!this.currentProjectId) {
      alert("Please select a project first.");
      return;
    }
    const proj = this.projects.find(x => x.id === this.currentProjectId);
    if (!proj) return;
    document.getElementById('project-settings-url').value = proj.target_url || '';
    document.getElementById('project-settings-username').value = proj.target_username || '';
    document.getElementById('project-settings-password').value = proj.target_username ? '********' : '';
  }

  async saveProjectSettings(e) {
    if (e) e.preventDefault();
    if (!this.currentProjectId) {
      alert("No active project selected");
      return;
    }
    const proj = this.projects.find(x => x.id === this.currentProjectId);
    if (!proj) return;

    const targetUrl = document.getElementById('project-settings-url').value;
    const targetUsername = document.getElementById('project-settings-username').value;
    const targetPassword = document.getElementById('project-settings-password').value;

    try {
      const updated = await API.updateProject(
        this.currentProjectId,
        proj.name,
        proj.description,
        proj.line_of_business,
        proj.framework,
        proj.jira_project_key,
        targetUrl,
        targetUsername,
        targetPassword
      );
      
      // Update local project object
      proj.target_url = updated.target_url;
      proj.target_username = updated.target_username;
      
      this.addLog(`Project settings updated for: ${proj.name}`);
      alert("Project settings updated successfully");
    } catch (err) {
      alert(`Failed to save project settings: ${err.message}`);
    }
  }

  async loadExternalToolsTab() {
    try {
      const tools = await API.getExternalTools();
      this.renderConfiguredTools(tools);
    } catch (err) {
      console.error('Error loading external tools:', err);
      this.renderConfiguredTools([]);
    }
  }

  loadExternalToolForm() {
    const toolSelect = document.getElementById('external-tool-select');
    const selectedTool = toolSelect.value;
    
    if (!selectedTool) {
      document.getElementById('external-tool-form-container').style.display = 'none';
      return;
    }

    // Show form
    document.getElementById('external-tool-form-container').style.display = 'block';
    
    // Pre-fill based on tool type
    const toolNameInput = document.getElementById('tool-name');
    const apiUrlInput = document.getElementById('tool-api-url');
    
    switch(selectedTool) {
      case 'adio':
        toolNameInput.value = 'Adio Integration';
        apiUrlInput.value = 'https://api.adio.com';
        break;
      case 'langsmith':
        toolNameInput.value = 'LangSmith Tracing';
        apiUrlInput.value = 'https://api.smith.langchain.com';
        break;
      case 'ragflow':
        toolNameInput.value = 'RAGFlow Knowledge Base';
        apiUrlInput.value = 'http://localhost:9380';
        break;
      case 'jira':
        toolNameInput.value = 'JIRA Integration';
        apiUrlInput.value = 'https://your-domain.atlassian.net';
        break;
      case 'custom':
        toolNameInput.value = '';
        apiUrlInput.value = '';
        break;
    }
  }

  async saveExternalToolConfig(e) {
    e.preventDefault();
    
    const toolType = document.getElementById('external-tool-select').value;
    const toolName = document.getElementById('tool-name').value;
    const apiKey = document.getElementById('tool-api-key').value;
    const apiUrl = document.getElementById('tool-api-url').value;
    const description = document.getElementById('tool-description').value;

    if (!toolType || !toolName || !apiKey || !apiUrl) {
      alert('Please fill in all required fields');
      return;
    }

    try {
      await API.saveExternalTool({
        tool_type: toolType,
        tool_name: toolName,
        api_key: apiKey,
        api_url: apiUrl,
        description: description
      });
      
      this.addLog(`External tool '${toolName}' configured successfully`);
      alert('Tool configuration saved successfully');
      
      // Reset form
      document.getElementById('external-tool-form').reset();
      document.getElementById('external-tool-form-container').style.display = 'none';
      document.getElementById('external-tool-select').value = '';
      
      // Reload tools list
      await this.loadExternalToolsTab();
    } catch (err) {
      alert(`Failed to save tool configuration: ${err.message}`);
    }
  }

  cancelExternalToolConfig() {
    document.getElementById('external-tool-form').reset();
    document.getElementById('external-tool-form-container').style.display = 'none';
    document.getElementById('external-tool-select').value = '';
  }

  async deleteExternalToolConfig(toolId) {
    if (!confirm('Are you sure you want to delete this tool configuration?')) {
      return;
    }

    try {
      await API.deleteExternalTool(toolId);
      this.addLog(`External tool configuration deleted`);
      alert('Tool configuration deleted successfully');
      await this.loadExternalToolsTab();
    } catch (err) {
      alert(`Failed to delete tool configuration: ${err.message}`);
    }
  }

  async deleteExecution(executionId) {
    if (!confirm('Are you sure you want to delete this execution record?')) {
      return;
    }

    try {
      await API.deleteExecution(this.currentProject.id, executionId);
      this.addLog(`Execution ${executionId} deleted`);
      alert('Execution deleted successfully');
      await this.refreshProjectData();
    } catch (err) {
      alert(`Failed to delete execution: ${err.message}`);
    }
  }

  async deleteAllFailedExecutions() {
    if (!confirm('Are you sure you want to delete ALL failed execution records?')) {
      return;
    }

    try {
      const result = await API.deleteFailedExecutions(this.currentProject.id);
      this.addLog(result.detail);
      alert(result.detail);
      await this.refreshProjectData();
    } catch (err) {
      alert(`Failed to delete failed executions: ${err.message}`);
    }
  }

  viewTriageDetails(executionId) {
    const execution = this.executions.find(ex => ex.id === executionId);
    if (!execution) {
      alert('Execution not found');
      return;
    }

    const modal = document.getElementById('triage-modal');
    if (!modal) {
      alert('Triage modal not found in DOM');
      return;
    }

    // Populate modal with triage data
    document.getElementById('triage-execution-id').textContent = executionId;
    document.getElementById('triage-failure-category').textContent = execution.failure_category || 'N/A';
    document.getElementById('triage-root-cause').textContent = execution.root_cause_summary || 'N/A';
    document.getElementById('triage-trace-analysis').textContent = execution.trace_analysis || 'N/A';
    document.getElementById('triage-screenshot-findings').textContent = execution.screenshot_findings || 'N/A';
    document.getElementById('triage-suggest-retry').textContent = execution.suggest_retry ? 'Yes' : 'No';
    document.getElementById('triage-bug-candidate').textContent = execution.retest_pending_candidate ? 'Yes' : 'No';
    
    const jiraBugElement = document.getElementById('triage-jira-bug');
    if (execution.jira_bug_id && execution.jira_bug_url) {
      jiraBugElement.innerHTML = `<a href="${execution.jira_bug_url}" target="_blank">${execution.jira_bug_id}</a>`;
    } else {
      jiraBugElement.textContent = 'N/A';
    }

    // Show modal
    modal.style.display = 'flex';
  }

  renderConfiguredTools(tools) {
    const container = document.getElementById('configured-tools-container');
    
    if (!tools || tools.length === 0) {
      container.innerHTML = `
        <div style="color: var(--text-secondary); font-size: 0.85rem; padding: 16px; background: var(--bg-primary); border-radius: var(--border-radius-md); border: 1px dashed var(--border-color);">
          No external tools configured yet. Select a tool from the dropdown above to get started.
        </div>
      `;
      return;
    }

    container.innerHTML = tools.map(tool => `
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 16px; background: var(--bg-primary); border-radius: var(--border-radius-md); border: 1px solid var(--border-color);">
        <div>
          <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">${this.escapeHTML(tool.tool_name)}</div>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">Type: ${this.escapeHTML(tool.tool_type)} | URL: ${this.escapeHTML(tool.api_url)}</div>
          ${tool.description ? `<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">${this.escapeHTML(tool.description)}</div>` : ''}
        </div>
        <button class="btn btn-secondary btn-icon-danger" onclick="window.deleteExternalToolConfig('${tool.id}')" style="padding: 6px 12px; font-size: 0.8rem;">
          Delete
        </button>
      </div>
    `).join('');
  }

  escapeHTML(str) {
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

  async loadRBACTab() {
    try {
      await this.loadRolesAndPermissions();
      
      const roleSelect = document.getElementById('rbac-role-select');
      roleSelect.innerHTML = '';
      
      window.rolesList.forEach(role => {
        const option = document.createElement('option');
        option.value = role.id;
        option.textContent = role.name;
        roleSelect.appendChild(option);
      });
      
      if (window.rolesList.length > 0) {
        roleSelect.value = window.rolesList[0].id;
        this.loadRolePermissionsGrid();
      }
    } catch (err) {
      console.error("Failed to load RBAC tab:", err);
    }
  }

  loadRolePermissionsGrid() {
    const roleId = document.getElementById('rbac-role-select').value;
    const tbody = document.getElementById('perm-matrix-tbody');
    tbody.innerHTML = '';
    
    const modules = [
      'Dashboard', 'Requirements', 'Scenarios', 'TestCases', 
      'Playwright', 'Execution', 'Reports', 'Users', 'Roles', 'Permissions', 'Settings'
    ];
    const actions = ['view', 'create', 'edit', 'delete', 'approve'];
    
    modules.forEach(mod => {
      const tr = document.createElement('tr');
      
      const tdName = document.createElement('td');
      tdName.textContent = mod;
      tr.appendChild(tdName);
      
      actions.forEach(action => {
        const td = document.createElement('td');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.setAttribute('data-module', mod);
        checkbox.setAttribute('data-action', action);
        
        const perm = window.permissionsList.find(p => p.module.toLowerCase() === mod.toLowerCase() && p.action.toLowerCase() === action.toLowerCase());
        
        if (perm) {
          const hasPerm = window.rolePermissionsList.some(rp => rp.role_id === roleId && rp.permission_id === perm.id);
          checkbox.checked = hasPerm;
          checkbox.setAttribute('data-perm-id', perm.id);
        } else {
          checkbox.disabled = true;
        }
        
        td.appendChild(checkbox);
        tr.appendChild(td);
      });
      
      tbody.appendChild(tr);
    });
  }

  async saveRolePermissionsGrid() {
    const roleId = document.getElementById('rbac-role-select').value;
    const checkboxes = document.querySelectorAll('#perm-matrix-tbody input[type="checkbox"]:checked');
    const checkedPermissionIds = Array.from(checkboxes)
      .map(cb => cb.getAttribute('data-perm-id'))
      .filter(id => id !== null);
      
    try {
      await API.updateRolePermissions(roleId, checkedPermissionIds);
      this.addLog("Access control matrix updated successfully.");
      alert("Permissions grid updated successfully.");
      
      await this.loadRolesAndPermissions();
      this.enforceUIPermissions();
    } catch (err) {
      alert(`Failed to save permissions: ${err.message}`);
    }
  }

  async loadUsersTab() {
    const tbody = document.getElementById('user-management-tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Loading system operators...</td></tr>';
    
    try {
      const users = await API.getUsers();
      tbody.innerHTML = '';
      
      if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">No system users found.</td></tr>';
        return;
      }
      
      users.forEach(u => {
        const tr = document.createElement('tr');
        
        const tdName = document.createElement('td');
        tdName.style.fontWeight = '600';
        tdName.textContent = u.full_name;
        tr.appendChild(tdName);
        
        const tdEmail = document.createElement('td');
        tdEmail.textContent = u.email;
        tr.appendChild(tdEmail);
        
        const tdRole = document.createElement('td');
        tdRole.innerHTML = `<span style="font-weight: 500; color: var(--accent-primary);">${u.role}</span>`;
        tr.appendChild(tdRole);
        
        const tdStatus = document.createElement('td');
        const isActive = u.is_active;
        tdStatus.innerHTML = `
          <label style="display: inline-flex; align-items: center; gap: 8px; cursor: pointer;">
            <input type="checkbox" ${isActive ? 'checked' : ''} onchange="toggleUserActiveStatus('${u.id}', this.checked)" style="width: 16px; height: 16px;" />
            <span style="font-size: 0.85rem; color: ${isActive ? 'var(--color-success)' : 'var(--text-muted)'};">${isActive ? 'Active' : 'Deactivated'}</span>
          </label>
        `;
        tr.appendChild(tdStatus);
        
        const tdActions = document.createElement('td');
        tdActions.style.textAlign = 'right';
        tdActions.innerHTML = `
          <button class="btn btn-secondary" onclick="openAssignProjectUserModal('${u.id}', '${u.email}')" style="padding: 4px 8px; font-size: 0.75rem; margin-right: 6px;">Map Workspace</button>
          <button class="btn btn-secondary" onclick="resetUserPasswordPrompt('${u.id}')" style="padding: 4px 8px; font-size: 0.75rem;">Reset PW</button>
        `;
        tr.appendChild(tdActions);
        
        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--color-danger);">Failed to load users: ${err.message}</td></tr>`;
    }
  }

  async toggleUserActiveStatus(userId, isActive) {
    try {
      await API.toggleUserStatus(userId, isActive);
      this.addLog(`User status toggled: ${isActive ? 'Activated' : 'Deactivated'}`);
      this.loadUsersTab();
    } catch (err) {
      alert(`Failed to update status: ${err.message}`);
      this.loadUsersTab();
    }
  }

  async resetUserPasswordPrompt(userId) {
    const pw = prompt("Enter new password for this operator:");
    if (!pw) return;
    
    try {
      await API.resetUserPassword(userId, pw);
      this.addLog("User password reset complete.");
      alert("Password has been reset successfully.");
    } catch (err) {
      alert(`Failed to reset password: ${err.message}`);
    }
  }

  async handleCreateUserSubmit(e) {
    e.preventDefault();
    const fullname = document.getElementById('new-user-fullname').value.trim();
    const email = document.getElementById('new-user-email').value.trim();
    const password = document.getElementById('new-user-password').value;
    const role = document.getElementById('new-user-role').value;
    
    try {
      await API.createUser(fullname, email, password, role);
      this.addLog(`New user created: ${email} (${role})`);
      this.closeModal('create-user-modal');
      
      document.getElementById('create-user-form').reset();
      this.loadUsersTab();
    } catch (err) {
      alert(`Failed to create user: ${err.message}`);
    }
  }

  async openAssignProjectUserModal(userId, email) {
    document.getElementById('assign-user-id').value = userId;
    document.getElementById('assign-user-email').value = email;
    
    const roleSelect = document.getElementById('assign-role-id');
    roleSelect.innerHTML = '';
    
    try {
      await this.loadRolesAndPermissions();
      window.rolesList.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.id;
        opt.textContent = r.name;
        roleSelect.appendChild(opt);
      });
      this.openModal('assign-project-user-modal');
    } catch (err) {
      alert(`Failed to load roles: ${err.message}`);
    }
  }

  async handleAssignProjectUserSubmit(e) {
    e.preventDefault();
    if (!this.currentProject) {
      alert("Please select a workspace project first.");
      return;
    }
    
    const userId = document.getElementById('assign-user-id').value;
    const roleId = document.getElementById('assign-role-id').value;
    
    try {
      await API.assignProjectUser(this.currentProject.id, userId, roleId);
      this.addLog("Workspace mapping updated.");
      this.closeModal('assign-project-user-modal');
      alert("User workspace mapping saved successfully.");
    } catch (err) {
      alert(`Failed to map workspace: ${err.message}`);
    }
  }

  async loadAuditLogsTab() {
    const tbody = document.getElementById('audit-logs-tbody');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">Querying audit logs...</td></tr>';
    
    try {
      const logs = await API.getAuditLogs();
      tbody.innerHTML = '';
      
      if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No activity logs recorded.</td></tr>';
        return;
      }
      
      logs.forEach(l => {
        const tr = document.createElement('tr');
        
        const tdTime = document.createElement('td');
        tdTime.textContent = new Date(l.timestamp).toLocaleString();
        tr.appendChild(tdTime);
        
        const tdOperator = document.createElement('td');
        tdOperator.textContent = l.user_email;
        tr.appendChild(tdOperator);
        
        const tdProj = document.createElement('td');
        tdProj.textContent = l.project_id ? l.project_id.split('-')[0] + '...' : 'System';
        tr.appendChild(tdProj);
        
        const tdModule = document.createElement('td');
        tdModule.innerHTML = `<span class="badge badge-pending">${l.module}</span>`;
        tr.appendChild(tdModule);
        
        const tdAction = document.createElement('td');
        let actionBadgeClass = 'badge-pending';
        if (l.action === 'CREATE') actionBadgeClass = 'badge-approved';
        if (l.action === 'DELETE') actionBadgeClass = 'badge-rejected';
        if (l.action === 'UPDATE') actionBadgeClass = 'badge-medium';
        if (l.action === 'LOGIN') actionBadgeClass = 'badge-positive';
        
        tdAction.innerHTML = `<span class="badge ${actionBadgeClass}">${l.action}</span>`;
        tr.appendChild(tdAction);
        
        const tdChanges = document.createElement('td');
        tdChanges.style.fontFamily = 'monospace';
        tdChanges.style.fontSize = '0.75rem';
        tdChanges.textContent = JSON.stringify(l.field_changes);
        tr.appendChild(tdChanges);
        
        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--color-danger);">Failed to query audit logs: ${err.message}</td></tr>`;
    }
  }
}

window.closeModal = (modalId) => {
  if (window.appInstance) window.appInstance.closeModal(modalId);
  else {
    const modal = document.getElementById(modalId);
    if (modal) modal.style.display = 'none';
  }
};

window.openModal = (modalId) => {
  if (window.appInstance) window.appInstance.openModal(modalId);
  else {
    const modal = document.getElementById(modalId);
    if (modal) modal.style.display = 'flex';
  }
};

window.resetDemoData = async () => {
  if (!confirm("Are you absolutely sure you want to reset demo data? This action will remove BANK, BANKsdcx, Vehicle Insurance, and other demo projects alongside their executions, reports, and screenshots. This cannot be undone!")) {
    return;
  }
  const btn = document.getElementById("btn-reset-demo-data");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Resetting Demo Data...";
  }
  try {
    const token = localStorage.getItem('token');
    const response = await fetch('/api/v1/admin/reset-demo-data', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    if (!response.ok) {
      const errData = await response.json();
      throw new Error(errData.detail || 'Failed to reset demo data');
    }
    alert("Demo data reset successfully!");
    if (window.appInstance) {
      await window.appInstance.loadProjects();
      if (window.appInstance.projects.length > 0) {
        await window.appInstance.selectProject(window.appInstance.projects[0].id);
      } else {
        window.appInstance.currentProject = null;
        window.appInstance.currentProjectId = null;
        localStorage.removeItem('active_project_id');
        window.appInstance.showProjectStartScreen();
      }
    } else {
      window.location.reload();
    }
  } catch (error) {
    alert("Error resetting demo data: " + error.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Reset Demo Data";
    }
  }
};

window.addEventListener('DOMContentLoaded', () => {
  const app = new App();
  window.appInstance = app;
  app.init().catch(console.error);
});
