document.addEventListener('DOMContentLoaded', () => {
  fetch('/api/dashboard/summary')
    .then(res => res.json())
    .then(data => {
      document.getElementById('totalOrders').innerText = data.totalOrders;
      document.getElementById('totalRevenue').innerText = `$${data.totalRevenue}`;
      document.getElementById('totalProducts').innerText = data.totalProducts;
      document.getElementById('totalCategories').innerText = data.totalCategories;
      document.getElementById('productViews').innerText = data.productViews;
      document.getElementById('conversionRate').innerText = data.conversionRate + '%';
    });

  fetch('/api/dashboard/low-stock')
    .then(res => res.json())
    .then(products => {
      const list = document.getElementById('lowStockList');
      list.innerHTML = '';

      if (products.length === 0) {
        list.innerHTML = '<p>No low stock items 🎉</p>';
        return;
      }

      products.forEach(p => {
        list.innerHTML += `<p>${p.name} (Stock: ${p.stock})</p>`;
      });
    });

    fetch('/api/dashboard/out-of-stock')
  .then(res => res.json())
  .then(products => {
    const alertBox = document.getElementById('outOfStockAlert');
    const message = document.getElementById('outOfStockMessage');

    if (products.length === 0) {
      // No issue → hide alert
      alertBox.style.display = 'none';
      return;
    }

    // Build real message
    const productNames = products.map(p => p.name).join(', ');

    message.textContent = `Out of stock: ${productNames}`;
    alertBox.style.display = 'flex';
  });

  fetch('/api/dashboard/recent-orders')
    .then(res => res.json())
    .then(orders => {
      const table = document.getElementById('recentOrdersTable');
      table.innerHTML = '';

      orders.forEach(o => {
        table.innerHTML += `
          <tr>
            <td>#${o.id}</td>
            <td>${o.username || 'Telegram User'}</td>
            <td>${o.items}</td>
            <td>$${o.total_amount}</td>
            <td>${o.status}</td>
            <td>${new Date(o.created_at).toLocaleDateString()}</td>
          </tr>
        `;
      });
    });
});
