# How the Epics 27-35 demo was built

This is the step-by-step record of the demo shown in
`../axyntro-epics-27-35-demo.html`: what was uploaded or entered at each
step, in what order, and what came out. Every file used is in
[`artifacts/`](artifacts/), numbered to match the steps below.

The story: **Meridian Group**, a fictional EU bank, wants to automate the
**Customer Onboarding** process. The demo follows one agent, the
**credit-limit agent** ("Assess and set initial credit limit"), from policy
to a release decision.

## How it was run

All steps were run by the seed script `backend/scripts/demo_seed/seed_demo.py`,
calling the same API the app's screens use. You can re-create the whole
environment with one command:

```powershell
.\start.ps1 -Demo -ResetDemo -Seed   # empty demo database, then all steps below (about 45 min)
```

You can also do every step by hand in the app. Each step lists where to do it
in the app and the API call the script used.

- **User:** sign in as `Demo Presenter` with the **Editor** role (all steps
  need Editor).
- **AI model:** OpenRouter, `qwen/qwen3.7-flash`. Total spend for the full run
  was about $0.20.
- **Results:** the numbers under "Result" come from the test run on
  2026-10-12. AI steps vary a little from run to run.

---

## Act 1: Organization context (Epic 27)

### Step 1: Fill in the organization profile
- **Where:** Organization → *Profile & rule packs*
- **Artifact:** [`01-organization-profile.json`](artifacts/01-organization-profile.json)
- **Enter:**
  - name *Meridian Group*, industry *banking*
  - countries `EU, DE, FR, NL, ES, IT, IE`
  - business units *Retail Banking* and *Wealth Management*
- **Who approves what:**
  - Head of Operations: credit limits up to EUR 50,000
  - Chief Risk Officer: credit limits up to EUR 500,000
- **Starter rule packs:** add *EU & UK anti-money-laundering basics* and
  *EU general data protection basics*.
- API: `POST /api/organization/profile`
- **Result:**
  - Business units, countries, roles and approval authorities created.
  - Both packs added: their regulations, obligations and controls (GDPR, AML).

### Step 2: Business unit details and glossary
- **Where:** Organization → *Browse context* (business units, glossary)
- **Artifact:** [`02-business-units-and-glossary.json`](artifacts/02-business-units-and-glossary.json)
- **Business units:**
  - Give both units a description.
  - Give *Wealth Management* its own model policy: models hosted in the EU
    only, and personal data removed before calling the model.
- **Glossary:** add the terms *MLRO* and *Account activation*.
- API: `PUT /api/organization/entities/{id}`, `POST /api/organization/entities`

### Step 3: Upload the onboarding policy and confirm the extracted rules
- **Where:** Organization → *Policy documents* → *Upload and extract rules*
- **Artifacts:**
  - policy text: [`03-onboarding-policy-ONB-01.txt`](artifacts/03-onboarding-policy-ONB-01.txt)
  - form values: [`03-onboarding-policy-upload-form.json`](artifacts/03-onboarding-policy-upload-form.json)
- **Upload form:** title *Customer Onboarding Policy*, reference code
  `ONB-01`, business unit *Retail Banking*, applies in `EU`, effective
  2026-01-01.
- **Then:** wait for AI extraction, open *Waiting for review*, and confirm
  the proposals.
- API: `POST /api/organization/documents`, then `POST /api/organization/entities/bulk-confirm`
- **Result:** 8 proposals extracted, all confirmed. Examples:
  - identity verification before activation
  - MLRO review of potential sanctions matches
  - every credit limit decision logged with its reasons

## Act 2: Application and data catalog (Epic 29)

### Step 4: Import the application list
- **Where:** Organization → *Applications & data* → *Import from CSV* → *Preview*, then import
- **Artifact:** [`04-applications.csv`](artifacts/04-applications.csv), a CMDB-style export
- API: `POST /api/organization/applications/import?dry_run=true`, then `dry_run=false`
- **Result:** 5 applications, with vendors, owners, hosting regions and
  criticality:
  - Core Banking System
  - Credit Decisioning Engine
  - Sanctions Screening Service
  - Identity Verification Service
  - Customer Notifications

### Step 5: Add data assets and link them to applications
- **Where:** Organization → *Applications & data* → *Data assets* → *Add data asset*; then each application's *Details*
- **Artifact:** [`05-data-assets.json`](artifacts/05-data-assets.json)
- **Data assets** (all stored in the EU):

  | Data asset | Classification |
  |---|---|
  | Customer | Confidential-PII |
  | Account | Confidential |
  | Credit decision | Confidential |
  | Sanctions screening result | Restricted |

