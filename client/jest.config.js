const nextJest = require("next/jest");

const createJestConfig = nextJest({
  dir: "./",
});

const customJestConfig = {
  testEnvironment: "jest-environment-jsdom",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  testPathIgnorePatterns: [
    "<rootDir>/.next/",
    "<rootDir>/node_modules/",
    "<rootDir>/e2e/",
  ],
  collectCoverageFrom: [
    "app/components/**/*.{ts,tsx}",
    "app/lib/**/*.{ts,tsx}",
    "!app/components/**/*.d.ts",
    "!app/components/**/__tests__/**",
  ],
};

// next/jest owns `transformIgnorePatterns` and re-ignores all of node_modules
// except geist/next internals. d3 (and its d3-* sub-packages + ESM transitive
// deps) ship pure ESM, so they stay un-transformed and any test that loads a
// d3-using chart component fails with "SyntaxError: Unexpected token 'export'"
// (e.g. PlayerSearch.test.tsx). Because next/jest *appends* user patterns and a
// file is skipped if it matches ANY pattern, a user override in customJestConfig
// is ineffective — next/jest's own pattern still matches d3. So resolve the
// config and replace the array, keeping next/jest's geist/next un-ignores and
// adding the d3 family.
module.exports = async () => {
  const config = await createJestConfig(customJestConfig)();
  config.transformIgnorePatterns = [
    "/node_modules/(?!(geist|next/dist/client|next/dist/shared/lib|next/src/client|next/src/shared/lib|d3|d3-[a-z-]+|internmap|delaunator|robust-predicates)/)",
    "^.+\\.module\\.(css|sass|scss)$",
  ];
  return config;
};
