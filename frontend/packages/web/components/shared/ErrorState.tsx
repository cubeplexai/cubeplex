import Link from 'next/link'

export function ErrorState({
  title,
  description,
  backHref,
  backLabel = 'Go back',
}: {
  title: string
  description?: string
  backHref: string
  backLabel?: string
}) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-md text-center space-y-3">
        <h2 className="text-lg font-semibold">{title}</h2>
        {description && <p className="text-sm text-foreground/60">{description}</p>}
        <Link href={backHref} className="inline-block text-sm underline">
          {backLabel}
        </Link>
      </div>
    </div>
  )
}