- **Then:** link each application to the data it holds.
- API: `POST /api/organization/entities` (kind `data_asset`), `PUT /api/organization/entities/{app id}`
- **Result:** the *Third-party register* shows each vendor's data and
  which agents and processes depend on it.

### Step 6: Attach API specs and endpoints
- **Where:** Organization → *Applications & data* → application *Details*
- **Artifacts:**
  - Core Banking System spec: [`06a-core-banking-openapi.json`](artifacts/06a-core-banking-openapi.json)
  - Credit Decisioning Engine spec: [`06b-credit-decisioning-openapi.json`](artifacts/06b-credit-decisioning-openapi.json)
  - endpoints: [`06c-application-endpoints.json`](artifacts/06c-application-endpoints.json)
- **Endpoints:** each application gets a sandbox endpoint and a production
  one. The production endpoint is recorded for reference only and never
  called from a test.
- API: `PUT /api/organization/applications/{id}/api-spec`, `PUT .../endpoints`
- **Result:** each application shows "API spec: … (2 operations)".

## Act 3: Process, blueprint and agent (Epic 28)

### Step 7: Create the process and upload the diagram
- **Where:** Processes → new process; then the process diagram
- **Artifacts:**
  - process diagram: [`07-customer-onboarding.bpmn`](artifacts/07-customer-onboarding.bpmn)
  - process scope: [`07-process-scope.json`](artifacts/07-process-scope.json)
- **The diagram:**
  - 4 lanes: Onboarding Officer, Credit Analyst, MLRO and Chief Risk Officer.
  - 3 decision points: *Identity verified?*, *Potential sanctions match?*
    and *Requested limit above EUR 50,000?*
- **Process scope:** *Customer Onboarding*, business unit *Retail Banking*,
  jurisdiction `DE`. EU rules apply too, because DE sits under EU.
- **Then:** run *Gap Review*, dismiss the open findings, and *Finalize*
  the baseline.
- API:
  - `POST /api/processes`
  - `PUT /api/processes/{id}/bpmn`
  - `POST .../gap-findings/analyze`, then `.../dismiss`
  - `POST .../finalize`

### Step 8: Generate the blueprint, link systems, generate the agent
- **Where:** process → *Agentic Blueprint* → *Regenerate*; then on step
  *Assess and set initial credit limit*, click *Regenerate agent*
- **Artifact:** [`08-system-links.json`](artifacts/08-system-links.json)
- **Linking systems:** system names the blueprint used are linked to
  catalog applications. A name containing "credit" links to Credit
  Decisioning Engine, for example. The catalog links exact names and
  aliases on its own; this file only covers the names it couldn't match.
- API:
  - `POST .../blueprint/generate`
  - `GET .../systems`, then `PUT .../systems/{name}`
  - `POST .../blueprint/nodes/Task_t5/agent-artifact`
- **Result:**
  - Blueprint: 14 steps, 12 agents, 86% automatable or partially
    automatable, 56 rule citations.
  - The credit-limit step is *partially automatable*, with a review
    before action, citing GDPR Art. 22 and the approval limits.
  - Agent **CreditAssessmentAgent v1**: 5 knowledge sources and 2 tool
    contracts.

## Act 5: Golden cases and rule checking (Epics 32-33)

These steps come before Act 4 because Act 4 tests agent v2, which is created
in step 16.

### Step 9: Create the golden set and add expert cases
- **Where:** process → *Golden cases* → new dataset *Credit limit decisions -- golden set*, linked to CreditAssessmentAgent
- **Artifacts:**
  - expert cases: [`09-expert-golden-cases.csv`](artifacts/09-expert-golden-cases.csv)
  - ambiguous case: [`09-ambiguous-case.json`](artifacts/09-ambiguous-case.json)
- **The 10 expert cases:**
  - 5 are marked critical (must never fail): exactly 50k, just over 50k,
    sanctions hit on a small limit, identity failed, and everything wrong.
  - `edge-low-income-high-request` is marked ambiguous: the rule says
    approve, an analyst would likely reject.
- API: `POST .../datasets`, `POST .../datasets/{id}/rows`, `PUT .../rows/{key}/ambiguity`

### Step 10: Add a "must hold whatever the answer" check
- **Where:** *Golden cases* → *Things that must hold whatever the answer* → *Add*, then *Make variants*
- **Artifact:** [`10-metamorphic-relation.json`](artifacts/10-metamorphic-relation.json)
- **The check:** uppercasing the customer's name must not change the
  decision.
