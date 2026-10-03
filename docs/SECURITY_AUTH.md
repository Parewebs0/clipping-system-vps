# Autenticación de la API y del dashboard (issue #11)

## Estado
- **Todos** los endpoints de datos exigen `Authorization: Bearer <token>` (`app/auth.py`, comparación en tiempo
  constante). `/health` y los estáticos de `/mission-control/` (HTML/JS sin datos) son públicos.
- El dashboard pide el token al usuario y lo guarda en `sessionStorage` (solo esa pestaña; nunca localStorage).

## Tokens
| Variable (`.env`) | Uso |
|---|---|
| `API_TOKEN` | token histórico; lo usa el worker Windows. Lee todo y, si no hay `API_WRITE_TOKEN`, también escribe campañas |
| `API_WRITE_TOKEN` (opcional, nuevo) | token de operador/dashboard. Si está definido: lee todo **y** es el único que puede crear/editar/cambiar estado/borrar campañas (`POST/PATCH/DELETE /campaigns…`, `POST /campaigns/{id}/status`). `API_TOKEN` recibe **403** en esos endpoints y sigue funcionando en todo lo demás (worker) |

Activarlo (decisión del operador):
```bash
# en /srv/datos/apps/clipping-system-vps/.env  (no commitear)
API_WRITE_TOKEN=$(openssl rand -hex 32)
docker compose up -d api   # recarga env
```
y usar ese token en Mission Control.

## Exposición de red
`docker-compose.yml` publica `${API_BIND_ADDR:-0.0.0.0}:8080`. En el mini PC `API_BIND_ADDR` no está fijado →
la API escucha también en la LAN. Recomendado: `API_BIND_ADDR=100.70.150.107` (IP Tailscale; el worker ya entra
por Tailscale) para que solo sea accesible desde la tailnet. Tráfico HTTP sin TLS: dentro de Tailscale va cifrado
por WireGuard; en la LAN no.
