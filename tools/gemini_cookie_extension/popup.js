document.getElementById("syncBtn").addEventListener("click", async () => {
  const msg = document.getElementById("msg");
  const btn = document.getElementById("syncBtn");
  btn.disabled = true;
  btn.innerText = "Đang lấy cookie...";
  msg.innerText = "";

  try {
    const psidCookie = await chrome.cookies.get({
      url: "https://gemini.google.com",
      name: "__Secure-1PSID"
    });

    if (!psidCookie || !psidCookie.value) {
      msg.style.color = "#ef4444";
      msg.innerText = "❌ Chưa tìm thấy cookie! Hãy mở gemini.google.com và đăng nhập trước.";
      btn.disabled = false;
      btn.innerText = "🚀 Đồng bộ Cookie vào Docker";
      return;
    }

    const psidtsCookie = await chrome.cookies.get({
      url: "https://gemini.google.com",
      name: "__Secure-1PSIDTS"
    });

    btn.innerText = "Đang gửi vào Docker...";

    const response = await fetch("http://localhost:8000/api/cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        psid: psidCookie.value,
        psidts: psidtsCookie ? psidtsCookie.value : ""
      })
    });

    const data = await response.json();
    if (response.ok) {
      msg.style.color = "#22c55e";
      msg.innerText = "✅ Đồng bộ thành công! Docker đã sẵn sàng xử lý Vision.";
    } else {
      msg.style.color = "#ef4444";
      msg.innerText = "❌ Lỗi: " + (data.detail || "Không thể xác thực");
    }
  } catch (err) {
    msg.style.color = "#ef4444";
    msg.innerText = "❌ Không kết nối được Docker (Port 8000). Hãy đảm bảo Docker đang chạy!";
  } finally {
    btn.disabled = false;
    btn.innerText = "🚀 Đồng bộ Cookie vào Docker";
  }
});
