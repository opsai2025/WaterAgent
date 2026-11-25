# Project handoff for coding agents

- Read `README.md` and `REDESIGN_HANDOFF.md` before starting the redesign.
- Use the existing working tree as the current baseline. Inspect `git status`;
  do not discard, overwrite, or assume ownership of unrelated changes.
- Run the application and checks in WSL `ubuntu-monk`, using the project's
  Linux `.venv` when available. Preserve local credentials and never print or
  commit `.env`, `earth_engine_key_base64.txt`, tokens, or their contents.
- Follow the current decisions and remaining work in `REDESIGN_HANDOFF.md`,
  `DEPLOY_POSIT_SUPABASE.md`, and `POSIT_SUPABASE_DESIGN.md`; older sections
  contain historical questions that have already been answered.
- Posit `cloud_preview` is temporary and is not durable Supabase storage.
  Do not push the historical local `main` branch: GitHub detected a secret in
  its history. Check the active branch and user instructions before any push.
- Protect existing monitoring, recovery, export, and scientific behavior unless
  the user authorizes changing it. Verify behavior and explain any limitations.
