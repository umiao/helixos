import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import 'prism-themes/themes/prism-one-dark.min.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
