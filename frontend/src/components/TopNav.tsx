// Workspace shell chrome: nav tabs, profile switcher, ingest/refresh actions, automation panel, settings.
import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  ArrowClockwise,
  ArrowUpRight,
  BookmarkSimple,
  Buildings,
  CaretDown,
  ClipboardText,
  GearSix,
  Question,
  List,
  MagnifyingGlass,
  Plus,
  ShieldCheck,
  Sparkle,
  UserCircle,
  X,
} from '@phosphor-icons/react'
import { useActiveProfile } from '../ProfileContext'
import {
  useProfiles,
  useTriggerIngestion,
  useRefreshCompanies,
  useScheduler,
  useSetScheduler,
  useSourceOverrides,
  useSetSourceOverride,
  useSavedSearches,
  useSourcesStatus,
  useStats,
  useMe,
} from '../api/client'
import AddToWatchlistModal from './AddToWatchlistModal'
import SettingsModal from './SettingsModal'
import HelpModal from './HelpModal'
import { UserMenu } from '../auth/UserMenu'

// The primary action does two different things depending on whether a profile is
// active, and the label alone never made that obvious. Both variants are spelled
// out here so the sidebar, the mobile drawer and HelpModal say the same thing.
const PRIMARY_HELP = {
  generic: 'Searches every source with generic tech keywords. Select a profile to target your roles instead.',
  matched: 'Searches with your target roles and keeps only the roles that pass your eligibility gates.',
}
const WATCHLIST_HELP = 'Re-checks only the companies you watch — fast and cheap.'

type NavIconName = 'foryou' | 'discover' | 'tracker' | 'saved' | 'companies' | 'profile' | 'admin'

const NAV_ITEMS: { to: string; label: string; icon: NavIconName }[] = [
  { to: '/for-you', label: 'For You', icon: 'foryou' },
  { to: '/', label: 'Discover', icon: 'discover' },
  { to: '/my-jobs', label: 'Tracker', icon: 'tracker' },
  { to: '/saved', label: 'Saved searches', icon: 'saved' },
  { to: '/companies', label: 'Companies', icon: 'companies' },
  { to: '/profile', label: 'Profile', icon: 'profile' },
]

function Glyph({ name, active = false }: { name: NavIconName; active?: boolean }) {
  const props = { size: 18, weight: active ? 'fill' as const : 'regular' as const }
  if (name === 'foryou') return <Sparkle {...props} />
  if (name === 'discover') return <MagnifyingGlass {...props} />
  if (name === 'tracker') return <ClipboardText {...props} />
  if (name === 'saved') return <BookmarkSimple {...props} />
  if (name === 'companies') return <Buildings {...props} />
  if (name === 'admin') return <ShieldCheck {...props} />
  return <UserCircle {...props} />
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className="control-focus flex min-w-0 items-center gap-2.5 rounded-lg" aria-label="JobScout home">
      <span className={`relative flex shrink-0 items-center justify-center rounded-lg border border-white/15 bg-white text-[0.68rem] font-extrabold tracking-[-0.08em] text-ink ${compact ? 'h-7 w-7' : 'h-9 w-9'}`}>
        JS
        <span className="absolute -right-px -top-px h-2.5 w-2.5 rounded-bl-md rounded-tr-lg bg-signal-500" aria-hidden="true" />
      </span>
      <span className={`${compact ? 'text-sm' : 'text-[1.02rem]'} truncate font-semibold tracking-[-0.035em] text-white`}>JobScout</span>
    </Link>
  )
}

function WorkspaceNav({ onNavigate, light = false }: { onNavigate?: () => void; light?: boolean }) {
  const { pathname } = useLocation()
  const { data: saved } = useSavedSearches()
  const { data: me } = useMe()
  const totalNew = (saved ?? []).reduce((total, item) => total + (item.new_count || 0), 0)
  // The operator console is only linked for admins (route is API-guarded regardless).
  const items = me?.is_admin
    ? [...NAV_ITEMS, { to: '/admin', label: 'Admin', icon: 'admin' as NavIconName }]
    : NAV_ITEMS

  return (
    <nav className="space-y-0.5" aria-label="Workspace">
      {items.map((item) => {
        const active = item.to === '/' ? pathname === '/' || pathname === '/jobs' : pathname.startsWith(item.to)
        return (
          <Link
            key={item.to}
            to={item.to}
            onClick={onNavigate}
            aria-current={active ? 'page' : undefined}
            className={`control-focus group relative flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 text-[0.8125rem] font-medium ${
              active
                ? light ? 'bg-ink text-white' : 'bg-white/[0.09] text-white'
                : light ? 'text-slate-600 hover:bg-slate-100 hover:text-ink' : 'text-white/55 hover:bg-white/[0.055] hover:text-white'
            }`}
          >
            {active && !light && <span className="absolute -left-4 h-5 w-0.5 rounded-r bg-signal-500" aria-hidden="true" />}
            <Glyph name={item.icon} active={active} />
            <span>{item.label}</span>
            {item.to === '/saved' && totalNew > 0 && (
              <span className={`ml-auto rounded-[0.35rem] px-1.5 py-0.5 font-mono text-[0.61rem] font-semibold ${active ? 'bg-white/15 text-white' : 'bg-signal-600 text-white'}`}>
                {totalNew > 99 ? '99+' : totalNew}
              </span>
            )}
          </Link>
        )
      })}
    </nav>
  )
}

