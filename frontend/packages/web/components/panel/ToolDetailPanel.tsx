'use client'

import { useToolDetail } from '@/hooks/useToolDetail'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PanelHeader } from './PanelHeader'
import { TerminalView } from './TerminalView'
import { SearchResultView } from './SearchResultView'
import { WebFetchView } from './WebFetchView'
import { GenericToolView } from './GenericToolView'

export function ToolDetailPanel() {
  const {
    toolName,
    toolArgs,
    toolResult,
    contentType,
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
          <SearchResultView result={toolResult} />
        )}
        {contentType === 'web_fetch' && (
          <WebFetchView
            args={toolArgs}
            result={toolResult}
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
