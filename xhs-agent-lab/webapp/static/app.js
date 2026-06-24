const $ = (id) => document.getElementById(id);

let currentPackage = null;   // result 区当前显示的包（用于推送）
let activeResult = null;     // 最近一次「生成」完成的结果
let generating = false;      // 是否有生成任务在跑
let viewing = "current";     // "current"=看最近生成 / 或某个历史包名
let editorCards = null;      // 当前编辑器里的 cards_used.json
let editorOpen = false;
let activeRegen = null;      // 当前正在编辑提示词的单张卡
let workbenchOpen = false;   // 高级工作台默认收起，避免压住主流程
const docEditing = { xhs: false, wechat: false };
let lastInspiration = null;

const escapeHTML = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  let data = null;
  try {
    data = await res.json();
  } catch (_err) {
    data = {};
  }
  if (!res.ok) {
    throw new Error(data.detail || `请求失败：${res.status}`);
  }
  return data;
}

function resetProgress(message = "等待开始…") {
  $("progress").classList.add("hidden");
  $("progress-label").textContent = message;
  $("progress-percent").textContent = "0%";
  $("progress-fill").style.width = "0%";
  $("progress-detail").textContent = "";
}

function renderProgress(progress) {
  if (!progress) return;
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  $("progress").classList.remove("hidden");
  $("progress-label").textContent = progress.message || "生成中…";
  $("progress-percent").textContent = `${Math.round(percent)}%`;
  $("progress-fill").style.width = `${percent}%`;
  if (Number(progress.total) > 0) {
    $("progress-detail").textContent = `图片进度：${progress.done || 0}/${progress.total}`;
  } else {
    $("progress-detail").textContent = progress.stage ? `阶段：${progress.stage}` : "";
  }
}

async function loadPresets() {
  const data = await fetchJson("/api/presets");
  const opt = (p) => `<option value="${escapeHTML(p.key)}">${escapeHTML(p.name)}</option>`;
  let html = opt(data.auto || { key: "", name: "自动匹配" });
  html += (data.groups || [])
    .map(
      (g) =>
        `<optgroup label="${escapeHTML(g.group)}">` + (g.items || []).map(opt).join("") + `</optgroup>`
    )
    .join("");
  $("style").innerHTML = html;
}

async function loadHistory() {
  const pkgs = await fetchJson("/api/packages");
  if (!pkgs.length) {
    $("history").innerHTML = `<div class="hist-empty">还没有发布包，生成一个试试。</div>`;
    return;
  }
  $("history").innerHTML = pkgs
    .map((p) => {
      const m = p.name.match(/^(\d{4}-\d{2}-\d{2})_(.*)$/);
      const date = m ? m[1] : "";
      const title = m ? m[2] : p.name;
      const cover =
        p.cards && p.cards.length
          ? `<img class="hist-cover" loading="lazy" alt="" src="/api/packages/${encodeURIComponent(p.name)}/cards/${encodeURIComponent(p.cards[0])}" />`
          : `<div class="hist-cover hist-nocover">无图</div>`;
      return `<button type="button" class="hist-card" data-name="${encodeURIComponent(p.name)}">
        ${cover}
        <div class="hist-meta">
          <span class="hist-title">${escapeHTML(title)}</span>
          <span class="hist-sub">${escapeHTML(date)} · ${p.cards.length} 张</span>
        </div>
      </button>`;
    })
    .join("");
  document.querySelectorAll(".hist-card").forEach((el) => {
    el.onclick = () => showPackage(decodeURIComponent(el.dataset.name));
  });
}

function setInspireBusy(busy) {
  ["inspire-topic", "inspire-copy"].forEach((id) => {
    $(id).disabled = busy;
  });
}

function inspirationSeedMessage(target) {
  const direction = $("inspire-direction").value.trim();
  const topic = $("topic").value.trim();
  const copy = $("copy").value.trim();
  if (direction || topic || copy) return "";
  return target === "topic" ? "先输入灵感关键词 / 方向，或一个粗略选题。" : "先输入灵感关键词 / 方向，或一两句原始想法。";
}

async function inspire(target) {
  const emptyMessage = inspirationSeedMessage(target);
  if (emptyMessage) {
    $("status").textContent = emptyMessage;
    return;
  }
  setInspireBusy(true);
  $("inspire-panel").classList.remove("hidden");
  $("inspire-panel").innerHTML = `
    <div class="inspire-head">
      <strong>灵感生成中…</strong>
      <span>会根据灵感方向、当前选题和素材一起判断</span>
    </div>`;
  try {
    const result = await fetchJson("/api/inspire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target,
        direction: $("inspire-direction").value,
        topic: $("topic").value,
        copy_text: $("copy").value,
      }),
    });
    lastInspiration = result;
    renderInspiration(result, target);
  } catch (err) {
    $("inspire-panel").innerHTML = `<div class="review-error">${escapeHTML(err.message)}</div>`;
  } finally {
    setInspireBusy(false);
  }
}

function renderInspiration(data, target) {
  const topics = Array.isArray(data.topics) ? data.topics : [];
  const materials = Array.isArray(data.materials) ? data.materials : [];
  const source = data.source === "ai" ? "AI 生成" : "本地灵感";
  const showTopics = target !== "copy";
  const showMaterials = target !== "topic" || !topics.length;
  const topicCards = showTopics ? topics.map((item, index) => `
    <div class="inspire-card">
      <strong>${escapeHTML(item.title || "选题候选")}</strong>
      <p>${escapeHTML(item.angle || "")}</p>
      <p>${escapeHTML(item.hook || "")}</p>
      <div class="inspire-actions">
        <button type="button" class="mini primary use-topic" data-index="${index}">填入选题</button>
      </div>
    </div>`).join("") : "";
  const materialCards = showMaterials ? materials.map((item, index) => {
    const bullets = Array.isArray(item.bullets) ? item.bullets.filter(Boolean).join(" / ") : "";
    return `<div class="inspire-card">
      <strong>${escapeHTML(item.title || "素材草稿")}</strong>
      <p>${escapeHTML(item.draft || "")}</p>
      ${bullets ? `<p>${escapeHTML(bullets)}</p>` : ""}
      <div class="inspire-actions">
        <button type="button" class="mini primary use-copy" data-index="${index}">填入素材</button>
        <button type="button" class="mini use-copy-append" data-index="${index}">追加到素材</button>
      </div>
    </div>`;
  }).join("") : "";
  $("inspire-panel").innerHTML = `
    <div class="inspire-head">
      <strong>灵感助手</strong>
      <span>${source}</span>
      <button id="inspire-close" type="button" class="mini">收起</button>
    </div>
    <div class="inspire-grid">
      ${topicCards}
      ${materialCards}
    </div>`;
  $("inspire-close").onclick = () => $("inspire-panel").classList.add("hidden");
  document.querySelectorAll(".use-topic").forEach((button) => {
    button.onclick = () => {
      const item = (lastInspiration?.topics || [])[Number(button.dataset.index)];
      if (item?.title) $("topic").value = item.title;
    };
  });
  document.querySelectorAll(".use-copy").forEach((button) => {
    button.onclick = () => {
      const item = (lastInspiration?.materials || [])[Number(button.dataset.index)];
      if (!item) return;
      const bullets = Array.isArray(item.bullets) ? item.bullets.map((line) => `- ${line}`).join("\n") : "";
      $("copy").value = [item.draft || "", bullets].filter(Boolean).join("\n\n");
    };
  });
  document.querySelectorAll(".use-copy-append").forEach((button) => {
    button.onclick = () => {
      const item = (lastInspiration?.materials || [])[Number(button.dataset.index)];
      if (!item) return;
      const bullets = Array.isArray(item.bullets) ? item.bullets.map((line) => `- ${line}`).join("\n") : "";
      const next = [item.draft || "", bullets].filter(Boolean).join("\n\n");
      $("copy").value = [$("copy").value.trim(), next].filter(Boolean).join("\n\n");
    };
  });
}

