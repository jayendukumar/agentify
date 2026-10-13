"""Builds v2 of the demo deck: a business-readable case study, self-contained HTML."""
import base64
import html
import io
import sys
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).parent
SHOTS = HERE / "screenshots"
OUT = Path(sys.argv[1])
E = html.escape


def trim(im: Image.Image) -> Image.Image:
    body = im.crop((0, 90, im.width - 20, im.height))
    bg = Image.new("RGB", body.size, body.getpixel((5, body.height - 5)))
    box = ImageChops.difference(body, bg).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
    if not box:
        return im
    left, right = max(0, box[0] - 24), min(im.width, box[2] + 24)
    return im.crop((left, 70, right, im.height)) if right - left < im.width * 0.9 else im


def data_uri(name: str) -> str:
    gif = SHOTS / f"{name}.gif"
    if gif.exists():
        return "data:image/gif;base64," + base64.b64encode(gif.read_bytes()).decode()
    buf = io.BytesIO()
    trim(Image.open(SHOTS / f"{name}.png").convert("RGB")).save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


STEPS = [
    "Upload the process",
    "Validate the proposed agents",
    "Ground them in the bank's rules",
    "Connect the bank's systems",
    "Rehearse in a safe simulation",
    "Build the answer key",
    "Test at volume",
    "Decide: go or no-go",
    "Scale across the bank",
]


# ---- slide types ------------------------------------------------------------

def title_slide(kicker, title, sub, hint=""):
    return f"""<section class="slide hero"><div class="inner">
  <div class="kicker">{E(kicker)}</div><h1>{E(title)}</h1><p class="sub">{E(sub)}</p>
  {f'<p class="hint">{E(hint)}</p>' if hint else ''}</div></section>"""


def text_slide(kicker, title, intro, cards, note=""):
    cs = "".join(f'<div class="card"><h3>{E(h)}</h3><p>{E(t)}</p></div>' for h, t in cards)
    return f"""<section class="slide light"><div class="inner">
  <div class="kicker dark">{E(kicker)}</div><h2>{E(title)}</h2>{f'<p class="intro">{E(intro)}</p>' if intro else ''}
  <div class="cards n{len(cards)}">{cs}</div>{f'<p class="note">{E(note)}</p>' if note else ''}</div></section>"""


def compare_slide(kicker, title, left_h, left, right_h, right):
    li = lambda xs: "".join(f"<li>{E(x)}</li>" for x in xs)
    return f"""<section class="slide light"><div class="inner">
  <div class="kicker dark">{E(kicker)}</div><h2>{E(title)}</h2>
  <div class="versus"><div class="col before"><h3>{E(left_h)}</h3><ul>{li(left)}</ul></div>
  <div class="col after"><h3>{E(right_h)}</h3><ul>{li(right)}</ul></div></div></div></section>"""


def flow_slide():
    main = [("Application received", "Onboarding Officer"), ("Verify identity", "Onboarding Officer"),
            ("Screen against EU sanctions list", "Onboarding Officer"), ("Assess and set credit limit", "Credit Analyst"),
            ("Activate account", "Onboarding Officer"), ("Customer onboarded", "")]
    boxes = "".join(
        f'<div class="fbox{" end" if i in (0, len(main) - 1) else ""}{" focus" if "credit" in n else ""}">'
        f'<b>{E(n)}</b>{f"<span>{E(r)}</span>" if r else ""}</div>' + ('<div class="arrow">&#10140;</div>' if i < len(main) - 1 else "")
        for i, (n, r) in enumerate(main))
    return f"""<section class="slide light"><div class="inner">
  <div class="kicker dark">The process in plain words</div><h2>How a new retail customer is onboarded today</h2>
  <div class="flow">{boxes}</div>
  <div class="branches">
    <div class="branch"><b>Identity can't be verified?</b> The application is rejected and the customer is told.</div>
    <div class="branch"><b>Possible sanctions match?</b> The Money Laundering Reporting Officer (MLRO) reviews it before anything else happens.</div>
    <div class="branch"><b>Credit limit above EUR 50,000?</b> The Chief Risk Officer must approve it. Up to EUR 50,000, the Head of Operations can.</div>
  </div>
  <p class="note">Four roles take part: Onboarding Officer, Credit Analyst, MLRO and Chief Risk Officer. The highlighted step is the focus of this study.</p>
  </div></section>"""


