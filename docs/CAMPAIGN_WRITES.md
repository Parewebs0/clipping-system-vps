# Escritura de campañas y máquina de estados manual (issue #9)

Fuente de verdad: `app/services/campaign_transitions.py` (expuesta en `GET /campaigns/status-machine`).

## Principio
Los **ticks del pipeline v2** (cron) son los únicos que mueven una campaña *hacia delante*.
Una persona (Mission Control / API) solo puede **reintentar un paso** (devolver la campaña a un estado anterior
que el tick correspondiente vuelve a recoger), **aparcarla** o **archivarla**. Así ningún cambio manual hace que
`download_enqueue_tick` encole descargas de una campaña cuyo brief/assets no se han procesado.

| Desde | Destinos manuales | Quién consume el estado de origen |
|---|---|---|
| `discovered` | `archived` | brief_reader_tick (3a) |
| `briefed` | `discovered`, `archived` | drive_resolver_tick (3b) |
| `assets_resolved` | `briefed`, `discovered`, `archived` | campaign_scorer_tick (3c) |
| `scored` | `assets_resolved`, `blocked_no_assets`, `archived` | download_enqueue_tick (7) |
| `blocked_no_assets` | `assets_resolved`, `briefed`, `archived` | — |
| `failed_brief` | `discovered`, `archived` | — |
| `failed_resolve` | `briefed`, `discovered`, `archived` | drive_resolver_tick (solo kind=gog) |
| `archived` (nuevo, 0017) | `discovered` | — |

Efectos:
- → `discovered`: 3a vuelve a leer el brief (**llamada LLM**, coste).
- → `briefed`: 3b vuelve a resolver enlaces/crear assets.
- → `assets_resolved`: 3c recalcula el score.
- → `blocked_no_assets`: deja de encolar descargas nuevas (los jobs ya encolados siguen).
- → `archived`: fuera del pipeline; **no** cuenta para `--max-active` de discovery; re-discovery no la reactiva
  (upsert por `source_url` conserva el estado). **No cancela** jobs ya encolados.

Cada cambio queda en `source_metadata.status_history` (últimos 50: from, to, at, by, reason).

## Endpoints (todos con `Authorization: Bearer`)
| Método | Ruta | Notas |
|---|---|---|
| `GET` | `/campaigns/status-machine` | estados, quién los consume, efecto y destinos manuales |
| `PATCH` | `/campaigns/{id}` | parcial: `name`, `source_url`, `source_id`, `source_instructions`, `spec` (parcial; `spec.extra` se mezcla, conserva datos del scorer), `source_metadata` (merge), `status` (+`status_reason`). `extra=forbid` → 422 con campos desconocidos |
| `POST` | `/campaigns/{id}/status` | `{status, reason?}` — 400 estado desconocido, 409 transición no permitida |
| `DELETE` | `/campaigns/{id}` | 204; solo `archived` y sin jobs activos (409 si no). Cascada FK a assets/candidatos/clips/publicaciones |

Reglas:
- `source_provider` no es editable.
- `source_url`/`source_id` solo editables en campañas `manual` (en `whop` `source_url` es la clave de dedup de discovery) → 409.
- Nombre duplicado → 409. `duration_min > duration_max` → 422 (o 400 si choca con el valor ya guardado).
