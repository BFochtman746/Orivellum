import path from 'path';
import { execSync } from 'child_process';
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

// Build identifier — git short SHA + build date. Surfaced in the UI (System
// page + update prompt) so the running version is always visible.
function buildId(): string {
  let sha = 'dev';
  try {
    sha = execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim();
  } catch {
    /* no git in some environments — fall back to 'dev' */
  }
  const d = new Date().toISOString().slice(0, 10);
  return `${sha} · ${d}`;
}

export default defineConfig({
  base: basePath,
  plugins: [
    react(),
    tailwindcss(),
    // Runtime error overlay — dev only (not available in production bundles)
    ...(process.env.NODE_ENV !== 'production' ? [runtimeErrorOverlay()] : []),
    // PWA — active in both dev (virtual SW, no-op) and production (real SW + manifest)
    VitePWA({
      // Prompt-based update model (WP5): the new service worker WAITS until
      // the user explicitly accepts the update (see src/lib/pwa-update.ts).
      // A reload can therefore never interrupt an unsaved draft, stream,
      // upload, or operation.
      registerType: 'prompt',
      // Registration is done manually in src/lib/pwa-update.ts via
      // virtual:pwa-register so the app can surface update-ready state.
      injectRegister: false,
      // In dev mode use the virtual service worker to avoid stale-cache issues
      devOptions: { enabled: false },
      manifest: {
        name: 'Orivellum',
        short_name: 'Orivellum',
        description: 'Local-first sovereign AI workspace',
        start_url: `${basePath}`,
        scope: `${basePath}`,
        display: 'standalone',
        background_color: '#F4F1E9',
        theme_color: '#F4F1E9',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Deep-link handler for browser notifications (notificationclick) —
        // lives in public/ so it ships next to the generated service worker.
        importScripts: ['sw-notifications.js'],
        // Return index.html for any navigate request that doesn't match a file
        navigateFallback: `${basePath}index.html`,
        // Don't intercept API calls with the service worker
        navigateFallbackDenylist: [/^\/api\//],
        // Precache ONLY the shell + critical assets (WP5): the HTML document,
        // the entry bundle (assets/entry-*), stylesheets, woff2 fonts and
        // icons. Route chunks are NOT precached — they cache on first use via
        // the runtime rule below, so installing the PWA never downloads the
        // whole app.
        globPatterns: [
          'index.html',
          'assets/entry-*.js',
          'assets/*.css',
          'assets/*.woff2',
          '*.{ico,png,svg}',
        ],
        runtimeCaching: [
          {
            // Route chunks + shared chunks: hashed filenames are immutable,
            // so cache-first is always correct. Cached the first time a
            // destination is visited.
            urlPattern: /\/assets\/.*\.js$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'route-chunks',
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            // Legacy-format fonts (woff) requested by older engines.
            urlPattern: /\/assets\/.*\.(woff|ttf)$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'fonts-fallback',
              expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
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
  define: {
    __BUILD_ID__: JSON.stringify(buildId()),
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, 'dist/public'),
    emptyOutDir: true,
    // Manifest powers scripts/check_bundle_budgets.mjs — it maps the entry to
    // its static-import closure so CI can measure Home's true initial payload.
    manifest: true,
    rollupOptions: {
      output: {
        // Rollup DEFAULT chunking (WP5). Route modules are dynamic imports
        // (React.lazy in App.tsx), so Rollup emits one chunk per destination
        // plus correctly-ordered shared chunks. The old all-node_modules-in-
        // one-vendor-chunk rule is gone — it forced Home to download every
        // dependency. NOTE: do NOT hand-split node_modules into named manual
        // chunks (react/radix/misc); that previously created circular
        // inter-chunk evaluation orders ("can't access 'forwardRef' of
        // undefined"). Default chunking computes a correct acyclic order.
        //
        // entry-* prefix lets the service worker precache exactly the shell
        // bundle (see VitePWA globPatterns) while route chunks cache on use.
        entryFileNames: 'assets/entry-[name]-[hash].js',
        chunkFileNames: 'assets/chunk-[name]-[hash].js',
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