function cardIndexFromName(fname) {
  const m = String(fname).match(/card_(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

function renderCards(name, cards) {
  if (!cards || !cards.length) {
    $("cards").innerHTML = `<div class="cards-empty">📝 纯文案模式：没有图片。下方是小红书 / 公众号正文，可直接复制；分卡脚本（每张配图该写什么）在「高级工作台 → 编辑卡片文案」里。</div>`;
    return;
  }
  $("cards").innerHTML = cards
    .map((c) => {
      const idx = cardIndexFromName(c);
      const url = `/api/packages/${encodeURIComponent(name)}/cards/${encodeURIComponent(c)}`;
      const imgUrl = `${url}?v=${Date.now()}`;
      const selected = activeRegen && activeRegen.name === name && activeRegen.idx === idx;
      return `<div class="card-item ${selected ? "regen-selected" : ""}">
        <img id="img-${idx}" src="${escapeHTML(imgUrl)}" title="${escapeHTML(c)}" onclick="window.open('${escapeHTML(url)}')" />
        <button class="regen-toggle ${selected ? "selected" : ""}" data-idx="${idx}" data-file="${escapeHTML(c)}">${selected ? "正在改这张" : "✏️ 改这张"}</button>
      </div>`;
    })
    .join("");
  document.querySelectorAll(".regen-toggle").forEach((b) => {
    b.onclick = () => {
      openRegenPanel(name, parseInt(b.dataset.idx, 10), b.dataset.file).catch((err) => {
        $("status").textContent = "打开提示词编辑器失败：" + err.message;
      });
    };
  });
}

function setWorkbenchOpen(open) {
  workbenchOpen = open;
  $("workbench").classList.toggle("collapsed", !open);
  $("workbench-body").classList.toggle("hidden", !open);
  $("workbench-toggle").textContent = open ? "收起高级" : "展开高级";
  $("workbench-toggle").setAttribute("aria-expanded", String(open));
}

function updateWorkbenchSummary(review) {
  const fallback = "默认收起，展开后可编辑文案和重渲染。";
  if (!review) {
    $("workbench-summary").textContent = fallback;
    return;
  }
  const hasScore = review.score !== null && review.score !== undefined && Number.isFinite(Number(review.score));
  const score = hasScore ? `${review.score} 分` : "待检查";
  $("workbench-summary").textContent = `${score} · ${review.summary || "发布检查已更新"}`;
}

function renderReview(review) {
  updateWorkbenchSummary(review);
  if (!review || !review.items) {
    $("review").innerHTML = "";
    return;
  }
  const levelLabel = { ok: "通过", warn: "可优化", fix: "先处理" };
  $("review").innerHTML = `
    <div class="review-summary">
      <span class="review-score">${review.score}</span>
      <span>${escapeHTML(review.summary || "")}</span>
    </div>
    <div class="review-grid">
      ${review.items.map((item) => `
        <div class="review-item ${escapeHTML(item.level)}">
          <span class="review-level">${escapeHTML(levelLabel[item.level] || item.level)}</span>
          <strong>${escapeHTML(item.title)}</strong>
          <p>${escapeHTML(item.detail)}</p>
        </div>
      `).join("")}
    </div>`;
}

async function loadReview(name) {
  try {
    renderReview(await fetchJson(`/api/packages/${encodeURIComponent(name)}/review`));
  } catch (err) {
    updateWorkbenchSummary({ score: null, summary: "发布检查读取失败" });
    $("review").innerHTML = `<div class="review-error">${escapeHTML(err.message)}</div>`;
  }
}

function normalizeBullets(value) {
  return String(value || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function renderCardEditor(cards) {
  editorCards = cards.map((card) => ({ ...card }));
  $("card-editor").innerHTML = editorCards.map((card, index) => {
    const bullets = Array.isArray(card.bullets) ? card.bullets.join("\n") : (card.bullets || "");
    return `<div class="edit-card" data-index="${index}">
      <div class="edit-card-head">
        <strong>卡 ${String(index + 1).padStart(2, "0")}</strong>
        <span>${escapeHTML(card.type || "content")}</span>
      </div>
      <label>小标
        <input data-field="kicker" type="text" value="${escapeHTML(card.kicker || "")}" />
      </label>
      <label>标题
        <input data-field="title" type="text" value="${escapeHTML(card.title || "")}" />
      </label>
      <label>副标题
        <textarea data-field="subtitle" rows="2">${escapeHTML(card.subtitle || "")}</textarea>
      </label>
      <label>要点（每行一条）
        <textarea data-field="bullets" rows="4">${escapeHTML(bullets)}</textarea>
      </label>
      <label>底部收束句
        <textarea data-field="note" rows="2">${escapeHTML(card.note || "")}</textarea>
      </label>
    </div>`;
  }).join("");
  $("save-cards").disabled = false;
}

function collectEditedCards() {
  return editorCards.map((card, index) => {
    const root = document.querySelector(`.edit-card[data-index="${index}"]`);
    const next = { ...card };
    next.kicker = root.querySelector('[data-field="kicker"]').value.trim();
    next.title = root.querySelector('[data-field="title"]').value.trim();
    next.subtitle = root.querySelector('[data-field="subtitle"]').value.trim();
    const bullets = normalizeBullets(root.querySelector('[data-field="bullets"]').value);
    if (bullets.length || "bullets" in next) next.bullets = bullets;
    const note = root.querySelector('[data-field="note"]').value.trim();
    if (note || "note" in next) next.note = note;
    return next;
  });
}

async function toggleCardEditor() {
  if (!currentPackage) return;
  editorOpen = !editorOpen;
  $("card-editor").classList.toggle("hidden", !editorOpen);
  $("edit-cards").textContent = editorOpen ? "收起卡片文案" : "编辑卡片文案";
  if (editorOpen && !editorCards) {
    $("card-editor").innerHTML = "<div class=\"editor-loading\">加载卡片文案中…</div>";
    const data = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/cards-data`);
    renderCardEditor(data.cards || []);
  }
}

async function saveCards(options = {}) {
  if (!currentPackage || !editorCards) return null;
  const cards = collectEditedCards();
  $("save-cards").disabled = true;
  if (!options.quiet) $("status").textContent = "正在保存卡片文案…";
  try {
    const result = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/cards-data`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cards }),
    });
    editorCards = cards;
    $("xhs").textContent = result.xhs_md || "";
    $("wechat").textContent = result.wechat_md || "";
    resetDocEditors();
    renderReview(result.review);
    renderInputs(result.inputs);
    if (activeResult && activeResult.package_name === currentPackage) {
      activeResult = {
        ...activeResult,
        xhs_md: result.xhs_md || "",
        wechat_md: result.wechat_md || "",
        quality: result.quality || activeResult.quality,
        review: result.review,
        inputs: result.inputs,
      };
    }
    if (!options.quiet) $("status").textContent = "卡片文案已保存，正文和发布检查已更新。";
    return result;
  } catch (err) {
    $("status").textContent = "保存失败：" + err.message;
    throw err;
  } finally {
    $("save-cards").disabled = false;
  }
}

function setWorkbenchBusy(busy) {
  ["edit-cards", "rerender-local", "rerender-direct"].forEach((id) => {
    $(id).disabled = busy;
  });
  $("save-cards").disabled = busy || !editorCards;
}

async function rerenderPackage(mode) {
  if (!currentPackage) return;
  try {
    if (editorOpen && editorCards) await saveCards({ quiet: true });
    setWorkbenchBusy(true);
    $("logs").textContent = "";
    const modeLabel = {
      background: "正式固定排版出图",
      local: "本地草稿重渲染",
      direct: "实验整卡直出",
    }[mode] || "重渲染";
    $("status").textContent = `已提交${modeLabel}任务…`;
    resetProgress("已提交，等待重渲染…");
    renderProgress({ percent: 1, message: "已提交，等待重渲染…", done: 0, total: 0 });
    const res = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/rerender`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        extra_brief: $("extra").value,
        refresh_style: false,
        push: $("push").checked,
      }),
    });
    pollRerender(res.task_id);
  } catch (err) {
    $("status").textContent = "提交失败：" + err.message;
    setWorkbenchBusy(false);
  }
}

async function pollRerender(taskId) {
  let task;
  try {
    task = await fetchJson(`/api/tasks/${taskId}`);
  } catch (err) {
    $("status").textContent = "轮询失败：" + err.message;
    setWorkbenchBusy(false);
    return;
  }
  $("logs").textContent = (task.logs || []).join("\n");
  renderProgress(task.progress);
  if (task.status === "done") {
    const r = task.result;
    activeResult = r;
    viewing = "current";
    renderInto(r.package_name, r.cards, r.xhs_md, r.wechat_md);
    renderInputs(r.inputs);
    applyInputsToForm(r.inputs);
    $("status").textContent = `✅ 已更新：${r.package_name}（${r.mode}）AI味 ${r.quality.score}/100`;
    setWorkbenchBusy(false);
    loadHistory();
    return;
  }
  if (task.status === "error") {
    $("status").textContent = `❌ 出错：${task.error}`;
    setWorkbenchBusy(false);
    return;
  }
  setTimeout(() => pollRerender(taskId), 1500);
}

function closeRegenPanel() {
  activeRegen = null;
  $("regen-panel").classList.add("hidden");
  $("regen-panel").innerHTML = "";
  document.querySelectorAll(".card-item.regen-selected").forEach((item) => item.classList.remove("regen-selected"));
  document.querySelectorAll(".regen-toggle.selected").forEach((button) => {
    button.classList.remove("selected");
    button.textContent = "✏️ 改这张";
  });
}

function setActiveRegenButton(idx) {
  document.querySelectorAll(".card-item.regen-selected").forEach((item) => item.classList.remove("regen-selected"));
  document.querySelectorAll(".regen-toggle").forEach((button) => {
    const selected = parseInt(button.dataset.idx, 10) === idx;
    button.classList.toggle("selected", selected);
    button.textContent = selected ? "正在改这张" : "✏️ 改这张";
    if (selected) button.closest(".card-item")?.classList.add("regen-selected");
  });
}

async function openRegenPanel(name, idx, file) {
  if (!Number.isFinite(idx)) {
    throw new Error("无法识别卡片序号");
  }
  activeRegen = { name, idx, file };
  setActiveRegenButton(idx);
  $("regen-panel").classList.remove("hidden");
  $("regen-panel").innerHTML = `
    <div class="regen-panel-head">
      <div>
        <strong>修改第 ${String(idx).padStart(2, "0")} 张图片提示词</strong>
        <span>${escapeHTML(file || `card_${String(idx).padStart(2, "0")}.png`)}</span>
      </div>
      <button id="regen-close" class="mini">收起</button>
    </div>
    <textarea id="regen-prompt" class="regen-prompt" rows="10" placeholder="在这里改画面提示词。建议写清主视觉、构图、材质、光线和不要出现的元素。">加载提示词中…</textarea>
    <div class="regen-panel-foot">
      <span id="regen-status" class="regen-status">读取当前提示词…</span>
      <button id="regen-submit" class="regen-go">重生成这张</button>
    </div>
  `;
  $("regen-close").onclick = closeRegenPanel;
  $("regen-submit").onclick = () => regenOne(name, idx);
  $("regen-panel").scrollIntoView({ block: "nearest", behavior: "smooth" });

  const ta = $("regen-prompt");
  try {
    const p = await fetchJson(`/api/packages/${encodeURIComponent(name)}/cards/${idx}/prompt`);
    ta.value = p.prompt || "";
    $("regen-status").textContent = "可以在这里直接改提示词。";
  } catch (err) {
    ta.value = "";
    $("regen-status").textContent = "提示词加载失败：" + err.message;
  }
}

async function regenOne(name, idx) {
  const btn = $("regen-submit");
  const st = $("regen-status");
  const ta = $("regen-prompt");
  if (!ta) return;
  btn.disabled = true;
  st.textContent = "提交中…";
  resetProgress("已提交，等待重生成单张图片…");
  renderProgress({ percent: 1, message: "已提交，等待重生成单张图片…", done: 0, total: 1 });
  const edited = ta.value;
  try {
    const res = await fetchJson(`/api/packages/${encodeURIComponent(name)}/cards/${idx}/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metaphor: edited }),
    });
    if (!res.task_id) {
      st.textContent = "提交失败";
      btn.disabled = false;
      return;
    }
    pollRegen(res.task_id, name, idx, btn);
  } catch (err) {
    st.textContent = "提交失败：" + err.message;
    btn.disabled = false;
  }
}

