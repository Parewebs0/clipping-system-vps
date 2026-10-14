import { cn } from '@/lib/utils'
import { formatPrizePool, spentPct } from '@/lib/campaigns'

interface BudgetBarProps {
  remaining?: number | null
  original?: number | null
  spent?: number | null
  className?: string
}

/** Prize pool «restante / original» + barra de gastado (MOLI-56). */
export function BudgetBar({ remaining, original, spent, className }: BudgetBarProps) {
  const pct = spentPct(spent, remaining, original)
  return (
    <div className={cn('inline-flex flex-col items-end gap-0.5', className)}>
      <span className="tabular-nums">{formatPrizePool(remaining, original)}</span>
      {pct != null && (
        <>
          <div className="h-1 w-24 overflow-hidden rounded-full bg-muted">
            <div
              className={cn('h-full', pct >= 85 ? 'bg-destructive' : 'bg-primary')}
              style={{ width: `${Math.round(pct)}%` }}
            />
          </div>
          <span className="text-muted-foreground text-[10px] tabular-nums">{Math.round(pct)}% gastado</span>
        </>
      )}
    </div>
  )
}