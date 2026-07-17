'use client'

import { useCallback, useEffect, useRef } from 'react'

interface OtpInputProps {
  value: string
  onChange: (v: string) => void
  length?: number
  disabled?: boolean
}

export function OtpInput({ value, onChange, length = 6, disabled = false }: OtpInputProps) {
  const inputsRef = useRef<(HTMLInputElement | null)[]>([])

  const focusCell = useCallback(
    (index: number) => {
      const clamped = Math.max(0, Math.min(length - 1, index))
      inputsRef.current[clamped]?.focus()
    },
    [length],
  )

  const handleChange = (index: number, char: string) => {
    // Only accept digits
    if (!/^\d$/.test(char)) return
    const digits = value.split('')
    digits[index] = char
    const next = digits.join('').slice(0, length)
    onChange(next)
    // Auto-advance to next cell
    if (index < length - 1) focusCell(index + 1)
  }

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace') {
      e.preventDefault()
      const digits = value.split('')
      if (digits[index]) {
        // Clear current cell
        digits[index] = ''
        onChange(digits.join(''))
      } else if (index > 0) {
        // Move to previous cell
        focusCell(index - 1)
      }
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      if (index > 0) focusCell(index - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      if (index < length - 1) focusCell(index + 1)
    }
  }

  const handlePaste = (e: React.ClipboardEvent) => {
    e.preventDefault()
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, length)
    if (!pasted) return
    onChange(pasted)
    // Focus the cell after the last pasted digit
    focusCell(Math.min(pasted.length, length - 1))
  }

  // Keep refs array sized
  useEffect(() => {
    inputsRef.current = inputsRef.current.slice(0, length)
  }, [length])

  const cells = Array.from({ length }, (_, i) => {
    const digit = value[i] ?? ''
    return (
      <input
        key={i}
        ref={(el) => {
          inputsRef.current[i] = el
        }}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={1}
        value={digit}
        disabled={disabled}
        aria-label={`Digit ${i + 1}`}
        className="h-12 w-10 rounded-md border border-border bg-background text-center text-lg font-semibold
                   disabled:opacity-50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        onChange={(e) => handleChange(i, e.target.value)}
        onKeyDown={(e) => handleKeyDown(i, e)}
        onPaste={i === 0 ? handlePaste : undefined}
      />
    )
  })

  return (
    <div className="flex items-center justify-center gap-2" dir="ltr">
      {cells}
    </div>
  )
}
