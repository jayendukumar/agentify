# Demo deck build scripts

These scripts rebuild the decks in `demo-deck/` from the screenshots in `screenshots/`
(captured from the demo-test environment seeded by `backend/scripts/demo_seed/seed_demo.py`).

| Script | Builds |
| --- | --- |
| `build_deck_v1.py` | `axyntro-epics-27-35-demo.html`: the feature walkthrough, Epics 27-35 |
| `build_deck_v2.py` | `axyntro-meridian-case-study-v2.html`: the case study for business audiences |
| `build_pptx.py` | `axyntro-meridian-case-study-v2.pptx`: the same case study as PowerPoint |
| `build_process_twin_deck.py` | `axyntro-process-twin-invoice-to-pay.html` + `.pptx`: the Epic 36 process twin demo (9-10 agents, Invoice to Pay) |
| `build_enterprise_deck.py` | `axyntro-enterprise-readiness.html` + `.pptx`: what it takes to reach enterprise grade |

`build_pptx.py` reads the slide text from `build_deck_v2.py`, so edit the story there and
rebuild both. Each script takes the output path as its only argument.

The two newer decks use `deck_kit.py`: slides are data, rendered to HTML and PowerPoint from the
same source, so the two can't drift. They read their numbers from files rather than from text:
`demo-deck/process-twin/artifacts/results/` (written by `backend/scripts/demo_seed/seed_process_twin.py`)
and `docs/code-health/report.json` (`python -m tools.code_health report`).

```sh
python build_process_twin_deck.py ../axyntro-process-twin-invoice-to-pay.html ../axyntro-process-twin-invoice-to-pay.pptx
python build_enterprise_deck.py ../axyntro-enterprise-readiness.html ../axyntro-enterprise-readiness.pptx
```

```sh
pip install pillow python-pptx
cd demo-deck/build
python build_deck_v1.py ../axyntro-epics-27-35-demo.html
python build_deck_v2.py ../axyntro-meridian-case-study-v2.html
python build_pptx.py ../axyntro-meridian-case-study-v2.pptx
```

Screenshots are cropped of empty side margins when embedded. `run-replay.gif` is the
18-frame replay of a test run (also in `demo-deck/run-replay.gif`).

To refresh the screenshots from a new simulation run, see `capture/README.md`.
