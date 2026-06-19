const $ = (id) => document.getElementById(id);

let currentPackage = null;   // result 区当前显示的包（用于推送）
let activeResult = null;     // 最近一次「生成」完成的结果
let generating = false;      // 是否有生成任务在跑
let viewing = "current";     // "current"=看最近生成 / 或某个历史包名

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

function renderInto(name, cards, xhs, wechat) {
  currentPackage = name;
  renderCards(name, cards);
  $("xhs").textContent = xhs;
  $("wechat").textContent = wechat;
  $("meta").textContent = `发布包：${name}`;
  $("result").classList.remove("hidden");
}

function renderInputs(inp) {
  if (!inp || !inp.topic) {
    $("inputs").innerHTML = "";
    return;
  }
  const parts = [`选题：${inp.topic}`];
  if (inp.style) parts.push(`风格：${inp.style}`);
  if (inp.mode) parts.push(`模式：${inp.mode}`);
  if (inp.extra_brief) parts.push(`额外指令：${inp.extra_brief}`);
  if (inp.copy_text) parts.push(`素材：${inp.copy_text.slice(0, 40)}…`);
  $("inputs").innerHTML = "📝 本次提示词 — " + parts.join("　·　");
}

// 是否显示「回到最近生成」返回条
function updateBackbar() {
  const show = viewing !== "current" && (activeResult || generating);
  $("backbar").classList.toggle("hidden", !show);
}

function showCurrent() {
  viewing = "current";
  updateBackbar();
  if (activeResult) {
    renderInto(activeResult.package_name, activeResult.cards, activeResult.xhs_md, activeResult.wechat_md);
    renderInputs(activeResult.inputs);
  } else if (generating) {
    // 还没生成完：不显示旧结果，提示去看进度
    $("result").classList.add("hidden");
  }
}

async function showPackage(name) {
  viewing = name;
  const pkgs = await fetch("/api/packages").then((r) => r.json());
  const pkg = pkgs.find((p) => p.name === name);
  if (!pkg) return;
  const docs = await fetch(`/api/packages/${encodeURIComponent(name)}/docs`).then((r) => r.json());
  renderInto(name, pkg.cards, docs.xhs, docs.wechat);
  const inp = await fetch(`/api/packages/${encodeURIComponent(name)}/inputs`).then((r) => r.json());
  renderInputs(inp);
  updateBackbar();
}

async function poll(taskId) {
  const task = await fetch(`/api/tasks/${taskId}`).then((r) => r.json());
  $("logs").textContent = (task.logs || []).join("\n");

  if (task.status === "done") {
    generating = false;
    activeResult = task.result;
    const r = task.result;
    const summary = `✅ 完成：${r.package_name}（${r.style_name || ""}）AI味 ${r.quality.score}/100${r.pushed ? " · 已推 Telegram" : ""}`;
    $("gen").disabled = false;
    loadHistory();
    if (viewing === "current") {
      $("status").textContent = summary;
      renderInto(r.package_name, r.cards, r.xhs_md, r.wechat_md);
      renderInputs(r.inputs);
    } else {
      // 用户正在看历史，不抢走视图，只提示 + 让返回条可用
      $("status").textContent = summary + "（点「回到最近生成」查看）";
      updateBackbar();
    }
    return;
  }
  if (task.status === "error") {
    generating = false;
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
  generating = true;
  activeResult = null;
  viewing = "current";
  updateBackbar();
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
  else {
    generating = false;
    $("gen").disabled = false;
    $("status").textContent = "提交失败：" + (res.detail || "未知错误");
  }
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

$("back-current").onclick = showCurrent;

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
