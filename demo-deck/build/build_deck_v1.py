"""Builds a self-contained HTML slide deck from the demo screenshots."""
import base64
import html
import io
import sys
from pathlib import Path

from PIL import Image, ImageChops

HERE = Path(__file__).parent
SHOTS = HERE / "screenshots"
OUT = Path(sys.argv[1])

# (screenshot, act label, title, bullets)
SLIDES = [
    ("01-org-profile", "Act 1 · Organization context · Epic 27", "Describe the organization once",
     ["Meridian Group: banking, 7 countries, Retail Banking and Wealth Management.",
      "Every later step that uses AI draws on this, and cites what it used."]),
    ("02-rule-packs", "Act 1 · Starter rule packs · Epic 27", "Start from plain-language rule packs",
     ["EU data protection and EU & UK anti-money-laundering packs added in one click.",
      "Versioned: a newer pack version shows what changed."]),
    ("03-approval-authorities", "Act 1 · Who approves what · Epic 27", "Approval limits become checkable facts",
     ["Head of Operations approves credit limits up to EUR 50,000; Chief Risk Officer up to EUR 500,000.",
      "These limits later drive the agent's decision rules and the test cases."]),
    ("04-controls", "Act 1 · Controls from policy · Epic 27", "Controls the agents must obey",
     ["From the starter packs, plus policy ONB-01: AI extracted 8 rules and a person confirmed them.",
      "Controls that need a human reviewer are flagged, with the role that must review."]),
    ("05-what-applies", "Act 1 · What applies where · Epic 27", "Ask: what applies to Retail Banking in Germany?",
     ["2 regulations, 19 obligations and 11 controls apply.",
      "Germany inherits everything that applies across the EU."]),
    ("06-applications", "Act 2 · Application catalog · Epic 29", "The systems the agents will use",
     ["5 applications imported from a CMDB-style CSV.",
      "Core Banking and Credit Decisioning come with their OpenAPI specs attached."]),
    ("07-data-assets", "Act 2 · Data and third parties · Epic 29", "What data lives where",
     ["Data assets carry classifications: Confidential-PII, Restricted.",
      "The third-party register shows which agents and processes depend on each vendor."]),
    ("11-blueprint", "Act 3 · Agentic blueprint · Epic 28", "From a process diagram to a blueprint",
     ["Customer Onboarding: 14 steps assessed, 12 agents proposed.",
      "86% of steps automatable or partially automatable."]),
    ("12-blueprint-step", "Act 3 · Grounded assessment · Epic 28", "Each step's assessment cites the rules",
     ["'Assess and set initial credit limit' is only partially automatable.",
      "Why: GDPR Art. 22 (significant automated decisions) and the approval limits."]),
    ("13-context-used", "Act 3 · Citations · Epic 28", "Show the organization context used",
     ["47 rules and facts plus 5 policy passages went into the blueprint.",
      "Each one is numbered (C1, C2 ...) so recommendations trace back to their source."]),
    ("14-agent", "Act 3 · The agent · Epic 28", "CreditAssessmentAgent",
     ["Generated with its purpose, trigger, tools and knowledge sources.",
      "Version 2 is the one under test for release."]),
    ("run-replay", "Act 4 · Simulation replay · Epic 30", "Watch a test run, step by step",
     ["A compliance test for the credit agent, replayed in the digital twin.",
      "The agent looks up Core Banking and Credit Decisioning, then asks a human to review before acting."]),
    ("13b-compliance-check", "Act 4 · Compliance-aware testing · Epic 30", "Every applicable rule gets a test",
     ["One test situation per rule the agent must obey (GDPR human-in-the-loop, AML PEP approval).",
      "Found a real gap: the agent escalated correctly but did not log a reason for its decision."]),
    ("15-golden-cases", "Act 5 · Golden cases · Epics 32-33", "Cases with known right answers",
     ["Each decision point in the process becomes a rule the system can check.",
      "Rules label cases automatically: identity, sanctions, and the EUR 50,000 limit."]),
    ("17-dataset", "Act 5 · The golden set · Epic 32", "93 cases from three sources",
     ["Expert cases (5 marked critical), past decisions with personal data pseudonymized, and AI-generated cases.",
      "9 cases held out for unbiased evaluation."]),
    ("19-oracle-conflicts", "Act 5 · Rules vs history · Epic 33", "The rules catch past decisions that broke them",
     ["3 past decisions were approved above EUR 50,000 without escalation.",
      "Either the label is wrong or the documented rule isn't how the process really runs."]),
    ("18-run-at-volume", "Act 6 · Run at volume · Epic 34", "Test the agent at volume, on a budget",
     ["Full batch: 92 runs per agent version, for about $0.08 per batch.",
      "Plus a load test at 8 concurrent runs, and a nightly regression schedule."]),
    ("21-batch-results", "Act 7 · Results · Epic 35", "53% correct, with an honest range",
     ["Between 43% and 63% with 95% confidence, from 92 graded runs.",
      "Weak spots: edge cases (20%) and name-casing variants (30%)."]),
    ("23-how-it-decides", "Act 7 · What goes wrong · Epic 35", "Where the agent goes wrong",
     ["Biggest failure group: 18 cases that should have been escalated were approved.",
      "13 cases that should have been rejected were approved."]),
    ("25-compare", "Act 7 · Release gate · Epic 35", "The release gate blocks v2",
     ["v2 vs v1: fixed 17 cases, broke 13, not a real change (p = 0.58).",
      "Blocked: pass rate could be as low as 43% against an 85% bar, and 4 critical cases failed."]),
    ("09-stewards", "Federation · Epic 31", "Each business unit stewards its own context",
     ["The Wealth Management analyst sees and stewards only Wealth Management and group-wide context."]),
    ("08-connectors", "Federation · Epic 31", "Keep context in sync with source systems",
     ["A SharePoint policy folder and the ServiceNow CMDB feed, each on its own schedule.",
      "API tokens are referenced by name and never stored."]),
]


