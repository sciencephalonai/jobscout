import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

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

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [activeProfileId, setActive] = useState<string | null>(
    () => localStorage.getItem(KEY) || null,
  )
  useEffect(() => {
    if (activeProfileId) localStorage.setItem(KEY, activeProfileId)
    else localStorage.removeItem(KEY)
  }, [activeProfileId])

  return (
    <Ctx.Provider value={{ activeProfileId, setActiveProfileId: setActive }}>
      {children}
    </Ctx.Provider>
  )
}

export function useActiveProfile() {
  return useContext(Ctx)
}
