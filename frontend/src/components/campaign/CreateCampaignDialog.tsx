import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { zodResolver } from '@hookform/resolvers/zod'
import { Plus } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { useCreateCampaign } from '@/api/mutations'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const schema = z.object({
  name: z.string().trim().min(1, 'Obligatorio').max(256),
  source_url: z
    .string()
    .trim()
    .max(2048)
    .refine((v) => v === '' || /^https?:\/\//.test(v), 'Debe empezar por http:// o https://'),
  source_instructions: z.string().max(20000),
})
type Values = z.infer<typeof schema>

/** Manual campaigns only (source_provider=manual): download_enqueue_tick skips them. */
export function CreateCampaignDialog() {
  const [open, setOpen] = useState(false)
  const create = useCreateCampaign()
  const navigate = useNavigate()
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { name: '', source_url: '', source_instructions: '' } })
  const onSubmit = form.handleSubmit(async (v) => {
    try {
      const c = await create.mutateAsync({
        name: v.name,
        source_provider: 'manual',
        source_url: v.source_url || null,
        source_instructions: v.source_instructions || null,
      })
      toast.success(`Campaña #${c.id} creada`)
      setOpen(false)
      form.reset()
      navigate(`/campaigns/${c.id}`)
    } catch (e) {
      toast.error('No se pudo crear', { description: e instanceof Error ? e.message : String(e) })
    }
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="size-4" /> Nueva campaña manual
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nueva campaña manual</DialogTitle>
          <DialogDescription>
            Se crea en «Descubierta» con source_provider=manual. Sin payload de Whop, el brief-reader la pasará a «Fallo brief» (no_materials) en su
            próximo tick, sin llamar al LLM.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
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
            <FormField
              control={form.control}
              name="source_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>URL (opcional)</FormLabel>
                  <FormControl>
                    <Input placeholder="https://…" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="source_instructions"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Instrucciones (opcional)</FormLabel>
                  <FormControl>
                    <Textarea rows={3} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? 'Creando…' : 'Crear'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
