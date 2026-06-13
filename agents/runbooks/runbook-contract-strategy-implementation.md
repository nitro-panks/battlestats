# Runbook: Contract Strategy Implementation

_Last updated: 2026-03-14_

_Status: Active maintenance reference_

## Purpose

Guide agents to implement and maintain the layered contract strategy for WoWS API integration, ensuring correctness and reliability through QA review.

## Execution Status

- ODCS contracts now exist for `player_summary` and `player_explorer_rows` alongside the existing `player_daily_snapshots` contract.
- Upstream YAML profiles already cover `account/info`, `account/list`, `account/statsbydate`, and `clans/accountinfo`.
- Contract-alignment tests now cover both upstream endpoint profiles and derived data-product contracts.
- Narrative knowledge coverage now includes `wows-statsbydate-status.md` and `wows-account-hydration-notes.md`.
- QA review for this runbook is executed by applying the criteria in `agents/archive/personas/qa.md` to the changed artifacts and focused tests.
- Focused QA evidence: `manage.py test warships.tests.test_upstream_contracts warships.tests.test_data_product_contracts` passed on 2026-03-14.
- Related follow-up work for player-detail and ranked hardening is tracked in `agents/runbooks/archive/runbook-player-detail-ranked-hardening.md`.

---

## 1. Expand ODCS Contracts for Internal Data Products

**Steps:**

- Identify new or evolving internal data surfaces (e.g., player summary, explorer rows).
- Assign Architect and Engineer-Web-Dev agents to draft ODCS contracts in `agents/contracts/data-products/`.
- Specify schema, ownership, quality checks, and freshness expectations.
- Submit draft contracts to QA agent for review and validation.
- On QA approval, commit contracts and notify Project Manager for documentation updates.

---

## 2. Add YAML Profiles for New/Critical Upstream Endpoints

**Steps:**

- Monitor integration and live testing for new or critical upstream endpoints.
- Assign Engineer-Web-Dev and Project Coordinator agents to draft YAML profiles in `agents/contracts/upstream/`.
- Capture endpoint path, host, params, response envelope, and known quirks.
- Submit profiles to QA agent for fact checking and operational accuracy.
- On QA approval, update endpoint documentation and inform Project Manager.

---

## 3. Validate Derived Payloads in Tests Against Contract Expectations

**Steps:**

- Assign QA and Engineer-Web-Dev agents to review backend code and tests.
- Implement or update test cases to validate derived payloads against ODCS contract fields and semantics.
- Run test suite and review results.
- QA agent verifies test coverage and correctness; requests fixes if needed.
- On QA sign-off, merge test updates and notify Project Manager.

---

## 4. Use Narrative Knowledge Notes for Upstream API Quirks

**Steps:**

- Assign Project Coordinator and Architect agents to document upstream API quirks, mismatches, and live findings in `agents/knowledge/`.
- Reference reproduction commands, realm-specific caveats, and operational evidence.
- Submit knowledge notes to QA agent for review and factual accuracy.
- On QA approval, update knowledge base and inform Project Manager.

---

## Agent Collaboration & QA Routing

- All contract, profile, test, and knowledge note drafts must be routed through QA agent for review.
- QA agent is responsible for fact checking, correctness, and operational reliability.
- Project Manager coordinates documentation updates and ensures all changes are communicated to relevant stakeholders.

---

## Checklist

- [x] ODCS contract drafted and QA approved
- [x] YAML endpoint profile drafted and QA approved
- [x] Test cases updated and QA approved
- [x] Knowledge note drafted and QA approved
- [x] Documentation updated and stakeholders notified

---

## Contact

For questions or clarifications, contact the QA agent or Project Manager.
