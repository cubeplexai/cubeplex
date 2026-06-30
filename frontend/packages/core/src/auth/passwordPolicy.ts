export type PasswordPolicy = 'low' | 'high'

export interface PasswordValidationResult {
  ok: boolean
  errors: string[]
}

function isSymbol(ch: string): boolean {
  const code = ch.charCodeAt(0)
  return code >= 33 && code <= 126 && !/[a-z0-9]/i.test(ch)
}

export function validatePassword(
  password: string,
  policy: PasswordPolicy,
): PasswordValidationResult {
  const errors: string[] = []
  const minLen = policy === 'high' ? 10 : 8
  if (password.length < minLen) errors.push('password_too_short')
  if (policy === 'high') {
    if (!/[A-Z]/.test(password)) errors.push('password_no_uppercase')
    if (!/[a-z]/.test(password)) errors.push('password_no_lowercase')
    if (!/[0-9]/.test(password)) errors.push('password_no_digit')
    if (![...password].some(isSymbol)) errors.push('password_no_symbol')
  }
  return { ok: errors.length === 0, errors }
}
