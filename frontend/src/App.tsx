import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { AuthProvider } from '@/auth/AuthContext'
import { Layout } from '@/components/layout/Layout'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { LoadingState } from '@/components/shared/LoadingState'
import { ThemeProvider } from '@/lib/theme'
import { NavModeProvider } from '@/lib/navMode'
import { TooltipProvider } from '@/components/ui/tooltip'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

const DashboardPage = lazy(() =>
  import('@/pages/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const DevicesPage = lazy(() =>
  import('@/pages/DevicesPage').then((m) => ({ default: m.DevicesPage })),
)
const InterfacesPage = lazy(() =>
  import('@/pages/InterfacesEnterprisePage').then((m) => ({ default: m.InterfacesEnterprisePage })),
)
const InterfaceDetailPage = lazy(() =>
  import('@/pages/InterfaceDetailPage').then((m) => ({ default: m.InterfaceDetailPage })),
)
const DiscoveryPage = lazy(() =>
  import('@/pages/DiscoveryPage').then((m) => ({ default: m.DiscoveryPage })),
)
const HistoryPage = lazy(() =>
  import('@/pages/HistoryPage').then((m) => ({ default: m.HistoryPage })),
)
const ReportsPage = lazy(() =>
  import('@/pages/ReportsPage').then((m) => ({ default: m.ReportsPage })),
)
const AccountPage = lazy(() =>
  import('@/pages/AccountPage').then((m) => ({ default: m.AccountPage })),
)
const SettingsPage = lazy(() =>
  import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)
const AlertsPage = lazy(() =>
  import('@/pages/AlertsPage').then((m) => ({ default: m.AlertsPage })),
)
const StormProtectionPage = lazy(() =>
  import('@/pages/StormProtectionPage').then((m) => ({ default: m.StormProtectionPage })),
)
const StormDashboardPage = lazy(() =>
  import('@/pages/StormDashboardPage').then((m) => ({ default: m.StormDashboardPage })),
)
const LoginPage = lazy(() =>
  import('@/pages/LoginPage').then((m) => ({ default: m.LoginPage })),
)

function PageFallback() {
  return <LoadingState label="Loading page…" />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <NavModeProvider>
        <TooltipProvider delayDuration={200}>
          <AuthProvider>
            <BrowserRouter>
              <Suspense fallback={<PageFallback />}>
                <Routes>
                  <Route path="/login" element={<LoginPage />} />
                  <Route element={<ProtectedRoute />}>
                    <Route element={<Layout />}>
                      <Route index element={<DashboardPage />} />
                      <Route path="devices/:deviceId?" element={<DevicesPage />} />
                      <Route path="interfaces" element={<InterfacesPage />} />
                      <Route
                        path="interfaces/:deviceId/:interfaceName"
                        element={<InterfaceDetailPage />}
                      />
                      <Route path="alerts" element={<AlertsPage />} />
                      <Route path="storm" element={<StormProtectionPage />} />
                      <Route path="storm/dashboard" element={<StormDashboardPage />} />
                      <Route path="discovery" element={<DiscoveryPage />} />
                      <Route path="history" element={<HistoryPage />} />
                      <Route path="reports" element={<ReportsPage />} />
                      <Route path="account" element={<AccountPage />} />
                      <Route path="settings" element={<SettingsPage />} />
                      <Route path="*" element={<Navigate to="/" replace />} />
                    </Route>
                  </Route>
                </Routes>
              </Suspense>
            </BrowserRouter>
            <Toaster
              theme="dark"
              position="top-right"
              richColors
              closeButton
              toastOptions={{
                classNames: {
                  toast: 'bg-card border-border text-foreground',
                },
              }}
            />
          </AuthProvider>
        </TooltipProvider>
        </NavModeProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