def journey_slide():
    items = "".join(f'<div class="jstep"><div class="jn">{i}</div><div>{E(s)}</div></div>' for i, s in enumerate(STEPS, 1))
    return f"""<section class="slide light"><div class="inner">
  <div class="kicker dark">The journey at a glance</div><h2>From a process document to a go / no-go decision</h2>
  <div class="journey">{items}</div>
  <p class="note">Each step is shown on the following slides with real screens from Axyntro.</p></div></section>"""


def section_slide(n):
    return f"""<section class="slide hero section"><div class="inner">
  <div class="kicker">Step {n} of {len(STEPS)}</div><h1>{E(STEPS[n - 1])}</h1></div></section>"""


def shot_slide(step, shot, title, what, why):
    whats = "".join(f"<li>{E(w)}</li>" for w in what)
    grow = " grow" if shot.startswith("run-") else ""
    return f"""<section class="slide shot-slide">
  <div class="shot"><img class="{grow}" src="{data_uri(shot)}" alt="{E(title)}"></div>
  <aside><div class="kicker">Step {step} · {E(STEPS[step - 1])}</div><h2>{E(title)}</h2>
  <ul>{whats}</ul>{f'<div class="why"><b>Why it matters</b>{E(why)}</div>' if why else ''}</aside></section>"""


# ---- the story ----------------------------------------------------------------

