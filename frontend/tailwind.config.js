/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b1020",
        panel: "#141c31",
        accent: "#ff7a18",
        accentSoft: "#ffb36f"
      },
      boxShadow: {
        card: "0 20px 45px rgba(0, 0, 0, 0.35)"
      }
    }
  },
  plugins: [],
};
