// The site's public settings (config.json), loaded once at start-up by app.js.
let config = {};

export function setConfig(value) { config = value || {}; }
export const getConfig = () => config;
