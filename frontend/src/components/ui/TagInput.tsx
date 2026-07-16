// Chip editor: chips with ×, Enter/comma commits, paste splits on commas and
// newlines. Used by every list-valued profile field (targets, skills, avoid
// lists) so editing feels identical everywhere.
import { useState, type KeyboardEvent, type ClipboardEvent } from 'react'
import { X } from '@phosphor-icons/react'

export default function TagInput({
  values,
  onChange,
  placeholder = 'Type and press Enter',
  accent = false,
}: {
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  /** signal-tinted chips (used for the primary Skills field). */
  accent?: boolean
}) {
  const [draft, setDraft] = useState('')

  const commit = (raw: string) => {
    const parts = raw
      .split(/[,\n]/)
      .map((p) => p.trim())
      .filter(Boolean)
    if (!parts.length) return
    const seen = new Set(values.map((v) => v.toLowerCase()))
    const additions = parts.filter((p) => !seen.has(p.toLowerCase()))
    if (additions.length) onChange([...values, ...additions])
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commit(draft)
    } else if (e.key === 'Backspace' && !draft && values.length) {
      onChange(values.slice(0, -1))
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text')
    if (text.includes(',') || text.includes('\n')) {
      e.preventDefault()
      commit(text)
    }
  }

  const chip = accent
    ? 'bg-signal-50 text-signal-800 border border-signal-100'
    : 'bg-slate-100 text-slate-700 border border-slate-200'

  return (
    <div>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1.5 focus-within:border-signal-400 focus-within:ring-2 focus-within:ring-signal-100">
        {values.map((v) => (
          <span key={v} className={`inline-flex items-center gap-1 rounded-[0.35rem] px-1.5 py-0.5 text-xs font-medium ${chip}`}>
            {v}
            <button
              type="button"
              aria-label={`Remove ${v}`}
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="rounded-sm text-current/60 hover:text-current"
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => commit(draft)}
          placeholder={values.length ? '' : placeholder}
          className="min-w-[8rem] flex-1 border-none bg-transparent py-0.5 text-sm text-ink outline-none placeholder:text-slate-400"
        />
      </div>
      <p className="mt-1 text-[0.68rem] text-slate-400">
        Tip: paste or type multiple items separated by commas
      </p>
    </div>
  )
}
