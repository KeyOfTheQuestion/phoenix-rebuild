# Phoenix contributor instructions

- This repository is an evidence-based engineering experiment. Never invent
  successful rebuilds, model identities, usage, USD costs, users, stars, or dates.
- Read `specs/webhook-inbox.md` and `docs/methodology.md` before changes.
- Keep reference checks separate from `ai-rebuild` records. Preserve failures.
- Treat archived evidence as historical records; do not silently edit candidate
  source or convert failed outcomes to passing outcomes.
- Keep implementation, generator and verifier separate. Do not copy reference
  source into generation directories. Do not put credentials or personal data in
  evidence, examples, screenshots or commits. Public payloads must be synthetic.
- A subprocess is not a hostile-code sandbox. Do not broaden isolation claims.
- Maintain Python 3.9+ and zero runtime dependencies for the baseline and harness.
- The English and Chinese README must agree about implemented functionality.
- For substantive changes, run `python3 -m unittest discover -s tests -v`,
  `python3 -m phoenix verify`, and `python3 -m phoenix audit` before committing.
- Use small descriptive commits. After publishing, check CI and Pages outcomes.
- Continue from `docs/roadmap.md`; prioritize one concrete, verifiable improvement
  per recurring iteration. Run at most one normal rebuild cycle (two attempts,
  300-second limit each) unless the human explicitly requests more.
- Never message third-party maintainers, market the project, or post to communities
  without separate user authorization.
