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
  const presets = await fetchJson("/api/presets");
  $("style").innerHTML = presets
    .map((p) => `<option value="${escapeHTML(p.key)}">${escapeHTML(p.name)}</option>`)
    .join("");
}

async function loadHistory() {
  const pkgs = await fetchJson("/api/packages");
  $("history").innerHTML = pkgs
    .map((p) => `<li data-name="${encodeURIComponent(p.name)}">${escapeHTML(p.name)}（${p.cards.length} 张）</li>`)
    .join("");
  document.querySelectorAll("#history li").forEach((li) => {
    li.onclick = () => showPackage(decodeURIComponent(li.dataset.name));
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
  $("tab-search").classList.toggle("hidden", tab !== "search");
  if (tab === "search") loadXhsStatus().catch(() => {});
  if (tab === "analyze") loadTeardowns().catch(() => {});
  if (tab === "playbook") loadPlaybook().catch(() => {});
}

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
  ["playbook", "distill-target"].forEach((id) => {
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

async function xhsSearch() {
  const keyword = $("xhs-keyword").value.trim();
  if (!keyword) {
    $("xhs-result").textContent = "先输入关键词。";
    return;
  }
  $("xhs-search-btn").disabled = true;
  $("xhs-result").textContent = `搜索「${keyword}」…`;
  try {
    const data = await fetchJson("/api/xhs/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword }),
    });
    $("xhs-result").textContent = data.output || "（无结果）";
  } catch (err) {
    $("xhs-result").textContent = "搜索失败：" + err.message;
  } finally {
    $("xhs-search-btn").disabled = false;
  }
}

async function xhsLoginQr() {
  $("xhs-result").textContent = "获取登录二维码…";
  try {
    const data = await fetchJson("/api/xhs/login-qr", { method: "POST" });
    $("xhs-result").textContent = data.output || "（无返回）";
  } catch (err) {
    $("xhs-result").textContent = "获取失败：" + err.message;
  }
}

function switchView(view) {
  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.dataset.view === view);
  });
  if (view === "history") loadHistory().catch(() => {});
}

document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
  btn.onclick = () => switchView(btn.dataset.view);
});

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
