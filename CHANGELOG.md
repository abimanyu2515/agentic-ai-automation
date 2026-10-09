# Changelog

All notable changes to the Agentic AI Automation Platform are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-07-30

### Added
- **LangSmith Tracing Integration**: Complete observability for all agent executions
  - Unified hierarchical trace showing all agents in one view
  - Automatic LLM call tracing with prompts, completions, and token usage
  - Environment variable configuration in `backend/graph/workflow.py`
  - Test script `test_all_agents_trace.py` for verification
  - Dashboard access at https://smith.langchain.com
  - See [docs/features/langsmith-tracing.md](docs/features/langsmith-tracing.md)

- **Jira HTML Report Attachments**: Execution reports attached to bug tickets
  - HTML reports automatically uploaded to JIRA defect tickets
  - Enhanced traceability with screenshots + full execution report
  - Graceful error handling if report unavailable
  - Modified `backend/agents/jira_sync_agent.py` (lines 314-323)
  - Filename format: `execution_report_{execution_id}.html`

### Changed
- **Documentation Reorganization**: Moved all markdown files to organized `docs/` structure
  - Created subdirectories: `features/`, `guides/`, `implementation-reports/`
  - Added comprehensive `docs/README.md` index
  - Updated architecture documentation with v1.3.0 features
  - Cleaned up project root directory

### Fixed
- LangSmith environment variables now properly exported to `os.environ`
- Test script field errors corrected (Scenario `requirement_id`, TestCase `evaluation_reason`)

## [1.2.0] - 2026-07-15

### Added
- **Professional Report Engine**: Enhanced HTML report generation
  - Modern responsive design with Chart.js visualizations
  - Pass/fail metrics and execution timeline
  - Screenshot gallery and execution details
  - See [docs/features/professional-reporting.md](docs/features/professional-reporting.md)

- **Enhanced Analytics Dashboard**: Improved metrics and visualizations
  - Real-time execution status tracking
  - Pass rate trends and coverage analysis
  - Priority distribution charts

### Changed
- Report generation logic centralized in `ReportAgent`
- Improved artifact management for screenshots and traces

## [1.1.0] - 2026-06-30

### Added
- **Naming Convention System**: Standardized User Story and Test Case IDs
  - Format: `US{sequence:02d}` for User Stories
  - Format: `US{sequence:02d}-TC{test_case_sequence:02d}` for Test Cases
  - Automatic ID generation and sequencing
  - See [docs/features/naming-convention.md](docs/features/naming-convention.md)

- **Filtering Improvements**: US/TC filtering in UI and API
  - Filter by status, priority, execution result
  - Search by ID or title
  - See [docs/features/filtering.md](docs/features/filtering.md)

- **Locator Fallback Strategy**: Hierarchical locator resolution (spec only)
  - Priority-based fallback chain
  - Multiple locator strategy support
  - See [docs/features/locator-fallback.md](docs/features/locator-fallback.md)

### Changed
- UI navigation restructured for better user experience
- Test case table enhanced with sortable columns

## [1.0.0] - 2026-06-01

### Initial Release
- **Multi-Agent LangGraph Pipeline**: 16 specialized agents
  - SupervisorAgent, ScenarioAgent, TestCaseAgent, EvaluationAgent
  - PlaywrightAgent, ExecutionAgent, ExecutionAnalysisAgent
  - DefectManagementAgent, ReportAgent, JiraSyncAgent
  - RequirementAnalystAgent, BacklogCreationAgent, QAStoryAnalyzerAgent
  - HumanApprovalAgent, FeatureInventoryAgent, AutomationOrchestratorAgent

- **Multi-Provider LLM Support**: OpenAI, Ollama, Anthropic, Gemini, Groq via LiteLLM

- **Requirement Ingestion**: Parse PDF, DOCX, XLSX, CSV; import JIRA stories

- **AI Test Generation**:
  - Scenario generation from requirements
  - Detailed test case creation
  - Playwright TypeScript script generation

- **Quality Evaluation**:
  - Duplicate detection using Jaccard similarity
  - Relevance scoring and confidence ranking

- **Human Review Gates**: Approve/reject scenarios and test cases

- **Test Execution**: Playwright test runner with real-time logs, screenshots, videos

- **Execution Analysis**: Root cause analysis, flakiness detection, failure categorization

- **JIRA Integration**:
  - Sync scenarios as Stories
  - Raise Bugs from failures
  - Bi-directional status sync

- **Dashboard Analytics**: Pass rates, coverage trends, defect tracking

- **RBAC**: Admin, Manager, Engineer, Viewer roles

- **Encrypted Vault**: Secure credential storage

- **RAG Support**: Optional RAGFlow/Chroma knowledge base

- **Audit Logging**: Complete mutation tracking

---

## Version History Summary

| Version | Release Date | Key Features |
|---------|--------------|--------------|
| **1.3.0** | 2026-07-30 | LangSmith tracing, Jira report attachments, docs reorganization |
| **1.2.0** | 2026-07-15 | Professional reporting engine, enhanced analytics |
| **1.1.0** | 2026-06-30 | Naming conventions, filtering, locator fallback spec |
| **1.0.0** | 2026-06-01 | Initial release with core multi-agent pipeline |

---

## Upgrade Notes

### 1.2.0 → 1.3.0
- **LangSmith Configuration**: Add to `.env`:
  ```env
  LANGSMITH_TRACING=true
  LANGSMITH_API_KEY=<your_api_key>
  LANGSMITH_ENDPOINT=https://api.smith.langchain.com
  LANGSMITH_PROJECT=My Project
  ```
- No database migrations required
- No breaking API changes
- Documentation moved to `docs/` directory (update internal links if needed)

### 1.1.0 → 1.2.0
- Report generation improved, no configuration changes needed
- Artifact storage structure unchanged

### 1.0.0 → 1.1.0
- Naming convention migration script provided for existing data
- UI routes updated for better navigation

---

## Future Roadmap

### Planned for 1.4.0
- [ ] Locator fallback integration (currently spec-only)
- [ ] Advanced flakiness detection with ML
- [ ] Multi-browser execution support (Firefox, Safari)
- [ ] Test data management enhancements

### Under Consideration
- [ ] Visual regression testing integration
- [ ] Performance testing capabilities
- [ ] API test generation
- [ ] Mobile testing support (Appium integration)
- [ ] Enhanced RAG with semantic search
- [ ] Real-time collaborative test authoring

---

## Contributing

For contribution guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md) (TBD).

## Support

For issues, questions, or feature requests, contact the development team or open an issue in the repository.
