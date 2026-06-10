import { defineConfig, presetUno } from 'unocss'
import transformerDirectives from '@unocss/transformer-directives'

export default defineConfig({
  presets: [presetUno()],
  transformers: [transformerDirectives()],
  shortcuts: {
    'text-xs': 'text-0.75rem',
    'text-sm': 'text-0.875rem',
    'text-base': 'text-1rem',
  },
})
