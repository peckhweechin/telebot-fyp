// =============================
// LIVE VIEW 
// =============================

async function loadLiveView() {
  try {
    const res = await fetch('/api/liveview');
    const data = await res.json();

    visitorsNow.textContent = data.visitors || 0;
    liveOrders.textContent = data.orders || 0;
    liveTotalSales.textContent = `SGD ${data.sales || 0}`;
    activeCarts.textContent = data.activeCarts || 0;
    checkingOut.textContent = data.checkingOut || 0;
    purchased.textContent = data.purchased || 0;

  } catch (err) {
    console.error('Live view fetch error:', err);

    // Fail safe = zero
    visitorsNow.textContent = 0;
    liveOrders.textContent = 0;
    liveTotalSales.textContent = 'SGD 0';
    activeCarts.textContent = 0;
    checkingOut.textContent = 0;
    purchased.textContent = 0;
  }
}

// Load once
loadLiveView();

// Refresh every 10s (still REAL data)
setInterval(loadLiveView, 10000);

// =============================
// MAP (SINGAPORE ONLY, REAL DATA)
// =============================

// Singapore bounding box
const singaporeBounds = [
  [1.15, 103.6],
  [1.48, 104.1]
];

const map = L.map('liveMap', {
  center: [1.3521, 103.8198],
  zoom: 12,
  minZoom: 11,
  maxZoom: 16,
  maxBounds: singaporeBounds,
  maxBoundsViscosity: 1.0
});

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap'
}).addTo(map);

// Fetch REAL coordinates from backend
fetch('/api/liveview/map')
  .then(res => res.json())
  .then(points => {
    points.forEach(p => {
      L.circleMarker([p.latitude, p.longitude], {
        radius: 6,
        color: '#2563eb',
        fillOpacity: 0.8
      }).addTo(map);
    });
  })
  .catch(err => {
    console.error('Map data error:', err);
  });
