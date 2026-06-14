# Solar System Builder

Solar System Builder is a Python GNOME  / GTK4 / Libadwaita application for configuring and simulating solar systems. It currently provides a 2D top-down orbital view, a bundled Solar System preset, editable body parameters, local JSON persistence, and a NumPy-backed physics core with a first post-Newtonian approximation.

## Development

Configure and test with Meson:

```sh
meson setup builddir
meson test -C builddir
```

Run the Python unit tests directly:

```sh
python3 -m unittest discover -s tests
```

GNOME Builder runs the app inside the Flatpak sandbox described by `io.github.jackrabbithanna.solarsystembuilder.json`. NumPy is installed by an inline Flatpak module in that manifest.

## Documentation

Start with:

- `docs/CODEBASE_OVERVIEW.md`
- `docs/PHYSICS.md`
- `docs/GTK_PLAYBACK.md`
- `docs/FLATPAK_AND_NUMPY.md`
- `docs/DECISIONS.md`
- `docs/NEXT_STEPS.md`
