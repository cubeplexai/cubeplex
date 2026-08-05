/**
 * Shared composer + message-column width so left/right edges stay aligned.
 * Base was Tailwind `max-w-3xl` (48rem); +20% ≈ 57.6rem.
 */
export const CHAT_COLUMN_MAX_CLASS = 'max-w-[57.6rem]'

/** Full-width column capped at {@link CHAT_COLUMN_MAX_CLASS}, horizontally centered. */
export const CHAT_COLUMN_CLASS = `w-full ${CHAT_COLUMN_MAX_CLASS} mx-auto`

/**
 * Assistant / left-rail content max width within the chat column.
 * Keeps model replies from stretching edge-to-edge while staying left-aligned.
 */
export const ASSISTANT_CONTENT_MAX_CLASS = 'max-w-[75%]'