function AutomationPanel({ dark = true }: { dark?: boolean }) {
  const [open, setOpen] = useState(false)
  const { data: scheduler } = useScheduler()
  const setScheduler = useSetScheduler()
  const { data: overrides } = useSourceOverrides()
  const setOverride = useSetSourceOverride()
  const jobSpyOn = !!overrides?.jobspy

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={`control-focus flex h-8 w-full items-center justify-between rounded-lg px-2 text-xs font-medium ${dark ? 'text-white/55 hover:bg-white/[0.055] hover:text-white' : 'text-slate-500 hover:bg-slate-100 hover:text-ink'}`}
      >
        <span>Automation</span>
        <CaretDown size={14} className={open ? 'rotate-180' : ''} />
      </button>
      {open && (
        <div className={`popover-enter absolute bottom-full left-0 right-0 z-30 mb-1.5 overflow-hidden rounded-lg border p-2 shadow-xl ${dark ? 'border-white/10 bg-[#242b32] text-white' : 'border-slate-200 bg-white text-slate-700'}`}>
          <label className={`flex cursor-pointer items-start justify-between gap-3 rounded-md px-2 py-2 text-xs ${dark ? 'hover:bg-white/[0.055]' : 'hover:bg-slate-50'}`}>
            <span>
              <strong className="block font-medium">Daily refresh</strong>
              <span className={dark ? 'text-white/45' : 'text-slate-400'}>{scheduler?.enabled ? 'Keeps the feed fresh daily' : 'Off — keeps the feed fresh; uses embedding quota'}</span>
            </span>
            <input type="checkbox" checked={!!scheduler?.enabled} onChange={() => setScheduler.mutate(!scheduler?.enabled)} className="mt-0.5 h-4 w-4 accent-signal-500" />
          </label>
          <label className={`flex cursor-pointer items-start justify-between gap-3 rounded-md px-2 py-2 text-xs ${dark ? 'hover:bg-white/[0.055]' : 'hover:bg-slate-50'}`}>
            <span>
              <strong className="block font-medium">JobSpy scrape</strong>
              <span className={dark ? 'text-white/45' : 'text-slate-400'}>Indeed and Glassdoor, opt-in</span>
            </span>
            <input type="checkbox" checked={jobSpyOn} onChange={() => setOverride.mutate({ jobspy: !jobSpyOn })} className="mt-0.5 h-4 w-4 accent-signal-500" />
          </label>
        </div>
      )}
    </div>
  )
}

