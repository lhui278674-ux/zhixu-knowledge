import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode})=>({plugins:[react()],base:mode==='public-demo'?'./':'/',define:mode==='public-demo'?{'import.meta.env.VITE_PUBLIC_DEMO':JSON.stringify('true')}:{},server:{proxy:{'/api':'http://127.0.0.1:8000'}}}));
