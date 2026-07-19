# Solar System Builder

Solar System Builder is a GNOME desktop application for creating, exploring, and simulating planetary and multi-star systems. Start from a bundled system, build one from scratch, or import current Solar System bodies from JPL Horizons, then inspect how the system evolves under gravity.

The model stores complete three-dimensional positions and velocities in SI units. The current canvas presents those systems as an interactive top-down 2D view, making the app useful for visual exploration and simulation experiments rather than precision navigation or mission planning.

## Features

- Explore bundled Solar System, dwarf-planet, and Alpha Centauri presets.
- Create single-star, binary-star, and hierarchical systems, then save editable copies in a local JSON library.
- Add and organize stars, planets, dwarf planets, moons, comets, asteroids, nested system groups, and persistent unbound flybys.
- Edit complete Cartesian state vectors or generate them from elliptic and hyperbolic orbital elements.
- Search JPL Horizons for bodies, import available physical data, and atomically refresh an entire compatible system to one current epoch.
- Run NumPy-backed N-body playback with bounded internal substeps, a first post-Newtonian correction, and scalable focus or barycenter approximation policies for large systems.
- Follow selected bodies and systems, focus on planet-and-moon subsystems, use logarithmic overviews, and compare focused motion with coarse outside context.
- Inspect barycenters, distances, orbital trails, elapsed simulation time, reference-frame provenance, and saved simulation settings.

## Using the App

Use the canvas to select bodies, inspect trails, and zoom through the active system. Playback controls step or continuously advance simulated time. The settings below the canvas control the visible time interval, integration accuracy, view scale, physics policy, and trail perspective.

The sidebar switches between bundled and saved systems, exposes the body and group hierarchy, and provides creation, editing, orbital-generation, and JPL tools. Bundled presets are read-only; duplicate one when you want to edit or save changes.

For a complete guide, see the [User Interface documentation](./docs/USER_INTERFACE.md).

## Screenshots

### Solar System overview

> Screenshot placeholder — capture the main Solar System preset with the hierarchy, playback controls, and several orbital trails visible. Suggested file: `screenshots/solar-system-overview.png`.

### Focus and Fit

> Screenshot placeholder — capture a focused planet-and-moon subsystem with focused-parent trails and the outside-context inset visible. Suggested file: `screenshots/focus-and-fit.png`.

### Building a system

> Screenshot placeholder — capture the system editor, body creation workflow, or JPL Horizons review dialog. Suggested file: `screenshots/system-editor-horizons.png`.

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

- [User Interface](./docs/USER_INTERFACE.md)
- [Orbital Data](./docs/ORBITAL_DATA.md)
- [Physics](./docs/PHYSICS.md)
