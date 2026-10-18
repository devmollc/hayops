import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/main.js",
      name: "HelpdeskWidget",
      formats: ["iife"],
      fileName: () => "helpdesk-widget.js",
    },
    outDir: "dist",
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
