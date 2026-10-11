---
name: test-pilot
description: "Use when the user finishes implementing a feature, discusses how to verify or test code, or asks for engineering-grade tests. The skill designs tests from the feature spec using equivalence partitioning, boundary-value analysis, decision tables, state-transition and property-based techniques; selects the right layer (unit, integration, or end-to-end); generates concrete test cases with real assertions (oracles); writes and runs them in the project's actual test framework (auto-detected per language, not hardcoded to pytest); and self-checks for missing coverage. Goal: real, runnable, category-covering tests instead of toy tests."
---

# Test Pilot

## Overview

Act as a test engineer, not a test scribbler. When a feature is implemented or
testing is discussed, produce tests driven by the feature's spec that cover the
meaningful input and behavior categories - not a random handful of happy paths.
Every test must have a real expected result (an oracle) and must actually be run.

## When to use

- The user just finished a feature and says "test it" / "how to verify" / "add tests".
- The user asks for "engineering-grade" or "real" tests.
- You are about to claim a feature is done - trigger a self-check first.
- A bug was found and the user wants regression tests.

Do not trigger for trivial one-liners where a single smoke check suffices, but
still prefer a real assertion over a print.

## Core principles

### 1. Test design techniques (pick the right one per situation)

- **Equivalence partitioning**: split inputs into classes that should behave the
  same; test one representative per class (valid and invalid classes).
- **Boundary value analysis**: test the edges (min, min+1, max-1, max, and the
  off-by-one values just outside). Catches inclusive/exclusive mistakes.
- **Decision tables**: enumerate combinations of conditions/rules - ideal for
  business rules with several booleans. Test one case per non-trivial column.
- **State-transition**: for stateful objects (sessions, orders, connections),
  test every valid transition and every invalid one (event in the wrong state).
- **Property-based testing**: assert invariants that hold for many generated
  inputs (e.g. sort is idempotent, encode(decode(x)) == x). Finds surprising
  inputs a human would not think of.

### 2. Layered testing - right test at the right level

- **Unit**: a single function/module in isolation (fast, many of them).
- **Integration**: interactions between modules, or with the DB / network
  boundary (fewer).
- **End-to-end**: the full user flow through the UI or API (fewest, slowest).

Choose the layer by what the feature actually exercises. Do not write an E2E test
for pure logic, and do not touch a database inside a unit test.

### 3. Mandatory self-check / gap report

After generating tests, explicitly list what is NOT covered - error paths,
concurrency, security boundaries, performance limits - and propose additions. A
suite that only covers the happy path is not engineering-grade.

## Adapt to the user's situation (do NOT hardcode pytest)

Detect the project's language and test framework BEFORE writing anything. Look at
the repo for signals:

| Language | Detect by | Preferred framework | Run command |
|---|---|---|---|
| Python | requirements.txt / pyproject.toml / *.py | pytest (or unittest) | `pytest` |
| JavaScript / TypeScript | package.json | jest / vitest / mocha | `npm test` / `vitest` |
| Go | go.mod | built-in `go test` | `go test ./...` |
| Java / Kotlin | pom.xml / build.gradle | JUnit 5 | `mvn test` / `gradle test` |
| Rust | Cargo.toml | built-in `cargo test` | `cargo test` |
| C# | *.csproj | xUnit / NUnit | `dotnet test` |
| Ruby | Gemfile | RSpec / Minitest | `rspec` |
| Swift | Package.swift | XCTest | `swift test` |

Rules:
- If a framework is already present in the project, use it. Never impose a new
  one just because it is familiar.
- If property-based testing fits, use the language's library (Python: hypothesis;
  JS: fast-check; Java: jqwik; Haskell: QuickCheck; Rust: proptest).
- Mirror the project's existing test style (file location, naming, fixtures) when
  tests already exist.

## Workflow

### Step 1 - Understand what is under test
Read the feature code and any spec/comments. Identify inputs, outputs, side
effects, error conditions, and state. Do not guess behavior - read it.

### Step 2 - Enumerate test categories
For each behavior, apply the techniques above and output a short categorized list
(valid/invalid classes, boundaries, decision combinations, transitions,
properties) before writing any code.

### Step 3 - Assign layers
Mark each case as unit / integration / E2E. Keep E2E minimal (only full-flow
paths); push the rest down to unit/integration.

### Step 4 - Generate cases with oracles
Each case: given inputs + setup, the expected result is concrete and checkable
(not `assert result is not None`). Include at least one failing-condition case.

### Step 5 - Write in the detected framework
Use the repo's actual framework and conventions. Mirror existing tests if present.

### Step 6 - Run and report
Execute the suite with the project's command. Report: pass/fail counts, which
categories ran, coverage if available, and any failure with actual vs expected.
Iterate until green - fix the test only when the test is wrong; otherwise flag the
code bug to the user.

### Step 7 - Self-check / gap report
State explicitly what is still untested and why, and suggest the next tests to
add. This step is required, not optional.

## Guardrails

- **No vacuous assertions** (assertions that cannot fail). A test that cannot fail
  is worse than no test - it creates false confidence.
- Prefer few, meaningful tests over many trivial ones.
- Never claim "done" or "verified" without having actually run the suite.
- If the code under test is unreadable or has no clear spec, say so and ask for
  clarification before writing tests.

## Examples

See `references/test_techniques.md` for a worked example (a login validator
tested with equivalence classes, boundaries, and state transitions) and a
framework-detection checklist.
