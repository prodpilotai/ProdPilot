const base = import.meta.env.VITE_API_URL;

export async function listOrders() {
  const res = await fetch(`${base}/orders`);
  return res.json();
}
