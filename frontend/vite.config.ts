import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Port 3000 matches the `web` service in .claude/skills/local-stack-bootstrap/SKILL.md.
// host pinned to IPv4 127.0.0.1 -- Vite's default "localhost" binds IPv6-only
// ([::1]) on this machine, which some browsers/tools fail to reach even
// though "localhost" resolves fine for others (e.g. curl falls back to
// IPv6 automatically, masking the problem). Pin to the same address family
// the backend (127.0.0.1:8000) uses to avoid this mismatch.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 3000,
  },
})
