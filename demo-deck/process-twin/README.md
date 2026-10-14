# Invoice to Pay: a whole-process digital twin

This is the demo behind `../axyntro-process-twin-invoice-to-pay.html` (and
`.pptx`). It shows Meridian Group Finance's supplier-invoice process run as
a digital twin before any production deployment: every generated agent,
the people who stay in the loop, simulated systems with sample data, and
75 labelled invoices. The result is measured against how the process runs
today. The product features it uses are
[Epic 36](../../planning/epics/36-process-twin-efficiency.md) and
[docs/wiki/Process-Twin.md](../../docs/wiki/Process-Twin.md).

## Run it

```powershell
python demo-deck/process-twin/generate_data.py      # deterministic sample data (already committed)
.\start.ps1 -Demo -ResetDemo -SeedProcessTwin       # stack + seed (about 60-90 min, about $0.50 of model calls)
```

Then sign in as **Demo Presenter** (Editor), open **Invoice to Pay**, and
choose **Process twin**. To fix something and run again without
reseeding:

```sh
python backend/scripts/demo_seed/seed_process_twin.py --resume                    # rehearsal + all twin runs again
python backend/scripts/demo_seed/seed_process_twin.py --resume --fix-and-rerun    # step 9 + one more run
```

The seed writes everything it measured to `artifacts/results/`. The deck
reads those files, never typed-in numbers:

```sh
msedge --headless=new --remote-debugging-port=9333 --user-data-dir=%TEMP%\edge-capture about:blank
node demo-deck/process-twin/capture.mjs           # screenshots/ from the running app
cd demo-deck/build && python build_process_twin_deck.py ../axyntro-process-twin-invoice-to-pay.html ../axyntro-process-twin-invoice-to-pay.pptx
```

## What's in `artifacts/`

| File | What it is |
|---|---|
| `00-ap-policy-AP-02.txt` | Meridian's Accounts Payable policy: supplier checks, duplicate and fraud controls, the 2% price tolerance, the EUR 10,000 approval limit. Ingested as organization context (Epic 27), so the blueprint and agents cite it |
| `01-invoice-to-pay.bpmn` | The as-is process: 12 steps, 4 lanes, 4 decision points, 3 endings. Built with the app's own BPMN builder |
| `02-operating-model.json` | How it runs today, from a time and motion study: per-step hands-on and waiting time (min / likely / max), role costs, 9,000 invoices a month |
| `03-system-data/*.json` | The simulated world, one file per system: 25 suppliers (one blocked, one inactive, one sanctioned, two with freshly changed bank details), 75 purchase orders and goods receipts, 40 paid invoices in the ledger, sanctions list, GL and VAT rates, plus empty payment and email systems that accept writes |
| `04-cases.json` / `.csv` | 75 invoices across 10 kinds, each with the outcome AP-02 requires: clean small and large, price variance over tolerance, missing goods receipt, duplicate, bank-change fraud, unknown, blocked or sanctioned supplier, messy OCR capture |
| `05-scenario.json` | The rehearsal settings: case-file hand-off, agent-routed judgement gateways, the rule-routed approval gateway, simulated reviewers |
| `results/` | What the seed measured: agents, system mapping, rehearsal run, one report per twin run, fixes, ids |

The right outcomes come from planted facts, not from a model. A
duplicate's supplier and invoice number are already in the ledger. A fraud
signal is an invoice IBAN that differs from a bank account changed in the
last 30 days. A price variance is a gap between the invoice and its
purchase order. That is what makes the twin's accuracy figure meaningful.

## The story the seed runs

1. **Organization context.** Group Finance, approval limits, policy AP-02
   extracted into rules and confirmed.
2. **The process,** uploaded and finalized.
3. **The blueprint and agents.** One generated agent per automatable task
   (8-10, depending on the model's blueprint). Fraud investigation and
   spend approval stay with people.
4. **The world.** Each agent's systems are mapped to the sample data by
   name (`ERP System` gets both purchase orders and the ledger).
5. **Today's operating model** (US36.4).
6. **The cases, and the approval rule** `amount_eur > 10,000`, confirmed.
7. **A rehearsal** of one clean invoice through every agent.
8. **Run 1: as generated.** 30 invoices, stratified across kinds, seed 7.
   **Run 2: the same again**, to measure how consistent the agents are.
9. **Fix what the twin found:** sandbox systems that must accept writes,
   agents missing a system (US20.8), and the runtime's JSON repair turn.
   **Run 3: after the fixes,** on the same invoices.

## What the 2026-10-13 run found

See the deck for the numbers. Every number in it comes from
`artifacts/results/`. In short:

- **The whole process works together.** 9 agents, 4 gateways (3 decided
  by agents, 1 by the confirmed rule) and the people in between ran end
  to end, at about $0.004 of model calls per invoice.
- **The twin found defects a single-agent test wouldn't:**
  - a fraud-signal invoice that ended in payment
  - agents ending on malformed answers
  - a remittance agent looping because its document service couldn't
    accept writes
  - an agent missing a system it needed
- **Identical runs disagree.** Repeating run 1 exactly changed the outcome
  of several invoices. Release decisions need repeated trials, not one run.
- **The business case is there, with ranges:** a large cut in people's
  time and cycle time against the operating model, and its FTE and cost
  equivalents at 9,000 invoices a month. The assumptions are stated in the
  report.
