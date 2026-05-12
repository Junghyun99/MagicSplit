# Palette's Journal

## Critical Learnings
- **Vanilla JS Templates**: This project uses a custom Vanilla JS string interpolation setup to build DOM elements for the dashboard (e.g. `docs/js/main.js`). Must ensure HTML strings are semantically correct and clean.
- **Accessibility**: Users and screen readers need explicit labels for profit/loss instead of relying purely on color classes like `pct-positive`.
- **Formatting**: Small details like spacing between numbers and units (e.g., "5 shares" instead of "5shares") significantly affect readability.
- **Micro-UX Goal**: Add visual indicators (`▲`/`▼`) with proper ARIA labels and hide them from screen readers to provide clear accessible financial feedback.