slides = [
    title_slide("A case study with Axyntro",
                "Can AI agents help Meridian onboard customers?",
                "Process discovery and safe AI adoption for retail customer onboarding",
                "Arrow keys or click to move · F for full screen"),
    text_slide("The client", "Meet Meridian Group",
               "A mid-size European bank serving retail and wealth customers.",
               [("Two businesses", "Retail Banking (accounts, cards, personal lending) and Wealth Management (private banking)."),
                ("Seven markets", "Germany, France, the Netherlands, Spain, Italy and Ireland, plus EU-wide operations."),
                ("Heavily regulated", "EU data protection (GDPR) and anti-money-laundering rules apply to every customer it takes on."),
                ("Its own policies", "A Customer Onboarding Policy (ONB-01) sets out how new customers are checked and approved.")],
               "Meridian Group is fictional. All customers and data in this study are synthetic."),
    text_slide("The question", "Which parts of onboarding could AI agents take on, safely?",
               "Opening an account for a new retail customer takes several checks and approvals, each done by hand today. "
               "Meridian wants to know where AI agents could help without breaking any of its rules.",
               [("Where can AI help?", "Which steps of the process are suited to an AI agent, and which need a person?"),
                ("Will it follow our rules?", "Can an agent respect the bank's policies, approval limits and regulations?"),
                ("How would we know?", "What evidence would show the agents decide correctly, before any real customer is affected?")]),
    flow_slide(),
    text_slide("Objectives", "What this study sets out to do", "",
               [("1 · Discover", "Map the onboarding process and find the steps AI agents could take on."),
                ("2 · Design", "Define the agents, grounded in Meridian's own policies, limits and systems."),
                ("3 · Prove", "Test the agents safely and at volume, before they touch a real customer."),
                ("4 · Decide", "Give leadership a clear, evidence-based go or no-go.")]),
    compare_slide("Why Axyntro", "Faster discovery, safer adoption",
                  "The usual way", [
                      "Weeks of workshops to map how the process really works",
                      "Experts read policy documents line by line",
                      "Test cases written by hand, a few at a time",
                      "Pilots run on real customers to see what happens",
                      "Go-live decided on confidence, not evidence"],
                  "With Axyntro", [
                      "Process documents are uploaded and turned into a diagram",
                      "AI proposes the agents, step by step, and people validate them",
                      "Every recommendation cites the policy it relies on",
                      "Agents rehearse in a digital twin: a safe, simulated copy of the process",
                      "Hundreds of test runs end in a go / no-go, with reasons"]),
    journey_slide(),

    section_slide(1),
    shot_slide(1, "v2-process", "Start from what the bank already has", [
        "The team creates the Customer Onboarding process and uploads its process documentation: PDF, Word, Visio or images.",
        "Axyntro guides the rest: draft the diagram, review and finalize it, then assess it for AI agents.",
        "For this study, Meridian's onboarding diagram was loaded directly."],
        "No blank page: discovery starts from material the bank already has."),
    shot_slide(1, "v2-diagram", "One picture of how the work is done", [
        "The process as a diagram: 4 roles, 7 tasks and 3 decision points.",
        "A diagram assistant answers questions and makes changes described in plain English.",
        "Once everyone agrees, the team finalizes it as the baseline to automate from."],
        "Business and IT agree on the same picture before anything is automated."),

    section_slide(2),
    shot_slide(2, "11-blueprint", "Axyntro assesses every step", [
        "14 steps assessed: 6 automatable, 6 partially automatable, 2 that should stay with people.",
        "12 AI agents proposed to take on that work.",
        "Colors on the diagram show the verdict for each step."],
        "A first view of where AI fits, in minutes, ready for the team to challenge."),
    shot_slide(2, "v2-agents", "The team validates the proposed agents", [
        "Each agent card says what it would do and which step it covers.",
        "The team reviews each one, and can approve, regenerate or override it.",
        "This study follows one: the CreditAssessmentAgent, which recommends a new customer's credit limit."],
        "People stay in charge: nothing proceeds until the team agrees."),
    shot_slide(2, "12-blueprint-step", "Every verdict comes with its reasons", [
        "Setting a credit limit is only partially automatable.",
        "Why: it significantly affects the customer (GDPR Article 22), and limits above EUR 50,000 need the Chief Risk Officer.",
        "So the agent recommends, and a person reviews before it acts."],
        "Recommendations are explained, not a black box."),

    section_slide(3),
    shot_slide(3, "v2-policy-docs", "Axyntro reads the bank's policies", [
        "Meridian's onboarding policy (ONB-01) was uploaded.",
        "AI extracted 8 rules from it, and a person confirmed each one before it was used.",
        "Ready-made rule packs added the essentials of GDPR and anti-money-laundering rules."],
        "Agents follow the bank's actual rules, not generic ones."),
    shot_slide(3, "03-approval-authorities", "Who is allowed to approve what", [
        "Approval limits become facts the agents must respect.",
        "Head of Operations: credit limits up to EUR 50,000.",
        "Chief Risk Officer: up to EUR 500,000."],
        "The same limits later decide what a correct answer looks like in testing."),
    shot_slide(3, "05-what-applies", "Know exactly which rules apply", [
        "Ask: what applies to Retail Banking in Germany?",
        "Answer: 2 regulations, 19 obligations and 11 controls.",
        "Germany automatically inherits every rule that applies across the EU."],
        "Compliance can see the rule set behind each process at a glance."),
    shot_slide(3, "13-context-used", "Every recommendation shows its sources", [
        "47 rules and facts, plus 5 passages from policies, went into the assessment.",
        "Each is numbered, so a reviewer can trace any recommendation back to its source."],
        "An audit trail from day one."),

    section_slide(4),
    shot_slide(4, "06-applications", "The systems the agents will work with", [
        "5 systems loaded from the bank's IT inventory: Core Banking, Credit Decisioning, Sanctions Screening, Identity Verification and Customer Notifications.",
        "Technical specifications are attached for two of them.",
        "Each agent's tools are linked to the real system they stand for."],
        "IT sees exactly which systems each agent would touch."),
    shot_slide(4, "07-data-assets", "Which data is sensitive, and who holds it", [
        "Customer data is marked Confidential-PII; sanctions results are Restricted. All of it is kept in the EU.",
        "A register shows which vendors hold which data, and which agents depend on them."],
        "Security and data protection see the exposure before anything is built."),

    section_slide(5),
    shot_slide(5, "v2-guided", "Test before it goes live", [
        "A guided, five-step test builder, written for business users.",
        "Tests run in a digital twin: a simulated copy of the process.",
        "No real system is called and no real person is contacted."],
        "Mistakes are found in a rehearsal, not with customers."),
    shot_slide(5, "run-replay", "Watch the agent work, step by step", [
        "A replay of one test run of the credit agent.",
        "It looks the customer up in Core Banking, gets a credit score from the Credit Decisioning Engine...",
        "...then asks a person to review before acting, as the bank's rules require."],
        "Anyone can see what the agent did, and in what order."),
    shot_slide(5, "13b-compliance-check", "Every rule gets its own test", [
        "Axyntro created one test for each rule the agent must obey: human review of significant decisions, and senior approval for politically exposed persons.",
        "Finding: the agent correctly asked for a person's review...",
        "...but did not record the reason for its decision, which the policy requires."],
        "A compliance gap found in simulation, not by an auditor."),

    section_slide(6),
    shot_slide(6, "15-golden-cases", "Write down what 'correct' means", [
        "The process's decision points become rules anyone can read:",
        "identity not verified: reject · possible sanctions match: escalate · limit above EUR 50,000: escalate · otherwise: approve.",
        "AI drafted the rules; a person corrected and confirmed them."],
        "The agent is judged against the bank's own rules."),
    shot_slide(6, "17-dataset", "93 test cases with known right answers", [
        "10 written by experts, 5 of which must never fail.",
        "40 past decisions from 2025, with names, emails and account numbers replaced.",
        "33 generated by AI and checked against the rules, plus 10 copies of expert cases with the name in capitals. 9 kept aside for an unbiased final check."],
        "A realistic, privacy-safe answer key, built in minutes rather than weeks."),
    shot_slide(6, "19-oracle-conflicts", "The rules caught past mistakes", [
        "3 past applications above EUR 50,000 were approved without escalation, against policy.",
        "Either the records are wrong, or day-to-day practice differs from the written policy.",
        "(These three were planted in the demo data to show the check.)"],
        "Discovery also surfaces where today's process doesn't follow its own rules."),

    section_slide(7),
    shot_slide(7, "18-run-at-volume", "Run every case through the agent", [
        "A full test batch: 92 runs, for about $0.08 in AI cost.",
        "The cost and time are estimated before anything runs, with a spending cap.",
        "A load test checks the agent with 8 cases running at once. A nightly re-test can be scheduled."],
        "Evidence at volume, for the price of a coffee."),
    shot_slide(7, "21-batch-results", "The agent is right about half the time", [
        "53% correct, and Axyntro is honest about the uncertainty: between 43% and 63%.",
        "It is weakest on edge cases (20%)...",
        "...and changes its answer when only the capitalization of a name changes (30% consistent)."],
        "Results come with a confidence range, not a single flattering number."),
    shot_slide(7, "23-how-it-decides", "Exactly where it goes wrong", [
        "18 cases that should have gone to a person were approved by the agent.",
        "13 cases that should have been rejected were approved.",
        "Each failure links to the cases behind it, so the fix can be targeted."],
        "The team knows what to fix, not just that something is wrong."),

    section_slide(8),
    shot_slide(8, "25-compare", "The release gate says: not ready", [
        "Leadership set the bar: at least 85% correct even at the low end of the range, every customer group at 70% or more, and every must-pass case passing.",
        "Result: NOT READY. 4 must-pass cases failed and the pass rate could be as low as 43%.",
        "The second version of the agent was no better: it fixed 17 cases and broke 13."],
        "A clear, defensible no-go, with the reasons written down."),

    section_slide(9),
    shot_slide(9, "09-stewards", "Each business owns its own rules", [
        "A Wealth Management analyst sees and maintains only Wealth Management's rules, plus group-wide ones.",
        "Wealth Management's AI calls stay in the EU, with personal data removed first."],
        "Scales across business units without losing control."),
    shot_slide(9, "08-connectors", "Stays current automatically", [
        "Connectors keep the policy library (SharePoint) and the IT inventory (ServiceNow) in sync on a schedule.",
        "Passwords and tokens are never stored in Axyntro."],
        "When a policy changes, the new version arrives for review, and scheduled re-tests can run automatically."),

    text_slide("Outcomes", "What Meridian learned", "",
               [("Discovery", "14 steps mapped and 12 agents proposed. 86% of the process could be fully or partly handled by AI agents."),
                ("Grounding", "8 rules extracted from the bank's own policy and confirmed. Every recommendation cites its sources."),
                ("Proof", "The credit agent is not ready yet: 53% correct against an 85% bar. The gate stopped it before any customer was affected."),
                ("Bonus", "Testing also found a compliance gap (reasons not recorded) and 3 past decisions that broke policy.")]),
    text_slide("The value", "What Axyntro brings", "",
               [("Speed", "The whole study, from the process diagram to the go / no-go, ran end to end in under an hour, for about $0.20 of AI cost."),
                ("Safety", "Every test ran in a simulation. No real customer, system or person was touched."),
                ("Evidence", "Every recommendation is cited, and every result comes with an honest confidence range."),
                ("Control", "People confirm the rules, validate the agents and set the bar. The gate enforces it.")]),
    text_slide("Next steps", "From here to a pilot", "",
               [("1 · Fix", "Make the agent send every limit above EUR 50,000 to a person, and record the reason for each decision."),
                ("2 · Re-test", "Run the same 93 cases again. Axyntro shows whether the new version is really better."),
                ("3 · Pilot", "When the gate passes, start with Retail Banking in Germany, with a person reviewing every decision."),
                ("4 · Extend", "Repeat the same journey for the other agents and processes.")]),
    title_slide("Thank you", "From process to production, with evidence at every step",
                "Axyntro · process discovery and safe AI adoption"),
]

