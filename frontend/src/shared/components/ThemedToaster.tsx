import { Toaster } from 'sonner'
import { useTheme } from '@/lib/theme'

export function ThemedToaster() {
  const { theme } = useTheme()

  return (
    <Toaster
      theme={theme}
      position="top-right"
      richColors
      closeButton
      toastOptions={{
        classNames: {
          toast: 'bg-card border-border text-foreground',
        },
      }}
    />
  )
}