async function pollRegen(taskId, name, idx, btn) {
  const st = $("regen-status");
  let task;
  try {
    task = await fetchJson(`/api/tasks/${taskId}`);
  } catch (err) {
    if (st) st.textContent = "轮询失败：" + err.message;
    if (btn) btn.disabled = false;
    return;
  }
  renderProgress(task.progress);
  if (task.status === "done") {
    const fname = task.result.card;
    $(`img-${idx}`).src = `/api/packages/${encodeURIComponent(name)}/cards/${encodeURIComponent(fname)}?t=${Date.now()}`;
    if (st) st.textContent = "已更新。可以继续改提示词再重生成。";
    if (btn) btn.disabled = false;
    return;
  }
  if (task.status === "error") {
    if (st) st.textContent = "出错：" + task.error;
    if (btn) btn.disabled = false;
    return;
  }
  if (st) st.textContent = (task.logs || []).slice(-1)[0] || "生成中…";
  setTimeout(() => pollRegen(taskId, name, idx, btn), 1500);
}

function renderInto(name, cards, xhs, wechat) {
  currentPackage = name;
  closeRegenPanel();
  editorCards = null;
  editorOpen = false;
  setWorkbenchOpen(false);
  updateWorkbenchSummary(null);
  $("card-editor").classList.add("hidden");
  $("card-editor").innerHTML = "";
  $("edit-cards").textContent = "编辑卡片文案";
  $("save-cards").disabled = true;
  renderCards(name, cards);
  $("xhs").textContent = xhs;
  $("wechat").textContent = wechat;
  resetDocEditors();
  $("meta").textContent = `发布包：${name}`;
  $("result").classList.remove("hidden");
  loadReview(name);
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
  if (inp.recovered) parts.push("历史恢复：原始素材不可还原");
  $("inputs").textContent = "📝 本次提示词 — " + parts.join("　·　");
}

function setSelectValue(id, value, fallback = "") {
  const select = $(id);
  const next = String(value ?? "");
  const hasOption = Array.from(select.options).some((option) => option.value === next);
  select.value = hasOption ? next : fallback;
}

function applyInputsToForm(inp) {
  if (!inp || !inp.topic) return;
  $("topic").value = inp.topic || "";
  $("copy").value = inp.copy_text || "";
  setSelectValue("style", inp.style || "", "");
  setSelectValue("mode", inp.mode || "local", "local");
  $("extra").value = inp.extra_brief || "";
  $("inspire-direction").value = inp.direction || "";
  setSelectValue("playbook", inp.playbook || "", "");
  setSelectValue("variant-playbook", inp.playbook || "", "");
  $("humanize").checked = Boolean(inp.humanize);
  $("push").checked = Boolean(inp.push);
}

function docButton(kind, cls) {
  return document.querySelector(`.${cls}[data-kind="${kind}"]`);
}

