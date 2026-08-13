import { createRoot } from 'react-dom/client';

// Self-hosted font subsets — bundled at build time, zero third-party requests.
// Retained faces only: wordmark stencil, condensed display plates, data mono.
// Interface copy uses the platform font stack (see gd-tokens.css --gd-body).
import '@fontsource/allerta-stencil/latin-400.css';
import '@fontsource/saira-condensed/latin-500.css';
import '@fontsource/saira-condensed/latin-600.css';
import '@fontsource/saira-condensed/latin-700.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-400-italic.css';
import '@fontsource/ibm-plex-mono/latin-600.css';

import App from './App';

import './index.css';
import { initTheme } from './lib/theme';

initTheme();

createRoot(document.getElementById('root')!).render(<App />);
