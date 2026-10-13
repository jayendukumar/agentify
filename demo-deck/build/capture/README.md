# Recapturing the deck screenshots

Use this after re-running the simulation, to refresh `../screenshots/` from a live environment.
It needs Node 22+ (built-in `fetch` and `WebSocket`) and Microsoft Edge or Chrome. Playwright isn't needed.

1. **Run and seed the demo environment** (see `demo-deck/simulation/README.md` and
   `backend/scripts/demo_seed/`). The step lists expect the API on port 8020.
2. **Update the IDs.** The step lists in `steps/` hold the process and batch IDs from the
   original run (`proc_9ea4381551bb`, `batch_41412c61b506`). Replace them with the IDs the seed prints.
3. **Build the frontend against the local proxy:**
   ```sh
   cd frontend
   VITE_API_BASE_URL=http://127.0.0.1:4310 npx vite build --outDir ../demo-deck/build/capture/demo-dist
   ```
4. **Serve it.** `node serve.mjs` serves the build on port 4310 and proxies `/api` to 8020, so the
   page and the API share one origin and CORS isn't involved. Set `DIST` to serve a build from another folder.
5. **Start a headless browser with its own throwaway profile:**
   ```sh
   msedge --headless=new --disable-extensions --remote-debugging-port=9333 --user-data-dir=<temp dir> about:blank
   ```
6. **Log in once, then run the steps.** `00-login.json` logs the browser in as the seed's presenter;
   the session cookie lasts for the remaining steps.
   ```sh
   node drive.mjs steps/00-login.json
   node drive.mjs steps/01-context-to-agent.json
   node drive.mjs steps/02-golden-set-to-gate.json
   node drive.mjs steps/03-case-study-extras.json
   node drive.mjs steps/04-replay-frames.json && python make_replay_gif.py
   ```
7. **Rebuild the decks** with the scripts in `..` (see `../README.md`).

Step types in the lists: `go` (path; the page is zoomed to `ZOOM`, default 0.8), `click` (by visible
text), `select` (option text), `fill` (by placeholder), `scroll` (pixels or heading text), `eval`,
`probe` (prints headings, tabs and buttons, useful when writing new steps) and `shot`.
Shots are full 1600x900 viewport captures; the deck scripts crop the empty margins.
