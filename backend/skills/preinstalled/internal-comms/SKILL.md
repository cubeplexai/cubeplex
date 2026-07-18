---
name: internal-comms
description: Use when writing internal communications such as status reports, leadership updates, 3P updates, company newsletters, FAQs, incident reports, and project updates.
version: 1.1.0
license: Complete terms in LICENSE.txt
keywords:
  - office
  - communications
  - status-report
  - leadership-update
  - writing
---

## When to use this skill
To write internal communications, use this skill for:
- 3P updates (Progress, Plans, Problems)
- Company newsletters
- FAQ responses
- Status reports
- Leadership updates
- Project updates
- Incident reports

## How to use this skill

To write any internal communication:

1. **Identify the communication type** from the request
2. **Load the appropriate guideline file** from the `examples/` directory under the `path` returned by `load_skill`:
    - `3p-updates.md` - For Progress/Plans/Problems team updates
    - `company-newsletter.md` - For company-wide newsletters
    - `faq-answers.md` - For answering frequently asked questions
    - `general-comms.md` - For anything else that doesn't explicitly match one of the above
3. **Follow the specific instructions** in that file for formatting, tone, and content gathering

If the communication type doesn't match any existing guideline, ask for clarification or more context about the desired format.

## Keywords
3P updates, company newsletter, company comms, weekly update, faqs, common questions, updates, internal comms
