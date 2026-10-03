import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { KeyRound, LogOut } from 'lucide-react'
import { toast } from 'sonner'
import { setToken } from '@/api/client'
import { useHasToken } from '@/hooks/useHasToken'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function TokenControl() {
  const qc = useQueryClient()
  const has = useHasToken()
  const [value, setValue] = useState('')

  if (has) {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800">
          <KeyRound className="size-3" /> token cargado
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setToken('')
            qc.clear()
            toast('Token borrado de esta pestaña')
          }}
        >
          <LogOut className="size-4" /> Salir
        </Button>
      </div>
    )
  }
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (!value.trim()) return
        setToken(value.trim())
        setValue('')
        qc.invalidateQueries()
        toast.success('Token guardado (solo en esta pestaña)')
      }}
    >
      <Input
        type="password"
        autoComplete="off"
        placeholder="Bearer token"
        aria-label="Bearer token"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-8 w-48"
      />
      <Button type="submit" size="sm">
        Guardar
      </Button>
    </form>
  )
}
