# Memorial Frontend

Bilingual (Arabic / Hebrew) Next.js memorial register. Deployed on Vercel — reads static JSON, no backend required in production.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Hero with animated stats, year timeline, searchable victim cards, contact strip |
| `/cases/[runId]/[caseIndex]` | Victim memorial — photo gallery, incident facts, source citations |
| `/contribute` | Call to action for data contributions |

## Local dev

```bash
cd ui/frontend
npm install
npm run dev       # http://localhost:3000
```

Static data lives in `frontend/public/data/`. To regenerate from the pipeline DB:

```bash
python scripts/export_static_data.py
```

## Optional: live API mode

Only needed when iterating on the API surface itself:

```bash
uvicorn ui.api.main:app --reload --port 8001
echo "NEXT_PUBLIC_API_URL=http://localhost:8001" > ui/frontend/.env.local
cd ui/frontend && npm run dev
```

## Key files

| File | Responsibility |
|------|---------------|
| `frontend/app/page.tsx` | Home — stats, year timeline, victim cards, search |
| `frontend/app/cases/[runId]/[caseIndex]/page.tsx` | Case detail — photos, facts, sources |
| `frontend/app/contribute/page.tsx` | Contribute call to action |
| `frontend/app/globals.css` | All styling (CSS custom properties, RTL, responsive) |
| `frontend/lib/i18n.ts` | Translation system — Arabic + Hebrew |
| `frontend/lib/api.ts` | Data fetching (static JSON or API) |
| `frontend/components/count-up.tsx` | Animated stat counter |
| `frontend/components/language-toggle.tsx` | AR / HE language switcher |
| `frontend/components/page-transition.tsx` | Route transition animation |
| `api/main.py` | FastAPI backend (dev convenience only) |
