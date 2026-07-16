/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Onest Variable', 'Avenir Next', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['IBM Plex Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        ink: {
          DEFAULT: '#151a1f',
          soft: '#242b32',
        },
        signal: {
          50: '#fff4ef', 100: '#ffe3d6', 200: '#ffc5ad', 300: '#ff9d78',
          400: '#f87448', 500: '#ed552b', 600: '#d43d17', 700: '#af2e12',
          800: '#8e2a18', 900: '#752719', 950: '#401109',
        },
      },
      animation: {
        'slide-in-right': 'slideInRight 0.22s cubic-bezier(0.23, 1, 0.32, 1)',
      },
      keyframes: {
        slideInRight: {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
      },
    },
  },
  plugins: [],
}
