// ===== CATEGORY DROPDOWN =====
const dropdownToggle = document.getElementById("selected-category");
const dropdownMenu = document.getElementById("category-menu");
const hiddenInput = document.getElementById("category_id");

if (dropdownToggle && dropdownMenu && hiddenInput) {

  // Open / close dropdown
  dropdownToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    dropdownMenu.classList.toggle("active");
  });

  // Select category
  document.querySelectorAll(".dropdown-item").forEach(item => {
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdownToggle.textContent = item.textContent;
      hiddenInput.value = item.dataset.id;
      dropdownMenu.classList.remove("active");
    });
  });

  // Close when clicking outside
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".category-dropdown")) {
      dropdownMenu.classList.remove("active");
    }
  });
}

// ===== OPTIONAL: ADD CATEGORY (IF USED) =====
const addCategoryBtn = document.getElementById("add-category-btn");

if (addCategoryBtn) {
  addCategoryBtn.addEventListener("click", async () => {
    const name = prompt("Enter new category name:");
    if (!name) return;

    const res = await fetch("/admin/addproduct/category/add", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ name })
    });

    if (res.ok) location.reload();
    else alert("Failed to add category");
  });
}

// ===== OPTIONAL: DELETE CATEGORY (IF USED) =====
document.querySelectorAll(".delete-btn").forEach(btn => {
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();

    if (!confirm("Delete category?")) return;

    const res = await fetch(
      `/admin/addproduct/category/delete/${btn.dataset.id}`,
      { method: "POST" }
    );

    if (res.ok) location.reload();
    else alert("Delete failed");
  });
});
