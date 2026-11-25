"use strict";

// Handles the login and register forms. The same file serves both pages;
// <form data-mode="login"> or data-mode="register" tells it which one it is on.
(function () {
  const form = document.getElementById("auth-form");
  const message = document.getElementById("message");
  const button = document.getElementById("submit");
  const mode = form.dataset.mode;

  function show(text) {
    message.textContent = text;
    message.className = "msg " + (text ? "err" : "");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    show("");

    const username = form.username.value.trim();
    const password = form.password.value;
    if (!username || !password) {
      show("Please fill in all the fields.");
      return;
    }
    if (mode === "register" && password !== form.confirm.value) {
      show("Passwords do not match.");
      return;
    }

    button.disabled = true;
    try {
      const response = await fetch("/api/" + mode, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username, password: password }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        show(data.error || "Something went wrong. Please try again.");
        return;
      }
      window.location.href = "/dashboard";
    } catch (error) {
      show("Cannot reach the server. Check your connection and try again.");
    } finally {
      button.disabled = false;
    }
  });
})();
