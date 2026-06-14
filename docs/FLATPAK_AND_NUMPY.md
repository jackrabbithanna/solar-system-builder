# Flatpak And NumPy

GNOME Builder runs the app inside the Flatpak sandbox from `io.github.jackrabbithanna.solarsystembuilder.json`. Host Python packages do not make modules available to the app at runtime.

## NumPy Dependency

`src/physics.py` imports NumPy. The Flatpak manifest includes an inline `python3-numpy` module before the app module so NumPy is installed into `/app`.

The module currently pins:

```txt
numpy==2.2.4
```

The manifest includes CPython 3.13 wheels for `aarch64` and `x86_64`. Pip chooses the compatible wheel during the Flatpak build.

## When Dependencies Change

After editing Flatpak dependencies:

1. Do a clean rebuild of the GNOME Builder Flatpak deployment.
2. Verify inside the sandbox that the import works:

   ```sh
   python3 -c "import numpy; print(numpy.__version__)"
   ```

3. Run the app from Builder.

## Local Tests

Local unit tests use the host Python environment. Passing local tests does not prove the Builder sandbox has the same dependencies. Manifest changes need a Builder/Flatpak rebuild.

## Manifest Notes

Keep dependency modules before the `solar-system-builder` app module. The app imports NumPy at startup, so NumPy must already be installed into `/app`.
