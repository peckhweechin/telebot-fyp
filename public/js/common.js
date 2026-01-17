document.querySelectorAll('.nav-dropdown-toggle').forEach(toggle => {
  toggle.addEventListener('click', () => {
    const parent = toggle.closest('.nav-dropdown');
    parent.classList.toggle('open');
  });
});

const notifList = document.getElementById('notificationsList');
const notifCount = document.getElementById('notifCount');

if (notifList && notifCount) {
  const notifications = [
    '🛒 New order received',
    '⚠️ Low stock: Phone Case',
    '👤 New customer signed up'
  ];

  notifList.innerHTML = '';

  notifications.forEach(text => {
    const div = document.createElement('div');
    div.className = 'notification-item';
    div.textContent = text;
    notifList.appendChild(div);
  });

  // 🔥 AUTO COUNT (no manual setting)
  notifCount.textContent = notifications.length;

  // Optional: hide badge if zero
  if (notifications.length === 0) {
    notifCount.style.display = 'none';
  }
}
