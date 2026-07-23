import { beforeEach, describe, expect, it } from 'vitest'
import { useComposerChromeStore } from '../composer-chrome'

describe('composer-chrome store', () => {
  beforeEach(() => {
    useComposerChromeStore.setState({ shareRequest: null, renameRequest: null })
  })

  it('consumes share request by matching nonce only', () => {
    useComposerChromeStore.getState().requestOpenShare('conv-1')
    const req = useComposerChromeStore.getState().shareRequest
    expect(req?.conversationId).toBe('conv-1')
    useComposerChromeStore.getState().consumeShareRequest((req?.nonce ?? 0) + 1)
    expect(useComposerChromeStore.getState().shareRequest).not.toBeNull()
    useComposerChromeStore.getState().consumeShareRequest(req!.nonce)
    expect(useComposerChromeStore.getState().shareRequest).toBeNull()
  })

  it('consumes rename request so remount does not replay', () => {
    useComposerChromeStore.getState().requestRename('conv-2')
    const req = useComposerChromeStore.getState().renameRequest!
    useComposerChromeStore.getState().consumeRenameRequest(req.nonce)
    expect(useComposerChromeStore.getState().renameRequest).toBeNull()
  })
})