function setDocEditMode(kind, editing, options = {}) {
  const pre = $(kind);
  const editor = $(`${kind}-editor`);
  if (!pre || !editor) return;
  docEditing[kind] = editing;
  if (editing || options.discard) {
    editor.value = pre.textContent;
  }
  pre.classList.toggle("hidden", editing);
  editor.classList.toggle("hidden", !editing);
  docButton(kind, "doc-edit")?.classList.toggle("hidden", editing);
  docButton(kind, "doc-save")?.classList.toggle("hidden", !editing);
  docButton(kind, "doc-cancel")?.classList.toggle("hidden", !editing);
}

function resetDocEditors() {
  ["xhs", "wechat"].forEach((kind) => setDocEditMode(kind, false, { discard: true }));
}

async function saveDoc(kind) {
  if (!currentPackage) return;
  const saveButton = docButton(kind, "doc-save");
  const editButton = docButton(kind, "doc-edit");
  const editor = $(`${kind}-editor`);
  if (!editor) return;
  const originalText = saveButton ? saveButton.textContent : "保存";
  if (saveButton) {
    saveButton.disabled = true;
    saveButton.textContent = "保存中…";
  }
  if (editButton) editButton.disabled = true;
  $("status").textContent = kind === "xhs" ? "正在保存小红书正文…" : "正在保存公众号文章…";
  try {
    const result = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/docs`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [kind]: editor.value }),
    });
    $("xhs").textContent = result.xhs || "";
    $("wechat").textContent = result.wechat || "";
    setDocEditMode(kind, false, { discard: true });
    renderReview(result.review);
    renderInputs(result.inputs);
    if (activeResult && activeResult.package_name === currentPackage) {
      activeResult = {
        ...activeResult,
        xhs_md: result.xhs || "",
        wechat_md: result.wechat || "",
        review: result.review,
        inputs: result.inputs || activeResult.inputs,
      };
    }
    $("status").textContent = kind === "xhs" ? "小红书正文已保存。" : "公众号文章已保存。";
  } catch (err) {
    $("status").textContent = "保存文案失败：" + err.message;
  } finally {
    if (saveButton) {
      saveButton.disabled = false;
      saveButton.textContent = originalText;
    }
    if (editButton) editButton.disabled = false;
  }
}

async function humanizeDoc(kind) {
  if (!currentPackage) {
    $("status").textContent = "先打开一个发布包。";
    return;
  }
  const btn = docButton(kind, "doc-humanize");
  const text = $(kind).textContent.trim();
  if (text.length < 10) return;
  const orig = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "去味中…";
  }
  $("status").textContent = "正在去 AI 味…";
  try {
    const h = await fetchJson("/api/humanize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const result = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/docs`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [kind]: h.text }),
    });
    $("xhs").textContent = result.xhs || $("xhs").textContent;
    $("wechat").textContent = result.wechat || $("wechat").textContent;
    renderReview(result.review);
    if (activeResult && activeResult.package_name === currentPackage) {
      activeResult = { ...activeResult, xhs_md: result.xhs, wechat_md: result.wechat, review: result.review };
    }
    $("status").textContent = "已去 AI 味并保存。";
    if (btn) btn.textContent = "✅ 已去味";
    setTimeout(() => {
      if (btn) btn.textContent = orig;
    }, 1800);
  } catch (err) {
    $("status").textContent = "去 AI 味失败：" + err.message;
    if (btn) btn.textContent = orig;
  } finally {
    if (btn) btn.disabled = false;
  }
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
    applyInputsToForm(activeResult.inputs);
  } else if (generating) {
    // 还没生成完：不显示旧结果，提示去看进度
    $("result").classList.add("hidden");
  }
}

async function showPackage(name) {
  try {
    switchView("create");
    window.scrollTo({ top: 0, behavior: "smooth" });
    viewing = name;
    const pkgs = await fetchJson("/api/packages");
    const pkg = pkgs.find((p) => p.name === name);
    if (!pkg) return;
    const docs = await fetchJson(`/api/packages/${encodeURIComponent(name)}/docs`);
    renderInto(name, pkg.cards, docs.xhs, docs.wechat);
    const inp = await fetchJson(`/api/packages/${encodeURIComponent(name)}/inputs`);
    renderInputs(inp);
    applyInputsToForm(inp);
    updateBackbar();
  } catch (err) {
    $("status").textContent = "打开历史包失败：" + err.message;
  }
}

async function poll(taskId) {
  let task;
  try {
    task = await fetchJson(`/api/tasks/${taskId}`);
  } catch (err) {
    generating = false;
    $("status").textContent = "轮询失败：" + err.message;
    $("gen").disabled = false;
    return;
  }
  $("logs").textContent = (task.logs || []).join("\n");
  renderProgress(task.progress);

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
      applyInputsToForm(r.inputs);
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
  resetProgress("已提交，等待生成…");
  renderProgress({ percent: 1, message: "已提交，等待生成…", done: 0, total: 0 });
  const body = {
    topic,
    copy_text: $("copy").value,
    style: $("style").value,
    mode: $("mode").value,
    extra_brief: $("extra").value,
    playbook: $("playbook").value,
    direction: $("inspire-direction").value,
    humanize: $("humanize").checked,
    push: $("push").checked,
  };
  try {
    const res = await fetchJson("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.task_id) poll(res.task_id);
    else {
      generating = false;
      $("gen").disabled = false;
      $("status").textContent = "提交失败：" + (res.detail || "未知错误");
    }
  } catch (err) {
    generating = false;
    $("gen").disabled = false;
    $("status").textContent = "提交失败：" + err.message;
  }
}

document.querySelectorAll(".copy").forEach((btn) => {
  btn.onclick = () => {
    const target = btn.dataset.target;
    const editor = $(`${target}-editor`);
    const text = editor && docEditing[target] ? editor.value : $(target).textContent;
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "已复制";
      setTimeout(() => (btn.textContent = "复制"), 1200);
    });
  };
});

$("back-current").onclick = showCurrent;
$("inspire-topic").onclick = () => inspire("topic");
$("inspire-copy").onclick = () => inspire("copy");
$("workbench-toggle").onclick = () => setWorkbenchOpen(!workbenchOpen);
$("edit-cards").onclick = () => {
  toggleCardEditor().catch((err) => {
    $("status").textContent = "打开编辑器失败：" + err.message;
  });
};
$("save-cards").onclick = () => {
  saveCards().catch(() => {});
};
$("rerender-local").onclick = () => rerenderPackage("local");
$("rerender-direct").onclick = () => rerenderPackage("background");

document.querySelectorAll(".doc-edit").forEach((button) => {
  button.onclick = () => setDocEditMode(button.dataset.kind, true);
});
document.querySelectorAll(".doc-save").forEach((button) => {
  button.onclick = () => saveDoc(button.dataset.kind);
});
document.querySelectorAll(".doc-cancel").forEach((button) => {
  button.onclick = () => setDocEditMode(button.dataset.kind, false, { discard: true });
});
document.querySelectorAll(".doc-humanize").forEach((button) => {
  button.onclick = () => humanizeDoc(button.dataset.kind);
});

$("push-now").onclick = async () => {
  if (!currentPackage) return;
  $("push-now").textContent = "推送中…";
  try {
    const r = await fetchJson(`/api/packages/${encodeURIComponent(currentPackage)}/push`, { method: "POST" });
    $("push-now").textContent = r.pushed ? "已推送 ✅" : "推送未生效（看日志）";
    $("logs").textContent = (r.logs || []).join("\n");
  } catch (err) {
    $("push-now").textContent = "推送失败";
    $("logs").textContent = err.message;
  }
  setTimeout(() => ($("push-now").textContent = "推送到 Telegram"), 2000);
};

