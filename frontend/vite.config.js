import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Entwicklung: Vite auf :5173, API-Aufrufe gehen an FastAPI auf :8000. Das Build landet in dist/ und wird vom Backend ausgeliefert.
export default defineConfig({ plugins: [react()], server: { proxy: { '/api': 'http://localhost:8000' } } });
