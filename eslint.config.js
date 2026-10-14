// ESLint for the dashboard's plain browser JavaScript (web/). Runs in `make lint` and CI.
import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
  {
    files: ['web/**/*.js'],
    languageOptions: { ecmaVersion: 2024, sourceType: 'module', globals: globals.browser },
    rules: {
      eqeqeq: ['error', 'smart'], // == only for null checks
      'no-unused-vars': ['error', { args: 'after-used', caughtErrors: 'none' }],
      'no-shadow': 'error',
      'prefer-const': 'error',
    },
  },
  { files: ['web/sw.js'], languageOptions: { sourceType: 'script', globals: globals.serviceworker } },
];