async function loadSettings() {
  const data = await fetchJson("/api/settings");
  $("env-path-label").textContent = data.env_path || ".env";
  $("settings-list").innerHTML = (data.keys || [])
    .map(
      (k) => `
    <div class="setting-row">
      <div class="setting-label">
        <strong>${escapeHTML(k.label)}</strong>
        <span class="setting-hint">${escapeHTML(k.hint)}</span>
      </div>
      <div class="setting-input">
        <input type="password" autocomplete="off" data-key="${escapeHTML(k.key)}"
          placeholder="${k.set ? "已设置 " + escapeHTML(k.preview) + " · 留空不改" : "未设置"}" />
        ${k.set ? `<button type="button" class="mini subtle clear-key" data-key="${escapeHTML(k.key)}">清除</button>` : ""}
      </div>
    </div>`
    )
    .join("");
  $("settings-list")
    .querySelectorAll(".clear-key")
    .forEach((btn) => {
      btn.onclick = () => saveSettings({ [btn.dataset.key]: "" }, true);
    });
}

async function saveSettings(explicitValues, isClear) {
  const values = explicitValues || {};
  if (!explicitValues) {
    $("settings-list")
      .querySelectorAll("input[data-key]")
      .forEach((input) => {
        const value = input.value.trim();
        if (value) values[input.dataset.key] = value;
      });
  }
  if (Object.keys(values).length === 0) {
    $("settings-msg").textContent = "没有要保存的改动。";
    return;
  }
  $("settings-msg").textContent = "保存中…";
  try {
    await fetchJson("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    $("settings-msg").textContent = isClear ? "已清除。" : "已保存，立即生效。";
    await loadSettings();
  } catch (err) {
    $("settings-msg").textContent = "失败：" + err.message;
  }
}

$("open-settings").onclick = () => {
  $("settings-modal").classList.remove("hidden");
  $("settings-msg").textContent = "";
  loadSettings().catch((err) => {
    $("settings-msg").textContent = "加载失败：" + err.message;
  });
};
$("close-settings").onclick = () => $("settings-modal").classList.add("hidden");
$("settings-modal").onclick = (event) => {
  if (event.target === $("settings-modal")) $("settings-modal").classList.add("hidden");
};
$("save-settings").onclick = () => saveSettings();

let lastTeardownId = null;

function switchResearchTab(tab) {
  document.querySelectorAll(".research-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  $("tab-analyze").classList.toggle("hidden", tab !== "analyze");
  $("tab-playbook").classList.toggle("hidden", tab !== "playbook");
  if (tab === "analyze") loadTeardowns().catch(() => {});
  if (tab === "playbook") loadPlaybook().catch(() => {});
}

// ===== 灵感发现 tab =====
function switchDiscoverTab(dtab) {
  document.querySelectorAll(".dtab").forEach((btn) => btn.classList.toggle("active", btn.dataset.dtab === dtab));
  $("dpane-xhs").classList.toggle("hidden", dtab !== "xhs");
  $("dpane-ai").classList.toggle("hidden", dtab !== "ai");
  if (dtab === "xhs") loadXhsStatus().catch(() => {});
}

async function discInspire() {
  const direction = $("disc-direction").value.trim();
  if (!direction) {
    $("disc-inspire-result").innerHTML = `<div class="disc-empty">先输入一个方向 / 关键词。</div>`;
    return;
  }
  $("disc-inspire-btn").disabled = true;
  $("disc-inspire-result").innerHTML = `<div class="disc-empty">AI 搜灵感中…</div>`;
  try {
    const data = await fetchJson("/api/inspire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "both", direction }),
    });
    const topics = (data.topics || [])
      .map(
        (t) => `
      <div class="disc-card">
        <div class="disc-card-body">
          <strong>${escapeHTML(t.title || "")}</strong>
          <span class="disc-meta">${escapeHTML(t.angle || "")}</span>
          ${t.hook ? `<span class="disc-hook">钩子：${escapeHTML(t.hook)}</span>` : ""}
        </div>
        <button type="button" class="mini primary disc-use" data-topic="${escapeHTML(t.title || "")}" data-dir="${escapeHTML(direction)}">用它去创作</button>
      </div>`
      )
      .join("");
    const mats = (data.materials || [])
      .map(
        (m) => `<div class="disc-card"><div class="disc-card-body"><strong>${escapeHTML(m.title || "素材")}</strong><span class="disc-meta">${escapeHTML(m.draft || "")}</span></div></div>`
      )
      .join("");
    $("disc-inspire-result").innerHTML =
      `<div class="disc-group-title">选题候选（${data.source === "ai" ? "AI" : "本地"}）</div>${topics || "<div class='disc-empty'>无</div>"}` +
      (mats ? `<div class="disc-group-title">素材草稿</div>${mats}` : "");
    document.querySelectorAll(".disc-use").forEach((btn) => {
      btn.onclick = () => {
        $("topic").value = btn.dataset.topic;
        $("inspire-direction").value = btn.dataset.dir;
        switchView("create");
        window.scrollTo({ top: 0, behavior: "smooth" });
        $("status").textContent = "已带入选题，填好其余项点「开始生成」。";
      };
    });
  } catch (err) {
    $("disc-inspire-result").innerHTML = `<div class="disc-empty">搜灵感失败：${escapeHTML(err.message)}</div>`;
  } finally {
    $("disc-inspire-btn").disabled = false;
  }
}

// 视图切换里历史已并入工作台（create），不再单独处理 history。

async function analyzeNote() {
  const text = $("analyze-input").value.trim();
  if (text.length < 10) {
    $("analyze-result").textContent = "先粘贴一篇笔记内容（标题 + 正文 + 标签）。";
    return;
  }
  $("analyze-btn").disabled = true;
  $("distill-row").classList.add("hidden");
  $("analyze-result").textContent = "拆解中…（DeepSeek 分析需几秒到十几秒）";
  try {
    const data = await fetchJson("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    $("analyze-result").textContent = data.report || "（无返回）";
    lastTeardownId = data.id || null;
    $("distill-msg").textContent = "";
    $("distill-row").classList.toggle("hidden", !lastTeardownId);
    loadTeardowns().catch(() => {});
  } catch (err) {
    $("analyze-result").textContent = "拆解失败：" + err.message;
  } finally {
    $("analyze-btn").disabled = false;
  }
}

async function distillTactics() {
  if (!lastTeardownId) return;
  $("distill-btn").disabled = true;
  $("distill-msg").textContent = "提炼中…";
  try {
    const data = await fetchJson("/api/playbook/distill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: lastTeardownId, playbook_id: $("distill-target").value }),
    });
    const added = data.added || [];
    loadPlaybook().catch(() => {});
    const cats = [...new Set(added.map((a) => a.category))].join("、");
    $("distill-msg").textContent = added.length
      ? `已沉淀 ${added.length} 条招式到「${data.playbook}」（${cats}），去「招式库」看，下次用这套打法生成即生效。`
      : "没有新招式（都已在库里）。";
  } catch (err) {
    $("distill-msg").textContent = "提炼失败：" + err.message;
  } finally {
    $("distill-btn").disabled = false;
  }
}

async function loadTeardowns() {
  const list = $("teardown-list");
  try {
    const rows = await fetchJson("/api/teardowns");
    if (!rows.length) {
      list.innerHTML = `<li class="th-empty">还没有拆解记录。</li>`;
      return;
    }
    list.innerHTML = rows
      .map(
        (r) => `<li data-id="${escapeHTML(r.id)}"><span class="th-time">${escapeHTML((r.timestamp || "").replace("T", " "))}</span> ${escapeHTML(r.preview || "")}…</li>`
      )
      .join("");
    list.querySelectorAll("li[data-id]").forEach((li) => {
      li.onclick = () => {
        const row = rows.find((r) => r.id === li.dataset.id);
        if (!row) return;
        $("analyze-result").textContent = row.report || "";
        lastTeardownId = row.id;
        $("distill-msg").textContent = "";
        $("distill-row").classList.remove("hidden");
      };
    });
  } catch (err) {
    list.innerHTML = `<li class="th-empty">加载历史失败：${escapeHTML(err.message)}</li>`;
  }
}

let playbookDoc = { active: "", playbooks: [] };
let curPb = 0;
const PB_DEFAULT_CATS = [
  ["base", "基础准则"], ["title", "标题钩子"], ["emotion", "情绪驱动"], ["save", "收藏动机"],
  ["interact", "互动评论"], ["social", "社交货币 / 转发"], ["structure", "结构节奏"], ["topic", "选题时机"],
];

function curCats() {
  const pb = playbookDoc.playbooks[curPb];
  return pb ? pb.categories : [];
}

function applyPbMeta() {
  const pb = playbookDoc.playbooks[curPb];
  if (!pb) return;
  pb.name = $("pb-name").value;
  pb.desc = $("pb-desc").value;
}

function renderPbBar() {
  $("pb-select").innerHTML = playbookDoc.playbooks
    .map((p, i) => `<option value="${i}">${escapeHTML(p.name)}${p.id === playbookDoc.active ? " ★默认" : ""}</option>`)
    .join("");
  $("pb-select").value = String(curPb);
  const pb = playbookDoc.playbooks[curPb];
  $("pb-name").value = pb ? pb.name : "";
  $("pb-desc").value = pb ? pb.desc || "" : "";
}

function renderPlaybook() {
  const box = $("playbook-cats");
  const cats = curCats();
  if (!cats.length) {
    box.innerHTML = `<div class="pb-empty">这套打法还没有分类，点「+ 新增分类」。</div>`;
    return;
  }
  box.innerHTML = cats
    .map(
      (cat, ci) => `
    <div class="pb-cat ${cat.enabled ? "" : "off"}">
      <div class="pb-cat-head">
        <label class="pb-toggle"><input type="checkbox" data-ci="${ci}" class="pb-enable" ${cat.enabled ? "checked" : ""} /> 启用</label>
        <input type="text" class="pb-name" data-ci="${ci}" value="${escapeHTML(cat.name)}" />
        <span class="pb-count">${cat.tactics.length} 条</span>
      </div>
      <ul class="pb-tactics">
        ${cat.tactics
          .map(
            (t, ti) =>
              `<li><span>${escapeHTML(t)}</span><button type="button" class="pb-del" data-ci="${ci}" data-ti="${ti}" title="删除">✕</button></li>`
          )
          .join("")}
      </ul>
      <div class="pb-add"><input type="text" class="pb-add-input" data-ci="${ci}" placeholder="手动加一条招式…回车添加" /></div>
    </div>`
    )
    .join("");

  box.querySelectorAll(".pb-enable").forEach((el) => {
    el.onchange = () => (cats[+el.dataset.ci].enabled = el.checked);
  });
  box.querySelectorAll(".pb-name").forEach((el) => {
    el.oninput = () => (cats[+el.dataset.ci].name = el.value);
  });
  box.querySelectorAll(".pb-del").forEach((el) => {
    el.onclick = () => {
      cats[+el.dataset.ci].tactics.splice(+el.dataset.ti, 1);
      renderPlaybook();
    };
  });
  box.querySelectorAll(".pb-add-input").forEach((el) => {
    el.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      const v = el.value.trim();
      if (!v) return;
      cats[+el.dataset.ci].tactics.push(v);
      renderPlaybook();
    };
  });
}

