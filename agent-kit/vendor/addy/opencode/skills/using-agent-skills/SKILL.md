---
name: using-agent-skills
description: Discovers and invokes agent skills. Use when starting a session, or when you need to decide which skill or workflow applies to the piece of work at hand. This is the meta-skill that governs how all other skills are discovered and invoked, including the installed ECC language skills, specialist agents and slash commands.
---

# Using Agent Skills

## Overview

Agent Skills is a collection of engineering workflow skills organized by development phase. Each skill encodes a specific process that senior engineers follow. This meta-skill helps you discover and apply the right skill for your current task.

## Skill Discovery

When a task arrives, identify the development phase and apply the corresponding skill:

```
Task arrives
    │
    ├── Don't know what you want yet? ──────→ interview-me
    ├── Have a rough concept, need variants? → idea-refine
    ├── New project/feature/change? ──→ spec-driven-development
    ├── No quality bar written down? ──→ constraint-driven-development
    ├── Have a spec, need tasks? ──────→ planning-and-task-breakdown
    ├── Implementing code? ────────────→ incremental-implementation
    │   ├── UI work? ─────────────────→ frontend-ui-engineering
    │   ├── API work? ────────────────→ api-and-interface-design
    │   ├── Need better context? ─────→ context-engineering
    │   ├── Need doc-verified code? ───→ source-driven-development
    │   └── Stakes high / unfamiliar code? ──→ doubt-driven-development
    ├── Writing/running tests? ────────→ test-driven-development
    │   └── Browser-based? ───────────→ browser-testing-with-devtools
    ├── Something broke? ──────────────→ debugging-and-error-recovery
    ├── Reviewing code? ───────────────→ code-review-and-quality
    │   ├── Too complex? ─────────────→ code-simplification
    │   ├── Security concerns? ───────→ security-and-hardening
    │   └── Performance concerns? ────→ performance-optimization
    ├── Committing/branching? ─────────→ git-workflow-and-versioning
    ├── CI/CD pipeline work? ──────────→ ci-cd-and-automation
    ├── Deprecating/migrating? ────────→ deprecation-and-migration
    ├── Writing docs/ADRs? ───────────→ documentation-and-adrs
    ├── Adding logs/metrics/alerts? ───→ observability-and-instrumentation
    └── Deploying/launching? ─────────→ shipping-and-launch
```

## Core Operating Behaviors

These behaviors apply at all times, across all skills. They are non-negotiable.

### 1. Surface Assumptions

Before implementing anything non-trivial, explicitly state your assumptions:

```
ASSUMPTIONS I'M MAKING:
1. [assumption about requirements]
2. [assumption about architecture]
3. [assumption about scope]
→ Correct me now or I'll proceed with these.
```

Don't silently fill in ambiguous requirements. The most common failure mode is making wrong assumptions and running with them unchecked. Surface uncertainty early — it's cheaper than rework.

### 2. Manage Confusion Actively

When you encounter inconsistencies, conflicting requirements, or unclear specifications:

1. **STOP.** Do not proceed with a guess.
2. Name the specific confusion.
3. Present the tradeoff or ask the clarifying question.
4. Wait for resolution before continuing.

**Bad:** Silently picking one interpretation and hoping it's right.
**Good:** "I see X in the spec but Y in the existing code. Which takes precedence?"

### 3. Push Back When Warranted

You are not a yes-machine. When an approach has clear problems:

- Point out the issue directly
- Explain the concrete downside (quantify when possible — "this adds ~200ms latency" not "this might be slower")
- Propose an alternative
- Accept the human's decision if they override with full information

Sycophancy is a failure mode. "Of course!" followed by implementing a bad idea helps no one. Honest technical disagreement is more valuable than false agreement.

### 4. Enforce Simplicity

Your natural tendency is to overcomplicate. Actively resist it.

Before finishing any implementation, ask:
- Can this be done in fewer lines?
- Are these abstractions earning their complexity?
- Would a staff engineer look at this and say "why didn't you just..."?

If you build 1000 lines and 100 would suffice, you have failed. Prefer the boring, obvious solution. Cleverness is expensive.

### 5. Maintain Scope Discipline

Touch only what you're asked to touch.

Do NOT:
- Remove comments you don't understand
- "Clean up" code orthogonal to the task
- Refactor adjacent systems as a side effect
- Delete code that seems unused without explicit approval
- Add features not in the spec because they "seem useful"

Your job is surgical precision, not unsolicited renovation.

### 6. Verify, Don't Assume

Every skill includes a verification step. A task is not complete until verification passes. "Seems right" is never sufficient — there must be evidence (passing tests, build output, runtime data).

