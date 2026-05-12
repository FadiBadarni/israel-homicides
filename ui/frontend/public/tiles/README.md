# Protomaps Tile File

The memorial map uses a self-hosted Protomaps `.pmtiles` file covering Israel.
The file is excluded from git because of size (~80 MB).

## Build or download

**Option A — download from the Protomaps public CDN, clipped to Israel:**

```bash
# Requires pmtiles CLI: https://docs.protomaps.com/pmtiles/cli
pmtiles extract https://build.protomaps.com/20260501.pmtiles \
  ui/frontend/public/tiles/israel.pmtiles \
  --bbox=34.2,29.5,35.9,33.5
```

**Option B — download the full planet file (~120 GB) and extract locally:**
See https://docs.protomaps.com/pmtiles for the canonical workflow.

## Verification

After download:

```bash
pmtiles show ui/frontend/public/tiles/israel.pmtiles
```

The output should report a non-zero tile count and a bounding box covering Israel.
