# Palette's Journal - Critical Learnings

- **Vanilla JS**: The frontend for this dashboard is written in vanilla JavaScript (no React/Vue). Stick to standard DOM manipulation.
- **Data Source**: It relies on pre-generated `status.json` and uses plain JavaScript `fetch`.
- **Accessibility & UX**: Added ARIA labels (`aria-label="Profit of X%"`) to profit/loss indicators and visual markers like `▲`/`▼` to improve a11y and clarity beyond just colors. Always test colorblind-safe concepts or use extra semantic visual cues.
- **Formatting**: Ensured proper spacing in quantities and prices for better readability.
