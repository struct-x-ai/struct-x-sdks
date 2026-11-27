import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    // Each test file gets a fresh module graph, so process.env
    // mutations in one file can't leak into another. Slightly slower
    // but safer for the env-var fallback tests in client.test.ts.
    isolate: true,
    // Surface deprecation warnings as test failures so we catch
    // anything the SDK is doing wrong against newer Node versions
    // before customers do.
    onConsoleLog(log) {
      if (log.includes('DeprecationWarning')) return true;
      return false;
    },
  },
});