Per-skill verification is the local check. The project-wide bar that applies to *every* change, regardless of which skill is active, is the Definition of Done: tests pass, no regressions, behavior verified at runtime, docs updated. See `../../references/definition-of-done.md`. It complements each task's acceptance criteria rather than replacing them.

## Failure Modes to Avoid

These are the subtle errors that look like productivity but create problems:

1. Making wrong assumptions without checking
2. Not managing your own confusion — plowing ahead when lost
3. Not surfacing inconsistencies you notice
4. Not presenting tradeoffs on non-obvious decisions
5. Being sycophantic ("Of course!") to approaches with clear problems
6. Overcomplicating code and APIs
7. Modifying code or comments orthogonal to the task
8. Removing things you don't fully understand
9. Building without a spec because "it's obvious"
10. Skipping verification because "it looks right"

## Skill Rules

1. **Check for an applicable skill before starting work.** Skills encode processes that prevent common mistakes.

2. **Skills are workflows, not suggestions.** Follow the steps in order. Don't skip verification steps.

3. **Multiple skills can apply.** A feature implementation might involve `idea-refine` → `spec-driven-development` → `planning-and-task-breakdown` → `incremental-implementation` → `test-driven-development` → `code-review-and-quality` → `code-simplification` → `shipping-and-launch` in sequence.

4. **When in doubt, start with a spec.** If the task is non-trivial and there's no spec, begin with `spec-driven-development`.

## Lifecycle Sequence

For a complete feature, the typical skill sequence is:

```
1.  interview-me                → Extract what the user actually wants
2.  idea-refine                 → Refine vague ideas
3.  spec-driven-development     → Define what we're building
4.  planning-and-task-breakdown → Break into verifiable chunks
5.  context-engineering         → Load the right context
6.  source-driven-development   → Verify against official docs
7.  incremental-implementation  → Build slice by slice
8.  observability-and-instrumentation → Instrument as you build (runs parallel with 7-9, not after)
9.  doubt-driven-development    → Cross-examine non-trivial decisions in-flight
10. test-driven-development     → Prove each slice works
11. code-review-and-quality     → Review before merge
12. code-simplification         → Reduce unnecessary complexity while preserving behavior
13. git-workflow-and-versioning → Clean commit history
14. documentation-and-adrs      → Document decisions
15. deprecation-and-migration   → Retire old systems and move users safely when needed
16. shipping-and-launch         → Deploy safely
```

Not every task needs every skill. A bug fix might only need: `debugging-and-error-recovery` → `test-driven-development` → `code-review-and-quality`.

## Quick Reference

| Phase | Skill | One-Line Summary |
|-------|-------|-----------------|
| Define | interview-me | Surface what the user actually wants before any plan, spec, or code exists |
| Define | idea-refine | Refine ideas through structured divergent and convergent thinking |
| Define | spec-driven-development | Requirements and acceptance criteria before code |
| Plan | planning-and-task-breakdown | Decompose into small, verifiable tasks |
| Build | incremental-implementation | Thin vertical slices, test each before expanding |
| Build | source-driven-development | Verify against official docs before implementing |
| Build | doubt-driven-development | Adversarial fresh-context review of every non-trivial decision |
| Build | context-engineering | Right context at the right time |
| Build | frontend-ui-engineering | Production-quality UI with accessibility |
| Build | api-and-interface-design | Stable interfaces with clear contracts |
| Verify | test-driven-development | Failing test first, then make it pass |
| Verify | browser-testing-with-devtools | Chrome DevTools MCP for runtime verification |
| Verify | debugging-and-error-recovery | Reproduce → localize → fix → guard |
| Review | code-review-and-quality | Five-axis review with quality gates |
| Review | code-simplification | Preserve behavior while reducing unnecessary complexity |
| Review | security-and-hardening | OWASP prevention, input validation, least privilege |
| Review | performance-optimization | Measure first, optimize only what matters |
| Ship | git-workflow-and-versioning | Atomic commits, clean history |
| Ship | ci-cd-and-automation | Automated quality gates on every change |
| Ship | deprecation-and-migration | Remove old systems and migrate users safely |
| Ship | documentation-and-adrs | Document the why, not just the what |
| Ship | observability-and-instrumentation | Structured logs, RED metrics, traces, symptom-based alerts |
| Ship | shipping-and-launch | Pre-launch checklist, monitoring, rollback plan |

<!-- BEGIN agent-kit generated section: regenerate with agent-kit/tools/build_vendor.py -->

## Full kit: agent-skills + ECC

This environment also has **ECC** installed next to agent-skills. Combine them like this:

