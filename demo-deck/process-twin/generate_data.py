"""Generates the sample data for the Meridian "Invoice to Pay" process twin.

Deterministic (fixed seed): rerunning produces byte-identical files.
Writes into ./artifacts/:

  01-invoice-to-pay.bpmn          the as-is process (built with the app's own BPMN builder)
  02-operating-model.json         how the process runs today (time & motion study)
  03-system-data/*.json           sample records behind each simulated system
  04-cases.csv / 04-cases.json    labelled invoice cases (inputs + expected outcome + kind)
  05-scenario.json                the rehearsal settings (case file, routing)

Every case's expected outcome follows from the planted facts, not from a
model: a duplicate is in the ledger, a fraud signal is a recently changed
bank account that doesn't match the invoice, a price variance is a gap
between the invoice and its purchase order. That is what makes the twin's
accuracy figure meaningful.

Usage:  python demo-deck/process-twin/generate_data.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts"
sys.path.insert(0, str(HERE.parents[1] / "backend"))

from app.bpmn.builder import build_bpmn_xml  # noqa: E402
from app.schemas.common import ProcessSchema  # noqa: E402

RNG = random.Random(2026)
TODAY = date(2026, 10, 13)
TOLERANCE = 0.02  # price tolerance in the AP policy: 2% of the PO value
APPROVAL_LIMIT = 10_000

# ---- the process ------------------------------------------------------------

ACTORS = [
    {"id": "ap", "name": "Accounts Payable Team", "type": "role"},
    {"id": "ctrl", "name": "Finance Controller", "type": "role"},
    {"id": "bh", "name": "Budget Holder", "type": "role"},
    {"id": "treas", "name": "Treasury", "type": "role"},
]
ELEMENTS = [
    ("s1", "start_event", "Supplier invoice received", "ap", []),
    ("t1", "task", "Capture invoice data from the received document", "ap", ["Invoice inbox (document capture)"]),
    ("t2", "task", "Verify supplier against the vendor master and sanctions list", "ap", ["Vendor master", "Sanctions screening service"]),
    ("g1", "decision", "Supplier verified?", "ap", []),
    ("t3", "task", "Reject invoice and notify the supplier", "ap", ["Supplier email"]),
    ("e1", "end_event", "Invoice rejected", "ap", []),
    ("t4", "task", "Check for duplicate invoices and payment fraud signals", "ap", ["AP ledger", "Vendor master"]),
    ("g2", "decision", "Duplicate or fraud suspected?", "ap", []),
    ("t5", "task", "Investigate suspected duplicate or fraud", "ctrl", ["AP ledger"]),
    ("e2", "end_event", "Invoice blocked", "ctrl", []),
    ("t6", "task", "Match invoice to purchase order and goods receipt", "ap", ["ERP purchasing"]),
    ("g3", "decision", "Matched within tolerance?", "ap", []),
    ("t7", "task", "Resolve the match exception with buyer and supplier", "ap", ["ERP purchasing", "Supplier email"]),
    ("t8", "task", "Code invoice to GL accounts and validate VAT", "ap", ["Chart of accounts and tax rules"]),
    ("g4", "decision", "Invoice above EUR 10,000?", "ap", []),
    ("t9", "task", "Approve invoice spend", "bh", []),
    ("t10", "task", "Post invoice to the ERP ledger", "ap", ["AP ledger"]),
    ("t11", "task", "Schedule payment in the next payment run", "treas", ["Payment system"]),
    ("t12", "task", "Send remittance advice to the supplier", "ap", ["Supplier email"]),
    ("e3", "end_event", "Invoice paid", "ap", []),
]
FLOWS = [
    ("s1", "t1", None), ("t1", "t2", None), ("t2", "g1", None),
    ("g1", "t4", "Yes"), ("g1", "t3", "No"), ("t3", "e1", None),
    ("t4", "g2", None), ("g2", "t5", "Yes"), ("g2", "t6", "No"), ("t5", "e2", None),
    ("t6", "g3", None), ("g3", "t8", "Yes"), ("g3", "t7", "No"), ("t7", "t8", None),
    ("t8", "g4", None), ("g4", "t9", "Yes"), ("g4", "t10", "No"), ("t9", "t10", None),
    ("t10", "t11", None), ("t11", "t12", None), ("t12", "e3", None),
]
END_OUTCOMES = {"Event_e1": "reject", "Event_e2": "escalate", "Event_e3": "approve"}


def bpmn() -> str:
    schema = ProcessSchema.model_validate({
        "process_name": "Invoice to Pay",
        "actors": ACTORS,
        "elements": [
            {"id": i, "type": t, "label": label, "actor_id": actor, "systems_touched": systems, "confidence": "high"}
            for i, t, label, actor, systems in ELEMENTS
        ],
        "flows": [{"id": f"f{n}", "from": a, "to": b, "condition": c} for n, (a, b, c) in enumerate(FLOWS, 1)],
    })
    xml, _ = build_bpmn_xml("invoice_to_pay", schema)
    return xml


# ---- the operating model (time & motion study, today) -------------------------


def tri(a: float, b: float, c: float) -> dict:
    return {"min": a, "likely": b, "max": c}


def operating_model() -> dict:
    # Minutes. Waits are working-time queues before each step today.
    steps = {
        "Task_t1": ("Accounts Payable Team", tri(3, 6, 12), tri(30, 120, 480)),
        "Task_t2": ("Accounts Payable Team", tri(2, 4, 10), tri(15, 60, 240)),
        "Task_t3": ("Accounts Payable Team", tri(3, 5, 10), tri(15, 60, 240)),
        "Task_t4": ("Accounts Payable Team", tri(2, 4, 8), tri(15, 60, 240)),
        "Task_t5": ("Finance Controller", tri(30, 60, 180), tri(240, 960, 2400)),
        "Task_t6": ("Accounts Payable Team", tri(4, 8, 20), tri(30, 120, 480)),
        "Task_t7": ("Accounts Payable Team", tri(15, 35, 120), tri(240, 960, 2880)),
        "Task_t8": ("Accounts Payable Team", tri(3, 5, 12), tri(15, 60, 240)),
        "Task_t9": ("Budget Holder", tri(3, 8, 20), tri(480, 1440, 3360)),
        "Task_t10": ("Accounts Payable Team", tri(2, 3, 6), tri(15, 60, 240)),
        "Task_t11": ("Treasury", tri(1, 2, 5), tri(240, 960, 2400)),
        "Task_t12": ("Accounts Payable Team", tri(2, 4, 8), tri(15, 60, 240)),
    }
    labels = {f"Task_{i}": label for i, t, label, _, _ in ELEMENTS if t == "task"}
    return {
        "currency": "EUR",
        "monthly_volume": 9000,
        "productive_hours_per_fte": 1600,
        "working_minutes_per_day": 480,
        "roles": [
            {"role": "Accounts Payable Team", "hourly_cost": 48},
            {"role": "Finance Controller", "hourly_cost": 95},
            {"role": "Budget Holder", "hourly_cost": 110},
            {"role": "Treasury", "hourly_cost": 70},
        ],
        "nodes": [
            {
                "node_id": node_id, "label": labels[node_id], "role": role,
                "handling_minutes": handling, "wait_minutes": wait, "hourly_cost": None,
                "review_minutes": tri(1, 3, 8),
                # People still pick work up from a queue, but a smaller one.
                "to_be_wait_minutes": tri(10, 45, 240) if node_id in ("Task_t5", "Task_t9") else tri(5, 20, 90),
            }
            for node_id, (role, handling, wait) in steps.items()
        ],
        "end_event_outcomes": END_OUTCOMES,
        "outcome_label": "decision",
        "usd_to_currency": 0.92,
        "default_review_minutes": tri(1, 3, 6),
    }


# ---- the world: suppliers, POs, receipts, ledger, sanctions ---------------------

NAMES = [
    "Acme Industrial GmbH", "Brightline Logistics BV", "Castellan Office Supplies SL", "Delta Fasteners SpA",
    "Elbe Packaging GmbH", "Fjord Cloud Services AS", "Gironde Facility Services SARL", "Hanse Electrical KG",
    "Iberia Print & Media SA", "Jura Precision Tools AG", "Kestrel IT Consulting Ltd", "Lumen Lighting BV",
    "Mosel Catering GmbH", "Nordic Freight AB", "Orbis Security Services SA", "Pavia Chemicals Srl",
    "Quayside Marine Supplies Ltd", "Rhone Valley Energy SAS", "Silesia Steel Sp. z o.o.", "Tagus Software Lda",
    "Umbria Furniture Srl", "Vltava Engineering s.r.o.", "Westerland Cleaning GmbH", "Volga Trading House LLC",
    "Zenith Temporary Staffing BV",
]
COUNTRIES = ["DE", "NL", "ES", "IT", "DE", "NO", "FR", "DE", "ES", "CH", "IE", "NL", "DE", "SE", "FR", "IT", "IE",
             "FR", "PL", "PT", "IT", "CZ", "DE", "RU", "NL"]
GL = [
    ("610100", "Office supplies"), ("620200", "IT services and software"), ("630300", "Logistics and freight"),
    ("640400", "Facility services"), ("650500", "Raw materials"), ("660600", "Energy"), ("670700", "Professional services"),
    ("680800", "Catering and hospitality"),
]
VAT_RATES = {"DE": 19, "NL": 21, "ES": 21, "IT": 22, "FR": 20, "IE": 23, "PT": 23, "PL": 23, "CZ": 21, "SE": 25,
             "NO": 0, "CH": 0, "RU": 0}


def iban(country: str) -> str:
    return f"{country}{RNG.randint(10, 99)}" + "".join(str(RNG.randint(0, 9)) for _ in range(18))


def suppliers() -> list[dict]:
    out = []
    for n, (name, country) in enumerate(zip(NAMES, COUNTRIES), 1):
        out.append({
            "supplier_id": f"SUP-{n:03d}",
            "name": name,
            "vat_number": f"{country}{RNG.randint(100000000, 999999999)}",
            "country": country,
            "iban": iban(country if country not in ("NO", "CH", "RU") else "DE"),
            "status": "active",
            "payment_terms_days": RNG.choice([30, 30, 45, 60]),
            "bank_details_changed_on": (TODAY - timedelta(days=RNG.randint(200, 900))).isoformat(),
            "default_gl_account": GL[n % len(GL)][0],
        })
    out[18]["status"] = "blocked"        # Silesia Steel: blocked after a quality dispute
    out[20]["status"] = "inactive"       # Umbria Furniture: no longer a supplier
    for idx in (7, 13):                  # Hanse Electrical, Nordic Freight: bank details just changed
        out[idx]["bank_details_changed_on"] = (TODAY - timedelta(days=RNG.randint(2, 9))).isoformat()
    return out


SANCTIONS = [
    {"name": "Volga Trading House LLC", "list": "EU consolidated financial sanctions", "match_type": "entity"},
    {"name": "Caspian Maritime Holdings", "list": "EU consolidated financial sanctions", "match_type": "entity"},
    {"name": "Northern Star Commodities FZE", "list": "OFAC SDN", "match_type": "entity"},
]


def build_cases(sups: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """Returns (cases, documents, purchase_orders, ledger_history, kinds)."""
    plan = (
        ["clean_small"] * 30 + ["clean_large"] * 10 + ["price_variance"] * 7 + ["missing_goods_receipt"] * 4
        + ["duplicate"] * 6 + ["bank_change_fraud"] * 4 + ["unknown_supplier"] * 3 + ["blocked_supplier"] * 3
        + ["sanctioned_supplier"] * 2 + ["messy_capture"] * 6
    )
    RNG.shuffle(plan)
    regular = [s for s in sups if s["status"] == "active" and s["country"] != "RU" and s["supplier_id"] not in ("SUP-008", "SUP-014")]
    cases, docs, pos, ledger = [], [], [], []

    # Ledger history: invoices already paid (duplicates are planted against these).
    for n in range(1, 41):
        s = RNG.choice(regular)
        ledger.append({
            "ledger_id": f"AP-2026-{n:05d}", "supplier_id": s["supplier_id"], "supplier_name": s["name"],
            "invoice_number": f"{s['supplier_id'][-3:]}-{RNG.randint(1000, 9999)}",
            "gross_amount": round(RNG.uniform(400, 18000), 2), "currency": "EUR",
            "posted_on": (TODAY - timedelta(days=RNG.randint(10, 120))).isoformat(), "status": "paid",
        })

    for n, kind in enumerate(plan, 1):
        inv_id = f"INV-2026-{n:04d}"
        doc_ref = f"DOC-{40000 + n * 7}"
        if kind == "bank_change_fraud":
            s = RNG.choice([sups[7], sups[13]])
        elif kind == "blocked_supplier":
            s = RNG.choice([sups[18], sups[20]])
        elif kind == "sanctioned_supplier":
            s = sups[23]
        else:
            s = RNG.choice(regular)

        large = kind == "clean_large" or (kind not in ("clean_small", "messy_capture") and RNG.random() < 0.25)
        net = round(RNG.uniform(10_500, 48_000) if large else RNG.uniform(180, 8_500), 2)
        vat_rate = VAT_RATES[s["country"]]
        po_number = f"PO-{450000 + n * 13}"
        po_net = net
        received = net
        if kind == "price_variance":
            po_net = round(net / (1 + RNG.uniform(0.035, 0.08)), 2)  # invoice 3.5-8% over the PO
            received = po_net
        if kind == "missing_goods_receipt":
            received = 0.0
        if kind == "clean_small" and RNG.random() < 0.3:
            po_net = round(net / (1 + RNG.uniform(0.001, 0.015)), 2)  # within the 2% tolerance
            received = po_net
        gl_code, gl_name = next((g for g in GL if g[0] == s["default_gl_account"]), GL[0])

        pos.append({
            "po_number": po_number, "supplier_id": s["supplier_id"], "supplier_name": s["name"],
            "ordered_net_amount": po_net, "currency": "EUR",
            "goods_receipt_status": "none" if received == 0 else "received",
            "received_net_amount": received, "buyer": RNG.choice(["L. Weber", "M. Rossi", "A. Janssen", "C. Moreau"]),
            "cost_center": RNG.choice(["CC-1100", "CC-2300", "CC-3400", "CC-5100"]), "gl_account": gl_code,
        })

        name, vat_no, inv_iban = s["name"], s["vat_number"], s["iban"]
        invoice_number = f"{s['supplier_id'][-3:]}-{RNG.randint(1000, 9999)}"
        if kind == "unknown_supplier":
            name = RNG.choice(["Polaris Media Group Ltd", "Atlas Spare Parts GmbH", "Meridia Consulting Sarl"])
            vat_no = f"DE{RNG.randint(100000000, 999999999)}"
            inv_iban = iban("DE")
            po_number = ""
        if kind == "bank_change_fraud":
            inv_iban = iban("LT")  # new account abroad, not the one in the vendor master
        if kind == "duplicate":
            prior = RNG.choice(ledger)
            s = next(x for x in sups if x["supplier_id"] == prior["supplier_id"])
            name, vat_no, inv_iban = s["name"], s["vat_number"], s["iban"]
            invoice_number = prior["invoice_number"]
            net = round(prior["gross_amount"] / (1 + VAT_RATES[s["country"]] / 100), 2)
            pos[-1].update(supplier_id=s["supplier_id"], supplier_name=s["name"], ordered_net_amount=net, received_net_amount=net)
        vat = round(net * vat_rate / 100, 2)
        gross = round(net + vat, 2)
        invoice_date = TODAY - timedelta(days=RNG.randint(1, 12))
        doc = {
            "document_ref": doc_ref, "invoice_id": inv_id, "invoice_number": invoice_number,
            "supplier_name": name, "supplier_vat_number": vat_no, "supplier_iban": inv_iban,
            "po_number": po_number, "invoice_date": invoice_date.isoformat(),
            "due_date": (invoice_date + timedelta(days=s["payment_terms_days"])).isoformat(),
            "currency": "EUR", "net_amount": net, "vat_rate_percent": vat_rate, "vat_amount": vat, "gross_amount": gross,
            "description": f"{gl_name} -- {RNG.choice(['monthly service', 'delivery', 'order', 'call-off', 'project milestone'])}",
            "capture_confidence": round(RNG.uniform(0.93, 0.99), 2),
        }
        if kind == "messy_capture":
            doc["capture_confidence"] = round(RNG.uniform(0.62, 0.78), 2)
            doc["supplier_name"] = name.upper().replace(" ", "  ", 1)
            doc["raw_text_excerpt"] = f"INVOlCE N0 {invoice_number}  T0TAL EUR {gross:,.2f}  PO: {po_number.replace('PO-', 'PO ')}"
        docs.append(doc)

        decision = {
            "unknown_supplier": "reject", "blocked_supplier": "reject", "sanctioned_supplier": "reject",
            "duplicate": "escalate", "bank_change_fraud": "escalate",
        }.get(kind, "approve")
        expected_path = {
            "reject": "rejected at supplier check",
            "escalate": "blocked for investigation",
        }.get(decision, "paid via exception" if kind in ("price_variance", "missing_goods_receipt") else "paid")
        cases.append({
            "row_key": inv_id, "segment": kind, "critical": kind in ("duplicate", "bank_change_fraud", "sanctioned_supplier"),
            "inputs": {
                "invoice_id": inv_id, "document_ref": doc_ref,
                "channel": RNG.choice(["email", "email", "supplier portal", "EDI"]),
                "received_on": (invoice_date + timedelta(days=RNG.randint(0, 2))).isoformat(),
                "amount_eur": gross,
            },
            "labels": {"decision": decision, "outputs": {"expected_route": expected_path}},
            "kind": kind,
        })
    return cases, docs, pos, ledger, plan


def system_data(sups, docs, pos, ledger) -> dict:
    return {
        "Invoice inbox": {
            "description": "Received supplier invoices with captured (OCR) fields, by document reference",
            "records": docs, "lookup_fields": ["document_ref", "invoice_id", "invoice_number"],
            "operations": ["lookup"], "reference_prefix": "DOC",
            "keywords": ["inbox", "capture", "ocr", "scan", "mailroom", "e-invoic", "intake", "document store",
                         "document management", "document parsing", "document processing", "parser", "extraction", "nlp"],
        },
        "Vendor master": {
            "description": "Supplier master data: legal name, VAT number, bank account, status, payment terms",
            "records": sups, "lookup_fields": ["supplier_id", "name", "vat_number", "iban"],
            "operations": ["lookup"], "reference_prefix": "VEN",
            "keywords": ["vendor", "supplier master", "supplier data", "supplier record", "master data", "mdg", "fraud"],
        },
        "Sanctions screening": {
            "description": "Sanctions and watchlist screening; returns listed entities matching a name",
            "records": SANCTIONS, "lookup_fields": ["name"], "operations": ["lookup"], "reference_prefix": "SCR",
            "keywords": ["sanction", "watchlist", "screening", "kyc", "aml", "compliance"],
        },
        "ERP purchasing": {
            "description": "Purchase orders and goods receipts",
            "records": pos, "lookup_fields": ["po_number", "supplier_id", "supplier_name"],
            "operations": ["lookup", "update"], "reference_prefix": "PUR",
            "keywords": ["purchas", "procure", "goods receipt", "grn", "po ", "purchase order", "receiving", "erp", "/mm", "matching", "gr ", "goods"],
        },
        "Chart of accounts and tax rules": {
            "description": "GL accounts and VAT rates by supplier country",
            "records": [{"gl_account": g, "description": d} for g, d in GL]
            + [{"country": c, "standard_vat_rate_percent": r, "reverse_charge": r == 0} for c, r in VAT_RATES.items()],
            "lookup_fields": ["gl_account", "country", "description"], "operations": ["lookup"], "reference_prefix": "TAX",
            "keywords": ["chart of accounts", "general ledger", "gl", "tax", "vat", "coding", "account"],
        },
        "Payment system": {
            "description": "Treasury payment runs; schedule a payment, look up scheduled payments",
            "records": [], "lookup_fields": ["invoice_id", "supplier_id"], "operations": ["lookup", "create"],
            "reference_prefix": "PAY",
            "keywords": ["payment", "treasury", "bank", "pay run", "remit"],
        },
        "Supplier email": {
            "description": "Outbound supplier correspondence (rejections, queries, remittance advice)",
            "records": [], "lookup_fields": ["invoice_id"], "operations": ["create"], "reference_prefix": "MSG",
            "keywords": ["email", "mail", "notification", "communication", "portal", "correspondence", "messag", "template", "generat", "remittance"],
        },
        "AP ledger": {
            "description": "Accounts payable sub-ledger: posted and paid invoices; post a new invoice",
            "records": ledger, "lookup_fields": ["invoice_number", "supplier_id", "supplier_name", "ledger_id"],
            "operations": ["lookup", "create"], "reference_prefix": "AP",
            "keywords": ["ledger", "accounts payable", "payable", "erp", "duplicate", "invoice history", "posting", "sap", "finance system"],
        },
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "03-system-data").mkdir(exist_ok=True)
    (OUT / "01-invoice-to-pay.bpmn").write_text(bpmn(), encoding="utf-8")
    (OUT / "02-operating-model.json").write_text(json.dumps(operating_model(), indent=2), encoding="utf-8")
    sups = suppliers()
    cases, docs, pos, ledger, _ = build_cases(sups)
    for name, data in system_data(sups, docs, pos, ledger).items():
        slug = name.lower().replace(" ", "-")
        (OUT / "03-system-data" / f"{slug}.json").write_text(json.dumps({"system": name, **data}, indent=2), encoding="utf-8")
    (OUT / "04-cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    with (OUT / "04-cases.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["invoice_id", "kind", "document_ref", "channel", "received_on", "amount_eur", "expected_decision", "expected_route"])
        for c in cases:
            i = c["inputs"]
            writer.writerow([c["row_key"], c["kind"], i["document_ref"], i["channel"], i["received_on"], i["amount_eur"],
                             c["labels"]["decision"], c["labels"]["outputs"]["expected_route"]])
    (OUT / "05-scenario.json").write_text(json.dumps({
        "name": "Invoice to Pay -- whole process",
        "handoff": "case_file",
        "default_gateway_routing": "agent",
        "gateway_routing": {"Gateway_g4": "rules"},
        "rules": {"Gateway_g4": {"Task_t9": {"field": "amount_eur", "op": "gt", "value": APPROVAL_LIMIT},
                                 "Task_t10": {"field": "amount_eur", "op": "lte", "value": APPROVAL_LIMIT}}},
        "manual_node_config": {
            "Task_t5": {"mode": "human_checkpoint", "human_checkpoint_config": {"mode": "probability", "approve_probability": 1.0}},
            "Task_t9": {"mode": "human_checkpoint", "human_checkpoint_config": {"mode": "probability", "approve_probability": 0.97}},
        },
        "reviewer": {"mode": "probability", "approve_probability": 1.0},
        "approval_limit_eur": APPROVAL_LIMIT,
        "price_tolerance": TOLERANCE,
    }, indent=2), encoding="utf-8")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"wrote {len(cases)} cases {kinds}, {len(sups)} suppliers, {len(pos)} POs, {len(ledger)} ledger rows -> {OUT}")


if __name__ == "__main__":
    main()