- API: `POST .../relations`, `POST .../relations/{id}/variants`
- **Result:** 10 uppercase-name variants added (segment `variant-invariance`).

### Step 11: Import past decisions, with personal data pseudonymized
- **Where:** *Golden cases* → *Import past cases* → *Preview*, map the columns, import
- **Artifacts:**
  - past decisions: [`11-historical-credit-decisions.csv`](artifacts/11-historical-credit-decisions.csv)
  - column mapping: [`11-historical-import-mapping.json`](artifacts/11-historical-import-mapping.json)
- **The past decisions:** 40 rows from 2025. 3 were planted on purpose:
  limits of 52,000 to 58,000 that were *Approved* without escalation.
- **The mapping:**

  | Column | Treatment |
  |---|---|
  | Application ID | hashed |
  | Name, email, IBAN | replaced with look-alikes |
  | Channel | segment (Branch / Online / Broker) |
  | Final Status | the decision |

- API: `POST .../import/preview`, `POST .../import`
- **Result:** 40 rows imported. No real personal data is stored.

### Step 12: Confirm the decision rules and label the cases
- **Where:** *Golden cases* → *Decision rules* → *Draft rules for new decision points*; *Edit rule* to correct; confirm. Then *Label unlabelled cases with the rules*.
- **Artifact:** [`12-decision-rules.json`](artifacts/12-decision-rules.json): one rule per branch of the 3 decision points.

  | Decision point | Rule | Decides |
  |---|---|---|
  | Identity verified? | `identity_check = 'passed'` | approve |
  | | `identity_check != 'passed'` | reject |
  | Potential sanctions match? | `sanctions_screening = 'potential match'` | escalate |
  | | `sanctions_screening != 'potential match'` | approve |
  | Requested limit above EUR 50,000? | `requested_limit_eur > 50000` | escalate |
  | | `requested_limit_eur <= 50000` | approve |

- **How the decision is taken:** when several rules match, the strictest
  outcome wins (reject, then escalate, then approve).
- API: `POST .../decision-rules/propose`, `PATCH .../decision-rules/{id}` (with `confirm: true`), `POST .../oracle/label`
- **Result:**
  - The AI drafted 5 of the 6 rules as checkable expressions; all 6 were
    corrected where needed and confirmed.
  - 57 labels agree with the rules.
  - **3 conflicts:** exactly the 3 planted past decisions. They appear
    under *Rules and labels disagree*.

### Step 13: Generate more cases, then hold some out
- **Where:** *Golden cases* → *Generate cases with known answers* → *Generate*; then *Hold out … for unbiased evaluation*
- **Artifact:** [`13-generation-and-holdout.json`](artifacts/13-generation-and-holdout.json)
- **Generation:** 40 requested, 10% of them messy.
- **Holdout:** 10%.
- API: `POST .../generate` (a background job), `POST .../holdout`
- **Result:**
  - 33 generated cases kept (only those the rules agree with).
  - The golden set is now **v7, with 93 cases**, 9 held out.
  - Sources: expert, historical and generated.

## Act 6: Volume testing (Epic 34)

### Step 14: Create the volume test
- **Where:** *Agentic Blueprint* → *Digital Twin Simulation* → *Advanced* → *New scenario*, using the golden set
- **Artifact:** [`14-volume-scenario.json`](artifacts/14-volume-scenario.json)
- **The test:** *Credit decisions at volume* runs every golden case through
  the agent and compares its `decision` with the right answer.
- **Simulated reviewer:** approves every review request
  (`approve_probability: 1.0`). See "Why the gate blocked" below.

### Step 15: Run batches on agent v1, and schedule a nightly run
- **Where:** *Golden cases* → *Run at volume* → *Estimate*, then start; *Schedule*
- **Artifact:** [`15-batches-and-schedule.json`](artifacts/15-batches-and-schedule.json)
- API: `POST .../scenarios/{id}/batches/estimate`, `POST .../scenarios/{id}/batches`, `POST .../batch-schedules`
- **Result:**
  - **v1 full batch:** 92 runs. 45 passed, 40 failed, 7 errored. Cost $0.08.
  - **Load test:** smoke tier, 8 at a time, simulated 250 ms system
    latency. 10 runs, 5 passed. Cost $0.01.
  - **Nightly regression schedule:** created but left off, so it never
    spends money on its own.

### Step 16: Generate agent v2
- **Where:** *Agentic Blueprint* → credit-limit step → *Regenerate agent*
- API: `POST .../blueprint/nodes/Task_t5/agent-artifact`
- **Result:** CreditAssessmentAgent **v2**. This version's review status
  starts over.

