# DealForge AI project site

Static site (no build step, no external requests) published with GitHub Pages.

- `index.html`, `docs/*.html` are generated: `python website/tools/build_site.py`
- `website/tools/facts.json` holds the agent and tool catalog extracted from the code registry
- Media lives in `assets/img` (WebP/JPG) and `assets/video` (H.264 MP4)
- Preview locally: `python -m http.server -d website 8080`
- Deploy: `.github/workflows/pages.yml` uploads `website/` on push to `main`
  (enable once under Settings → Pages → Source: GitHub Actions)