1. **Pick the phase with the flowchart above.** The agent-skills phase skills are the default process.
2. **Layer in the stack-specific skills** for the language you are touching (table below). They add idioms, testing and verification detail to the phase skill; they do not replace it.
3. **Delegate to a specialist agent** for a focused review or build fix: mention it as `@agent-name`, or let the primary agent call it via the task tool.
4. **Suggest slash commands** to the user when one fits. Commands are typed by the user; never claim one ran.
5. **Everything installed is in `catalog.md`** next to this file, with one-line descriptions. Read it when nothing below fits.

### By language

| Stack | Skills | Agents | Commands |
|---|---|---|---|
| Python | `python-patterns`, `python-testing`, `django-patterns`, `django-tdd`, `django-verification`, `django-celery`, `django-security`, `fastapi-patterns`, `generating-python-installer` | `python-reviewer` | `/python-review`, `/fastapi-review` |
| Java / JVM | `java-coding-standards`, `springboot-patterns`, `springboot-tdd`, `springboot-verification`, `springboot-security`, `quarkus-patterns`, `quarkus-tdd`, `quarkus-verification`, `quarkus-security`, `jpa-patterns`, `tinystruct-patterns` | `java-reviewer`, `java-build-resolver` | — |
| JavaScript / TypeScript (web) | `frontend-patterns`, `react-patterns`, `react-performance`, `react-testing`, `nextjs-turbopack`, `vue-patterns`, `nuxt4-patterns`, `ui-to-vue`, `angular-developer`, `nestjs-patterns`, `vite-patterns`, `bun-runtime`, `prisma-patterns`, `design-system`, `frontend-design-direction`, `frontend-a11y`, `accessibility`, `make-interfaces-feel-better`, `motion-foundations`, `motion-patterns`, `motion-advanced`, `frontend-slides` | — | `/react-build`, `/react-review`, `/react-test`, `/vue-review`, `/pm2` |
| Go | `golang-patterns`, `golang-testing` | `go-reviewer`, `go-build-resolver` | `/go-build`, `/go-review`, `/go-test` |
| Rust | `rust-patterns`, `rust-testing` | `rust-reviewer`, `rust-build-resolver` | `/rust-build`, `/rust-review`, `/rust-test` |
| C++ | `cpp-coding-standards`, `cpp-testing` | `cpp-reviewer`, `cpp-build-resolver` | `/cpp-build`, `/cpp-review`, `/cpp-test` |
| .NET (C# / F#) | `dotnet-patterns`, `csharp-testing`, `fsharp-testing` | — | — |
| Machine learning | `pytorch-patterns`, `mle-workflow`, `ml-adoption-playbook`, `recsys-pipeline-architect` | — | — |
| Any backend | `api-design`, `backend-patterns`, `coding-standards`, `contract-first`, `hexagonal-architecture`, `mcp-server-patterns` | — | — |

### When both kits cover the same step

Follow the agent-skills skill as the process and pull in the ECC item for depth:

- `test-driven-development` → also `tdd-workflow` skill, `e2e-testing` skill
- `code-review-and-quality` → also `code-reviewer` agent
- `security-and-hardening` → also `security-review` skill, `security-scan` skill
- `git-workflow-and-versioning` → also `git-workflow` skill
- `documentation-and-adrs` → also `architecture-decision-records` skill
- `api-and-interface-design` → also `api-design` skill
- `planning-and-task-breakdown` → also `planner` agent
- `frontend-ui-engineering` → also `frontend-patterns` skill

### Other ECC skills by area

- **Quality workflow (TDD, verification, review, git, e2e):** `agent-sort`, `agent-introspection-debugging`, `ai-regression-testing`, `configure-ecc`, `code-tour`, `continuous-learning`, `continuous-learning-v2`, `council`, `council-multi-model`, `dev-team`, `e2e-testing`, `error-handling`, `eval-harness`, `hookify-rules`, `iterative-retrieval`, `plan-canvas`, `plankton-code-quality`, `production-audit`, `skill-comply`, `skill-scout`, `skill-stocktake`, `strategic-compact`, `tdd-workflow`, `verification-loop`, `windows-desktop-e2e`, `agent-self-evaluation`, `architecture-decision-records`, `browser-qa`, `ck`, `click-path-audit`, `codebase-onboarding`, `codehealth-mcp`, `config-gc`, `context-budget`, `delivery-gate`, `ecc-guide`, `ecc-recipes`, `growth-log`, `inherit-legacy-style`, `intent-driven-development`, `living-docs-governance`, `loop-design-check`, `product-lens`, `repo-scan`, `rules-distill`, `santa-method`, `git-workflow`
- **Security:** `security-review`, `security-scan`, `gateguard`, `safety-guard`
- **Databases:** `clickhouse-io`, `database-migrations`, `mysql-patterns`, `postgres-patterns`, `redis-patterns`
- **DevOps and deployment:** `deployment-patterns`, `docker-patterns`, `terminal-opener`, `canary-watch`, `kubernetes-patterns`
- **Agentic patterns (building and orchestrating AI agents):** `agent-architecture-audit`, `agent-harness-construction`, `agentic-engineering`, `agentic-os`, `ai-first-engineering`, `autonomous-loops`, `blueprint`, `claude-devfleet`, `content-hash-cache-pattern`, `continuous-agent-loop`, `cost-aware-llm-pipeline`, `data-scraper-agent`, `dynamic-workflow-mode`, `enterprise-agent-ops`, `nanoclaw-repl`, `prompt-optimizer`, `ralphinho-rfc-pipeline`, `regex-vs-llm-structured-text`, `search-first`, `team-agent-orchestration`, `token-budget-advisor`, `team-builder`, `agent-payment-x402`, `autonomous-agent-harness`, `gan-style-harness`, `hermes-imports`, `openclaw-persona-forge`, `opensource-pipeline`, `orch-add-feature`, `orch-build-mvp`, `orch-change-feature`, `orch-fix-defect`, `orch-pipeline`, `orch-refine-code`, `plan-orchestrate`
- **Parallel worktree orchestration:** `dmux-workflows`
- **Research:** `deep-research`, `exa-search`, `research-ops`, `scientific-db-pubmed-database`, `scientific-db-uspto-database`, `scientific-pkg-gget`, `scientific-thinking-literature-review`, `scientific-thinking-scholar-evaluation`, `documentation-lookup`
- **Operator workflows (GitHub, Jira, project ops):** `automation-audit-ops`, `api-connector-builder`, `connections-optimizer`, `cost-tracking`, `customer-billing-ops`, `dashboard-builder`, `ecc-tools-cost-audit`, `email-ops`, `finance-billing-ops`, `github-ops`, `google-workspace-ops`, `jira-integration`, `knowledge-ops`, `messages-ops`, `project-flow-ops`, `terminal-ops`, `unified-notifications-ops`, `workspace-surface-audit`, `mailtrap-email-integration`
- **Benchmarking and performance:** `benchmark-optimization-loop`, `data-throughput-accelerator`, `latency-critical-systems`, `parallel-execution-optimizer`, `recursive-decision-ledger`, `agent-eval`, `benchmark`, `benchmark-methodology`
- **Memory:** `unified-memory`

### Agents

- **agent-skills personas** (fanned out by `/ship`): `addy-code-reviewer`, `security-auditor`, `test-engineer`
- **ECC general-purpose:** `architect`, `build`, `build-error-resolver`, `code-reviewer`, `database-reviewer`, `doc-updater`, `docs-lookup`, `e2e-runner`, `harness-optimizer`, `loop-operator`, `planner`, `refactor-cleaner`, `security-reviewer`, `tdd-guide`
- Language reviewers and build resolvers are in the table above.

### Commands

- **agent-skills lifecycle:** `/addy-plan`, `/build`, `/code-simplify`, `/constraints`, `/review`, `/ship`, `/spec`, `/test`
- **ECC:** `/aside`, `/auto-update`, `/build-fix`, `/checkpoint`, `/code-review`, `/cost-report`, `/e2e`, `/ecc-guide`, `/epic-claim`, `/epic-decompose`, `/epic-publish`, `/epic-review`, `/epic-sync`, `/epic-unblock`, `/epic-validate`, `/eval`, `/evolve`, `/feature-dev`, `/gan-build`, `/gan-design`, `/harness-audit`, `/hookify`, `/hookify-configure`, `/hookify-help`, `/hookify-list`, `/instinct-export`, `/instinct-import`, `/instinct-status`, `/jira`, `/learn`, `/learn-eval`, `/loop-start`, `/loop-status`, `/model-route`, `/multi-backend`, `/multi-execute`, `/multi-frontend`, `/multi-plan`, `/multi-workflow`, `/orch-add-feature`, `/orch-build-mvp`, `/orch-change-feature`, `/orch-fix-defect`, `/orch-refine-code`, `/orch-review`, `/orchestrate`, `/plan`, `/plan-canvas`, `/plan-prd`, `/pr`, `/project-init`, `/projects`, `/promote`, `/prp-commit`, `/prp-implement`, `/prp-plan`, `/prp-pr`, `/prp-prd`, `/prune`, `/quality-gate`, `/refactor-clean`, `/resume-session`, `/review-pr`, `/santa-loop`, `/save-session`, `/security`, `/security-scan`, `/sessions`, `/setup-pm`, `/skill-create`, `/skill-health`, `/tdd`, `/test-coverage`, `/update-codemaps`, `/update-docs`, `/verify`
- Language-specific commands are in the table above.

<!-- END agent-kit generated section -->
