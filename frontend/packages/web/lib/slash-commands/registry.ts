import type { SlashCommand, SlashCommandContext } from './types'

function hasConversation(ctx: SlashCommandContext): boolean {
  return Boolean(ctx.conversationId)
}

function hasWorkspace(ctx: SlashCommandContext): boolean {
  return Boolean(ctx.workspaceId)
}

/**
 * Static slash commands, ordered by day-to-day usefulness in the composer.
 * Dynamic enabled skills are appended at filter time (see skillCommands).
 *
 * Order rationale:
 * 1. new — leave / reset thread
 * 2. stop — only while streaming (appears near top when relevant)
 * 3. model / effort — every-turn controls
 * 4. skills / attach / mcp — context you add before send
 * 5. compact / share / rename — occasional thread ops
 */
export const SLASH_COMMANDS: SlashCommand[] = [
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
    id: 'skills',
    name: 'skills',
    descriptionKey: 'commands.skills.description',
    category: 'tools',
    keywords: ['skill', 'marketplace', 'install', 'pick', 'browse'],
    isAvailable: hasWorkspace,
    run: (ctx) => {
      ctx.openSkillsPicker()
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
    id: 'mcp',
    name: 'mcp',
    descriptionKey: 'commands.mcp.description',
    category: 'tools',
    keywords: ['connector', 'tools', 'integrations'],
    isAvailable: hasWorkspace,
    run: (ctx) => {
      ctx.openMcpPicker()
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
]
