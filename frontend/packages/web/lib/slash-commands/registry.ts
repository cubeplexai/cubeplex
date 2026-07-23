import type { SlashCommand, SlashCommandContext } from './types'

function hasConversation(ctx: SlashCommandContext): boolean {
  return Boolean(ctx.conversationId)
}

function hasWorkspace(ctx: SlashCommandContext): boolean {
  return Boolean(ctx.workspaceId)
}

/** P0 slash commands. Order is the default palette order when query is empty. */
export const SLASH_COMMANDS: SlashCommand[] = [
  {
    id: 'help',
    name: 'help',
    descriptionKey: 'commands.help.description',
    category: 'help',
    keywords: ['commands', 'list', '?', '援助'],
    isAvailable: () => true,
    run: (ctx) => {
      ctx.showHelp()
    },
  },
  {
    id: 'new',
    name: 'new',
    aliases: ['clear'],
    descriptionKey: 'commands.new.description',
    category: 'conversation',
    keywords: ['chat', 'conversation', 'reset'],
    isAvailable: () => true,
    run: (ctx) => {
      void ctx.createNewChat()
    },
  },
  {
    id: 'stop',
    name: 'stop',
    descriptionKey: 'commands.stop.description',
    category: 'run',
    keywords: ['cancel', 'abort', 'halt'],
    isAvailable: (ctx) => ctx.isStreaming && hasConversation(ctx),
    run: (ctx) => {
      if (ctx.conversationId) ctx.cancelStream(ctx.conversationId)
    },
  },
  {
    id: 'model',
    name: 'model',
    descriptionKey: 'commands.model.description',
    category: 'composer',
    keywords: ['llm', 'preset', 'provider'],
    isAvailable: (ctx) => ctx.modelPickerAvailable,
    run: (ctx) => {
      ctx.openModelPicker()
    },
  },
  {
    id: 'effort',
    name: 'effort',
    descriptionKey: 'commands.effort.description',
    category: 'composer',
    keywords: ['thinking', 'reasoning', 'level'],
    isAvailable: (ctx) => ctx.effortAvailable && ctx.modelPickerAvailable,
    run: (ctx) => {
      ctx.openEffortControl()
    },
  },
  {
    id: 'rename',
    name: 'rename',
    descriptionKey: 'commands.rename.description',
    category: 'conversation',
    keywords: ['title', 'name'],
    isAvailable: hasConversation,
    run: (ctx) => {
      ctx.startRename()
    },
  },
  {
    id: 'share',
    name: 'share',
    descriptionKey: 'commands.share.description',
    category: 'conversation',
    keywords: ['link', 'public'],
    isAvailable: hasConversation,
    run: (ctx) => {
      ctx.openShare()
    },
  },
  {
    id: 'attach',
    name: 'attach',
    descriptionKey: 'commands.attach.description',
    category: 'composer',
    keywords: ['file', 'upload', 'paperclip'],
    isAvailable: () => true,
    run: (ctx) => {
      ctx.openAttach()
    },
  },
  {
    id: 'skills',
    name: 'skills',
    descriptionKey: 'commands.skills.description',
    category: 'tools',
    keywords: ['skill', 'marketplace', 'install'],
    isAvailable: hasWorkspace,
    run: (ctx) => {
      ctx.openSkills()
    },
  },
  {
    id: 'mcp',
    name: 'mcp',
    descriptionKey: 'commands.mcp.description',
    category: 'tools',
    keywords: ['connector', 'tools', 'integrations'],
    isAvailable: hasWorkspace,
    run: (ctx) => {
      ctx.openMcp()
    },
  },
  {
    id: 'compact',
    name: 'compact',
    descriptionKey: 'commands.compact.description',
    category: 'tools',
    keywords: ['summarize', 'compress', 'context'],
    isAvailable: (ctx) => hasConversation(ctx) && !ctx.isStreaming && ctx.compactAvailable,
    run: (ctx) => {
      if (ctx.conversationId) void ctx.compactConversation(ctx.conversationId)
    },
  },
]
