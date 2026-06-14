# Solar System Builder Implementation State

This file replaces the original starter-project implementation plan. Current architectural notes are split across `docs/` so future Codex sessions can load only the context they need.

## Implemented

- Python GNOME 49 / GTK4 / Libadwaita application shell.
- 2D GTK drawing surface for solar-system playback.
- NumPy-backed physics core with Newtonian and first post-Newtonian modes.
- Stable playback through `physics.advance()`, which splits large user-visible time intervals into bounded internal steps.
- Background playback worker that keeps GTK responsive and applies completed simulation states on the main thread.
- Bundled Solar System preset data.
- Schema-versioned `Body` and `SolarSystem` models.
- Local JSON storage library.
- Meson test wiring and Python unit tests.
- Flatpak manifest with inline NumPy wheel dependency for GNOME Builder.

## Active References

- `docs/CODEBASE_OVERVIEW.md` for module responsibilities and data flow.
- `docs/PHYSICS.md` for integrator constraints and the Mercury 30-day regression.
- `docs/GTK_PLAYBACK.md` for threading and GTK update rules.
- `docs/FLATPAK_AND_NUMPY.md` for Builder dependency behavior.
- `docs/NEXT_STEPS.md` for likely follow-up work.
