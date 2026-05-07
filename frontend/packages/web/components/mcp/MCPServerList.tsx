'use client'

import Link from 'next/link'
import { CircleDot, Plug } from 'lucide-react'
import type { MCPServer } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

import { MCPScopeBadge } from './MCPScopeBadge'

export interface MCPServerListProps {
  servers: MCPServer[]
  loading: boolean
  detailHrefBase: string
  emptyTitle: string
  emptyDescription: string
}

export function MCPServerList({
  servers,
  loading,
  detailHrefBase,
  emptyTitle,
  emptyDescription,
}: MCPServerListProps) {
  const t = useTranslations('mcp.list')
  if (loading) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">{t('loading')}</CardContent>
      </Card>
    )
  }

  if (servers.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <Plug className="size-10 text-muted-foreground" />
          <h3 className="font-semibold">{emptyTitle}</h3>
          <p className="max-w-md text-sm text-muted-foreground">{emptyDescription}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t('name')}</TableHead>
            <TableHead>{t('scope')}</TableHead>
            <TableHead>{t('transport')}</TableHead>
            <TableHead>{t('tools')}</TableHead>
            <TableHead>
              <span className="sr-only">{t('actions')}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {servers.map((server) => (
            <TableRow key={server.id}>
              <TableCell className="font-medium">
                <span className="flex items-center gap-2">
                  <CircleDot
                    className={cn('size-3', server.authed ? 'text-primary' : 'text-destructive')}
                    aria-label={server.authed ? t('authenticated') : t('connectionError')}
                  />
                  <span className="truncate">{server.name}</span>
                </span>
                {server.last_error ? (
                  <p className="mt-1 max-w-xs truncate text-xs text-muted-foreground">
                    {server.last_error}
                  </p>
                ) : null}
              </TableCell>
              <TableCell>
                <MCPScopeBadge scope={server.credential_scope} />
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{server.transport}</TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {server.tools_cache?.length ?? 0}
              </TableCell>
              <TableCell className="text-right">
                <Link
                  href={`${detailHrefBase}/${server.id}`}
                  className={buttonVariants({ variant: 'ghost', size: 'sm' })}
                >
                  {t('details')}
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}
