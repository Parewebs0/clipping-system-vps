import { NavLink, Outlet } from 'react-router-dom'
import { Briefcase, Clapperboard, Film, LayoutDashboard, ListChecks, PlayCircle, Scissors } from 'lucide-react'
import { cn } from '@/lib/utils'
import { KeyRound } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useHasToken } from '@/hooks/useHasToken'
import { TokenControl } from './TokenControl'

const NAV = [
  { to: '/overview', label: 'Overview', icon: LayoutDashboard },
  { to: '/campaigns', label: 'Campañas', icon: Briefcase },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/videos', label: 'Vídeos', icon: Film },
  { to: '/candidates', label: 'Candidatos', icon: Scissors },
  { to: '/clips', label: 'Clips', icon: Clapperboard },
]

export function AppShell() {
  const hasToken = useHasToken()
  return (
    <div className="flex min-h-svh">
      <aside className="bg-sidebar sticky top-0 hidden h-svh w-56 shrink-0 flex-col border-r md:flex">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="bg-primary text-primary-foreground grid size-8 place-items-center rounded-md">
            <PlayCircle className="size-5" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold">Mission Control</div>
            <div className="text-muted-foreground text-xs">Clipping pipeline</div>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="Navegación">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive ? 'bg-sidebar-accent text-foreground' : 'text-muted-foreground hover:bg-sidebar-accent hover:text-foreground',
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="text-muted-foreground border-t px-4 py-3 text-xs">v3 · React + shadcn/ui</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="bg-background/80 sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b px-4 backdrop-blur md:px-6">
          <nav className="flex gap-1 md:hidden">
            {NAV.map(({ to, icon: Icon, label }) => (
              <NavLink key={to} to={to} aria-label={label} className={({ isActive }) => cn('rounded-md p-2', isActive && 'bg-muted')}>
                <Icon className="size-4" />
              </NavLink>
            ))}
          </nav>
          <div className="text-muted-foreground hidden text-sm md:block">Gestión de campañas, transcripción y clips</div>
          <TokenControl />
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">
          {hasToken ? (
            <Outlet />
          ) : (
            <Card className="mx-auto mt-16 max-w-md">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <KeyRound className="size-4" /> Introduce el token de la API
                </CardTitle>
                <CardDescription>
                  Mission Control usa el Bearer token de la API (API_TOKEN, o API_WRITE_TOKEN si está configurado para poder editar campañas). Se guarda solo en esta pestaña
                  (sessionStorage).
                </CardDescription>
              </CardHeader>
              <CardContent>
                <TokenControl />
              </CardContent>
            </Card>
          )}
        </main>
      </div>
    </div>
  )
}
