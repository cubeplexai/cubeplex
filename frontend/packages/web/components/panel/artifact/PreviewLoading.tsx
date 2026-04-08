import { Loader2 } from 'lucide-react'

/** Centered loading spinner shared by all artifact preview components. */
export function PreviewLoading() {
  return (
    <div className="flex items-center justify-center h-full w-full py-20">
      <Loader2 className="size-5 animate-spin text-muted-foreground" />
    </div>
  )
}
