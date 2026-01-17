document.getElementById("stockForm").addEventListener("submit", async e => {
  e.preventDefault();

  const productId = document.getElementById("stockProductId").value;
  const newStock = document.getElementById("stockQuantity").value;

  const res = await fetch("/admin/inventory/update-stock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      productId,
      newStock
    })
  });

  if (res.ok) {
    location.reload();
  } else {
    alert("Failed to update stock");
  }
});
