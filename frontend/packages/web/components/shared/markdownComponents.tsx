'use client'

import type { Components } from 'react-markdown'
import { cn } from '@/lib/utils'
import { MermaidDiagram } from './MermaidDiagram'

export const markdownComponents: Components = {
  pre: ({ node, children, ...props }) => {
    const code = node?.children[0]
    if (
      code?.type === 'element' &&
      code.tagName === 'code' &&
      Array.isArray(code.properties.className) &&
      code.properties.className.includes('language-mermaid')
    ) {
      const source = code.children
        .map((child) => (child.type === 'text' ? child.value : ''))
        .join('')
      return <MermaidDiagram source={source} />
    }
    return <pre {...props}>{children}</pre>
  },
  table: ({ node: _node, children, className, ...props }) => (
    <div className="my-2 overflow-x-auto">
      <table className={cn('my-0', className)} {...props}>
        {children}
      </table>
    </div>
  ),
}
