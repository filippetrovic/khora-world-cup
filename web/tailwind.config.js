/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // World Cup 2026 palette: deep pitch green + warm gold accent.
        pitch: {
          50: "#eefcf3",
          100: "#d6f7e2",
          200: "#aeecc6",
          300: "#79dca3",
          400: "#41c279",
          500: "#1ba65c",
          600: "#0f8549",
          700: "#0d693c",
          800: "#0f5333",
          900: "#0d442b",
          950: "#042617",
        },
        gold: {
          50: "#fdf9ec",
          100: "#faefc9",
          200: "#f4dd8f",
          300: "#eec455",
          400: "#e9ab2d",
          500: "#d98e1c",
          600: "#bd6c16",
          700: "#9d4d16",
          800: "#803d18",
          900: "#6a3317",
          950: "#3d1909",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "grow-x": {
          "0%": { transform: "scaleX(0)" },
          "100%": { transform: "scaleX(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s cubic-bezier(0.16, 1, 0.3, 1) both",
        "fade-in": "fade-in 0.3s ease both",
        "grow-x": "grow-x 0.7s cubic-bezier(0.16, 1, 0.3, 1) both",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};
