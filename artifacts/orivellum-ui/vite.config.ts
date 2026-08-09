import path from 'path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import runtimeErrorOverlay from '@replit/vite-plugin-runtime-error-modal';
import { VitePWA } from 'vite-plugin-pwa';
import { defineConfig } from 'vite';

const rawPort = process.env.PORT ?? '5173';
const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

// BASE_PATH is set by Replit's artifact routing; default to '/orivellum-ui/' for local dev
const basePath = process.env.BASE_PATH ?? '/orivellum-ui/';

export default defineConfig({
  base: basePath,
  plugins: [
    react(),
    tailwindcss(),
    // Runtime error overlay — dev only (not available in production bundles)
    ...(process.env.NODE_ENV !== 'production' ? [runtimeErrorOverlay()] : []),
    // PWA — active in both dev (virtual SW, no-op) and production (real SW + manifest)
    VitePWA({
      registerType: 'autoUpdate',
      // In dev mode use the virtual service worker to avoid stale-cache issues
      devOptions: { enabled: false },
      manifest: {
        name: 'Orivellum',
        short_name: 'Orivellum',
        description: 'Local-first sovereign AI workspace',
        start_url: `${basePath}`,
        scope: `${basePath}`,
        display: 'standalone',
        background_color: '#14181D',
        theme_color: '#14181D',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Return index.html for any navigate request that doesn't match a file
        navigateFallback: `${basePath}index.html`,
        // Don't intercept API calls with the service worker
        navigateFallbackDenylist: [/^\/api\//],
        // Cache all assets produced by the build
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2}'],
        // Safety margin: if the vendor chunk ever creeps past Workbox's
        // 2 MiB default, precache it anyway instead of silently skipping
        // it (which would break offline mode for the biggest file).
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
      },
    }),
    ...(process.env.NODE_ENV !== 'production' &&
    process.env.REPL_ID !== undefined
      ? [
          await import('@replit/vite-plugin-cartographer').then((m) =>
            m.cartographer({
              root: path.resolve(import.meta.dirname, '..'),
            }),
          ),
          await import('@replit/vite-plugin-dev-banner').then((m) =>
            m.devBanner(),
          ),
        ]
      : []),
  ],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
      '@assets': path.resolve(
        import.meta.dirname,
        '..',
        '..',
        'attached_assets',
      ),
    },
    dedupe: ['react', 'react-dom'],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, 'dist/public'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Split app code from dependencies so no single chunk exceeds
        // Workbox's default 2 MiB precache limit (~2.11 MB unsplit).
        //
        // IMPORTANT: keep ALL of node_modules in ONE vendor chunk. Splitting
        // react / @radix-ui / misc into separate chunks created circular
        // inter-chunk imports whose evaluation order broke in production:
        // "can't access property 'forwardRef' of undefined" — a blank page
        // on every browser. A single vendor chunk has a strictly one-way
        // dependency (index → vendor) and cannot hit that ordering bug.
        manualChunks: (id) => {
          if (id.includes('node_modules/')) {
            return 'vendor';
          }
        },
      },
    },
  },
  server: {
    port,
    strictPort: false,
    host: '0.0.0.0',
    allowedHosts: true,
    // When running outside Replit (self-hosted), set ORIVELLUM_API_URL to the
    // Python API base URL (e.g. http://127.0.0.1:8080) so that /api/* requests
    // from the Vite dev server are proxied to the backend instead of returning 404.
    proxy: process.env.ORIVELLUM_API_URL
      ? {
          '/api': {
            target: process.env.ORIVELLUM_API_URL,
            changeOrigin: true,
            rewrite: (p) => p,
          },
        }
      : undefined,
    fs: {
      strict: true,
    },
    // Windows NTFS doesn't support inotify — use polling so HMR works reliably
    watch: {
      usePolling: process.platform === "win32",
      interval: 300,
    },
  },
  preview: {
    port,
    host: '0.0.0.0',
    allowedHosts: true,
  },
});
