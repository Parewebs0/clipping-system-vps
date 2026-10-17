import { useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { useCreateSocialAccount, useUpdateSocialAccount } from '@/api/mutations'
import { useSocialAccounts } from '@/api/queries'
import type { SocialAccount, SocialAccountCreate } from '@/api/types'
import { PageHeader } from '@/components/common/PageHeader'
import { EmptyBlock, ErrorBlock, LoadingBlock } from '@/components/common/States'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fmtAgo } from '@/lib/format'

const PLATFORMS = ['youtube', 'instagram', 'tiktok', 'facebook'] as const
const STATUSES = ['healthy', 'expired', 'disabled'] as const
const AUTH_KINDS = ['oauth', 'browser_profile'] as const

const STATUS_LABEL: Record<(typeof STATUSES)[number], string> = {
  healthy: 'sana',
  expired: 'caducada',
  disabled: 'desactivada',
}
const AUTH_LABEL: Record<(typeof AUTH_KINDS)[number], string> = {
  oauth: 'OAuth',
  browser_profile: 'perfil de navegador',
}

const schema = z.object({
  platform: z.enum(PLATFORMS),
  account_key: z.string().regex(/^[a-z0-9_]{1,64}$/, 'Solo minúsculas, números y _ (1–64)'),
  handle: z.string().max(128, 'Máximo 128 caracteres'),
  status: z.enum(STATUSES),
  auth_kind: z.enum(AUTH_KINDS),
})
type Values = z.infer<typeof schema>

const EMPTY: Values = {
  platform: 'tiktok',
  account_key: 'default',
  handle: '',
  status: 'healthy',
  auth_kind: 'oauth',
}

function isPlatform(value: string): value is Values['platform'] {
  return (PLATFORMS as readonly string[]).includes(value)
}
function isStatus(value: string): value is Values['status'] {
  return (STATUSES as readonly string[]).includes(value)
}
function isAuthKind(value: string): value is Values['auth_kind'] {
  return (AUTH_KINDS as readonly string[]).includes(value)
}

function toCreate(v: Values): SocialAccountCreate {
  return {
    platform: v.platform,
    account_key: v.account_key,
    handle: v.handle.trim() ? v.handle.trim() : null,
    status: v.status,
    auth_kind: v.auth_kind,
  }
}

export function SocialAccountsPage() {
  const { data, error, isLoading } = useSocialAccounts()
  const create = useCreateSocialAccount()
  const update = useUpdateSocialAccount()
  const [editing, setEditing] = useState<{ platform: string; accountKey: string } | null>(null)
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: EMPTY })
  const pending = create.isPending || update.isPending

  function resetForm() {
    setEditing(null)
    form.reset(EMPTY)
  }

  function startEdit(row: SocialAccount) {
    if (!isPlatform(row.platform)) return
    setEditing({ platform: row.platform, accountKey: row.account_key })
    form.reset({
      platform: row.platform,
      account_key: row.account_key,
      handle: row.handle ?? '',
      status: isStatus(row.status) ? row.status : 'healthy',
      auth_kind: isAuthKind(row.auth_kind) ? row.auth_kind : 'oauth',
    })
  }

  const onSubmit = form.handleSubmit(async (v) => {
    try {
      if (editing) {
        await update.mutateAsync({
          platform: editing.platform,
          accountKey: editing.accountKey,
          body: { handle: v.handle.trim() ? v.handle.trim() : null, status: v.status, auth_kind: v.auth_kind },
        })
        toast.success(`Cuenta ${editing.platform}/${editing.accountKey} actualizada`)
      } else {
        await create.mutateAsync(toCreate(v))
        toast.success(`Cuenta ${v.platform}/${v.account_key} creada`)
      }
      resetForm()
    } catch (e) {
      toast.error(editing ? 'No se pudo guardar' : 'No se pudo crear', {
        description: e instanceof Error ? e.message : String(e),
      })
    }
  })

  return (
    <>
      <PageHeader
        eyebrow="Publicación"
        title="Cuentas"
        description="Inventario para la cola. Solo entran las que están healthy. Aquí no se guardan tokens."
      />
      {isLoading && <LoadingBlock rows={4} />}
      {error && <ErrorBlock error={error} />}
      {data && (
        <div className="space-y-6">
          {data.length === 0 ? (
            <EmptyBlock>No hay cuentas. Da de alta al menos tiktok/default e instagram/default.</EmptyBlock>
          ) : (
            <Card className="py-0">
              <CardContent className="px-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">Plataforma</TableHead>
                      <TableHead>Clave</TableHead>
                      <TableHead>Handle</TableHead>
                      <TableHead>Estado</TableHead>
                      <TableHead>Auth</TableHead>
                      <TableHead>Actualizada</TableHead>
                      <TableHead className="pr-4" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((row) => {
                      const selected = editing?.platform === row.platform && editing.accountKey === row.account_key
                      return (
                        <TableRow key={`${row.platform}/${row.account_key}`} data-state={selected ? 'selected' : undefined}>
                          <TableCell className="pl-4 font-mono text-xs">{row.platform}</TableCell>
                          <TableCell className="font-mono text-xs">{row.account_key}</TableCell>
                          <TableCell>{row.handle || '—'}</TableCell>
                          <TableCell>{isStatus(row.status) ? STATUS_LABEL[row.status] : row.status}</TableCell>
                          <TableCell>{isAuthKind(row.auth_kind) ? AUTH_LABEL[row.auth_kind] : row.auth_kind}</TableCell>
                          <TableCell className="text-muted-foreground text-sm">{fmtAgo(row.updated_at)}</TableCell>
                          <TableCell className="pr-4 text-right">
                            <Button type="button" size="sm" variant="outline" onClick={() => startEdit(row)}>
                              Editar
                            </Button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>{editing ? `Editar ${editing.platform}/${editing.accountKey}` : 'Nueva cuenta'}</CardTitle>
              <CardDescription>
                La clave identifica las credenciales en el worker. Un segundo alta de la misma plataforma y clave responde 409.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Form {...form}>
                <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
                  <FormField
                    control={form.control}
                    name="platform"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Plataforma</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange} disabled={editing !== null || pending}>
                          <FormControl>
                            <SelectTrigger className="w-full" aria-label="Plataforma">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {PLATFORMS.map((platform) => (
                              <SelectItem key={platform} value={platform}>
                                {platform}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="account_key"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Clave</FormLabel>
                        <FormControl>
                          <Input {...field} disabled={editing !== null || pending} autoComplete="off" spellCheck={false} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="handle"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Handle</FormLabel>
                        <FormControl>
                          <Input {...field} placeholder="@cuenta" disabled={pending} autoComplete="off" />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="status"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Estado</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange} disabled={pending}>
                          <FormControl>
                            <SelectTrigger className="w-full" aria-label="Estado">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {STATUSES.map((status) => (
                              <SelectItem key={status} value={status}>
                                {STATUS_LABEL[status]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="auth_kind"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Tipo de auth</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange} disabled={pending}>
                          <FormControl>
                            <SelectTrigger className="w-full" aria-label="Tipo de auth">
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {AUTH_KINDS.map((kind) => (
                              <SelectItem key={kind} value={kind}>
                                {AUTH_LABEL[kind]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <div className="flex items-end gap-2">
                    <Button type="submit" disabled={pending}>
                      {pending ? 'Guardando…' : editing ? 'Guardar' : 'Crear'}
                    </Button>
                    {editing && (
                      <Button type="button" variant="outline" onClick={resetForm} disabled={pending}>
                        Cancelar
                      </Button>
                    )}
                  </div>
                </form>
              </Form>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
