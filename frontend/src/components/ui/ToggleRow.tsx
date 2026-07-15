// Labeled switch row for boolean profile fields (signal accent, description line).
export default function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description?: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3 rounded-lg px-2 py-2 hover:bg-slate-50">
      <span>
        <span className="block text-sm font-medium text-ink">{label}</span>
        {description && <span className="mt-0.5 block text-xs text-slate-400">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`control-focus relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? 'bg-signal-500' : 'bg-slate-300'}`}
      >
        <span
          // left-0 anchors the knob: without it, an absolute element keeps its
          // STATIC (button-centered) position and translate-x pushes it past the
          // track's right edge — the "orange pill + detached knob" bug.
          className={`absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-[1.15rem]' : 'translate-x-0.5'}`}
        />
      </button>
    </label>
  )
}
