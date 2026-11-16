/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // Mirrors the CSS variables in src/index.css
      colors: {
        ground: '#0A0A09',
        pane: '#111110',
        well: '#0A0A09',
        rule: '#2B2924',
        text: '#E8E2D0',
        dim: '#8A8473',
        amber: '#FFA028',
        cyan: '#5CC8E0',
        cream: '#D8D2BE',
        alert: '#FF4A3D',
        ok: '#4FD18B',
      },
      fontFamily: {
        headline: ['"IBM Plex Sans Condensed"', '"Arial Narrow"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', '"Cascadia Mono"', 'Consolas', 'monospace'],
        sans: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '0px',
        sm: '2px',
      },
      boxShadow: {
        none: 'none',
      },
    },
  },
  plugins: [],
};
