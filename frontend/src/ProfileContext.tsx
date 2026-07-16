import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useProfiles } from './api/client'

/**
 * Active-profile context. The selected profile id is persisted in localStorage so
 * the whole app (Jobs verdicts/sort, Shortlist, Applied, job actions) follows one
 * profile across tabs and reloads. No auth — this is a single-user/local tool.
 */
interface ProfileCtx {
  activeProfileId: string | null
  setActiveProfileId: (id: string | null) => void
}

const Ctx = createContext<ProfileCtx>({
  activeProfileId: null,
  setActiveProfileId: () => {},
})

const KEY = 'jobscout.activeProfileId'
const EXPLICIT_NONE = '__none__'

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [activeProfileId, setActive] = useState<string | null>(
    () => {
      const stored = localStorage.getItem(KEY)
      return stored && stored !== EXPLICIT_NONE ? stored : null
    },
  )
  const [explicitlyNone, setExplicitlyNone] = useState(
    () => localStorage.getItem(KEY) === EXPLICIT_NONE,
  )
  const { data: profiles, isSuccess: profilesLoaded } = useProfiles()

  // First run should be useful without another hidden setup step: select the first
  // saved profile when no preference has ever been stored. A sentinel preserves an
  // intentional "No active profile" choice, while stale ids repair to the first
  // available profile instead of sending every personalized endpoint a 404.
  useEffect(() => {
    if (!profilesLoaded) return
    const firstProfileId = profiles?.[0]?.id ?? null
    if (activeProfileId) {
      if (!(profiles ?? []).some((profile) => profile.id === activeProfileId)) {
        setActive(firstProfileId)
        setExplicitlyNone(false)
      }
      return
    }
    if (!explicitlyNone && firstProfileId) {
      setActive(firstProfileId)
    }
  }, [activeProfileId, explicitlyNone, profiles, profilesLoaded])

  useEffect(() => {
    if (activeProfileId) localStorage.setItem(KEY, activeProfileId)
    else if (explicitlyNone) localStorage.setItem(KEY, EXPLICIT_NONE)
    else localStorage.removeItem(KEY)
  }, [activeProfileId, explicitlyNone])

  const setActiveProfileId = (id: string | null) => {
    setExplicitlyNone(id === null)
    setActive(id)
  }

  return (
    <Ctx.Provider value={{ activeProfileId, setActiveProfileId }}>
      {children}
    </Ctx.Provider>
  )
}

export function useActiveProfile() {
  return useContext(Ctx)
}