def trim(im: Image.Image) -> Image.Image:
    """Crop the empty page margins either side of the app's content column
    (below the nav bar, minus the scrollbar), so the content fills the slide."""
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


def slide(i: int, total: int, shot: str, act: str, title: str, bullets: list[str]) -> str:
    items = "".join(f"<li>{html.escape(b)}</li>" for b in bullets)
    return f"""<section class="slide">
  <div class="shot"><img class="{'grow' if shot.startswith('run-') else ''}" src="{data_uri(shot)}" alt="{html.escape(title)}"></div>
  <aside><div class="act">{html.escape(act)}</div><h2>{html.escape(title)}</h2><ul>{items}</ul>
  <div class="num">{i} / {total}</div></aside>
</section>"""


total = len(SLIDES) + 2
body = [f"""<section class="slide title"><div>
  <div class="act">Axyntro · Epics 27-35 demo</div>
  <h1>From policy to a release decision</h1>
  <p>Meridian Group · Customer Onboarding · the credit-limit agent</p>
  <p class="hint">Arrow keys or click to move · F for full screen</p></div></section>"""]
body += [slide(i + 2, total, *s) for i, s in enumerate(SLIDES)]
body.append(f"""<section class="slide title"><div>
  <div class="act">Summary</div><h1>What the audience saw</h1>
  <ul class="wrap">
  <li>Organization context and policies turned into rules that cite their source</li>
  <li>A blueprint and agent grounded in those rules</li>
  <li>Compliance tests generated per rule, with a gap found</li>
  <li>A 93-case golden set, checked against the rules</li>
  <li>Volume testing with honest confidence ranges</li>
  <li>A release gate that blocked an agent that wasn't ready</li>
  </ul><div class="num">{total} / {total}</div></div></section>""")

OUT.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>Axyntro demo: Epics 27-35</title>
<style>
*{{box-sizing:border-box}} html,body{{margin:0;height:100%;background:#0d1530;font-family:Inter,Segoe UI,Arial,sans-serif}}
.slide{{display:none;position:absolute;inset:0;margin:auto;width:100vw;height:56.25vw;max-height:100vh;max-width:177.78vh;background:#f6f8fc;
  grid-template-columns:72% 28%;overflow:hidden}}
.slide.on{{display:grid}}
.shot{{display:flex;align-items:center;justify-content:center;padding:1.6vmin;background:#e9edf6}}
.shot img.grow{{width:100%}} .shot img{{max-width:100%;max-height:100%;border-radius:.8vmin;box-shadow:0 .5vmin 2vmin rgba(13,21,48,.25)}}
aside{{position:relative;background:#16245c;color:#fff;padding:5vmin 3.5vmin}}
.act{{color:#8fb3ff;font-size:1.9vmin;font-weight:600;letter-spacing:.03em;text-transform:uppercase}}
h2{{font-size:3.8vmin;line-height:1.2;margin:2vmin 0 3vmin}}
ul{{padding-left:2.4vmin;margin:0}} li{{font-size:2.3vmin;line-height:1.45;margin-bottom:2vmin;color:#dfe7ff}}
.num{{position:absolute;bottom:3vmin;right:3.5vmin;font-size:1.7vmin;color:#8fb3ff}}
.slide.title{{grid-template-columns:1fr;place-items:center;background:linear-gradient(135deg,#16245c,#0d1530);color:#fff}}
.title > div{{position:relative;width:75%}} .title h1{{font-size:7vmin;margin:2vmin 0}} .title p{{font-size:2.8vmin;color:#dfe7ff}}
.title .hint{{font-size:1.9vmin;color:#8fb3ff;margin-top:6vmin}} .title .wrap li{{font-size:2.9vmin}}
.title .num{{bottom:-8vmin;right:0}}
</style></head><body>
{chr(10).join(body)}
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
