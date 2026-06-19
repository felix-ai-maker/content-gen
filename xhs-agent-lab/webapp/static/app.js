const $ = (id) => document.getElementById(id);
let currentPackage = null;

async function loadPresets() {
  const presets = await fetch("/api/presets").then((r) => r.json());
  $("style").innerHTML = presets
    .map((p) => `<option value="${p.key}">${p.name}</option>`)
    .join("");
}

async function loadHistory() {
  const pkgs = await fetch("/api/packages").then((r) => r.json());
  $("history").innerHTML = pkgs
    .map((p) => `<li data-name="${encodeURIComponent(p.name)}">${p.name}（${p.cards.length} 张）</li>`)
    .join("");
  document.querySelectorAll("#history li").forEach((li) => {
    li.onclick = () => showPackage(decodeURIComponent(li.dataset.name));
  });
}

function renderCards(name, cards) {
  $("cards").innerHTML = cards
    .map((c) => {
      const url = `/api/packages/${encodeURIComponent(name)}/cards/${encodeURIComponent(c)}`;
      return `<img src="${url}" title="${c}" onclick="window.open('${url}')" />`;
    })
    .join("");
}

async function showPackage(name) {
  currentPackage = name;
  const pkgs = await fetch("/api/packages").then((r) => r.json());
  const pkg = pkgs.find((p) => p.name === name);
  if (!pkg) return;
  renderCards(name, pkg.cards);
  const docs = await fetch(`/api/packages/${encodeURIComponent(name)}/docs`).then((r) => r.json());
  $("xhs").textContent = docs.xhs;
  $("wechat").textContent = docs.wechat;
  $("meta").textContent = `发布包：${name}`;
  $("result").classList.remove("hidden");
}

async function poll(taskId) {
  const task = await fetch(`/api/tasks/${taskId}`).then((r) => r.json());
  $("logs").textContent = (task.logs || []).join("\n");
  if (task.status === "done") {
    const r = task.result;
    $("status").textContent = `✅ 完成：${r.package_name}（${r.style_name || ""}）AI味 ${r.quality.score}/100${r.pushed ? " · 已推 Telegram" : ""}`;
    currentPackage = r.package_name;
    renderCards(r.package_name, r.cards);
    $("xhs").textContent = r.xhs_md;
    $("wechat").textContent = r.wechat_md;
    $("meta").textContent = `发布包：${r.package_name}`;
    $("result").classList.remove("hidden");
    $("gen").disabled = false;
    loadHistory();
    return;
  }
  if (task.status === "error") {
    $("status").textContent = `❌ 出错：${task.error}`;
    $("gen").disabled = false;
    return;
  }
  setTimeout(() => poll(taskId), 1500);
}

async function generate() {
  const topic = $("topic").value.trim();
  if (!topic) {
    $("status").textContent = "请先填选题。";
    return;
  }
  $("gen").disabled = true;
  $("result").classList.add("hidden");
  $("status").textContent = "已提交，生成中…（正式模式要几分钟）";
  $("logs").textContent = "";
  const body = {
    topic,
    copy_text: $("copy").value,
    style: $("style").value,
    mode: $("mode").value,
    extra_brief: $("extra").value,
    push: $("push").checked,
  };
  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  if (res.task_id) poll(res.task_id);
  else $("status").textContent = "提交失败：" + (res.detail || "未知错误");
}

document.querySelectorAll(".copy").forEach((btn) => {
  btn.onclick = () => {
    const text = $(btn.dataset.target).textContent;
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "已复制";
      setTimeout(() => (btn.textContent = "复制"), 1200);
    });
  };
});

$("push-now").onclick = async () => {
  if (!currentPackage) return;
  $("push-now").textContent = "推送中…";
  const r = await fetch(`/api/packages/${encodeURIComponent(currentPackage)}/push`, { method: "POST" }).then((x) => x.json());
  $("push-now").textContent = r.pushed ? "已推送 ✅" : "推送未生效（看日志）";
  $("logs").textContent = (r.logs || []).join("\n");
  setTimeout(() => ($("push-now").textContent = "推送到 Telegram"), 2000);
};

$("gen").onclick = generate;
loadPresets();
loadHistory();
