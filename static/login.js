document.querySelector("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const response = await fetch("/login", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({password:document.querySelector("#password").value})});
  if (response.ok) location.assign("/");
  else document.querySelector("#login-error").textContent = (await response.json()).error || "登录失败";
});
