/**
 * Composer slash-command registry types.
 * Shell injects all side-effect callbacks; the registry never imports AppShell.
 */

export type SlashCommandCategory = 'conversation' | 'run' | 'composer' | 'help' | 'tools'

export type SlashCommandContext = {
  conversationId?: string
  workspaceId: string | null
  isStreaming: boolean
  /** When false, effort control is not shown in the composer. */
  effortAvailable: boolean
  /** When false, model picker is not mounted (no workspace). */
  modelPickerAvailable: boolean
  /** Force-compact API / helper is wired. */
  compactAvailable: boolean
  cancelStream: (conversationId: string) => void
  openModelPicker: () => void
  openEffortControl: () => void
  startRename: () => void
  openAttach: () => void
  createNewChat: () => void | Promise<void>
  openShare: () => void
  openSkills: () => void
  openMcp: () => void
  compactConversation: (conversationId: string) => void | Promise<void>
  /** Enter help mode in the popover (clear filter / show all). */
  showHelp: () => void
}

export type SlashCommand = {
  id: string
  /** Without leading slash, e.g. "new" */
  name: string
  aliases?: string[]
  descriptionKey: string
  category: SlashCommandCategory
  keywords?: string[]
  isAvailable: (ctx: SlashCommandContext) => boolean
  run: (ctx: SlashCommandContext) => void | Promise<void>
}

export type CommandToken = {
  kind: 'command'
  raw: string
  query: string
}
