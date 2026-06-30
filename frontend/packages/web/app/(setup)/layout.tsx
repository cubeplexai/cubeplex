export default function SetupLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-background text-foreground p-8">
      <div className="w-full max-w-md">{children}</div>
    </div>
  )
}
