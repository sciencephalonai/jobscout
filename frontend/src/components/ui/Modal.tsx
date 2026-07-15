// Shared dialog shell (design-system version of the SettingsModal pattern):
// ink/50 blurred backdrop, rounded-xl paper surface, Escape to close,
// dialog role, sticky footer with Cancel / primary action.
import { useEffect, useRef, type ReactNode } from 'react'
import { X } from '@phosphor-icons/react'

export default function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
  tall = false,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  /** Right-aligned footer actions; omit for read-only dialogs. */
  footer?: ReactNode
  wide?: boolean
  /** Use a FIXED-height frame (content scrolls inside) instead of sizing to
   *  content — for content-heavy dialogs that would otherwise resize/feel unstable. */
  tall?: boolean
}) {
  const closeRef = useRef<HTMLButtonElement>(null)

  // Move focus into the dialog once, when it opens. Deliberately mount-only:
  // callers pass a fresh inline `onClose` each render, so folding this into the
  // onClose-dependent effect below re-focused the close button on every parent
  // re-render — which stole the caret out of text inputs after each keystroke.
  useEffect(() => {
    closeRef.current?.focus()
  }, [])

  // Escape-to-close. Safe to re-subscribe when `onClose` changes — no focus side effect.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-ink/50 p-2 backdrop-blur-[2px] sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className={`popover-enter flex w-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl ${tall ? 'h-[min(640px,82dvh)]' : 'max-h-[86dvh]'} ${wide ? 'max-w-3xl' : 'max-w-xl'}`}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold tracking-[-0.02em] text-ink">{title}</h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="control-focus rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-ink"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-4">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/60 px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
