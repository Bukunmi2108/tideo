import { defineConfig } from "vitest/config"

export default defineConfig({
  build: {
    rollupOptions: {
      // paths are relative to this config file (the project root)
      input: {
        main:    "index.html",
        job:     "job.html",
        history: "history.html",
      },
    },
  },
  test: {
    environment: "happy-dom",
  },
})
