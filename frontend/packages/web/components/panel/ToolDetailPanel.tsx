'use client'

import { useToolDetail } from '@/hooks/useToolDetail'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PanelHeader } from './PanelHeader'
import { TerminalView } from './TerminalView'
import { SearchResultView } from './SearchResultView'
import { WebFetchView } from './WebFetchView'
import { GenericToolView } from './GenericToolView'
import { SkillView } from './SkillView'
import { WriteFilePreviewView } from './WriteFilePreviewView'
import { FileReadView } from './FileReadView'

export function ToolDetailPanel() {
  const {
    toolName,
    toolArgs,
    toolResult,
    contentType,
    toolRef,
    highlightText,
    highlightKey,
    close,
  } = useToolDetail()

  return (
    <div className="flex flex-col h-full bg-background">
      <PanelHeader source={{ kind: 'tool', toolName, toolArgs, toolResult }} onClose={close} />
      <ScrollArea className="flex-1">
        {contentType === 'terminal' && <TerminalView args={toolArgs} result={toolResult} />}
        {contentType === 'search' && (
          <SearchResultView
            result={toolResult}
            args={toolArgs}
            highlightText={highlightText}
            highlightKey={highlightKey}
          />
        )}
        {contentType === 'web_fetch' && (
          <WebFetchView
            args={toolArgs}
            result={toolResult}
            highlightText={highlightText}
            highlightKey={highlightKey}
          />
        )}
        {contentType === 'skill' && <SkillView args={toolArgs} result={toolResult} />}
        {contentType === 'write_file' && (
          <WriteFilePreviewView args={toolArgs} result={toolResult} toolRef={toolRef} />
        )}
        {contentType === 'file_read' && (
          <FileReadView
            args={toolArgs}
            result={toolResult}
            highlightText={highlightText}
            highlightKey={highlightKey}
          />
        )}
        {(contentType === 'generic' ||
          contentType === 'code_execute' ||
          contentType === 'artifact') && (
          <GenericToolView
            args={toolArgs}
            result={toolResult}
            highlightText={highlightText}
            highlightKey={highlightKey}
          />
        )}
      </ScrollArea>
    </div>
  )
}
