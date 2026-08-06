import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type NavMode = 'ping' | 'storm'

interface NavModeContextValue {
  mode: NavMode
  setMode: (mode: NavMode) => void
}

const NavModeContext = createContext<NavModeContextValue | null>(null)
const STORAGE_KEY = 'netpulse_nav_mode'

export function NavModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<NavMode>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'storm' ? 'storm' : 'ping'
  })

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, mode)
  }, [mode])

  const setMode = useCallback((next: NavMode) => setModeState(next), [])

  const value = useMemo(() => ({ mode, setMode }), [mode, setMode])

  return <NavModeContext.Provider value={value}>{children}</NavModeContext.Provider>
}

export function useNavMode() {
  const ctx = useContext(NavModeContext)
  if (!ctx) throw new Error('useNavMode must be used within NavModeProvider')
  return ctx
}
