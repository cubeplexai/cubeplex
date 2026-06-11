export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return <div className="min-h-screen flex flex-col bg-background">{children}</div>
}
