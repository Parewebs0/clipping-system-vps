import { AlertTriangle, KeyRound } from 'lucide-react'
import { ApiError } from '@/api/client'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  )
}

export function ErrorBlock({ error }: { error: unknown }) {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return (
      <Alert>
        <KeyRound className="size-4" />
        <AlertTitle>Hace falta el token de la API</AlertTitle>
        <AlertDescription>Pega el Bearer token (API_TOKEN) en la barra superior y pulsa «Guardar».</AlertDescription>
      </Alert>
    )
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="size-4" />
        <AlertTitle>No encontrado</AlertTitle>
        <AlertDescription>
          {String(error.message)}. Si es todo el dashboard, comprueba MISSION_CONTROL_ENABLED=true en el .env.
        </AlertDescription>
      </Alert>
    )
  }
  return (
    <Alert variant="destructive">
      <AlertTriangle className="size-4" />
      <AlertTitle>Error al cargar</AlertTitle>
      <AlertDescription>{error instanceof Error ? error.message : String(error)}</AlertDescription>
    </Alert>
  )
}

export function EmptyBlock({ children }: { children: React.ReactNode }) {
  return <div className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">{children}</div>
}
