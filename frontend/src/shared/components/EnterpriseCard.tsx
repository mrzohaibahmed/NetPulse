import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card'
import type { VariantProps } from 'class-variance-authority'
import { cardVariants } from '@/shared/ui/card'

type CardVariant = NonNullable<VariantProps<typeof cardVariants>['variant']>

interface EnterpriseCardProps {
  title?: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  variant?: CardVariant
  className?: string
  contentClassName?: string
  /** Optional id for section anchors */
  id?: string
}

/**
 * Standardized section / primary / secondary / status card shell.
 * Prefer this over ad-hoc Card + Header compositions for page sections.
 */
export function EnterpriseCard({
  title,
  description,
  actions,
  children,
  variant = 'section',
  className,
  contentClassName,
  id,
}: EnterpriseCardProps) {
  const hasHeader = Boolean(title || description || actions)

  return (
    <Card id={id} variant={variant} className={cn(className)}>
      {hasHeader ? (
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div className="min-w-0 space-y-1">
            {title ? <CardTitle>{title}</CardTitle> : null}
            {description ? <CardDescription>{description}</CardDescription> : null}
          </div>
          {actions ? <div className="np-toolbar shrink-0">{actions}</div> : null}
        </CardHeader>
      ) : null}
      <CardContent className={cn(!hasHeader && 'pt-5', contentClassName)}>{children}</CardContent>
    </Card>
  )
}

/** Alias helpers for consistent call sites */
export function PrimaryCard(props: Omit<EnterpriseCardProps, 'variant'>) {
  return <EnterpriseCard {...props} variant="primary" />
}

export function SecondaryCard(props: Omit<EnterpriseCardProps, 'variant'>) {
  return <EnterpriseCard {...props} variant="secondary" />
}

export function SectionCard(props: Omit<EnterpriseCardProps, 'variant'>) {
  return <EnterpriseCard {...props} variant="section" />
}

export function MetricCard(props: Omit<EnterpriseCardProps, 'variant'>) {
  return <EnterpriseCard {...props} variant="metric" />
}

export function StatusCard(props: Omit<EnterpriseCardProps, 'variant'>) {
  return <EnterpriseCard {...props} variant="status" />
}
