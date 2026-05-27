function decodeEscapeSequence(char: string): string {
  switch (char) {
    case 'n':
      return '\n'
    case 'r':
      return '\r'
    case 't':
      return '\t'
    case 'b':
      return '\b'
    case 'f':
      return '\f'
    case '"':
      return '"'
    case '\\':
      return '\\'
    case '/':
      return '/'
    default:
      return char
  }
}

/** Tolerantly extract a JSON string field's value from possibly-incomplete JSON. */
export function extractJsonStringPrefix(raw: string, key: string): string {
  const keyMatch = new RegExp(`"${key}"\\s*:\\s*"`, 'm').exec(raw)
  if (!keyMatch) return ''

  let i = keyMatch.index + keyMatch[0].length
  let value = ''

  while (i < raw.length) {
    const char = raw[i]
    if (char === '"') break
    if (char === '\\') {
      const next = raw[i + 1]
      if (!next) break
      if (next === 'u') {
        const hex = raw.slice(i + 2, i + 6)
        if (hex.length < 4 || /[^0-9a-fA-F]/.test(hex)) break
        value += String.fromCharCode(parseInt(hex, 16))
        i += 6
        continue
      }
      value += decodeEscapeSequence(next)
      i += 2
      continue
    }
    value += char
    i++
  }

  return value
}

export function extractWidgetCode(rawArgsText: string): string {
  return extractJsonStringPrefix(rawArgsText, 'widget_code')
}