function Sidebar() {
  const { activeProfileId, setActiveProfileId } = useActiveProfile()
  const { data: profiles } = useProfiles()
  const ingest = useTriggerIngestion()
  const refreshCompanies = useRefreshCompanies()
  const queryClient = useQueryClient()
  const { data: stats } = useStats()
  const { data: sourceStatus } = useSourcesStatus()
  const [showAddModal, setShowAddModal] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showHelp, setShowHelp] = useState(false)

  const active = profiles?.find((profile) => profile.id === activeProfileId)
  const isBusy = ingest.isPending || refreshCompanies.isPending
  const checkedSourceCount = (sourceStatus ?? []).filter((source) => !!source.last_run_at).length

  const refreshJobQueries = () => {
    for (const delay of [10_000, 30_000, 60_000]) {
      window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ['jobs'] }), delay)
    }
    queryClient.invalidateQueries({ queryKey: ['stats'] })
    queryClient.invalidateQueries({ queryKey: ['sources', 'status'] })
  }

  const getLatest = () => {
    const keywords = active?.target_titles?.length ? active.target_titles : ['software engineer', 'data engineer', 'data scientist']
    ingest.mutate({ keywords, results_wanted: 250, profile_id: activeProfileId ?? undefined }, { onSuccess: refreshJobQueries })
  }

  const refreshWatchlist = () => refreshCompanies.mutate({ keywords: [] }, { onSuccess: refreshJobQueries })

  return (
    <aside className="hidden h-dvh w-[15.75rem] shrink-0 flex-col overflow-hidden border-r border-black/20 bg-ink px-3.5 py-3.5 xl:flex">
      <div className="px-1 pb-3"><Brand /></div>
      <WorkspaceNav />

      <div className="mt-3 border-t border-white/10 pt-3">
        <label htmlFor="active-profile" className="px-2 font-mono text-[0.61rem] font-semibold uppercase tracking-[0.12em] text-white/35">Matching profile</label>
        <select
          id="active-profile"
          value={activeProfileId ?? ''}
          onChange={(event) => setActiveProfileId(event.target.value || null)}
          className="control-focus mt-1.5 h-9 w-full rounded-lg border border-white/10 bg-white/[0.065] px-2.5 text-xs font-medium text-white outline-none"
        >
          <option className="text-ink" value="">No active profile</option>
          {(profiles ?? []).map((profile) => <option className="text-ink" key={profile.id} value={profile.id}>{profile.label}</option>)}
        </select>
        <p className="mt-1.5 line-clamp-2 px-2 text-[0.68rem] leading-4 text-white/40">
          {active ? `Targeting ${active.target_titles.slice(0, 2).join(' and ') || active.label}.` : 'Add a resume to personalize eligibility and match evidence.'}
        </p>
      </div>

      <div className="mt-3 grid gap-1.5">
        <button type="button" onClick={getLatest} disabled={isBusy} title={active ? PRIMARY_HELP.matched : PRIMARY_HELP.generic} className="control-focus flex h-9 w-full items-center justify-between rounded-lg bg-signal-600 px-3 text-xs font-semibold text-white hover:bg-signal-700 disabled:cursor-not-allowed disabled:opacity-50">
          <span>{ingest.isPending ? 'Searching sources…' : active ? 'Find profile matches' : 'Get latest jobs'}</span><ArrowUpRight size={15} weight="bold" className="shrink-0" />
        </button>
        <p className="px-2 pb-0.5 text-[0.66rem] leading-4 text-white/40">{active ? PRIMARY_HELP.matched : PRIMARY_HELP.generic}</p>
        <button type="button" onClick={refreshWatchlist} disabled={isBusy} title={WATCHLIST_HELP} className="control-focus flex h-9 w-full items-center justify-between rounded-lg border border-white/10 px-3 text-xs font-medium text-white/70 hover:bg-white/[0.055] hover:text-white disabled:opacity-50">
          <span>{refreshCompanies.isPending ? 'Refreshing…' : 'Refresh watchlist'}</span><ArrowClockwise size={15} />
        </button>
        <p className="px-2 text-[0.66rem] leading-4 text-white/40">{WATCHLIST_HELP}</p>
        <button type="button" onClick={() => setShowAddModal(true)} className="control-focus mt-0.5 flex h-8 items-center gap-1.5 rounded-lg px-2 text-left text-[0.68rem] font-medium text-white/45 hover:bg-white/[0.055] hover:text-white"><Plus size={13} />Add company to watch</button>
      </div>

      <div className="mt-auto border-t border-white/10 pt-2.5">
        <div className="mb-1 flex items-center justify-between px-2 py-1.5">
          <div>
            <span className="font-mono text-[0.96rem] font-semibold tabular-nums text-white">{(stats?.total_jobs ?? 0).toLocaleString()}</span>
            <span className="ml-1.5 text-[0.66rem] text-white/40">indexed</span>
          </div>
          <span className="flex items-center gap-1.5 text-[0.64rem] text-white/40"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />{checkedSourceCount || '–'} sources</span>
        </div>
        <AutomationPanel />
        <button type="button" onClick={() => setShowHelp(true)} className="control-focus flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-white/55 hover:bg-white/[0.055] hover:text-white"><Question size={16} />How JobScout works</button>
        <button type="button" onClick={() => setShowSettings(true)} className="control-focus flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-white/55 hover:bg-white/[0.055] hover:text-white"><GearSix size={16} />Data & backend</button>
        <UserMenu />
      </div>

      {showAddModal && <AddToWatchlistModal onClose={() => setShowAddModal(false)} />}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
    </aside>
  )
}