total = len(slides)
numbered = [s.replace("</section>", f'<div class="num">{i} / {total}</div></section>', 1) for i, s in enumerate(slides, 1)]

OUT.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>Meridian Group x Axyntro: AI agents for customer onboarding</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;height:100%;background:#0d1530;font-family:Inter,Segoe UI,Arial,sans-serif}}
.slide{{display:none;position:absolute;inset:0;margin:auto;width:100vw;height:56.25vw;max-height:100vh;max-width:177.78vh;overflow:hidden}}
.slide.on{{display:block}} .slide.shot-slide.on{{display:grid;grid-template-columns:70% 30%}}
.num{{position:absolute;bottom:2.4vmin;right:3vmin;font-size:1.6vmin;color:#8fb3ff}}
.kicker{{color:#8fb3ff;font-size:1.8vmin;font-weight:700;letter-spacing:.06em;text-transform:uppercase}} .kicker.dark{{color:#2f5bd3}}
.hero{{background:linear-gradient(135deg,#16245c,#0d1530);color:#fff}}
.hero .inner{{position:absolute;left:10%;right:10%;top:50%;transform:translateY(-50%)}}
.hero h1{{font-size:6.6vmin;line-height:1.1;margin:2vmin 0}} .hero .sub{{font-size:2.8vmin;color:#dfe7ff;margin:0}}
.hero .hint{{font-size:1.8vmin;color:#8fb3ff;margin-top:6vmin}} .hero.section h1{{font-size:8vmin}}
.light{{background:#f6f8fc;color:#16245c}} .light .inner{{position:absolute;inset:5vmin 7vmin 7vmin;display:flex;flex-direction:column;justify-content:center}}
.light h2{{font-size:5.2vmin;margin:1.2vmin 0 2vmin}} .intro{{font-size:2.9vmin;line-height:1.5;color:#3b4a75;max-width:85%;margin:0 0 3vmin}}
.note{{position:absolute;bottom:-3vmin;font-size:1.8vmin;color:#5d6a90;font-style:italic}}
.cards{{display:grid;gap:3vmin}} .cards.n2,.cards.n4{{grid-template-columns:1fr 1fr}} .cards.n3{{grid-template-columns:1fr 1fr 1fr}}
.card{{background:#fff;border-radius:1.4vmin;padding:3.6vmin 3.6vmin;box-shadow:0 .4vmin 1.6vmin rgba(22,36,92,.08);border-top:.6vmin solid #2f5bd3}}
.card h3{{margin:0 0 1.4vmin;font-size:3.3vmin}} .card p{{margin:0;font-size:2.7vmin;line-height:1.5;color:#3b4a75}}
.versus{{display:grid;grid-template-columns:1fr 1fr;gap:3vmin;margin-top:3vmin}}
.col{{border-radius:1.4vmin;padding:3.6vmin 3.6vmin}} .col h3{{margin:0 0 2vmin;font-size:3.4vmin}}
.col ul{{margin:0;padding-left:2.6vmin}} .col li{{font-size:2.8vmin;line-height:1.45;margin-bottom:2vmin}}
.before{{background:#eceef4;color:#4a5370}} .after{{background:#16245c;color:#fff}} .after li{{color:#dfe7ff}}
.flow{{display:flex;align-items:center;gap:1vmin;margin:4vmin 0 3vmin}}
.fbox{{flex:1;background:#fff;border:.3vmin solid #c9d3ee;border-radius:1.4vmin;padding:2vmin 1.4vmin;text-align:center;min-height:18vmin;display:flex;flex-direction:column;justify-content:center}}
.fbox b{{font-size:2.6vmin}} .fbox span{{font-size:1.9vmin;color:#5d6a90;margin-top:.8vmin}}
.fbox.end{{border-radius:7vmin;background:#eef2fb}} .fbox.focus{{border-color:#2f5bd3;background:#e6edff;box-shadow:0 0 0 .5vmin rgba(47,91,211,.2)}}
.arrow{{color:#2f5bd3;font-size:3vmin}}
.branches{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2vmin}}
.branch{{background:#fff;border-left:.6vmin solid #e59b2f;border-radius:1vmin;padding:2.4vmin 2.4vmin;font-size:2.4vmin;line-height:1.45;color:#3b4a75}}
.branch b{{display:block;color:#16245c;margin-bottom:.6vmin}}
.journey{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2vmin;margin-top:3vmin}}
.jstep{{display:flex;align-items:center;gap:2vmin;background:#fff;border-radius:1.4vmin;padding:3.4vmin 2.8vmin;font-size:3vmin;font-weight:600;box-shadow:0 .4vmin 1.6vmin rgba(22,36,92,.08)}}
.jn{{flex:none;width:5.4vmin;height:5.4vmin;border-radius:50%;background:#2f5bd3;color:#fff;display:grid;place-items:center;font-size:2.6vmin}}
.shot{{display:flex;align-items:center;justify-content:center;padding:1.6vmin;background:#e9edf6}}
.shot img{{max-width:100%;max-height:100%;border-radius:.8vmin;box-shadow:0 .5vmin 2vmin rgba(13,21,48,.25)}} .shot img.grow{{width:100%}}
aside{{position:relative;background:#16245c;color:#fff;padding:4.5vmin 3.2vmin}}
aside h2{{font-size:3.5vmin;line-height:1.2;margin:1.6vmin 0 2.4vmin}}
aside ul{{padding-left:2.4vmin;margin:0}} aside li{{font-size:2.1vmin;line-height:1.45;margin-bottom:1.5vmin;color:#dfe7ff}}
.why{{margin-top:2.6vmin;background:rgba(143,179,255,.14);border-left:.5vmin solid #8fb3ff;border-radius:.8vmin;padding:1.6vmin 1.8vmin;font-size:2vmin;line-height:1.45}}
.why b{{display:block;color:#8fb3ff;font-size:1.6vmin;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.6vmin}}
.light .num{{color:#5d6a90}}
</style></head><body>
{chr(10).join(numbered)}
<script>
const s=[...document.querySelectorAll('.slide')]; let i=0;
const show=n=>{{i=Math.max(0,Math.min(s.length-1,n)); s.forEach((e,k)=>e.classList.toggle('on',k===i)); location.hash=i+1}};
document.addEventListener('keydown',e=>{{if(['ArrowRight','PageDown',' '].includes(e.key))show(i+1);
  if(['ArrowLeft','PageUp'].includes(e.key))show(i-1); if(e.key==='Home')show(0); if(e.key==='End')show(s.length-1);
  if(e.key==='f'||e.key==='F')document.documentElement.requestFullscreen?.()}});
document.addEventListener('click',e=>show(e.clientX<innerWidth/4?i-1:i+1));
const fromHash=()=>{{const n=(parseInt(location.hash.slice(1))||1)-1; if(n!==i||!s[i].classList.contains('on'))show(n)}};
addEventListener('hashchange',fromHash); fromHash();
</script></body></html>""", encoding="utf-8")
print(OUT, OUT.stat().st_size // 1024, "KB,", total, "slides")
