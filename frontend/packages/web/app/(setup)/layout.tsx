export default function SetupLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-background text-foreground p-8">
      <div className="w-full max-w-md">
        <h1 className="mb-6 text-xl font-semibold">Set up your organization</h1>
        {children}
      </div>
    </div>
  )
}
