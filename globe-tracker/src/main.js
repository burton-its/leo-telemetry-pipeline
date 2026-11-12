import {
  CzmlDataSource,
  Ion,
  ImageryLayer,
  IonWorldImageryStyle,
  Terrain,
  TimeInterval,
  Transforms,
  Viewer,
} from "cesium";

import "cesium/Build/Cesium/Widgets/widgets.css";
import "./style.css";


// Prep query for LEOs.
// TODO: Create add/remove functions.
const NORAD_IDS = [1, 2, 5, 114, 20580, 7530, 25544, 48274, 50463, 44714, 100000];
const QUERY = NORAD_IDS.map((id) => `ids=${id}`).join("&");

// Call LEO-Telemetry-API:
// ISS Ex: http://localhost:5173/api/get-tle-telemetry?&ids=25544
const CZML_URL = `/api/get-czml?${QUERY}`;
const TELEMETRY_URL = `/api/get-tle-telemetry?${QUERY}`;

// Handle page updates:
const UPDATE_CZML = 30 * 60 * 1000;  // 30 minutes
const UPDATE_TELEMETRY = 1000;  // 1 second


// Retrieve token from .env:
const token = import.meta.env.VITE_CESIUM_ION_TOKEN;

if (!token) {
  throw new Error("Missing VITE_CESIUM_ION_TOKEN in .env file.");
}
Ion.defaultAccessToken = token;


// GLOBE RENDERING
const viewer = new Viewer("cesiumContainer", {
  terrain: Terrain.fromWorldTerrain({
    requestVertexNormals: true,
    requestWaterMask: true,
  }),
  baseLayer: ImageryLayer.fromWorldImagery({
    style: IonWorldImageryStyle.AERIAL_WITH_LABELS,
  }),
  animation: false,
  timeline: false,
  geocoder:  false,
});

viewer.scene.globe.enableLighting = true;
viewer.clock.shouldAnimate = true;  // Run clock despite hidden animation widget.


// Prep LEOs:
const satellites = new CzmlDataSource();
viewer.dataSources.add(satellites);


// Render LEOs:
const reload = async () => {

  try {

    // Load CZMLs:
    await satellites.load(CZML_URL);
    await updateTelemetry();

    // Fix satellite position using ICRF:
    // ICRF: International Celestial Reference Frame.
    // Reference: https://cesium.com/learn/cesiumjs/ref-doc/Transforms.html
    const {startTime: start, stopTime: stop} = satellites.clock;
    await Transforms.preloadIcrfFixed(new TimeInterval({start, stop}));
    
  } catch (err) {
    console.error("Failed to load CZML:", err);
  }
};


// Update telemetry in description box: 
async function updateTelemetry() {
  
  try {

    // Get telemetry from LEO Telemetry API:
    const res = await fetch(TELEMETRY_URL);
    if (!res.ok) return;

    // Pullout telemetry for display in LEO description box:
    for (const telemetry of await res.json()) {
      const sat = satellites.entities.getById(`sat-${telemetry.norad_id}`);
      if (!sat) continue;

      sat.description =
        `<b>NORAD:</b> ${telemetry.norad_id}<br>` +
        `<b>Int'l designator:</b> ${telemetry.intl_designator}<br>` +
        `<b>Inclination:</b> ${telemetry.inclination_deg.toFixed(2)}°<br>` +
        `<b>TLE epoch:</b> ${telemetry.utc_epoch.slice(0, 19).replace("T", " ")} UTC<br>` +
        `<b>TLE age:</b> ${telemetry.tle_age_hours.toFixed(1)} h<br>` +
        `<b>Lat:</b> ${telemetry.latitude_deg.toFixed(2)}°<br>` +
        `<b>Lon:</b> ${telemetry.longitude_deg.toFixed(2)}°<br>` +
        `<b>Alt:</b> ${telemetry.altitude_km.toFixed(1)} km<br>` +
        `<b>Ground speed:</b> ${telemetry.speed_ground_kms.toFixed(2)} km/s`;
    }

  } catch (err) {
    console.error("Telemetry update failed:", err);
  }
}


// Reload -> then: prevents double query to CelesTrak
reload().then(() => setInterval(updateTelemetry, UPDATE_TELEMETRY));
setInterval(reload, UPDATE_CZML);
