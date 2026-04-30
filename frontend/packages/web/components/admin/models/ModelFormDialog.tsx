'use client'

import { useState, useEffect } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { Model, ModelCreate, ModelUpdate } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion'
import { cn } from '@/lib/utils'

const MODALITIES = ['text', 'image', 'audio', 'video'] as const

interface ModelFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  model: Model | null // null = create, non-null = edit
  onSave: (body: ModelCreate | ModelUpdate) => Promise<void>
}

export function ModelFormDialog({ open, onOpenChange, model, onSave }: ModelFormDialogProps) {
  const isEdit = model !== null
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Form fields
  const [modelId, setModelId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [reasoning, setReasoning] = useState(false)
  const [inputModalities, setInputModalities] = useState<string[]>([])
  const [costInput, setCostInput] = useState('')
  const [costOutput, setCostOutput] = useState('')
  const [costCacheRead, setCostCacheRead] = useState('')
  const [costCacheWrite, setCostCacheWrite] = useState('')
  const [contextWindow, setContextWindow] = useState('')
  const [maxTokens, setMaxTokens] = useState('')

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (open) {
      if (model) {
        setModelId(model.model_id)
        setDisplayName(model.display_name)
        setReasoning(model.reasoning)
        setInputModalities([...model.input_modalities])
        setCostInput(model.cost_input.toString())
        setCostOutput(model.cost_output.toString())
        setCostCacheRead(model.cost_cache_read.toString())
        setCostCacheWrite(model.cost_cache_write.toString())
        setContextWindow(model.context_window.toString())
        setMaxTokens(model.max_tokens.toString())
      } else {
        setModelId('')
        setDisplayName('')
        setReasoning(false)
        setInputModalities(['text'])
        setCostInput('')
        setCostOutput('')
        setCostCacheRead('')
        setCostCacheWrite('')
        setContextWindow('')
        setMaxTokens('')
      }
      setError(null)
      setSaving(false)
    }
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [open, model])

  function reset(): void {
    setModelId('')
    setDisplayName('')
    setReasoning(false)
    setInputModalities(['text'])
    setCostInput('')
    setCostOutput('')
    setCostCacheRead('')
    setCostCacheWrite('')
    setContextWindow('')
    setMaxTokens('')
    setError(null)
    setSaving(false)
  }

  function handleOpenChange(next: boolean): void {
    if (!next) reset()
    onOpenChange(next)
  }

  function toggleModality(mod: string) {
    setInputModalities((prev) =>
      prev.includes(mod) ? prev.filter((m) => m !== mod) : [...prev, mod],
    )
  }

  function parseNumber(val: string): number {
    const n = parseFloat(val)
    return isNaN(n) ? 0 : n
  }

  async function handleSave(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      if (isEdit) {
        const body: ModelUpdate = {
          display_name: displayName || null,
          reasoning: reasoning || null,
          input_modalities: inputModalities.length > 0 ? inputModalities : null,
          cost_input: costInput ? parseNumber(costInput) : null,
          cost_output: costOutput ? parseNumber(costOutput) : null,
          cost_cache_read: costCacheRead ? parseNumber(costCacheRead) : null,
          cost_cache_write: costCacheWrite ? parseNumber(costCacheWrite) : null,
          context_window: contextWindow ? parseNumber(contextWindow) : null,
          max_tokens: maxTokens ? parseNumber(maxTokens) : null,
        }
        await onSave(body)
      } else {
        const body: ModelCreate = {
          model_id: modelId,
          display_name: displayName,
          reasoning,
          input_modalities: inputModalities,
          cost_input: parseNumber(costInput),
          cost_output: parseNumber(costOutput),
          cost_cache_read: parseNumber(costCacheRead),
          cost_cache_write: parseNumber(costCacheWrite),
          context_window: parseNumber(contextWindow),
          max_tokens: parseNumber(maxTokens),
        }
        await onSave(body)
      }
      handleOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="model-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">
                {isEdit ? '编辑模型' : '添加模型'}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                {isEdit ? '修改模型配置' : '为当前 provider 添加新模型'}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <div className="mt-4 flex flex-col gap-3">
            {/* Model ID */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="model-id">Model ID</Label>
              <Input
                id="model-id"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                placeholder="gpt-4o"
                disabled={isEdit}
              />
              {isEdit && <span className="text-[11px] text-muted-foreground">创建后不可修改</span>}
            </div>

            {/* Display Name */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="model-display-name">显示名称</Label>
              <Input
                id="model-display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="GPT-4o"
              />
            </div>

            {/* Reasoning */}
            <div className="flex items-center gap-3">
              <Switch
                id="model-reasoning"
                checked={reasoning}
                onCheckedChange={(c: boolean) => setReasoning(c)}
              />
              <Label htmlFor="model-reasoning" className="cursor-pointer">
                思考能力 (Reasoning)
              </Label>
            </div>

            {/* Input Modalities */}
            <div className="flex flex-col gap-1.5">
              <Label>支持模态</Label>
              <div className="flex flex-wrap gap-3">
                {MODALITIES.map((mod) => (
                  <label key={mod} className="flex items-center gap-2 text-sm cursor-pointer">
                    <Checkbox
                      checked={inputModalities.includes(mod)}
                      onCheckedChange={() => toggleModality(mod)}
                    />
                    <span>{mod}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Costs & Limits */}
            <Accordion className="mt-1">
              <AccordionItem value="costs">
                <AccordionTrigger className="text-xs text-muted-foreground">
                  费用配置
                </AccordionTrigger>
                <AccordionContent>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-cost-input" className="text-xs">
                        Input (per 1M)
                      </Label>
                      <Input
                        id="model-cost-input"
                        type="number"
                        step="any"
                        value={costInput}
                        onChange={(e) => setCostInput(e.target.value)}
                        placeholder="0"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-cost-output" className="text-xs">
                        Output (per 1M)
                      </Label>
                      <Input
                        id="model-cost-output"
                        type="number"
                        step="any"
                        value={costOutput}
                        onChange={(e) => setCostOutput(e.target.value)}
                        placeholder="0"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-cost-cache-read" className="text-xs">
                        Cache Read (per 1M)
                      </Label>
                      <Input
                        id="model-cost-cache-read"
                        type="number"
                        step="any"
                        value={costCacheRead}
                        onChange={(e) => setCostCacheRead(e.target.value)}
                        placeholder="0"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-cost-cache-write" className="text-xs">
                        Cache Write (per 1M)
                      </Label>
                      <Input
                        id="model-cost-cache-write"
                        type="number"
                        step="any"
                        value={costCacheWrite}
                        onChange={(e) => setCostCacheWrite(e.target.value)}
                        placeholder="0"
                      />
                    </div>
                  </div>
                </AccordionContent>
              </AccordionItem>
              <AccordionItem value="limits">
                <AccordionTrigger className="text-xs text-muted-foreground">
                  上下文限制
                </AccordionTrigger>
                <AccordionContent>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-context-window" className="text-xs">
                        Context Window
                      </Label>
                      <Input
                        id="model-context-window"
                        type="number"
                        step="1"
                        value={contextWindow}
                        onChange={(e) => setContextWindow(e.target.value)}
                        placeholder="128000"
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="model-max-tokens" className="text-xs">
                        Max Tokens
                      </Label>
                      <Input
                        id="model-max-tokens"
                        type="number"
                        step="1"
                        value={maxTokens}
                        onChange={(e) => setMaxTokens(e.target.value)}
                        placeholder="4096"
                      />
                    </div>
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                {error}
              </div>
            )}
          </div>

          <div className="mt-4 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={saving}>
                  取消
                </Button>
              }
            />
            <Button
              type="button"
              size="sm"
              onClick={() => void handleSave()}
              disabled={saving || !modelId}
            >
              {saving ? '保存中...' : '保存'}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
