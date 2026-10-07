import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

if (
  window.matchMedia('(display-mode: standalone)').matches ||
  navigator.standalone
)
  document.documentElement.classList.add('standalone');
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
if (
  import.meta.env.PROD &&
  'serviceWorker' in navigator &&
  window.isSecureContext
)
  navigator.serviceWorker.register('/sw.js').catch(() => {});
