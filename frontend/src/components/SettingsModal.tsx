// Settings dialog: scheduler, source toggles, backups, storage status.
import { useEffect, useRef, useState } from 'react'
import { X } from '@phosphor-icons/react'
import { useSettings, useSourcesStatus, useUpdateSettings } from '../api/client'

interface Props {
  onClose: () => void
}

const MODES: { value: 'both' | 'cloud' | 'local'; label: string; hint: string }[] = [
  { value: 'both', label: 'Both', hint: 'Save to local + cloud (cloud best-effort) so data remains portable' },
  { value: 'cloud', label: 'Cloud', hint: 'Cloud primary; auto-falls back to local if unreachable' },
  { value: 'local', label: 'Local', hint: 'Local Docker Weaviate only' },
]

export default function SettingsModal({ onClose }: Props) {
  const { data, isLoading } = useSettings()
  const { data: sources } = useSourcesStatus()
  const update = useUpdateSettings()

  const [mode, setMode] = useState<'both' | 'cloud' | 'local' | ''>('')
  const [provider, setProvider] = useState<'deepseek' | 'nvidia' | ''>('')
  const [keys, setKeys] = useState<Record<string, string>>({})
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', closeOnEscape)
      document.body.style.overflow = ''
      previous?.focus()
    }
  }, [onClose])

  const effMode = (mode || data?.storage_mode || 'both') as 'both' | 'cloud' | 'local'
  const effProvider = (provider || data?.llm?.provider || 'deepseek') as 'deepseek' | 'nvidia'
  const modelField = effProvider === 'nvidia' ? 'nvidia_model' : 'deepseek_model'
  const modelValue = keys[modelField] ?? (effProvider === 'nvidia' ? 'z-ai/glm-5.2' : data?.llm?.model ?? 'deepseek-chat')
  const k = (name: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setKeys((s) => ({ ...s, [name]: e.target.value }))

  const apply = () => {
    const body: Record<string, string> = { storage_mode: effMode, llm_provider: effProvider }
    for (const [field, val] of Object.entries(keys)) if (val.trim()) body[field] = val.trim()
    update.mutate(body, { onSuccess: () => setKeys({}) })
  }

  const present = (b?: boolean) => (b ? 'set' : 'not set')

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/50 p-2 backdrop-blur-[2px] sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className="my-auto flex max-h-[calc(100dvh-1rem)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl sm:max-h-[calc(100dvh-2rem)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-none items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <h2 id="settings-title" className="text-base font-semibold tracking-[-0.025em] text-ink">Data &amp; backend</h2>
            <p className="mt-0.5 text-[0.68rem] text-slate-400">Storage, source health, and AI provider configuration.</p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} className="control-focus flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-ink" aria-label="Close settings"><X size={17} /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin">
        {isLoading ? (
          <p className="mt-4 text-sm text-slate-500">Loading…</p>
        ) : (
          <div className="mt-4 space-y-4">
            {/* Storage mode */}
            <div>
              <p className="text-sm font-medium text-slate-700">Vector store (where jobs are saved)</p>
              <div className="mt-2 flex gap-2">
                {MODES.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    title={m.hint}
                    aria-pressed={effMode === m.value}
                    onClick={() => setMode(m.value)}
                    className={`control-focus h-8 flex-1 rounded-lg border px-3 text-xs font-medium ${
                      effMode === m.value
                        ? 'border-signal-400 bg-signal-50 text-signal-800'
                        : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-xs text-slate-500">{MODES.find((m) => m.value === effMode)?.hint}</p>
            </div>

            {/* Active backend */}
            <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              Active now: <strong>{data?.backend.primary}</strong>
              {data?.backend.dual_write ? ' + cloud mirror' : ''} ·{' '}
              Keys: Google {present(data?.keys_present.google)}, DeepSeek {present(data?.keys_present.deepseek)}, NVIDIA {present(data?.keys_present.nvidia)},
              Weaviate-cloud {present(data?.keys_present.weaviate_cloud)}
            </div>

            <div>
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-sm font-medium text-slate-700">Source health</p>
                <p className="text-xs text-slate-400">last completed run</p>
              </div>
              {(sources ?? []).length === 0 ? (
                <p className="mt-2 text-xs text-slate-500">Run a refresh to populate source health.</p>
              ) : (
                <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-slate-200">
                  {(sources ?? []).map((source) => (
                    <div key={source.source} className="grid grid-cols-[1fr_auto] gap-x-3 border-b border-slate-100 px-3 py-2 text-xs last:border-0">
                      <span className="font-semibold capitalize text-slate-700">{source.source}</span>
                      <span className={source.last_run_status === 'failed' ? 'text-rose-600' : 'text-emerald-700'}>{source.last_run_status ?? 'not run'}</span>
                      <span className="col-span-2 mt-0.5 text-slate-500">
                        seen {source.last_seen ?? 0} · saved {source.last_ingested ?? 0} · filtered {source.last_filtered ?? 0} · failed {source.last_failed ?? 0}
                        {(source.last_closed ?? 0) > 0 ? ` · closed ${source.last_closed}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-lg border border-slate-200 bg-[#f7f8f6] p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-800">AI provider for parsing &amp; matching</p>
                  <p className="mt-0.5 text-xs text-slate-500">One provider is active at a time. NVIDIA uses its OpenAI-compatible API, so no separate LangChain package is needed.</p>
                </div>
                <select value={effProvider} onChange={(event) => setProvider(event.target.value as 'deepseek' | 'nvidia')}
                  aria-label="AI provider"
                  className="control-focus h-8 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 outline-none">
                  <option value="deepseek">DeepSeek</option>
                  <option value="nvidia">NVIDIA NIM</option>
                </select>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-[0.68rem] font-medium text-slate-600">API key
                <input type="password" autoComplete="new-password" placeholder="Leave blank to keep current"
                  value={keys[effProvider === 'nvidia' ? 'nvidia_api_key' : 'deepseek_api_key'] ?? ''}
                  onChange={k(effProvider === 'nvidia' ? 'nvidia_api_key' : 'deepseek_api_key')}
                  className="control-focus mt-1 h-8 w-full rounded-lg border border-slate-300 bg-white px-2.5 text-xs outline-none" /></label>
                <label className="text-[0.68rem] font-medium text-slate-600">Model
                <input list={effProvider === 'nvidia' ? 'nvidia-models' : undefined} value={modelValue}
                  onChange={k(modelField)} placeholder="Model identifier"
                  className="control-focus mt-1 h-8 w-full rounded-lg border border-slate-300 bg-white px-2.5 text-xs outline-none" /></label>
                {effProvider === 'nvidia' && <datalist id="nvidia-models"><option value="z-ai/glm-5.2">GLM-5.2</option></datalist>}
              </div>
              <p className="mt-2 font-mono text-[0.64rem] text-signal-700">Active: {data?.llm?.provider ?? 'DeepSeek'} · {data?.llm?.model ?? 'deepseek-chat'} · {data?.llm?.configured ? 'key configured' : 'key not configured'}</p>
            </div>

            {/* Keys (only sent if filled; stored server-side in .env) */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-slate-700">Storage and embedding keys</p>
              <p className="text-[0.68rem] text-slate-400">Optional. Leave blank to keep the current value.</p>
              {[
                ['google_api_key', 'Google API key (embeddings)'],
                ['weaviate_cluster_url', 'Weaviate Cloud URL'],
                ['weaviate_api_key', 'Weaviate Cloud API key'],
              ].map(([field, label]) => (
                <input
                  key={field}
                  type={field === 'weaviate_cluster_url' ? 'text' : 'password'}
                  placeholder={label}
                  value={keys[field] ?? ''}
                  onChange={k(field)}
                  aria-label={label}
                  autoComplete="new-password"
                  className="control-focus h-8 w-full rounded-lg border border-slate-300 px-2.5 text-xs outline-none"
                />
              ))}
            </div>

            <p className="text-xs text-slate-500">
              Your company list, profiles &amp; saved searches live locally (DuckDB) and are never affected by
              switching. With <strong>Both</strong>, every job is saved to local and cloud, so switching never
              loses data. Keys are stored on your machine (.env), never in the browser.
            </p>

            {update.isError && (
              <p className="text-sm text-red-600">Failed to apply: {update.error.message}</p>
            )}

            {update.isSuccess && <p className="text-xs text-emerald-700" aria-live="polite">Settings saved.</p>}

            <div className="sticky bottom-0 -mx-4 flex justify-end gap-2 border-t border-slate-200 bg-white/95 px-4 pt-3 backdrop-blur">
              <button type="button" onClick={onClose} className="control-focus h-8 rounded-lg px-3 text-xs font-medium text-slate-600 hover:bg-slate-100">
                Cancel
              </button>
              <button
                type="button"
                onClick={apply}
                disabled={update.isPending}
                className="control-focus h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white hover:bg-ink-soft disabled:opacity-50"
              >
                {update.isPending ? 'Applying…' : 'Apply'}
              </button>
            </div>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