function _mapPlaybooks(data) {
  return {
    active: data.active || "",
    playbooks: (data.playbooks || []).map((p) => ({
      id: p.id || "",
      name: p.name || "未命名打法",
      desc: p.desc || "",
      categories: (p.categories || []).map((c) => ({
        key: c.key || "",
        name: c.name || "未命名",
        enabled: c.enabled !== false,
        tactics: Array.isArray(c.tactics) ? c.tactics.slice() : [],
      })),
    })),
  };
}

async function loadPlaybook() {
  try {
    playbookDoc = _mapPlaybooks(await fetchJson("/api/playbook"));
    if (!playbookDoc.playbooks.length) playbookDoc.playbooks = [{ id: "default", name: "默认打法", desc: "", categories: [] }];
    if (curPb >= playbookDoc.playbooks.length) curPb = 0;
    $("playbook-msg").textContent = "";
    renderPbBar();
    renderPlaybook();
  } catch (err) {
    $("playbook-msg").textContent = "加载失败：" + err.message;
  }
}

function switchPlaybook() {
  applyPbMeta();
  curPb = +$("pb-select").value || 0;
  renderPbBar();
  renderPlaybook();
}

function newPlaybook() {
  applyPbMeta();
  playbookDoc.playbooks.push({
    id: "pb_" + Date.now().toString(36),
    name: "新打法",
    desc: "",
    categories: PB_DEFAULT_CATS.map(([key, name]) => ({ key, name, enabled: true, tactics: [] })),
  });
  curPb = playbookDoc.playbooks.length - 1;
  renderPbBar();
  renderPlaybook();
  $("pb-name").focus();
}

function deletePlaybook() {
  if (playbookDoc.playbooks.length <= 1) {
    $("playbook-msg").textContent = "至少保留一套打法。";
    return;
  }
  const removed = playbookDoc.playbooks.splice(curPb, 1)[0];
  if (removed && removed.id === playbookDoc.active) playbookDoc.active = playbookDoc.playbooks[0].id;
  curPb = 0;
  renderPbBar();
  renderPlaybook();
  $("playbook-msg").textContent = "已删除（记得保存）。";
}

function setActivePlaybook() {
  applyPbMeta();
  const pb = playbookDoc.playbooks[curPb];
  if (!pb) return;
  playbookDoc.active = pb.id;
  renderPbBar();
  $("playbook-msg").textContent = `「${pb.name}」已设为默认（记得保存）。`;
}

function addPlaybookCategory() {
  curCats().push({ key: "", name: "新分类", enabled: true, tactics: [] });
  renderPlaybook();
}

async function savePlaybook() {
  applyPbMeta();
  $("playbook-save").disabled = true;
  $("playbook-msg").textContent = "保存中…";
  try {
    const data = await fetchJson("/api/playbook", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: playbookDoc.active, playbooks: playbookDoc.playbooks }),
    });
    playbookDoc = _mapPlaybooks(data);
    if (curPb >= playbookDoc.playbooks.length) curPb = 0;
    renderPbBar();
    renderPlaybook();
    loadPlaybookOptions().catch(() => {});
    $("playbook-msg").textContent = "已保存，下次生成即生效。";
  } catch (err) {
    $("playbook-msg").textContent = "保存失败：" + err.message;
  } finally {
    $("playbook-save").disabled = false;
  }
}

async function loadPlaybookOptions() {
  const data = await fetchJson("/api/playbooks");
  const opts = (data.playbooks || [])
    .map((p) => `<option value="${escapeHTML(p.id)}">${escapeHTML(p.name)}</option>`)
    .join("");
  ["playbook", "distill-target", "variant-playbook"].forEach((id) => {
    const sel = $(id);
    if (sel) {
      sel.innerHTML = opts;
      sel.value = data.active || "";
    }
  });
}

async function loadXhsStatus() {
  const box = $("xhs-status");
  box.textContent = "检测小红书服务状态…";
  box.className = "xhs-status";
  try {
    const s = await fetchJson("/api/xhs/status");
    if (!s.installed) {
      box.textContent = "未安装 openclaw-xhs skill。";
      box.classList.add("warn");
    } else if (!s.running) {
      box.textContent = "MCP 服务未启动 · 终端运行 .agents/skills/xiaohongshu/scripts/start-mcp.sh 后再搜索";
      box.classList.add("warn");
    } else if (!s.logged_in) {
      box.textContent = "MCP 已启动，但未登录 · 点「获取登录二维码」用小红书 App 扫码";
      box.classList.add("warn");
    } else {
      box.textContent = "✅ 已就绪，可以搜索爆款了";
      box.classList.add("ok");
    }
  } catch (err) {
    box.textContent = "状态检测失败：" + err.message;
    box.classList.add("warn");
  }
}

function _coverProxy(url) {
  // 小红书图片有防盗链，直连常 403；交给后端图片代理。
  return url ? `/api/xhs/img?u=${encodeURIComponent(url)}` : "";
}

