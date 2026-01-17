document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('userModal');
  const modalCloseBtns = document.querySelectorAll('.modal-close');
  const userDetails = document.getElementById('userDetails');
  const searchInput = document.getElementById('searchUsers');

  // ================= OPEN USER MODAL =================
  document.addEventListener('click', async (e) => {
    if (e.target.classList.contains('view-user')) {
      const userId = e.target.dataset.id;
      modal.style.display = 'flex';
      userDetails.innerHTML = '<p>Loading...</p>';

      try {
        const res = await fetch(`/api/users/${userId}`);
        const user = await res.json();

        userDetails.innerHTML = `
          <p><strong>User ID:</strong> ${user.id}</p>
          <p><strong>Name:</strong> ${user.name}</p>
          <p><strong>Email:</strong> ${user.email}</p>
          <p><strong>Role:</strong> ${user.role}</p>
          <p><strong>Joined:</strong> ${formatDate(user.created_at)}</p>
          <p><strong>Last Updated:</strong> ${formatDate(user.updated_at)}</p>
        `;
      } catch (err) {
        userDetails.innerHTML = '<p>Error loading user details</p>';
      }
    }
  });

  // ================= CLOSE MODAL =================
  modalCloseBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      modal.style.display = 'none';
    });
  });

  window.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.style.display = 'none';
    }
  });

  // ================= SEARCH USERS =================
  searchInput.addEventListener('input', () => {
    const filter = searchInput.value.toLowerCase();
    const rows = document.querySelectorAll('#usersTable tr');

    rows.forEach(row => {
      const text = row.innerText.toLowerCase();
      row.style.display = text.includes(filter) ? '' : 'none';
    });
  });

  // ================= DATE FORMAT =================
  function formatDate(dateStr) {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString('en-SG', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  }
});
