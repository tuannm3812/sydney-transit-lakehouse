// Copy the map libraries into static/replay-map-map/vendor/, so the replay
// page is served from the dashboard itself, with no CDN at run time.
import { copyFileSync, mkdirSync } from 'node:fs';

const out = 'static/replay-map-map/vendor';
mkdirSync(out, { recursive: true });
for (const [from, to] of [
  ['node_modules/deck.gl/dist.min.js', 'deck.gl.min.js'],
  ['node_modules/maplibre-gl/dist/maplibre-gl.js', 'maplibre-gl.js'],
  ['node_modules/maplibre-gl/dist/maplibre-gl.css', 'maplibre-gl.css'],
]) {
  copyFileSync(from, `${out}/${to}`);
}
console.log(`map libraries copied to ${out}`);
