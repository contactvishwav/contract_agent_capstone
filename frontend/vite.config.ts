import path from "path"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { viteStaticCopy } from 'vite-plugin-static-copy'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    viteStaticCopy({
      targets: [
        {
          src: 'node_modules/pdfjs-dist/standard_fonts',
          dest: 'pdfjs',
        },
        {
          src: 'node_modules/pdfjs-dist/cmaps',
          dest: 'pdfjs',
        },
      ],
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    restoreMocks: true,
    // e2e/ holds real Playwright specs (playwright.config.ts, run via
    // `npm run test:e2e` against a live dev stack) - a different test
    // runner with its own test()/expect(), not part of this jsdom suite.
    exclude: ['node_modules/**', 'e2e/**'],
  },
})
