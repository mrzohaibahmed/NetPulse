import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity,
  Bell,
  ChevronDown,
  FileBarChart,
  GitBranch,
  History,
  LayoutDashboard,
  Menu,
  Network,
  Pin,
  PinOff,
  Radar,
  RotateCcw,
  Server,
  Settings,
  Share2,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react'
import { useAuth } from '@/shared/auth/AuthContext'
import { useHealthQuery } from '@/hooks/queries'
import { cn } from '@/lib/utils'
import { Button } from '@/shared/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip'

interface SidebarProps {
  pinned: boolean
  onPinnedChange: (value: boolean) => void
  mobileOpen: boolean
  onMobileOpenChange: (value: boolean) => void
}

type NavIcon = typeof LayoutDashboard

type NavItem = {
  id: string
  to: string
  label: string
  icon: NavIcon
  end?: boolean
  adminOnly?: boolean
  /** Custom active matching for shared paths / query variants */
  isActive?: (pathname: string, search: string) => boolean
}

type NavGroup = {
  id: string
  title: string
  icon: NavIcon
  items: NavItem[]
  defaultOpen?: boolean
}

const GROUP_STORAGE_KEY = 'netpulse.sidebar.groups'

type StormView = 'overview' | 'incidents' | 'pipeline' | 'mitigation' | 'recovery'

function stormViewActive(expected: StormView) {
  return (pathname: string, search: string) => {
    if (pathname !== '/storm') return false
    const view = new URLSearchParams(search).get('view')
    if (expected === 'overview') {
      return !view || view === 'overview'
    }
    return view === expected
  }
}

