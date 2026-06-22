import { defineConfig, presetUno } from 'unocss'
import transformerDirectives from '@unocss/transformer-directives'

export default defineConfig({
  presets: [presetUno()],
  transformers: [transformerDirectives()],
  shortcuts: {
    // ── Typography ──
    'text-xs': 'text-0.75rem',
    'text-sm': 'text-0.875rem',
    'text-base': 'text-1rem',
    'text-lg': 'text-1.125rem',
    'text-xl': 'text-1.25rem',
    'text-2xl': 'text-1.5rem',

    // ── Text colors (semantic) ──
    'text-muted': 'text-[var(--color-text-muted)]',
    'text-primary': 'text-[var(--color-primary)]',

    // ── Surfaces ──
    'card': 'bg-[var(--color-surface)] rounded-[var(--radius-lg)] border border-[var(--color-border)]',
    'card-shadow': 'card shadow-[var(--shadow)]',

    // ── Flex helpers ──
    'flex-center': 'flex items-center justify-center',
    'flex-between': 'flex items-center justify-between',
  },
})
