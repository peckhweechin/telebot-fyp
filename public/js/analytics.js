document.addEventListener('DOMContentLoaded', () => {
  const timeSelect = document.getElementById('timePeriod');

  let salesChart, sessionsChart, conversionChart;

  /* ------------------------------
     Helpers
  ------------------------------ */

  function generateLabels(days) {
    const labels = [];
    const today = new Date();

    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      labels.push(
        d.toLocaleDateString('en-SG', { day: '2-digit', month: 'short' })
      );
    }
    return labels;
  }

  function destroyCharts() {
    salesChart?.destroy();
    sessionsChart?.destroy();
    conversionChart?.destroy();
  }

  /* ------------------------------
     Charts (EMPTY / REAL STATE)
     Backend will replace data later
  ------------------------------ */

  function renderCharts(labels) {
    const emptyData = labels.map(() => 0);

    destroyCharts();

    // -------- Sales over time --------
    salesChart = new Chart(
      document.getElementById('salesOverTimeChart'),
      {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Sales',
            data: emptyData,
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37,99,235,0.12)',
            borderWidth: 2,
            pointRadius: 2,
            tension: 0.3,
            fill: true
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              min: 0,
              suggestedMax: 1
            }
          },
          plugins: {
            legend: { display: false }
          }
        }
      }
    );

    // -------- Sessions over time --------
    sessionsChart = new Chart(
      document.getElementById('sessionsChart'),
      {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label: 'Sessions',
            data: emptyData,
            backgroundColor: '#10b981'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              min: 0,
              suggestedMax: 1
            }
          },
          plugins: {
            legend: { display: false }
          }
        }
      }
    );

    // -------- Conversion rate --------
    conversionChart = new Chart(
      document.getElementById('conversionChart'),
      {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Conversion Rate',
            data: emptyData,
            borderColor: '#f59e0b',
            borderWidth: 2,
            pointRadius: 2,
            tension: 0.3
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              min: 0,
              suggestedMax: 1,
              ticks: {
                callback: v => v + '%'
              }
            }
          },
          plugins: {
            legend: { display: false }
          }
        }
      }
    );
  }

  /* ------------------------------
     REAL SUMMARY (from backend)
     Frontend DOES NOT decide values
  ------------------------------ */

  async function loadSummary() {
    try {
      const res = await fetch('/api/analytics/summary');
      const data = await res.json();

      document.getElementById('analyticsRevenue').textContent =
        `SGD ${data.revenue}`;

      document.getElementById('analyticsTotalOrders').textContent =
        data.orders;

      document.getElementById('ordersFulfilled').textContent =
        data.fulfilled;

      document.getElementById('returningRate').textContent =
        `${data.returningRate}%`;

    } catch (err) {
      console.error('Analytics summary error:', err);
    }
  }

  /* ------------------------------
     Main render
  ------------------------------ */

  function renderAnalytics() {
    const days =
      timeSelect.value === 'today' ? 1 : Number(timeSelect.value);

    const labels = generateLabels(days);

    // Charts render even with no data
    renderCharts(labels);

    // Summary comes ONLY from backend
    loadSummary();
  }

  // Initial load
  renderAnalytics();

  // Reload on dropdown change
  timeSelect.addEventListener('change', renderAnalytics);
});
