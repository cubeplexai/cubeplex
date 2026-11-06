import { describe, expect, it } from 'vitest'
import type { AssistantMessage, ContentBlock, Message } from '@cubeplex/core'
import { foldToolActivity, groupAssistantRuns, mergeAssistantRun } from '../foldToolActivity'

function thinking(text: string): ContentBlock {
  return { type: 'thinking', thinking: text }
}

function tool(id: string, name = 'execute'): ContentBlock {
  return { type: 'tool_call', id, name, arguments: {} }
}

function text(value: string): ContentBlock {
  return { type: 'text', text: value }
}

function assistant(
  id: string,
  runId: string | null,
  content: ContentBlock[],
  seq?: number,
): AssistantMessage {
  return {
    id,
    role: 'assistant',
    run_id: runId,
    seq,
    content,
    stop_reason: 'tool_use',
  }
}

describe('foldToolActivity', () => {
  it('keeps thinking between tool calls inside one activity until text', () => {
    const pieces = foldToolActivity([
      thinking('A'),
      tool('1'),
      thinking('B'),
      tool('2'),
      text('answer'),
      thinking('C'),
      tool('3'),
    ])

    expect(pieces).toEqual([
      {
        kind: 'activity',
        blocks: [thinking('A'), tool('1'), thinking('B'), tool('2')],
      },
      { kind: 'block', block: text('answer') },
      { kind: 'activity', blocks: [thinking('C'), tool('3')] },
    ])
  })

  it('leaves thinking that never meets a tool call as its own block', () => {
    const pieces = foldToolActivity([thinking('only'), text('answer')])
    expect(pieces).toEqual([
      { kind: 'block', block: thinking('only') },
      { kind: 'block', block: text('answer') },
    ])
  })

  it('ignores blank text and write_todos when deciding the split', () => {
    const pieces = foldToolActivity([
      tool('1'),
      text('  \n'),
      { type: 'tool_call', id: 'todo', name: 'write_todos', arguments: { todos: [] } },
      tool('2'),
    ])
    expect(pieces).toEqual([{ kind: 'activity', blocks: [tool('1'), tool('2')] }])
  })

  it('splits on a tool that renders its own card', () => {
    const subagent = tool('sub', 'subagent')
    const pieces = foldToolActivity([tool('1'), subagent, tool('2')])
    expect(pieces).toEqual([
      { kind: 'activity', blocks: [tool('1')] },
      { kind: 'block', block: subagent },
      { kind: 'activity', blocks: [tool('2')] },
    ])
  })

  it.each([
    'save_artifact',
    'generate_image',
    'present_file',
    'show_widget',
    'platform_skills_find',
  ])('splits on %s', (name) => {
    const special = tool('s', name)
    const pieces = foldToolActivity([tool('1'), special])
    expect(pieces.map((piece) => piece.kind)).toEqual(['activity', 'block'])
  })

  it('keeps an in-progress tool call in the open activity', () => {
    const streaming: ContentBlock = {
      type: 'tool_call_streaming',
      name: 'execute',
      args_text: '',
      tool_call_id: 's',
      index: 0,
    }
    expect(foldToolActivity([tool('1'), streaming])).toEqual([
      { kind: 'activity', blocks: [tool('1'), streaming] },
    ])
  })

  it('splits on a legacy skills find call', () => {
    const find: ContentBlock = {
      type: 'tool_call',
      id: 'find',
      name: 'skills',
      arguments: { operation: 'find' },
    }
    const pieces = foldToolActivity([tool('1'), find, tool('2')])
    expect(pieces.map((piece) => piece.kind)).toEqual(['activity', 'block', 'activity'])
  })
})

describe('groupAssistantRuns', () => {
  it('joins same-run assistant rows across tool results', () => {
    const toolResult: Message = {
      id: 'tr',
      role: 'tool_result',
      tool_call_id: '1',
      tool_name: 'execute',
      content: [text('ok')],
    }
    const messages: Message[] = [
      assistant('a1', 'run-1', [thinking('A'), tool('1')], 1),
      toolResult,
      assistant('a2', 'run-1', [thinking('B'), tool('2')], 3),
      assistant('a3', 'run-1', [text('answer')], 4),
    ]

    expect(groupAssistantRuns(messages)).toMatchObject([
      { kind: 'assistant-run', messages: [messages[0], messages[2], messages[3]] },
    ])
  })

  it('keeps the anchor on the last row and the clock on the first', () => {
    const first = assistant('a1', 'run-1', [tool('1')])
    first.timestamp = 10
    const last = assistant('a2', 'run-1', [text('hi')])
    last.timestamp = 20
    last.stop_reason = 'stop'

    const merged = mergeAssistantRun([first, last])
    expect(merged.id).toBe('a2')
    expect(merged.stop_reason).toBe('stop')
    expect(merged.timestamp).toBe(10)
    expect(merged.content).toEqual([tool('1'), text('hi')])
    expect(mergeAssistantRun([first])).toBe(first)
  })

  it('keeps earlier text and takes aborted from a trailing empty stop marker', () => {
    const marker = assistant('a-stop', 'run-1', [])
    marker.stop_reason = 'aborted'
    marker.metadata = { kind: 'user_stop' }
    const messages: Message[] = [assistant('a1', 'run-1', [text('partial')]), marker]

    const rows = groupAssistantRuns(messages)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      kind: 'assistant-run',
      merged: {
        id: 'a-stop',
        stop_reason: 'aborted',
        content: [text('partial')],
      },
    })
  })

  it('starts a new run at a user message or a different run id', () => {
    const user: Message = { id: 'u', role: 'user', content: [text('next')] }
    const messages: Message[] = [
      assistant('a1', 'run-1', [tool('1')]),
      user,
      assistant('a2', 'run-2', [tool('2')]),
      assistant('a3', null, [tool('3')]),
      assistant('a4', null, [tool('4')]),
    ]

    const rows = groupAssistantRuns(messages)
    expect(rows.map((row) => row.kind)).toEqual([
      'assistant-run',
      'message',
      'assistant-run',
      'assistant-run',
      'assistant-run',
    ])
    expect(rows[2]).toMatchObject({ messages: [messages[2]] })
    expect(rows[3]).toMatchObject({ messages: [messages[3]] })
    expect(rows[4]).toMatchObject({ messages: [messages[4]] })
  })
})
