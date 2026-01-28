document.addEventListener("DOMContentLoaded", () => {
  /* ================= DROPDOWN TOGGLE ================= */
  document.querySelectorAll(".nav-dropdown-toggle").forEach((toggle) => {
    toggle.addEventListener("click", (e) => {
      e.preventDefault(); // important if toggle is <a href="#">
      const parent = toggle.closest(".nav-dropdown");
      if (!parent) return;

      parent.classList.toggle("open");
    });
  });

  /* ================= NOTIFICATIONS (REAL DATA ONLY) ================= */
  const notifList = document.getElementById("notificationsList");
  const notifCount = document.getElementById("notifCount");

  async function loadNotifications() {
    if (!notifList || !notifCount) return;

    try {
      const res = await fetch("/api/dashboard/out-of-stock");
      const data = await res.json();

      notifList.innerHTML = "";

      // No sold out products
      if (!data || data.length === 0) {
        notifCount.style.display = "none";
        notifList.innerHTML = `<div class="notification-item">No notifications 🎉</div>`;
        return;
      }

      // Badge count
      notifCount.style.display = "inline-block";
      notifCount.textContent = data.length;

      // Render sold out list
      data.forEach((p) => {
        const div = document.createElement("div");
        div.className = "notification-item";
        div.textContent = `⚠️ Sold out: ${p.name}`;
        notifList.appendChild(div);
      });
    } catch (err) {
      console.error("Failed to load notifications:", err);
      notifCount.style.display = "none";
      notifList.innerHTML = `<div class="notification-item">Unable to load notifications</div>`;
    }
  }

  loadNotifications();
});
