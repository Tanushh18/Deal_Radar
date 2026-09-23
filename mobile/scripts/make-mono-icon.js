// Renders the DealRadar radar mark (three concentric rings + centre dot, as in
// static/assets/icons/icon-512.png) as white-on-transparent PNGs. Android
// notification small icons and themed (monochrome) launcher icons must be
// alpha-only silhouettes; the full-colour icon renders as a white square.
// Usage: node scripts/make-mono-icon.js   (no dependencies)
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

/** scale: fraction of the canvas the mark's outer ring spans. */
function render(size, scale) {
  const c = size / 2;
  // Geometry from icon-512: outer ring r≈157 (stroke 16), mid r≈92 (stroke 16), dot r≈30
  const k = (size * scale) / (2 * 165);
  const rings = [[157 * k, 8 * k], [92 * k, 8 * k]];
  const dot = 30 * k;
  const SS = 4; // supersampling
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      let hit = 0;
      for (let sy = 0; sy < SS; sy++) for (let sx = 0; sx < SS; sx++) {
        const d = Math.hypot(x + (sx + 0.5) / SS - c, y + (sy + 0.5) / SS - c);
        if (d <= dot || rings.some(([r, hw]) => Math.abs(d - r) <= hw)) hit++;
      }
      const o = y * (size * 4 + 1) + 1 + x * 4;
      raw[o] = raw[o + 1] = raw[o + 2] = 255;
      raw[o + 3] = Math.round((hit / (SS * SS)) * 255);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ]);
}

const out = path.join(__dirname, '..', 'assets');
fs.writeFileSync(path.join(out, 'notification-icon.png'), render(96, 0.86));
fs.writeFileSync(path.join(out, 'android-icon-monochrome.png'), render(432, 0.62));
console.log('wrote notification-icon.png, android-icon-monochrome.png');
