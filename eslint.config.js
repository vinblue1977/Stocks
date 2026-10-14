// ESLint for the dashboard's plain browser JavaScript (web/). Runs in `make lint` and CI.
import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    files: ['web/**/*.js'],
    ignores: ['web/sw.js'], // the service worker gets its own globals below (no document or window there)
    languageOptions: { ecmaVersion: 2024, sourceType: 'module', globals: globals.browser },
    rules: {
      eqeqeq: ['error', 'smart'], // === except `x == null` (and typeof or literal comparisons)
      'no-unused-vars': ['error', { args: 'after-used', caughtErrors: 'none' }],
      'no-shadow': 'error',
      'prefer-const': 'error',
    },
  },
  { files: ['web/sw.js'], languageOptions: { ecmaVersion: 2024, sourceType: 'script', globals: globals.serviceworker } },
];
