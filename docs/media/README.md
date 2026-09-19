# Flowtest presentation assets

`flowtest.gif` is a programmatically drawn concept demonstration, not a product screen recording or proof of execution. It contains synthetic order data and no account, application or browser-session data.

The story is specific to Flowtest: capture actions and a real assertion; freeze source plus environment for a historical rerun; retain the first failed attempt even after a successful retry. Indigo represents evidence and stable identity, graphite supports readable source and state, green is reserved for a passed assertion, and amber keeps flaky outcomes visible. Shallow depth and restrained spring motion make the flow tangible without a fictional dashboard.

- `flowtest-poster.png`: static alternative for readers who prefer no motion.
- `architecture.svg`: actual component relationships, with source mapping in `../architecture.md`.
- `render.py`: animation source, maintained in the backend repository.

Regenerate from the backend root in a separate environment (not needed to run Flowtest):

```sh
python -m venv .media-venv
# Activate the environment using your shell's normal activation command.
python -m pip install Pillow==12.1.0
python docs/media/render.py
```

The renderer uses installed fonts only. Set `FLOWTEST_MEDIA_FONT` to a TrueType font path on other systems. It renders at 2x resolution and downsamples to 1120 x 640, uses a shared palette, and retains the final evidence frame before looping. Font metrics may vary by platform. No font files are distributed.
