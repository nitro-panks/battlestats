import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

const config = [
  {
    ignores: [".next/**", "coverage/**", "**/coverage/**", "node_modules/**"],
  },
  ...nextCoreWebVitals,
  {
    rules: {
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/unsupported-syntax": "off",
    },
  },
];

export default config;