function MobileDrawer({ onClose }: { onClose: () => void }) {
  const { activeProfileId, setActiveProfileId } = useActiveProfile()
  const { data: profiles } = useProfiles()
  const ingest = useTriggerIngestion()
  const refreshCompanies = useRefreshCompanies()
  const [showSettings, setShowSettings] = useState(false)
  const [showAddModal, setShowAddModal] = useState(false)
  const active = profiles?.find((profile) => profile.id === activeProfileId)
  const busy = ingest.isPending || refreshCompanies.isPending

  const getLatest = () => {
    const keywords = active?.target_titles?.length ? active.target_titles : ['software engineer', 'data engineer', 'data scientist']
    ingest.mutate({ keywords, results_wanted: 250, profile_id: activeProfileId ?? undefined })
  }

  return (
    <div className="fixed inset-0 z-50 xl:hidden" role="dialog" aria-modal="true" aria-label="Workspace menu">
      <button type="button" aria-label="Close menu" onClick={onClose} className="absolute inset-0 bg-ink/45 backdrop-blur-[2px]" />
      <aside className="relative flex h-full w-[min(19rem,calc(100vw-2rem))] flex-col overflow-y-auto border-r border-slate-200 bg-[#fbfcfa] p-3.5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between rounded-lg bg-ink px-2.5 py-2">
          <Brand compact />
          <button type="button" onClick={onClose} className="control-focus flex h-7 w-7 items-center justify-center rounded-md text-white/60 hover:bg-white/10 hover:text-white" aria-label="Close menu"><X size={17} /></button>
        </div>
        <WorkspaceNav onNavigate={onClose} light />

        <div className="mt-4 border-t border-slate-200 pt-3">
          <label htmlFor="mobile-active-profile" className="section-label px-2">Matching profile</label>
          <select id="mobile-active-profile" value={activeProfileId ?? ''} onChange={(event) => setActiveProfileId(event.target.value || null)} className="control-focus mt-1.5 h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 outline-none">
            <option value="">No active profile</option>
            {(profiles ?? []).map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
          </select>
        </div>

        <div className="mt-3 grid gap-1.5">
          <button type="button" onClick={getLatest} disabled={busy} className="control-focus flex h-9 items-center justify-between rounded-lg bg-signal-600 px-3 text-xs font-semibold text-white disabled:opacity-50"><span>{ingest.isPending ? 'Searching sources…' : active ? 'Find profile matches' : 'Get latest jobs'}</span><ArrowUpRight size={15} className="shrink-0" /></button>
          <p className="px-2 pb-0.5 text-[0.66rem] leading-4 text-slate-500">{active ? PRIMARY_HELP.matched : PRIMARY_HELP.generic}</p>
          <button type="button" onClick={() => refreshCompanies.mutate({ keywords: [] })} disabled={busy} className="control-focus flex h-9 items-center justify-between rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 disabled:opacity-50"><span>{refreshCompanies.isPending ? 'Refreshing…' : 'Refresh watchlist'}</span><ArrowClockwise size={15} /></button>
          <p className="px-2 text-[0.66rem] leading-4 text-slate-500">{WATCHLIST_HELP}</p>
          <button type="button" onClick={() => setShowAddModal(true)} className="control-focus flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-slate-500 hover:bg-slate-100"><Plus size={13} />Add company to watch</button>
        </div>

        <div className="mt-auto border-t border-slate-200 pt-2">
          <AutomationPanel dark={false} />
          <button type="button" onClick={() => setShowSettings(true)} className="control-focus flex h-8 w-full items-center gap-2 rounded-lg px-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-ink"><GearSix size={16} />Data & backend</button>
        </div>

        {showAddModal && <AddToWatchlistModal onClose={() => setShowAddModal(false)} />}
        {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      </aside>
    </div>
  )
}

export default function TopNav() {
  const [menuOpen, setMenuOpen] = useState(false)

  return (
    <>
      <Sidebar />
      <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center border-b border-black/20 bg-ink px-3 xl:hidden">
        <Brand compact />
        <button type="button" onClick={() => setMenuOpen(true)} className="control-focus ml-auto flex h-8 w-8 items-center justify-center rounded-lg text-white/70 hover:bg-white/10 hover:text-white" aria-label="Open workspace menu"><List size={20} /></button>
      </header>
      {menuOpen && <MobileDrawer onClose={() => setMenuOpen(false)} />}
    </>
  )
}
