import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/shared/auth/AuthContext'
import { LoadingState } from '@/shared/components/LoadingState'

export function ProtectedRoute() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LoadingState label="Checking session…" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: window.location.pathname }} />
  }

  if (user.mustChangePassword && location.pathname !== '/account') {
    return <Navigate to="/account" replace />
  }

  return <Outlet />
}
