import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

const config = [
  ...nextCoreWebVitals,
  {
    ignores: [".next/**", "coverage/**", "node_modules/**"],
    rules: {
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/unsupported-syntax": "off",
    },
  },
];

export default config;
