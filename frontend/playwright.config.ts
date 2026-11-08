import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 180000,
  workers: 1,
  use: {
    baseURL: process.env.BASE_URL || "http://127.0.0.1:8000",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: { "X-Balance-Request": "1" },
  },
  outputDir: "../artifacts/playwright",
  reporter: "list",
});
