import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export function StatCard({ label, value, hint, icon, className }: { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ReactNode; className?: string }) {
  return (
    <Card className={cn('gap-2 py-4', className)}>
      <CardContent className="px-4">
        <div className="text-muted-foreground flex items-center justify-between text-xs font-medium">
          <span>{label}</span>
          {icon}
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="text-muted-foreground mt-1 text-xs">{hint}</div>}
      </CardContent>
    </Card>
  )
}