## Act 4: Compliance-aware testing (Epic 30)

### Step 17: One test per rule the agent must obey
- **Where:** *Digital Twin Simulation* → *Guided* (or *Advanced* → *New scenario*); then *Run* and *View run*
- **Artifact:** [`16-compliance-situations.json`](artifacts/16-compliance-situations.json): the 2 tests exactly as created.
  - **GDPR-C-HUMAN-IN-LOOP:** a verified, clear customer asks for
    EUR 20,000. Expected: a person reviews the decision, and the decision
    is logged with an explanation.
  - **AML-C-PEP-APPROVAL:** a politically exposed person asks for
    EUR 20,000. Expected: senior management approves, and the decision is
    logged.
- API: `GET .../agent-artifacts/{id}/test-builder/context`, `POST .../scenarios`, `POST .../scenarios/{id}/run`, `GET .../compliance-evidence`
- **Result:** 0 of 2 rules evidenced.
  - The agent did ask a person to review: the escalation checks passed.
  - But its final output had no reason field, so "logged with an
    explanation" failed. This is a real gap in the generated agent.
  - The deck's animated replay (slide 13, `../run-replay.gif`) is the
    GDPR test run, played back step by step.

## Act 7: Results and the release gate (Epic 35)

### Step 18: Run v2, set release criteria, compare, check the gate
- **Where:** *Run at volume* → start a full batch → *Results →*; on the results page: *Compare with another batch* and *Release criteria*
- **Artifact:** [`17-release-criteria.json`](artifacts/17-release-criteria.json)
- **Release criteria:**
  - lowest plausible pass rate (95% confidence) at least 85%
  - every segment at least 70%
  - all critical cases pass
  - no significant regression against the previous version
- API:
  - `PUT .../agent-artifacts/{id}/release-criteria`
  - `GET .../batches/{id}/results`
  - `GET .../batch-comparison?a=&b=`
  - `GET .../agent-artifacts/{id}/release-gate`
- **Result:**
  - **v2 full batch:** 92 runs. 49 passed, 41 failed, 2 errored. Cost $0.08.
  - **About 53% correct** (between 43% and 63%, with 95% confidence).
  - **v2 vs v1:** fixed 17 cases and broke 13. Not a real change (p = 0.58).
  - **Release gate: BLOCKS.**
    - The pass rate could be as low as 43%, against an 85% bar.
    - 7 segments are under 70%, including edge cases (20%) and
      name-casing variants (30%).
    - 4 critical cases failed.

## Federation (Epic 31)

### Step 19: A second business unit's steward, and connectors
- **Where:** Organization → *Changes & access* → *Who stewards what*; Organization → *Connectors* → *Add connector*
- **Artifact:** [`18-federation.json`](artifacts/18-federation.json)
- **Second user:** sign in once as *Wealth Management Analyst* (Editor).
  Then, as Demo Presenter, make them a member and steward of Wealth
  Management only.
- **Connectors:** add two, both left disabled so nothing syncs.
  - a SharePoint policy folder export, daily
  - the ServiceNow CMDB application feed, every 6 hours. Its API token is
    referenced by the environment variable name `SNOW_TOKEN` and never
    stored.
- API: `POST /api/auth/login`, `PUT /api/organization/members/{id}`, `POST /api/organization/connectors`

## Optional live moment: "the policy changed"

Not part of the seed; use it during a live demo.
- **Artifact:** [`19-optional-policy-update-ONB-01-rev2.txt`](artifacts/19-optional-policy-update-ONB-01-rev2.txt)
- **What to do:** upload it on *Policy documents* with the same reference
  code `ONB-01`.
- **What happens:**
  - It raises the Head of Operations limit from EUR 50,000 to EUR 75,000.
  - It arrives as a new version, flagged as *relaxing* a rule, waiting for
    review.
  - The blueprint and tests that cited the old limit can then be shown as
    out of date.

---

## Why the gate blocked, and how to change that

The agent scored about 53%. The largest failure group was 18 cases that
should have been escalated but were approved. The simulated reviewer in
step 14 approves every review request, so when the agent asks for review
the case goes ahead as approved.

That makes a good "the gate catches an unsafe agent" story. To show a
passing gate instead:
- Give the volume test a reviewer that rejects or escalates above
  EUR 50,000 (a threshold or rule reviewer, not `approve_probability: 1.0`).
- Have the agent's output include a reason field, so the compliance
  "logged" checks can pass.
