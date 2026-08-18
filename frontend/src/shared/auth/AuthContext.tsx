import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { getMe, login as loginRequest } from '@/api'
import { getStoredToken, setAuthExpiredHandler, setStoredToken } from '@/shared/api/client'
import type { User } from '@/types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  isAdmin: boolean
  isUser: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  applySession: (token: string, nextUser: User) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const logout = useCallback(() => {
    setStoredToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    setAuthExpiredHandler(() => {
      setUser(null)
    })
    return () => setAuthExpiredHandler(null)
  }, [])

  useEffect(() => {
    const token = getStoredToken()
    if (!token) {
      setLoading(false)
      return
    }

    getMe()
      .then((res) => setUser(res.user))
      .catch(() => {
        setStoredToken(null)
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await loginRequest(username, password)
    setStoredToken(res.token)
    setUser(res.user)
  }, [])

  const applySession = useCallback((token: string, nextUser: User) => {
    setStoredToken(token)
    setUser(nextUser)
  }, [])

  const value = useMemo(
    () => ({
      user,
      loading,
      isAdmin: user?.role === 'admin',
      isUser: user?.role === 'user' || user?.role === 'admin',
      login,
      logout,
      applySession,
    }),
    [user, loading, login, logout, applySession],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
