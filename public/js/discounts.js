// public/js/discounts.js

let discounts = [];
let currentPage = 1;
const pageSize = 10;
let editingId = null;

/* ================= LOAD ================= */
document.addEventListener('DOMContentLoaded', () => {
  loadDiscounts();

  document.getElementById('createBtn').addEventListener('click', () => {
    openCreateModal();
  });

  document.getElementById('discountForm').addEventListener('submit', saveDiscount);
});

/* ================= FETCH ================= */
async function loadDiscounts() {
  try {
    const res = await fetch('/api/discounts');
    discounts = await res.json();
    currentPage = 1;
    renderTable();
  } catch (err) {
    console.error('Failed to load discounts', err);
  }
}

/* ================= TABLE ================= */
function renderTable() {
  const tbody = document.getElementById('discountsTableBody');
  tbody.innerHTML = '';

  const start = (currentPage - 1) * pageSize;
  const pageData = discounts.slice(start, start + pageSize);

  if (pageData.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center">No discounts found</td></tr>`;
    return;
  }

  pageData.forEach(d => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${d.code}</td>
      <td>${d.type}</td>
      <td>${d.type === 'percentage' ? d.value + '%' : 'SGD ' + d.value}</td>
      <td>${d.usage_limit ?? '∞'}</td>
      <td>${d.used}</td>
      <td>${d.valid_until}</td>
      <td>
        <span class="badge ${d.is_active ? 'badge-success' : 'badge-secondary'}">
          ${d.is_active ? 'Active' : 'Inactive'}
        </span>
      </td>
      <td>
        <button class="btn btn-sm btn-primary" onclick="editDiscount(${d.id})">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteDiscount(${d.id})">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  updatePagination();
}

/* ================= PAGINATION ================= */
function updatePagination() {
  document.getElementById('prevBtn').disabled = currentPage === 1;
  document.getElementById('nextBtn').disabled = currentPage * pageSize >= discounts.length;

  document.getElementById('prevBtn').onclick = () => {
    currentPage--;
    renderTable();
  };

  document.getElementById('nextBtn').onclick = () => {
    currentPage++;
    renderTable();
  };
}

/* ================= MODAL ================= */
function openCreateModal() {
  editingId = null;
  document.getElementById('modalTitle').innerText = 'Create Discount';
  document.getElementById('discountForm').reset();
  document.getElementById('isActive').checked = true;
  document.getElementById('discountModal').style.display = 'flex';
}

function editDiscount(id) {
  const d = discounts.find(x => x.id === id);
  if (!d) return;

  editingId = id;
  document.getElementById('modalTitle').innerText = 'Edit Discount';

  discountCode.value = d.code;
  discountType.value = d.type;
  discountValue.value = d.value;
  usageLimit.value = d.usage_limit ?? '';
  validUntil.value = d.valid_until;
  discountDescription.value = d.description ?? '';
  isActive.checked = !!d.is_active;

  document.getElementById('discountModal').style.display = 'flex';
}

function closeDiscountModal() {
  document.getElementById('discountModal').style.display = 'none';
}

/* ================= SAVE ================= */
async function saveDiscount(e) {
  e.preventDefault();

  const payload = {
    code: discountCode.value.trim(),
    type: discountType.value,
    value: discountValue.value,
    usage_limit: usageLimit.value || null,
    valid_until: validUntil.value,
    description: discountDescription.value,
    is_active: isActive.checked ? 1 : 0
  };

  const url = editingId
    ? `/api/discounts/${editingId}`
    : '/api/discounts';

  const method = editingId ? 'PUT' : 'POST';

  await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  closeDiscountModal();
  loadDiscounts();
}

/* ================= DELETE ================= */
async function deleteDiscount(id) {
  if (!confirm('Delete this discount?')) return;

  await fetch(`/api/discounts/${id}`, { method: 'DELETE' });
  loadDiscounts();
}
