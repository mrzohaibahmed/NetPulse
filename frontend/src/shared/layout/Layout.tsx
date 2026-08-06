import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from '@/shared/layout/Sidebar'
import { TopNavbar } from '@/shared/layout/TopNavbar'
import { PageTransition } from '@/shared/components/PageTransition'

export function Layout() {
  const [pinned, setPinned] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar
        pinned={pinned}
        onPinnedChange={setPinned}
        mobileOpen={mobileOpen}
        onMobileOpenChange={setMobileOpen}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopNavbar />
        <main className="min-w-0 flex-1 overflow-x-auto p-4 pb-8 md:p-6 md:pb-10">
          <PageTransition>
            <Outlet />
          </PageTransition>
        </main>
      </div>
    </div>
  )
}