export function Sidebar({ pinned, onPinnedChange, mobileOpen, onMobileOpenChange }: SidebarProps) {
  const [hovered, setHovered] = useState(false)
  const collapsed = !pinned && !hovered
  const { isAdmin, user, logout } = useAuth()
  const location = useLocation()
  const health = useHealthQuery()
  const apiOk = health.isError ? false : health.data ? true : null
  const dbOk = health.data ? health.data.database === 'Connected' : health.isError ? false : null

  const groups: NavGroup[] = [
    {
      id: 'dashboard',
      title: 'Dashboard',
      icon: LayoutDashboard,
      defaultOpen: true,
      items: [
        {
          id: 'dashboard-overview',
          to: '/',
          label: 'Enterprise Overview',
          icon: LayoutDashboard,
          end: true,
        },
      ],
    },
    {
      id: 'ping',
      title: 'Ping Monitoring',
      icon: Activity,
      defaultOpen: true,
      items: [
        { id: 'ping-devices', to: '/devices', label: 'Devices', icon: Server },
        {
          id: 'ping-discovery',
          to: '/discovery',
          label: 'Discovery',
          icon: Radar,
          adminOnly: true,
        },
        { id: 'ping-history', to: '/history', label: 'History', icon: History },
        { id: 'ping-reports', to: '/reports', label: 'Reports', icon: FileBarChart },
      ],
    },
    {
      id: 'storm',
      title: 'Storm Protection',
      icon: Shield,
      defaultOpen: true,
      items: [
        {
          id: 'storm-overview',
          to: '/storm',
          label: 'Overview',
          icon: Shield,
          isActive: stormViewActive('overview'),
        },
        { id: 'storm-interfaces', to: '/interfaces', label: 'Interfaces', icon: Network },
        { id: 'storm-topology', to: '/topology', label: 'Topology', icon: Share2 },
        {
          id: 'storm-pipeline',
          to: '/storm?view=pipeline',
          label: 'Risk Analysis',
          icon: GitBranch,
          isActive: stormViewActive('pipeline'),
        },
        {
          id: 'storm-incidents',
          to: '/storm?view=incidents',
          label: 'Incidents',
          icon: ShieldAlert,
          isActive: stormViewActive('incidents'),
        },
        {
          id: 'storm-mitigation',
          to: '/storm?view=mitigation',
          label: 'Mitigation',
          icon: ShieldCheck,
          isActive: stormViewActive('mitigation'),
        },
        {
          id: 'storm-recovery',
          to: '/storm?view=recovery',
          label: 'Recovery',
          icon: RotateCcw,
          isActive: stormViewActive('recovery'),
        },
      ],
    },
    {
      id: 'operations',
      title: 'Operations',
      icon: Bell,
      defaultOpen: true,
      items: [{ id: 'ops-alerts', to: '/alerts', label: 'Alerts', icon: Bell }],
    },
    {
      id: 'admin',
      title: 'Administration',
      icon: Settings,
      defaultOpen: true,
      items: [
        { id: 'admin-users', to: '/account', label: 'Users', icon: Users },
        {
          id: 'admin-settings',
          to: '/settings',
          label: 'Settings',
          icon: Settings,
          adminOnly: true,
        },
      ],
    },
  ]

  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => {
    try {
      const raw = localStorage.getItem(GROUP_STORAGE_KEY)
      if (raw) return JSON.parse(raw) as Record<string, boolean>
    } catch {
      /* ignore */
    }
    return Object.fromEntries(groups.map((g) => [g.id, g.defaultOpen !== false]))
  })

  useEffect(() => {
    try {
      localStorage.setItem(GROUP_STORAGE_KEY, JSON.stringify(openGroups))
    } catch {
      /* ignore */
    }
  }, [openGroups])

  const filterItems = (items: NavItem[]) => items.filter((item) => !item.adminOnly || isAdmin)

  const toggleGroup = (id: string) => {
    setOpenGroups((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const itemIsActive = (item: NavItem) => {
    if (item.isActive) {
      return item.isActive(location.pathname, location.search)
    }
    if (item.end) {
      return location.pathname === item.to.split('?')[0]
    }
    const path = item.to.split('?')[0]
    return location.pathname === path || location.pathname.startsWith(`${path}/`)
  }

  const renderNavLink = (item: NavItem) => {
    const Icon = item.icon
    const active = itemIsActive(item)
    const link = (
      <NavLink
        key={item.id}
        to={item.to}
        end={item.end}
        onClick={() => onMobileOpenChange(false)}
        className={cn(
          'group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
          active && 'bg-primary/15 text-white',
          collapsed && 'justify-center px-2',
        )}
      >
        {active ? (
          <motion.span
            layoutId="nav-indicator"
            className="absolute left-0 top-1/2 h-6 w-1 -translate-y-1/2 rounded-r-full bg-primary"
          />
        ) : null}
        <Icon className="h-4.5 w-4.5 shrink-0" />
        {!collapsed ? <span>{item.label}</span> : null}
      </NavLink>
    )

    if (collapsed) {
      return (
        <Tooltip key={item.id} delayDuration={0}>
          <TooltipTrigger asChild>{link}</TooltipTrigger>
          <TooltipContent side="right">{item.label}</TooltipContent>
        </Tooltip>
      )
    }
    return link
  }

  const renderNavGroupSection = (group: NavGroup) => {
    const items = filterItems(group.items)
    if (items.length === 0) return null

    const isOpen = collapsed ? true : openGroups[group.id] !== false
    const GroupIcon = group.icon
    const groupHasActive = items.some((item) => itemIsActive(item))

    if (collapsed) {
      return (
        <div key={group.id} className="space-y-1">
          {items.map((item) => renderNavLink(item))}
        </div>
      )
    }

    return (
      <div key={group.id} className="space-y-1">
        <button
          type="button"
          onClick={() => toggleGroup(group.id)}
          className={cn(
            'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-sidebar-accent/60',
            groupHasActive && 'text-white',
          )}
          aria-expanded={isOpen}
        >
          <GroupIcon
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-muted-foreground',
              groupHasActive && 'text-primary',
            )}
          />
          <span className="flex-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            {group.title}
          </span>
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
              isOpen && 'rotate-180',
            )}
          />
        </button>

        <AnimatePresence initial={false}>
          {isOpen ? (
            <motion.div
              key={`${group.id}-items`}
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden"
            >
              <div className="space-y-0.5 pl-0.5">{items.map((item) => renderNavLink(item))}</div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    )
  }

  const content = (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-in-out',
        collapsed ? 'w-[72px]' : 'w-[248px]',
      )}
    >
      <div className={cn('flex items-center gap-3 px-4 py-5', collapsed && 'justify-center px-2')}>
        <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-cyan-500 shadow-lg shadow-primary/30">
          <Activity className="h-5 w-5 text-white" />
          <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-success ring-2 ring-sidebar" />
        </div>
        <AnimatePresence initial={false}>
          {!collapsed ? (
            <motion.div
              initial={{ opacity: 0, width: 0 }}
              animate={{ opacity: 1, width: 'auto' }}
              exit={{ opacity: 0, width: 0 }}
              className="flex flex-1 items-center justify-between overflow-hidden"
            >
              <div>
                <p className="text-lg font-bold tracking-tight text-white">NetPulse</p>
                <p className="text-xs text-muted-foreground">Network Monitor</p>
              </div>
              <Tooltip delayDuration={0}>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0 text-muted-foreground hover:text-white"
                    onClick={() => onPinnedChange(!pinned)}
                    aria-label={pinned ? 'Unpin sidebar' : 'Pin sidebar'}
                  >
                    {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">{pinned ? 'Unpin' : 'Pin'}</TooltipContent>
              </Tooltip>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      <nav className="np-sidebar-scroll flex-1 space-y-4 overflow-y-auto px-2 pb-4" aria-label="Primary">
        {groups.map((group) => renderNavGroupSection(group))}
      </nav>

      <div className="space-y-3 border-t border-sidebar-border p-3">
        <div className={cn('space-y-2 rounded-lg bg-sidebar-accent/50 p-3 text-xs', collapsed && 'px-2')}>
          <HealthRow label="API" ok={apiOk} collapsed={collapsed} />
          <HealthRow label="DB" ok={dbOk} collapsed={collapsed} />
        </div>

        {!collapsed ? (
          <div className="flex items-center justify-between gap-2 rounded-lg bg-sidebar-accent/40 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-white">{user?.username}</p>
              <p className="text-xs capitalize text-muted-foreground">{user?.role}</p>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={logout} className="text-xs">
              Log out
            </Button>
          </div>
        ) : null}
      </div>
    </aside>
  )

  return (
    <>
      {/* Desktop */}
      <div
        className="sticky top-0 hidden h-screen shrink-0 md:block"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {content}
      </div>

      {/* Mobile toggle */}
      <Button
        type="button"
        variant="secondary"
        size="icon"
        className="fixed left-3 top-3 z-40 md:hidden"
        onClick={() => onMobileOpenChange(true)}
        aria-label="Open navigation"
      >
        <Menu className="h-4 w-4" />
      </Button>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen ? (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => onMobileOpenChange(false)}
            />
            <motion.div
              className="fixed inset-y-0 left-0 z-50 md:hidden"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', stiffness: 320, damping: 32 }}
            >
              <div className="relative h-full w-[248px]">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-2 top-2 z-10"
                  onClick={() => onMobileOpenChange(false)}
                  aria-label="Close navigation"
                >
                  <X className="h-4 w-4" />
                </Button>
                <div className="h-full [&_aside]:w-full">{content}</div>
              </div>
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>
    </>
  )
}

function HealthRow({
  label,
  ok,
  collapsed,
}: {
  label: string
  ok: boolean | null
  collapsed: boolean
}) {
  return (
    <div className={cn('flex items-center gap-2', collapsed && 'justify-center')}>
      <span
        className={cn(
          'h-2 w-2 rounded-full',
          ok === true && 'bg-success shadow-[0_0_0_3px_rgba(34,197,94,0.2)]',
          ok === false && 'bg-danger',
          ok === null && 'bg-slate-500',
        )}
      />
      {!collapsed ? (
        <span className="text-sidebar-foreground/80">
          {label}{' '}
          {ok === null
            ? '…'
            : ok
              ? label === 'API'
                ? 'online'
                : 'connected'
              : label === 'API'
                ? 'down'
                : 'disconnected'}
        </span>
      ) : null}
    </div>
  )
}