async function xhsSearch() {
  const keyword = $("xhs-keyword").value.trim();
  const box = $("xhs-result");
  if (!keyword) {
    box.innerHTML = `<div class="disc-empty">先输入关键词。</div>`;
    return;
  }
  $("xhs-search-btn").disabled = true;
  box.innerHTML = `<div class="disc-empty">搜索「${escapeHTML(keyword)}」…</div>`;
  try {
    const data = await fetchJson("/api/xhs/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword }),
    });
    const notes = data.notes || [];
    if (!notes.length) {
      box.innerHTML = `<div class="disc-empty">没解析出笔记。原始返回：</div><pre class="research-result">${escapeHTML((data.output || "").slice(0, 1500))}</pre>`;
      return;
    }
    box.innerHTML = notes
      .map(
        (n, i) => `
      <div class="xhs-card" data-i="${i}">
        ${n.cover ? `<img class="xhs-cover" loading="lazy" alt="" src="${_coverProxy(n.cover)}" />` : `<div class="xhs-cover xhs-nocover">无封面</div>`}
        <div class="xhs-card-body">
          <strong>${escapeHTML(n.title)}</strong>
          <span class="xhs-author">@${escapeHTML(n.author || "")}</span>
          <span class="xhs-metrics">❤ ${escapeHTML(n.liked)} · ⭐ ${escapeHTML(n.collected)} · 💬 ${escapeHTML(n.comment)}</span>
          <button type="button" class="mini primary xhs-teardown" data-id="${escapeHTML(n.id)}" data-token="${escapeHTML(n.xsec_token)}" data-title="${escapeHTML(n.title)}">拆传播力</button>
        </div>
      </div>`
      )
      .join("");
    box.querySelectorAll(".xhs-teardown").forEach((btn) => {
      btn.onclick = () => xhsTeardown(btn);
    });
  } catch (err) {
    box.innerHTML = `<div class="disc-empty">搜索失败：${escapeHTML(err.message)}</div>`;
  } finally {
    $("xhs-search-btn").disabled = false;
  }
}

async function xhsTeardown(btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "取正文…(含重试)";
  try {
    const data = await fetchJson("/api/xhs/feed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feed_id: btn.dataset.id, xsec_token: btn.dataset.token }),
    });
    const text = (data.text || "").trim();
    if (!text) throw new Error("没取到正文");
    $("analyze-input").value = text;
    switchView("research");
    switchResearchTab("analyze");
    window.scrollTo({ top: 0, behavior: "smooth" });
    analyzeNote();
    btn.textContent = orig;
  } catch (err) {
    btn.textContent = "打不开";
    btn.title = err.message || "";
    setTimeout(() => {
      btn.textContent = orig;
      btn.title = "";
    }, 2500);
  } finally {
    btn.disabled = false;
  }
}

async function xhsLoginQr() {
  const box = $("xhs-qr");
  box.innerHTML = `<span class="disc-empty">获取登录二维码…（首次拉起浏览器稍慢）</span>`;
  try {
    const data = await fetchJson("/api/xhs/login-qr", { method: "POST" });
    if (data.qr_image) {
      box.innerHTML = `<img class="xhs-qr-img" alt="登录二维码" src="${data.qr_image}" /><div class="disc-empty">${escapeHTML(data.text || "用小红书 App 扫码登录")}</div>`;
      pollLoginStatus();
    } else {
      box.innerHTML = `<pre class="research-result">${escapeHTML(data.text || "（无返回，确认 MCP 已启动）")}</pre>`;
    }
  } catch (err) {
    box.innerHTML = `<span class="disc-empty">获取失败：${escapeHTML(err.message)}</span>`;
  }
}

let loginPollTimer = null;
function pollLoginStatus() {
  if (loginPollTimer) clearInterval(loginPollTimer);
  let tries = 0;
  loginPollTimer = setInterval(async () => {
    tries += 1;
    try {
      const s = await fetchJson("/api/xhs/status");
      if (s.logged_in) {
        clearInterval(loginPollTimer);
        loginPollTimer = null;
        $("xhs-qr").innerHTML = `<div class="login-ok">✅ 登录成功！可以搜索了</div>`;
        loadXhsStatus().catch(() => {});
      }
    } catch (_e) {}
    if (tries >= 40) {
      clearInterval(loginPollTimer);
      loginPollTimer = null;
    }
  }, 3000);
}

// ===== 起号计划 =====
let planData = null;
const PLAN_STATUS = { todo: "⚪ 待写", generated: "🟡 已生成", published: "✅ 已发布" };
const PLAN_NEXT = { todo: "generated", generated: "published", published: "todo" };

async function loadPlan() {
  try {
    const data = await fetchJson("/api/plan");
    planData = data && (data.stages || data.topics) ? data : null;
    if (planData && planData.positioning) {
      $("plan-domain").value = planData.positioning.domain || "";
      $("plan-persona").value = planData.positioning.persona || "";
      $("plan-audience").value = planData.positioning.audience || "";
      $("plan-goal").value = planData.positioning.goal || "";
    }
    renderPlan();
  } catch (_e) {}
}

async function generatePlan() {
  $("plan-gen").disabled = true;
  $("plan-msg").textContent = "AI 生成中…（约十几秒）";
  try {
    planData = await fetchJson("/api/plan/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain: $("plan-domain").value,
        persona: $("plan-persona").value,
        audience: $("plan-audience").value,
        goal: $("plan-goal").value,
      }),
    });
    $("plan-msg").textContent = "已生成。";
    renderPlan();
  } catch (err) {
    $("plan-msg").textContent = "失败：" + err.message;
  } finally {
    $("plan-gen").disabled = false;
  }
}

async function savePlan() {
  if (!planData) return;
  try {
    await fetchJson("/api/plan", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan: planData }),
    });
  } catch (_e) {}
}

function renderPlan() {
  const box = $("plan-body");
  if (!planData) {
    box.innerHTML = `<div class="disc-empty">还没有计划。填上面的定位，点「生成起号计划」。</div>`;
    return;
  }
  const stages = (planData.stages || [])
    .map(
      (s) => `
    <div class="plan-stage">
      <div class="plan-stage-head"><strong>${escapeHTML(s.name)}</strong><span>${escapeHTML(s.cadence || "")}</span></div>
      <div class="plan-stage-goal">🎯 ${escapeHTML(s.goal || "")}</div>
      ${s.topic_focus ? `<div class="plan-stage-focus">优先写：${escapeHTML(s.topic_focus)}</div>` : ""}
      ${(s.actions || []).length ? `<ul class="plan-actions">${s.actions.map((a) => `<li>${escapeHTML(a)}</li>`).join("")}</ul>` : ""}
    </div>`
    )
    .join("");

  const topics = planData.topics || [];
  const pillars = planData.pillars && planData.pillars.length ? planData.pillars : [...new Set(topics.map((t) => t.pillar))];
  const done = topics.filter((t) => t.status === "published").length;
  const groups = pillars
    .map((p) => {
      const items = topics.filter((t) => (t.pillar || "") === p);
      if (!items.length) return "";
      return `<div class="plan-pillar"><div class="plan-pillar-name">📌 ${escapeHTML(p)}</div>${items.map(planTopicRow).join("")}</div>`;
    })
    .join("");
  const ungrouped = topics.filter((t) => !pillars.includes(t.pillar || ""));
  const extra = ungrouped.length ? `<div class="plan-pillar"><div class="plan-pillar-name">📌 其它</div>${ungrouped.map(planTopicRow).join("")}</div>` : "";

  box.innerHTML =
    `<div class="plan-section-title">起号三阶段</div><div class="plan-stages">${stages}</div>` +
    `<div class="plan-section-title">选题库（${topics.length} 条 · 已发 ${done}）</div>${groups}${extra}`;

  box.querySelectorAll(".plan-status").forEach((el) => {
    el.onclick = () => {
      const t = (planData.topics || []).find((x) => x.id === el.dataset.id);
      if (!t) return;
      t.status = PLAN_NEXT[t.status] || "todo";
      renderPlan();
      savePlan();
    };
  });
  box.querySelectorAll(".plan-gen-topic").forEach((el) => {
    el.onclick = () => {
      const t = (planData.topics || []).find((x) => x.id === el.dataset.id);
      if (!t) return;
      $("topic").value = t.title;
      $("inspire-direction").value = t.pillar || t.angle || "";
      if (t.status === "todo") {
        t.status = "generated";
        savePlan();
      }
      switchView("create");
      window.scrollTo({ top: 0, behavior: "smooth" });
      $("status").textContent = "已带入选题，填好其余项点「开始生成」。";
    };
  });
}

