document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('searchSegments');
  const tableBody = document.getElementById('segmentsTableBody');

  // ================= SEARCH SEGMENTS =================
  if (searchInput && tableBody) {
    searchInput.addEventListener('input', () => {
      const keyword = searchInput.value.toLowerCase();
      const rows = tableBody.querySelectorAll('tr');

      rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(keyword) ? '' : 'none';
      });
    });
  }
});
