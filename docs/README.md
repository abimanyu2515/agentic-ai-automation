# Documentation Index

This directory contains comprehensive documentation for the Agentic AI Automation Platform.

## Core Documentation

### Architecture & Design
- [**Architecture Overview**](architecture.md) - System architecture and component design
- [**Workflow Engine**](workflow.md) - LangGraph multi-agent pipeline
- [**Agent System**](agents.md) - Detailed agent descriptions and responsibilities
- [**API Reference**](api.md) - Complete REST API documentation

## Feature Documentation

### Core Features
- [**LangSmith Tracing**](features/langsmith-tracing.md) - Unified observability and tracing
- [**Professional Reporting**](features/professional-reporting.md) - Enhanced HTML report generation
- [**Report Specification**](features/report-specification.md) - Report engine design and capabilities
- [**Locator Fallback**](features/locator-fallback.md) - Hierarchical locator resolution
- [**Naming Convention**](features/naming-convention.md) - User Story and Test Case ID system
- [**Filtering System**](features/filtering.md) - US/TC filtering implementation

## Guides

### User Guides
- [**Adactin Reference**](guides/adactin-reference.md) - Test application reference guide
- [**Navigation Verification**](guides/navigation-verification.md) - UI navigation testing guide

### Developer Guides
- [**Report Debugging**](guides/report-debugging.md) - Professional report engine troubleshooting
- [**Report Audit**](guides/report-audit.md) - Report generation verification procedures
- [**Playwright Fix**](guides/playwright-fix.md) - Common Playwright issues and fixes

## Implementation Reports

### Completed Features
- [**Implementation Summary**](implementation-reports/implementation-summary.md) - Overall implementation status
- [**Task 4 Summary**](implementation-reports/task-4-summary.md) - Task 4 completion report
- [**Task 5 Summary**](implementation-reports/task-5-summary.md) - Task 5 completion report
- [**Task 6 Summary**](implementation-reports/task-6-summary.md) - Task 6 completion report
- [**Requirement Documents**](implementation-reports/requirement-documents.md) - Requirement system implementation
- [**Naming Convention Status**](implementation-reports/naming-convention-status.md) - ID convention rollout status
- [**Professional Report Implementation**](implementation-reports/professional-report-implementation.md) - Report engine implementation
- [**Report Enhancement**](implementation-reports/report-enhancement.md) - Report system enhancements
- [**Report Fix**](implementation-reports/report-fix.md) - Report generation fixes

## Quick Links

### Getting Started
1. Read [Architecture Overview](architecture.md) to understand the system design
2. Review [Workflow Engine](workflow.md) to understand the agent pipeline
3. Check [API Reference](api.md) for endpoint documentation
4. See [Agent System](agents.md) for agent responsibilities

### Common Tasks
- **Enable LangSmith Tracing**: See [LangSmith Tracing](features/langsmith-tracing.md)
- **Customize Reports**: See [Report Specification](features/report-specification.md)
- **Debug Report Issues**: See [Report Debugging](guides/report-debugging.md)
- **Understand Naming**: See [Naming Convention](features/naming-convention.md)

## Documentation Structure

```
docs/
├── README.md                    # This file
├── architecture.md              # System architecture
├── workflow.md                  # LangGraph pipeline
├── agents.md                    # Agent documentation
├── api.md                       # API reference
│
├── features/                    # Feature documentation
│   ├── langsmith-tracing.md
│   ├── professional-reporting.md
│   ├── report-specification.md
│   ├── locator-fallback.md
│   ├── naming-convention.md
│   └── filtering.md
│
├── guides/                      # How-to guides
│   ├── adactin-reference.md
│   ├── navigation-verification.md
│   ├── report-debugging.md
│   ├── report-audit.md
│   └── playwright-fix.md
│
└── implementation-reports/      # Implementation summaries
    ├── implementation-summary.md
    ├── task-4-summary.md
    ├── task-5-summary.md
    ├── task-6-summary.md
    ├── requirement-documents.md
    ├── naming-convention-status.md
    ├── professional-report-implementation.md
    ├── report-enhancement.md
    └── report-fix.md
```

## Contributing

When adding new documentation:
1. Place feature docs in `features/`
2. Place user/developer guides in `guides/`
3. Place implementation reports in `implementation-reports/`
4. Update this README with links
5. Follow the existing markdown formatting style

## Version History

- **v1.3.0** - LangSmith tracing integration, Jira HTML report attachments
- **v1.2.0** - Professional reporting engine, enhanced analytics
- **v1.1.0** - Naming convention system, filtering improvements
- **v1.0.0** - Initial release with core multi-agent pipeline
