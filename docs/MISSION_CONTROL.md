# Mission Control (dashboard)

SPA en **React 19 + TypeScript + Vite + Tailwind v4 + shadcn/ui** (`frontend/`), servida por la propia API
FastAPI en `/mission-control/` (StaticFiles + hash router). Sustituye a la SPA vanilla anterior (issue #8).

## Cómo se sirve / despliega
- `Dockerfile` multi-stage: la etapa `node:22-alpine` hace `npm ci && npm run build` y copia
  `app/static/mission-control/` a la imagen Python. Desplegar = lo mismo de siempre:
  `docker compose build api && docker compose up -d api`.
- Requiere `MISSION_CONTROL_ENABLED=true` en `.env` (si no, los endpoints `/mission-control/*` dan 404).
- URL en el mini PC: `http://100.70.150.107:8080/mission-control/` (Tailscale).
- Auth: el usuario pega el Bearer token en la barra superior; se guarda en `sessionStorage` (solo esa pestaña).

## Contrato con el backend
- Los endpoints `/mission-control/*` tienen `response_model` (`app/schemas/mission_control.py`).
- `python scripts/export_openapi.py` → `frontend/openapi.json` (test `tests/test_openapi_export.py` falla si está desactualizado).
- `cd frontend && npm run gen:api` → `src/api/schema.d.ts` (tipos generados; `npm run check:api` comprueba que están al día).
- Flujo al cambiar un modelo: tocar el schema Pydantic → `export_openapi.py` → `npm run gen:api` → `npm run typecheck`.

## Desarrollo local
```bash
cd frontend
npm ci
MC_API_TARGET=http://100.70.150.107:8080 npm run dev   # proxy a la API real (o a uvicorn local)
npm run typecheck && npm run lint && npm test && npm run build
```
`npm run build` escribe en `../app/static/mission-control/` (ignorado en git).

## Páginas
Overview · Campañas (filtro por estado, búsqueda) · Detalle (resumen Whop, errores 3a/3b con acción sugerida,
coste LLM, tabs Assets / Pipeline / Clips / Jobs activos / Reglas y score / Metadata) · Jobs · Vídeos · Clips.

## Escritura (issue #10)
- **Editar** (Sheet, react-hook-form + zod; `src/lib/campaignForm.ts` replica la validación Pydantic): nombre,
  URL/Source ID (solo `manual`), instrucciones, spec (duración min ≤ max, formato, idioma, subtítulos, watermark,
  keywords). Solo se envían los campos cambiados (`PATCH /campaigns/{id}`).
- **Cambiar estado**: menú con las transiciones de `GET /campaigns/status-machine` + `AlertDialog` con el efecto
  en el pipeline y motivo opcional (`POST /campaigns/{id}/status`). Pestaña *Historial* con `status_history`.
- **Borrar**: solo campañas archivadas, confirmando con el nombre (`DELETE /campaigns/{id}`).
- **Nueva campaña manual** (`source_provider=manual`) desde la lista.
- Feedback con toasts (sonner) e invalidación de queries (lista, detalle, overview).
