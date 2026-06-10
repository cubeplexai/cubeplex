import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Shared Markdown prose styling for all rendered content */
export const proseClasses = [
  'prose prose-sm dark:prose-invert max-w-none',
  'prose-p:leading-relaxed prose-p:my-1',
  'prose-headings:font-semibold prose-headings:mt-3',
  'prose-headings:mb-1 prose-headings:text-foreground',
  'prose-p:text-foreground prose-li:text-foreground',
  'prose-strong:text-foreground prose-strong:font-semibold',
  'prose-code:text-foreground prose-code:text-[0.8em]',
  'prose-code:bg-muted prose-code:px-1',
  'prose-code:py-0.5 prose-code:rounded',
  'prose-code:before:content-none',
  'prose-code:after:content-none',
  'prose-pre:bg-sunken prose-pre:border prose-pre:border-border prose-pre:rounded',
  'prose-pre:text-[0.8em]',
  'prose-ul:my-1 prose-ol:my-1 prose-li:my-0',
  'prose-blockquote:border-l-primary/40',
  'prose-blockquote:text-muted-foreground',
  'prose-hr:border-border prose-a:text-primary',
  'prose-table:text-foreground',
  'prose-th:text-foreground prose-td:text-foreground',
].join(' ')
