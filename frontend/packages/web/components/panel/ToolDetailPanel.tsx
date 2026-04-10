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

export function ToolDetailPanel() {
  const {
    toolName,
    toolArgs,
    toolResult,
    contentType,
    toolRef,
    close,
  } = useToolDetail()

  return (
    <div className="flex flex-col h-full bg-background">
      <PanelHeader
        toolName={toolName}
        toolArgs={toolArgs}
        toolResult={toolResult}
        onClose={close}
      />
      <ScrollArea className="flex-1">
        {contentType === 'terminal' && (
          <TerminalView
            args={toolArgs}
            result={toolResult}
          />
        )}
        {contentType === 'search' && (
          <SearchResultView
            result={toolResult}
            args={toolArgs}
          />
        )}
        {contentType === 'web_fetch' && (
          <WebFetchView
            args={toolArgs}
            result={toolResult}
          />
        )}
        {contentType === 'skill' && (
          <SkillView
            args={toolArgs}
            result={toolResult}
          />
        )}
        {contentType === 'write_file' && (
          <WriteFilePreviewView
            args={toolArgs}
            result={toolResult}
            toolRef={toolRef}
          />
        )}
        {(contentType === 'generic' ||
          contentType === 'code_execute' ||
          contentType === 'artifact') && (
          <GenericToolView
            args={toolArgs}
            result={toolResult}
          />
        )}
      </ScrollArea>
    </div>
  )
}
