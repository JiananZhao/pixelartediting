# pixelartediting
# Open Pixel Studio Web (Streamlit)

This folder contains a browser-oriented Streamlit port of the original desktop editor.

## What changed

The original app used `tkinter`, which works as a desktop GUI but does not run as-is on Streamlit Community Cloud. This port keeps the Pillow/project logic and replaces the interface with a Streamlit web UI.

## Included files

- `streamlit_app.py` - Streamlit browser app
- `ops_core.py` - shared project model and image-editing logic
- `requirements.txt` - deployment dependencies

## Features in this port

- load and save `.opsprite` projects
- import PNG, GIF, JPG, and sprite sheets
- layers and frames
- onion skin and grid
- tools: pencil, eraser, fill, eyedropper, line, rectangle, ellipse, move, slice
- export current frame PNG, animated GIF, and sprite sheet bundle ZIP

## Deployment on Streamlit Community Cloud

1. Put these files in a GitHub repository.
2. Sign in to Streamlit Community Cloud.
3. Create a new app.
4. Choose your repository and set the entrypoint to `streamlit_app.py`.
5. Deploy.

## Local run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Notes

- This is a browser port, not a byte-for-byte clone of the desktop UI.
- The canvas interaction is built around a Streamlit image-click component, so the editing feel is web-friendly but not identical to a native desktop pixel editor.
- Community Cloud is convenient for sharing and editing in the browser, but very large projects may feel slower than a desktop app.
