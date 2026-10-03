import { useEffect, useState } from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import { Pencil } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { useUpdateCampaign } from '@/api/mutations'
import type { CampaignDetailCampaign } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { buildPatch, campaignFormSchema, formDefaults, FORMATS, type CampaignFormValues } from '@/lib/campaignForm'

const NONE = '__none'

export function EditCampaignSheet({ campaign }: { campaign: CampaignDetailCampaign }) {
  const [open, setOpen] = useState(false)
  const update = useUpdateCampaign(campaign.id)
  const form = useForm<CampaignFormValues>({
    resolver: zodResolver(campaignFormSchema),
    defaultValues: formDefaults(campaign),
  })
  const sourceLocked = campaign.source_provider !== 'manual'

  useEffect(() => {
    if (open) form.reset(formDefaults(campaign))
  }, [open, campaign, form])

  const onSubmit = form.handleSubmit(async (values) => {
    const patch = buildPatch(campaign, values)
    if (!Object.keys(patch).length) {
      toast.info('No hay cambios que guardar')
      return
    }
    try {
      await update.mutateAsync(patch)
      toast.success('Campaña actualizada', { description: Object.keys(patch).join(', ') })
      setOpen(false)
    } catch (e) {
      toast.error('No se pudo guardar', { description: e instanceof Error ? e.message : String(e) })
    }
  })

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="sm">
          <Pencil className="size-4" /> Editar
        </Button>
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Editar campaña #{campaign.id}</SheetTitle>
          <SheetDescription>Solo se envían los campos modificados (PATCH). Los datos del scorer en spec.extra se conservan.</SheetDescription>
        </SheetHeader>
        <Form {...form}>
          <form onSubmit={onSubmit} className="space-y-5 px-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nombre</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="source_url"
                render={({ field }) => (
                  <FormItem className="sm:col-span-2">
                    <FormLabel>URL de origen</FormLabel>
                    <FormControl>
                      <Input {...field} disabled={sourceLocked} placeholder="https://…" />
                    </FormControl>
                    {sourceLocked && <FormDescription>No editable en campañas {campaign.source_provider}: es la clave de deduplicación de discovery.</FormDescription>}
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="source_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Source ID</FormLabel>
                    <FormControl>
                      <Input {...field} disabled={sourceLocked} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <div className="space-y-2">
                <div className="text-sm font-medium">Proveedor</div>
                <Input value={campaign.source_provider} disabled aria-label="Proveedor" />
              </div>
            </div>
            <FormField
              control={form.control}
              name="source_instructions"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Instrucciones</FormLabel>
                  <FormControl>
                    <Textarea rows={4} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Separator />
            <div className="text-sm font-semibold">Spec</div>
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="duration_min"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Duración mínima (s)</FormLabel>
                    <FormControl>
                      <Input inputMode="decimal" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="duration_max"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Duración máxima (s)</FormLabel>
                    <FormControl>
                      <Input inputMode="decimal" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="format"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Formato</FormLabel>
                    <Select value={field.value || NONE} onValueChange={(v) => field.onChange(v === NONE ? '' : v)}>
                      <FormControl>
                        <SelectTrigger className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={NONE}>— sin definir —</SelectItem>
                        {[...new Set([...FORMATS, ...(field.value ? [field.value] : [])])].map((f) => (
                          <SelectItem key={f} value={f}>
                            {f}
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
                name="language"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Idioma</FormLabel>
                    <FormControl>
                      <Input placeholder="es, en…" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="captions_required"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between rounded-lg border p-3">
                  <div>
                    <FormLabel>Subtítulos obligatorios</FormLabel>
                    <FormDescription>captions_required</FormDescription>
                  </div>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="watermark_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Watermark URL</FormLabel>
                  <FormControl>
                    <Input placeholder="https://…" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="keywords"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Keywords</FormLabel>
                  <FormControl>
                    <Input placeholder="separadas por comas" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="exclude_keywords"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Excluir keywords</FormLabel>
                  <FormControl>
                    <Input placeholder="separadas por comas" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <SheetFooter className="px-0">
              <Button type="submit" disabled={update.isPending}>
                {update.isPending ? 'Guardando…' : 'Guardar cambios'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  )
}
