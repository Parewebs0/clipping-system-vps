import { ExternalLink } from 'lucide-react'
import { hostOf } from '@/lib/format'
import { cn } from '@/lib/utils'

export function ExtLink({ href, children, className }: { href?: string | null; children?: React.ReactNode; className?: string }) {
  if (!href) return <span className="text-muted-foreground">—</span>
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className={cn('inline-flex max-w-full items-center gap-1 text-sky-700 hover:underline', className)}>
      <span className="truncate">{children ?? hostOf(href) ?? href}</span>
      <ExternalLink className="size-3 shrink-0" />
    </a>
  )
}

export function PathCell({ path, baseUrl }: { path?: string | null; baseUrl?: string | null }) {
  if (!path) return <span className="text-muted-foreground">—</span>
  if (baseUrl) {
    const href = baseUrl.replace(/\/$/, '') + '/' + path.replace(/\\/g, '/').replace(/^\/+/, '')
    return (
      <a href={href} target="_blank" rel="noreferrer noopener" className="font-mono text-xs break-all text-sky-700 hover:underline">
        {path}
      </a>
    )
  }
  return <span className="font-mono text-xs break-all">{path}</span>
}

export function JsonBlock({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={cn('bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs leading-relaxed', className)}>
      {JSON.stringify(value ?? null, null, 2)}
    </pre>
  )
}

export function KV({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('font-mono text-xs', className)}>{children}</span>
}
