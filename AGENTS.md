# Agent Instructions

This is a Python based GNOME 49 / GTK4 / Libadwaita application.

Read `docs/CODEBASE_OVERVIEW.md` before making non-trivial changes.

Important project rules:

- The app is packaged and run by GNOME Builder through the Flatpak manifest `io.github.jackrabbithanna.solarsystembuilder.json`.
- NumPy is required by the physics engine and is installed inside the Flatpak sandbox by an inline `python3-numpy` module. Host Python packages do not make NumPy available to the running app.
- Physics and model code must stay independent of GTK so it remains unit-testable.
- Use SI units internally: kg, meters, m/s, and seconds.
- `physics.step()` is a low-level single integrator step. UI playback must use `physics.advance()` so large user-visible time steps are internally substepped.
- GTK widgets and mutable UI model objects must only be touched on the GTK main thread. Playback simulation work runs in a background worker and applies completed states through `GLib.idle_add`.
- Do not update GTK widgets from worker threads.
- Run `python3 -m unittest discover -s tests` and `meson test -C builddir` after changes that affect behavior or packaging.
