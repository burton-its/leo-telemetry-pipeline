import { defineConfig } from "vite";
import { viteStaticCopy } from "vite-plugin-static-copy";

// Set folder for CesiumJS files.
const cesiumSource = "node_modules/cesium/Build/Cesium";
const cesiumBaseUrl = "cesiumStatic";

export default defineConfig({

  define: {

    // Get CesiumJS assets/workers.
    CESIUM_BASE_URL: JSON.stringify(`/${cesiumBaseUrl}`),
  },

  // LEO Telemetry API:
  server: {
    proxy: {
        "/api": "http://127.0.0.1:8000",
    },
  },

  plugins: [
    viteStaticCopy({
      targets: [
        { src: `${cesiumSource}/ThirdParty`, dest: cesiumBaseUrl },
        { src: `${cesiumSource}/Workers`, dest: cesiumBaseUrl },
        { src: `${cesiumSource}/Assets`, dest: cesiumBaseUrl },
        { src: `${cesiumSource}/Widgets`, dest: cesiumBaseUrl },
      ],
    }),
  ],
});
