import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold tracking-wide transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary/20 text-primary',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border text-foreground',
        muted: 'border-transparent bg-slate-500/20 text-slate-300',
        /** Success / Online */
        success: 'border-success/30 bg-success/15 text-success',
        online: 'border-success/30 bg-success/15 text-success',
        /** Warning */
        warning: 'border-warning/30 bg-warning/15 text-warning',
        /** Critical / Danger / Offline */
        danger: 'border-danger/30 bg-danger/15 text-danger',
        critical: 'border-danger/30 bg-danger/15 text-danger',
        offline: 'border-danger/30 bg-danger/15 text-danger',
        /** Information */
        info: 'border-sky-400/30 bg-sky-400/15 text-sky-300',
        /** Domain accents */
        storm: 'border-violet-400/30 bg-violet-500/15 text-violet-300',
        recovery: 'border-emerald-400/30 bg-emerald-500/15 text-emerald-300',
        mitigation: 'border-orange-400/30 bg-orange-500/15 text-orange-300',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
