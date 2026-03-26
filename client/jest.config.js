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

module.exports = createJestConfig(customJestConfig);
