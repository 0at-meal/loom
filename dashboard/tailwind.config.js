/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ground: '#0F1115',
        panel: '#16181D',
        'panel-inset': '#0F1115',
        border: '#2A2D34',
        hairline: '#2A2D34',
        primary: '#E4E6EB',
        secondary: '#8B8F98',
        accent: '#5B8DEF',
        'accent-2': '#C084FC',
        'route-gamma': '#7C808A',
        alert: '#E5484D',
      },
      fontFamily: {
        headline: ['"Space Grotesk"', 'sans-serif'],
        mono: ['"Space Mono"', 'monospace'],
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
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