function planTopicRow(t) {
  return `<div class="plan-topic">
    <button type="button" class="plan-status" data-id="${escapeHTML(t.id)}">${PLAN_STATUS[t.status] || PLAN_STATUS.todo}</button>
    <div class="plan-topic-body"><strong>${escapeHTML(t.title)}</strong>${t.angle ? `<span>${escapeHTML(t.angle)}</span>` : ""}</div>
    <button type="button" class="mini primary plan-gen-topic" data-id="${escapeHTML(t.id)}">去生成</button>
  </div>`;
}

// ===== 我的数据 =====
let myposts = [];

async function loadMyposts() {
  try {
    const d = await fetchJson("/api/myposts");
    myposts = d.posts || [];
    renderMyposts();
  } catch (_e) {}
}

async function saveMyposts() {
  try {
    const d = await fetchJson("/api/myposts", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ posts: myposts }),
    });
    myposts = d.posts || myposts;
  } catch (_e) {}
}

function addMypost() {
  const title = $("mp-title").value.trim();
  if (!title) {
    $("mp-msg").textContent = "先填标题。";
    return;
  }
  myposts.push({
    id: "mp_" + Date.now().toString(36),
    title,
    liked: +$("mp-liked").value || 0,
    collected: +$("mp-collected").value || 0,
    comment: +$("mp-comment").value || 0,
    content: $("mp-content").value.trim(),
  });
  ["mp-title", "mp-liked", "mp-collected", "mp-comment", "mp-content"].forEach((id) => ($(id).value = ""));
  $("mp-msg").textContent = "已添加。";
  saveMyposts();
  renderMyposts();
}

function renderMyposts() {
  const box = $("mp-list");
  if (!myposts.length) {
    box.innerHTML = `<div class="disc-empty">还没数据。发布后把标题 + 点赞/收藏/评论填进来。</div>`;
    return;
  }
  const sorted = [...myposts].sort((a, b) => b.liked + b.collected - (a.liked + a.collected));
  const top = sorted[0].liked + sorted[0].collected;
  box.innerHTML = sorted
    .map(
      (p, i) => `
    <div class="mypost ${i === 0 && top > 0 ? "winner" : ""}">
      <div class="mp-body">
        <strong>${i === 0 && top > 0 ? "🏆 " : ""}${escapeHTML(p.title)}</strong>
        <span class="mp-metrics">❤ ${p.liked} · ⭐ ${p.collected} · 💬 ${p.comment}</span>
      </div>
      ${p.content ? `<button type="button" class="mini primary mp-teardown" data-id="${escapeHTML(p.id)}">拆传播力</button>` : ""}
      <button type="button" class="mini subtle mp-del" data-id="${escapeHTML(p.id)}" title="删除">✕</button>
    </div>`
    )
    .join("");
  box.querySelectorAll(".mp-del").forEach((b) => {
    b.onclick = () => {
      myposts = myposts.filter((x) => x.id !== b.dataset.id);
      saveMyposts();
      renderMyposts();
    };
  });
  box.querySelectorAll(".mp-teardown").forEach((b) => {
    b.onclick = () => {
      const p = myposts.find((x) => x.id === b.dataset.id);
      if (!p || !p.content) return;
      $("analyze-input").value = p.content;
      switchView("research");
      switchResearchTab("analyze");
      window.scrollTo({ top: 0, behavior: "smooth" });
      analyzeNote();
    };
  });
}

// ===== 照片优先 =====
function pfToggle() {
  const body = $("pf-body");
  const willOpen = body.classList.contains("hidden");
  body.classList.toggle("hidden", !willOpen);
  $("pf-toggle").textContent = willOpen ? "收起" : "展开";
}

async function pfGen() {
  const scene = $("pf-scene").value.trim();
  if (scene.length < 4) {
    $("pf-caption").textContent = "先用一句话描述照片场景 / 你在干嘛。";
    return;
  }
  $("pf-gen").disabled = true;
  $("pf-caption").textContent = "AI 围绕照片写文案中…";
  $("pf-copy").classList.add("hidden");
  try {
    const d = await fetchJson("/api/photo-caption", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene }),
    });
    $("pf-caption").textContent = d.caption || "（无返回）";
    $("pf-copy").classList.remove("hidden");
  } catch (err) {
    $("pf-caption").textContent = "失败：" + err.message;
  } finally {
    $("pf-gen").disabled = false;
  }
}

function switchView(view) {
  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.dataset.view === view);
  });
  if (view === "discover") loadXhsStatus().catch(() => {});
  if (view === "plan") loadPlan().catch(() => {});
  if (view === "mydata") loadMyposts().catch(() => {});
}

document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
  btn.onclick = () => switchView(btn.dataset.view);
});
document.querySelectorAll(".dtab").forEach((btn) => {
  btn.onclick = () => switchDiscoverTab(btn.dataset.dtab);
});
$("plan-gen").onclick = generatePlan;
$("mp-add").onclick = addMypost;
$("pf-toggle").onclick = pfToggle;
$("pf-gen").onclick = pfGen;
$("pf-photo").onchange = (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => ($("pf-preview").innerHTML = `<img class="pf-img" src="${r.result}" alt="" />`);
  r.readAsDataURL(f);
};
$("pf-copy").onclick = () => {
  navigator.clipboard.writeText($("pf-caption").textContent).then(() => {
    $("pf-copy").textContent = "已复制";
    setTimeout(() => ($("pf-copy").textContent = "复制文案"), 1200);
  });
};
$("disc-inspire-btn").onclick = discInspire;

document.querySelectorAll(".research-tab").forEach((btn) => {
  btn.onclick = () => switchResearchTab(btn.dataset.tab);
});
$("analyze-btn").onclick = analyzeNote;
$("distill-btn").onclick = distillTactics;
$("th-refresh").onclick = () => loadTeardowns().catch(() => {});
$("playbook-save").onclick = savePlaybook;
$("playbook-add-cat").onclick = addPlaybookCategory;
$("pb-select").onchange = switchPlaybook;
$("pb-new").onclick = newPlaybook;
$("pb-del").onclick = deletePlaybook;
$("pb-setactive").onclick = setActivePlaybook;
$("pb-name").oninput = applyPbMeta;
$("pb-desc").oninput = applyPbMeta;
$("hist-refresh").onclick = () => loadHistory().catch(() => {});
$("variant-gen").onclick = () => {
  if (!$("topic").value.trim()) {
    $("status").textContent = "先打开一个发布包或填选题，再换打法出一版。";
    return;
  }
  $("playbook").value = $("variant-playbook").value;
  window.scrollTo({ top: 0, behavior: "smooth" });
  generate();
};
$("xhs-search-btn").onclick = xhsSearch;
$("xhs-login-btn").onclick = xhsLoginQr;

$("gen").onclick = generate;
loadPresets().catch((err) => {
  $("status").textContent = "加载视觉风格失败：" + err.message;
});
loadPlaybookOptions().catch(() => {});
loadHistory().catch((err) => {
  $("status").textContent = "加载历史发布包失败：" + err.message;
});
