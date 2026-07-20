# Flatpak And NumPy

GNOME Builder runs the app inside the Flatpak sandbox from `io.github.jackrabbithanna.solarsystembuilder.json`. Host Python packages do not make modules available to the app at runtime.

## Application Source

The final `solar-system-builder` module in the manifest is the application source, not a Python dependency. Its HTTPS Git URL points to the public `main` branch so a standalone `flatpak-builder` invocation can fetch the project without relying on a developer-specific filesystem path.

During normal development, GNOME Builder stops dependency processing at the application module and builds the open working tree. Local edits therefore do not need to be pushed before they can be built in Builder.

## NumPy Dependency

`src/physics.py` imports NumPy. The Flatpak manifest includes an inline `python3-numpy` module before the app module so NumPy is installed into `/app`.

The module currently pins:

```txt
numpy==2.2.4
```

The manifest includes CPython 3.13 wheels for `aarch64` and `x86_64`. Pip chooses the compatible wheel during the Flatpak build. These are the architectures currently supported by the manifest.

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

The manifest targets GNOME Platform and SDK 49. Both matching Flatpak refs must be installed on a development machine before performing a complete standalone build.
