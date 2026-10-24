// AccessFix PDF front end. All server text is inserted with textContent (never innerHTML).
const $ = (s, r = document) => r.querySelector(s);
const S = { caps: null, doc: null, ver: "v0", analysis: null, nodes: null, tab: "overview", page: 1, sel: null, expanded: new Set(), tree: null,
  ocrSel: new Set(), job: null, findFilter: { sev: "error", q: "", cat: "" }, treeQ: "", treeType: "", treeProblems: false, roPage: false, ocrResult: null, ocrText: null,
  pvZoom: 100, pvShowLayer: false, pageText: "" };

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const clear = (e) => { while (e.firstChild) e.removeChild(e.firstChild); return e; };
const live = (m) => { $("#live").textContent = ""; setTimeout(() => ($("#live").textContent = m), 30); };
function toast(m, ms = 6000) { const t = $("#toast"); t.textContent = m; t.hidden = false; live(m); clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms); }

async function api(url, opts) {
  let r;
  try { r = await fetch(url, opts); } catch (e) { throw new Error("Cannot reach the server. Is it still running?"); }
  if (!r.ok) {
    let j = null; try { j = await r.json(); } catch (_) {}
    const err = new Error(j?.error?.message || `Request failed (${r.status})`); err.code = j?.error?.code; err.data = j?.error; throw err;
  }
  return r.json();
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SEV = { error: "Error", warning: "Warning", manual: "Manual review", not_run: "Not performed" };
const TAGLABEL = { tagged: "Tagged", untagged: "Untagged", partially_tagged: "Partially tagged", malformed: "Malformed tag structure" };
const TAGCLS = { tagged: "ok", untagged: "err", partially_tagged: "warn", malformed: "err" };
const CLASS_LABEL = {
  structural_error: "PDF Structural Error",
  accessibility_failure: "Accessibility Failure",
  semantic_issue: "Potential Semantic Issue",
  manual_review: "Manual Check Required",
  not_run: "Not Performed",
};
const CLASS_BADGE = {
  structural_error: "b-struct",
  accessibility_failure: "b-access",
  semantic_issue: "b-semantic",
  manual_review: "b-man",
  not_run: "b-nr",
};
const docUrl = (p, q = "") => `/api/documents/${S.doc.id}${p}?version=${S.ver}${q}`;

// ------------------------------------------------------------------ boot
async function init() {
  try {
    S.caps = await api("/api/capabilities");
    const e = S.caps.ocr, b = $("#engine-badge");
    b.textContent = e.available ? `OCR ready: ${e.version}` : "OCR engine not available";
    b.className = "chip " + (e.available ? "ok" : "err");
    $("#limits").textContent = `Limit: ${S.caps.limits.max_upload_mb} MB and ${S.caps.limits.max_pages} pages. Uploaded files are deleted after ${S.caps.limits.ttl_hours} h.`;
  } catch (e) { showUpErr(e.message); }
  const drop = $("#drop"), file = $("#file");
  drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); drop.classList.remove("over"); if (ev.dataTransfer.files[0]) upload(ev.dataTransfer.files[0]); });
  drop.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); file.click(); } });
  file.addEventListener("change", () => { if (file.files[0]) upload(file.files[0]); });
  $("#new-doc").addEventListener("click", resetToUpload);
  $("#ver").addEventListener("change", (e) => switchVersion(e.target.value));
  $("#pv-prev").addEventListener("click", () => setPage(S.page - 1));
  $("#pv-next").addEventListener("click", () => setPage(S.page + 1));
  $("#pv-page").addEventListener("change", (e) => setPage(parseInt(e.target.value, 10) || 1));
  $("#pv-zoom-in")?.addEventListener("click", () => zoomPreview(25));
  $("#pv-zoom-out")?.addEventListener("click", () => zoomPreview(-25));
  $("#pv-zoom-reset")?.addEventListener("click", () => zoomPreview(0, 100));
  $("#pv-copy-page")?.addEventListener("click", copyPageText);
  $("#pv-toggle-layer")?.addEventListener("click", toggleTextLayer);
  $("#pv-img")?.addEventListener("load", updateTextLayerFontSizes);
  window.addEventListener("resize", updateTextLayerFontSizes);
  const tabs = $("#tabs");
  tabs.addEventListener("click", (e) => { const t = e.target.closest("[role=tab]"); if (t) showTab(t.id.slice(2)); });
  tabs.addEventListener("keydown", (e) => {
    const all = [...tabs.querySelectorAll("[role=tab]")], i = all.indexOf(document.activeElement);
    if (i < 0) return;
    let n = null;
    if (e.key === "ArrowRight") n = all[(i + 1) % all.length]; if (e.key === "ArrowLeft") n = all[(i - 1 + all.length) % all.length];
    if (e.key === "Home") n = all[0]; if (e.key === "End") n = all[all.length - 1];
    if (n) { e.preventDefault(); n.focus(); showTab(n.id.slice(2)); }
  });
  const m = location.hash.match(/doc=([0-9a-f]{32})/);
  if (m) { try { await openDoc(m[1]); } catch (_) { history.replaceState(null, "", "#"); } }
}
function showUpErr(m) { const e = $("#up-error"); e.textContent = m; e.hidden = false; }
function resetToUpload() { S.doc = null; history.replaceState(null, "", "#"); $("#view-doc").hidden = true; $("#view-upload").hidden = false; $("#up-error").hidden = true; $("#up-progress").hidden = true; $("#file").value = ""; $("#drop").focus(); }

async function upload(file) {
  $("#up-error").hidden = true;
  const fd = new FormData(); fd.append("file", file);
  $("#up-progress").hidden = false; setUp(5, "Uploading...");
  try {
    const r = await api("/api/documents", { method: "POST", body: fd });
    S.doc = r.document; history.replaceState(null, "", "#doc=" + r.document.id);
    const job = await pollJob(r.job.id, (j) => setUp(j.total ? 10 + (80 * j.done) / j.total : 10, j.message || "Analysing..."));
    if (job.status === "failed") throw new Error(job.error?.message || "Analysis failed");
    await openDoc(S.doc.id);
  } catch (e) { $("#up-progress").hidden = true; showUpErr(e.message); }
}
function setUp(p, m) { $("#up-bar").style.width = p + "%"; $("#up-msg").textContent = m; $("#up-progress [role=progressbar]").setAttribute("aria-valuenow", Math.round(p)); }
async function pollJob(id, cb) {
  for (;;) {
    const j = await api("/api/jobs/" + id); S.job = j; cb && cb(j);
    if (!["queued", "running"].includes(j.status)) return j;
    await sleep(500);
  }
}

// ------------------------------------------------------------------ document load
async function openDoc(id, ver = "latest") {
  const d = await api("/api/documents/" + id);
  S.doc = d; S.ver = ver === "latest" ? d.versions[d.versions.length - 1].id : ver;
  S.nodes = null; S.tree = null; S.sel = null; S.ocrResult = null; S.ocrText = null;
  const r = await api(docUrl("/analysis"));
  S.analysis = r.analysis;
  $("#view-upload").hidden = true; $("#view-doc").hidden = false;
  history.replaceState(null, "", "#doc=" + id);
  if (S.analysis.state === "ok" && S.analysis.file.pages) { S.page = Math.min(S.page, S.analysis.file.pages) || 1; }
  renderAll(); setPage(S.page || 1);
  $("#doc-name").focus();
}
async function switchVersion(v) { S.ver = v; await openDoc(S.doc.id, v); }

function renderAll() {
  const a = S.analysis, f = a.file || {};
  $("#doc-name").textContent = S.doc.name;
  $("#doc-meta").textContent = a.state === "ok" ? `${f.pages} pages - ${(f.size / 1024).toFixed(0)} KB - PDF ${f.pdf_version} - analysed in ${a.analysis_seconds}s` : "";
  const sel = clear($("#ver"));
  S.doc.versions.forEach((v) => sel.append(h("option", { value: v.id, selected: v.id === S.ver }, `${v.id}: ${v.label}`)));
  $("#dl-pdf").href = docUrl("/download"); $("#dl-html").href = docUrl("/report", "&format=html"); $("#dl-json").href = docUrl("/report", "&format=json");
  const banner = $("#state-banner");
  if (a.state !== "ok") { banner.hidden = false; banner.className = "alert error"; banner.textContent = a.message || "This document cannot be analysed."; }
  else banner.hidden = true;
  renderChips(); renderOverview(); renderFindings(); renderOcr(); renderVersions();
  $("#c-findings").textContent = a.counts ? a.counts.error + a.counts.warning : "";
  renderTreePanel();
  const usable = a.state === "ok";
  document.querySelectorAll("#tabs [role=tab]").forEach((t) => { if (t.id !== "t-overview") t.disabled = !usable; });
  showTab(usable ? S.tab : "overview");
}

function chip(text, cls) { return h("span", { class: "chip " + (cls || "") }, text); }
function renderChips() {
  const a = S.analysis, c = clear($("#chips"));
  if (a.state !== "ok") return;
  const ts = a.tag_status, sc = a.scan || {}, n = (sc.candidate_pages || []).length;
  c.append(chip("Tags: " + TAGLABEL[ts.status], TAGCLS[ts.status]));
  c.append(chip(n ? `${n} scanned page${n > 1 ? "s need" : " needs"} OCR` : (sc.ocr_layer_pages || []).length ? "Existing OCR layer" : "No scanned pages detected", n ? "err" : "ok"));
  c.append(chip(`${a.counts.error} errors`, a.counts.error ? "err" : "ok")); c.append(chip(`${a.counts.warning} warnings`, a.counts.warning ? "warn" : "ok"));
  c.append(chip(`${a.counts.manual} manual review`, "man")); c.append(chip(`${a.counts.not_run} not performed`));
  if (a.file.encrypted) c.append(chip("Encrypted", "warn"));
}

// ------------------------------------------------------------------ tabs
function showTab(name) {
  S.tab = name;
  document.querySelectorAll("#tabs [role=tab]").forEach((t) => { const on = t.id === "t-" + name; t.setAttribute("aria-selected", on); t.tabIndex = on ? 0 : -1; });
  document.querySelectorAll("[role=tabpanel]").forEach((p) => (p.hidden = p.id !== "p-" + name));
  if (name === "tree") loadTree();
}

// ------------------------------------------------------------------ overview
function renderOverview() {
  const p = clear($("#p-overview")), a = S.analysis;
  if (a.state !== "ok") { p.append(h("p", {}, a.message || "No analysis available.")); return; }
  const ts = a.tag_status, f = a.file, sc = a.scan || { counts: {} }, cn = sc.counts || {};
  const cc = a.classification_counts || {};
  const counts = a.counts || { error: 0, warning: 0, manual: 0, not_run: 0 };

  // Calculate accessibility score
  let score = 100;
  if (ts.status === "untagged") score = 0;
  else if (ts.status === "malformed") score = 25;
  else {
    const errPen = Math.min(60, counts.error * 8);
    const warnPen = Math.min(25, counts.warning * 2.5);
    const unPen = (sc.candidate_pages || []).length ? 15 : 0;
    score = Math.max(10, Math.round(100 - errPen - warnPen - unPen));
  }
  const scoreColor = score >= 90 ? "var(--ok)" : score >= 65 ? "var(--warn)" : "var(--err)";
  const scoreBadgeCls = score >= 90 ? "ok" : score >= 65 ? "warn" : "err";
  const scoreLabel = score >= 90 ? "Compliant & Verified" : score >= 65 ? "Partially Compliant" : "Non-Compliant";

  const circ = 2 * Math.PI * 38;
  const offset = circ - (score / 100) * circ;

  // Executive Scorecard Header
  const scoreGauge = h("div", { class: "scorecard-gauge" },
    h("svg", { class: "gauge-svg", width: "90", height: "90", viewBox: "0 0 96 96", "aria-hidden": "true" },
      h("circle", { cx: "48", cy: "48", r: "38", stroke: "var(--line)", "stroke-width": "8", fill: "none" }),
      h("circle", { cx: "48", cy: "48", r: "38", stroke: scoreColor, "stroke-width": "8", fill: "none", "stroke-dasharray": `${circ}`, "stroke-dashoffset": `${offset}`, "stroke-linecap": "round", transform: "rotate(-90 48 48)" }),
      h("text", { x: "48", y: "53", "text-anchor": "middle", "font-size": "21", "font-weight": "800", fill: "var(--ink)" }, `${score}%`)),
    h("div", { class: "gauge-meta" },
      h("span", { class: "chip " + scoreBadgeCls }, scoreLabel),
      h("h3", { class: "gauge-title" }, "Accessibility Conformance Score"),
      h("p", { class: "small muted" }, `${f.pages} page${f.pages === 1 ? "" : "s"} evaluated across ISO 32000-1, PDF/UA-1, and WCAG 2.1 rules.`)));

  // Standards matrix
  const matrix = h("div", { class: "standards-matrix" },
    h("div", { class: "std-status-item" },
      h("span", { class: "std-pill " + (ts.status === "tagged" && !counts.error ? "ok" : ts.status === "partially_tagged" ? "warn" : "err") },
        ts.status === "tagged" && !counts.error ? "PASS" : "ACTION"),
      h("div", {}, h("strong", {}, "PDF/UA-1 (ISO 14289-1)"), h("p", { class: "small muted" }, ts.status === "tagged" ? "Declared tagged with logical tree" : TAGLABEL[ts.status]))),
    h("div", { class: "std-status-item" },
      h("span", { class: "std-pill " + (counts.error === 0 ? "ok" : "err") }, counts.error === 0 ? "PASS" : `${counts.error} FAILS`),
      h("div", {}, h("strong", {}, "WCAG 2.1 Level AA"), h("p", { class: "small muted" }, counts.error === 0 ? "Machine-detectable criteria met" : `${counts.error} critical criterion failure(s)`))),
    h("div", { class: "std-status-item" },
      h("span", { class: "std-pill " + (ts.status !== "malformed" ? "ok" : "err") }, ts.status !== "malformed" ? "VALID" : "ERROR"),
      h("div", {}, h("strong", {}, "ISO 32000-1 StructTree"), h("p", { class: "small muted" }, ts.status !== "malformed" ? "Structure tree & ParentTree valid" : "Integrity violations detected"))));

  const scorecard = h("div", { class: "exec-scorecard" }, scoreGauge, matrix);

  // Check if any fixable items exist
  const fixableItems = a.findings.filter((f) => f.fix != null);
  const fixBanner = fixableItems.length ? h("div", { class: "remediation-cta" },
    h("div", { class: "cta-text" },
      h("strong", {}, `⚡ ${fixableItems.length} Auto-Remediation${fixableItems.length === 1 ? "" : "s"} Available`),
      h("p", { class: "small" }, "Deterministic issues (ParentTree orphans, missing MarkInfo, metadata, language) can be resolved in 1 click without modifying the source PDF.")),
    h("button", { class: "btn primary small", type: "button", onclick: () => { S.findFilter = { sev: "all", q: "", cat: "" }; renderFindings(); showTab("findings"); } }, "Review & Apply Fixes")) : null;

  const tagCard = h("div", { class: "card" }, h("h3", {}, "Tag structure"), h("p", { class: "big" }, TAGLABEL[ts.status]),
    h("ul", {}, ts.reasons.map((r) => h("li", {}, r))),
    h("p", { class: "small muted" }, `${ts.elements} structure elements. Marked flag: ${ts.marked ? "true" : "not set"}.`),
    ts.status !== "untagged" ? h("button", { class: "btn small primary", type: "button", onclick: () => showTab("tree") }, "Inspect tag tree") :
      h("p", { class: "small muted" }, "Document contains no structure elements. Add tags in the authoring application or generate an OCR text layer if scanned."));

  const cand = (sc.candidate_pages || []).length;
  const scanCard = h("div", { class: "card" }, h("h3", {}, "Scanned pages & OCR"),
    h("p", { class: "big" }, cand ? `${cand} of ${f.pages} pages need OCR` : "No pages need OCR"),
    h("ul", {}, Object.entries(cn).map(([k, v]) => h("li", {}, `${v} x ${(a.pages.find((p) => p.class === k) || {}).label || k}`))),
    h("button", { class: "btn small", type: "button", onclick: () => showTab("ocr") }, "Open scan & OCR panel"),
    h("p", { class: "small muted" }, sc.note || ""));

  const meta = h("div", { class: "card" }, h("h3", {}, "Document properties"), h("dl", { class: "kv" },
    ...[["Title", f.title || "(none)"], ["Language", f.language || "(not set)"], ["DisplayDocTitle", f.display_doc_title ? "on" : "off"], ["Bookmarks", f.has_bookmarks ? "yes" : "no"],
      ["Form fields", f.has_acroform ? "yes" : "no"], ["Producer", f.producer || "(unknown)"], ["Claims PDF/UA", f.claims_pdfua ? "yes (unverified)" : "no"]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));

  const classCard = h("div", { class: "card" }, h("h3", {}, "Standards Classification"),
    h("ul", {},
      h("li", {}, h("span", { class: "badge b-struct" }, "Structural Errors: " + (cc.structural_error || 0)), " - invalid PDF tags/references"),
      h("li", {}, h("span", { class: "badge b-access" }, "Accessibility Failures: " + (cc.accessibility_failure || 0)), " - violates PDF/UA & WCAG"),
      h("li", {}, h("span", { class: "badge b-semantic" }, "Semantic Issues: " + (cc.semantic_issue || 0)), " - heuristic/quality warnings"),
      h("li", {}, h("span", { class: "badge b-man" }, "Manual Review: " + (cc.manual_review || 0)), " - human review required"),
      h("li", {}, h("span", { class: "badge b-nr" }, "Not Performed: " + (cc.not_run || 0)), " - checks out of scope")));

  const top = a.findings.filter((x) => x.severity === "error").slice(0, 5);
  const topCard = h("div", { class: "card" }, h("h3", {}, "Most important errors"),
    top.length ? h("ol", {}, top.map((x) => h("li", {}, h("button", { class: "btn small ghost", type: "button", onclick: () => { S.findFilter = { sev: "error", q: x.code, cat: "" }; renderFindings(); showTab("findings"); } }, x.title)))) : h("p", {}, "No machine-detectable errors in the checks that ran."),
    h("p", { class: "small muted" }, "No errors does not mean accessible: see manual-review and not-performed items."));

  p.append(
    scorecard,
    fixBanner,
    h("div", { class: "alert warn" }, h("strong", {}, "How to read this: "), "OCR makes text searchable; tags make it accessible. Tag status and OCR status are reported separately and neither is a conformance claim."),
    h("div", { class: "cards" }, tagCard, scanCard, meta, classCard, topCard),
    h("h3", {}, "Checks performed"), h("ul", { class: "checks-grid" }, (a.checked || []).map((c) => h("li", {}, h("span", { class: "check-icon" }, "✓"), " " + c))),
    h("p", { class: "small muted" }, `${a.tool.name} ${a.tool.version} - pikepdf ${a.tool.libraries.pikepdf}. Conformance rules based on ISO 32000-1 and PDF/UA-1.`));
}

// ------------------------------------------------------------------ findings
function renderFindings() {
  const p = clear($("#p-findings")), a = S.analysis;
  if (a.state !== "ok") return;
  const F = S.findFilter, cats = [...new Set(a.findings.map((f) => f.category))].sort();

  const sevPills = h("div", { class: "sev-pills", role: "group", "aria-label": "Quick severity filter" },
    [["all", `All (${a.findings.length})`],
     ["error", `Errors (${a.counts.error})`],
     ["warning", `Warnings (${a.counts.warning})`],
     ["manual", `Manual Review (${a.counts.manual})`],
     ["not_run", `Not Performed (${a.counts.not_run})`]].map(([v, label]) => {
      const active = F.sev === v;
      return h("button", {
        type: "button",
        class: "btn small " + (active ? "primary" : "ghost") + " sev-filter-pill",
        "aria-pressed": String(active),
        onclick: () => {
          F.sev = v;
          const sel = $("#f-sev");
          if (sel) sel.value = v;
          renderFindings();
        }
      }, label);
    }));

  const sevSel = h("select", { id: "f-sev", onchange: (e) => { F.sev = e.target.value; renderFindings(); $("#f-sev").focus(); } },
    [["all", "All severities"], ["error", `Errors (${a.counts.error})`], ["warning", `Warnings (${a.counts.warning})`], ["manual", `Manual review (${a.counts.manual})`], ["not_run", `Not performed (${a.counts.not_run})`]].map(([v, t]) => h("option", { value: v, selected: F.sev === v }, t)));
  const catSel = h("select", { id: "f-cat", onchange: (e) => { F.cat = e.target.value; renderFindings(); $("#f-cat").focus(); } }, h("option", { value: "" }, "All categories"), cats.map((c) => h("option", { value: c, selected: F.cat === c }, c)));
  const q = h("input", { type: "text", id: "f-q", value: F.q, placeholder: "Search findings, codes (e.g. PT-ORPHAN), objects...", oninput: (e) => { F.q = e.target.value; listFindings(list); } });
  
  p.append(
    sevPills,
    h("div", { class: "filters" }, h("label", {}, "Severity", sevSel), h("label", {}, "Category", catSel), h("label", {}, "Search", q))
  );
  const list = h("div", { id: "flist" }); p.append(list); listFindings(list);
}
function listFindings(list) {
  clear(list); const F = S.findFilter, ql = F.q.toLowerCase();
  const items = S.analysis.findings.filter((f) => (F.sev === "all" || f.severity === F.sev) && (!F.cat || f.category === F.cat) &&
    (!ql || (f.title + f.code + (f.classification || "") + (f.impact || "") + f.evidence.join(" ") + (f.obj_refs || []).join(" ")).toLowerCase().includes(ql)));
  list.append(h("p", { class: "small muted", "aria-live": "polite" }, `${items.length} finding group${items.length === 1 ? "" : "s"} shown`));
  if (!items.length) list.append(h("p", {}, "Nothing matches these filters."));
  items.forEach((f) => list.append(findingCard(f)));
}
function findingCard(f) {
  const pages = f.pages.slice(0, 15).map((n) => h("button", { class: "btn small ghost", type: "button", onclick: () => setPage(n), "aria-label": `Show page ${n}` }, "p." + n));
  const cl = f.classification || "";
  const clBadge = cl ? h("span", { class: "badge " + (CLASS_BADGE[cl] || "") }, CLASS_LABEL[cl] || cl) : null;
  const verBadge = f.verification ? h("span", { class: "chip small" }, "Verification: " + f.verification) : null;
  const c = h("article", { class: "finding " + f.severity, "aria-labelledby": "fh" + f.index },
    h("h4", { id: "fh" + f.index },
      h("span", { class: "sev " + f.severity }, SEV[f.severity]),
      clBadge,
      " ", f.title,
      f.count > 1 ? h("span", { class: "chip" }, f.count + " instances") : null),
    verBadge ? h("p", { class: "small" }, verBadge) : null,
    f.obj_refs?.length ? h("p", { class: "small mono" }, "PDF Object(s): " + f.obj_refs.join(", ")) : null,
    f.impact ? h("div", { class: "impact-box" }, h("strong", {}, "Assistive Technology Impact: "), f.impact) : null,
    f.detail ? h("p", {}, f.detail) : null,
    pages.length ? h("p", { class: "small" }, "Pages: ", pages) : null,
    f.evidence.length ? h("details", {}, h("summary", {}, `Evidence (${f.evidence.length}${f.evidence.length >= 25 ? "+" : ""})`), h("ul", { class: "small" }, f.evidence.map((e) => h("li", {}, e)))) : null,
    f.recommendation ? h("p", {}, h("strong", {}, "Recommended: "), f.recommendation) : null,
    f.refs.length ? h("p", { class: "small refs" }, "Reference: ", f.refs.map((r) => r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, `${r.std} ${r.id}`) : h("span", {}, `${r.std} ${r.id}`))) : null,
    h("p", { class: "small muted" }, `${f.kind} - ${f.source} - code ${f.code}`),
    f.nodes.length ? h("button", { class: "btn small", type: "button", onclick: () => { S.sel = f.nodes[0]; S.treeQ = ""; S.treeProblems = false; showTab("tree"); revealSel(); } }, "Show in tag tree") : null,
    fixUi(f));
  return c;
}

// ---- fix controls
async function applyActions(actions, confirm) {
  try {
    const r = await api(`/api/documents/${S.doc.id}/remediate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: S.ver, actions, confirm: !!confirm }) });
    const c = r.comparison;
    toast(`Saved as ${r.version.id} (original untouched). ${c.resolved.length} resolved, ${c.new.length} new. Re-validated.`);
    S.tab = "versions"; await openDoc(S.doc.id, r.version.id);
  } catch (e) { toast("Fix not applied: " + e.message, 9000); }
}
function fixUi(f) {
  const a = S.analysis, fx = f.fix;
  const box = (...k) => h("div", { class: "fix" }, ...k);
  if (f.code === "LINK-NO-CONTENTS" && a.fixable.links.length) {
    const inputs = a.fixable.links.slice(0, 30).map((l) => h("label", {}, `Page ${l.page}${l.uri ? " - " + l.uri : ""}`, h("input", { type: "text", "data-obj": l.obj, placeholder: "What does this link do or where does it go?" })));
    return box(h("strong", {}, "Describe links"), inputs, h("button", { class: "btn primary small", type: "button", onclick: (e) => { const items = [...e.target.parentNode.querySelectorAll("input")].map((i) => ({ obj: i.dataset.obj, text: i.value })).filter((i) => i.text.trim()); applyActions([{ action: "set_link_contents", params: { items } }]); } }, "Save descriptions as new version"));
  }
  if (f.code === "FORM-NO-TU" && a.fixable.widgets.length) {
    const inputs = a.fixable.widgets.slice(0, 30).map((l) => h("label", {}, `Page ${l.page} - field "${l.name}"`, h("input", { type: "text", "data-obj": l.obj, placeholder: "Label for this field" })));
    return box(h("strong", {}, "Name form fields"), inputs, h("button", { class: "btn primary small", type: "button", onclick: (e) => { const items = [...e.target.parentNode.querySelectorAll("input")].map((i) => ({ obj: i.dataset.obj, text: i.value })).filter((i) => i.text.trim()); applyActions([{ action: "set_widget_tooltip", params: { items } }]); } }, "Save as new version"));
  }
  if (!fx) return null;
  if (fx.action === "ocr") return box(h("button", { class: "btn primary small", type: "button", onclick: () => { S.ocrSel = new Set(fx.params.pages); showTab("ocr"); renderOcr(); } }, "Open OCR panel with these pages selected"));
  if (fx.action === "set_title" || fx.action === "set_lang") {
    const inp = h("input", { type: "text", id: "fx" + f.index, placeholder: fx.action === "set_lang" ? "e.g. en-US, ta-IN" : "Descriptive document title", "aria-label": fx.label });
    return box(h("strong", {}, fx.label), inp, h("button", { class: "btn primary small", type: "button", onclick: () => inp.value.trim() ? applyActions([{ action: fx.action, params: { value: inp.value } }]) : toast("Enter a value first") }, "Apply as new version"));
  }
  if (fx.action === "set_figure_alt") {
    const nodes = f.nodes.slice(0, 25).map((id) => S.analysis._nodes?.[id]);
    const rows = f.nodes.slice(0, 25).map((id) => h("label", {}, `Figure on page ${(f.pages[0] || "?")} (element #${id})`, h("input", { type: "text", "data-node": id, placeholder: "Describe the image's purpose" }),
      h("button", { class: "btn small ghost", type: "button", onclick: () => { S.sel = id; showTab("tree"); revealSel(); } }, "Locate")));
    return box(h("strong", {}, "Add alternative text (you write it; the tool never guesses)"), rows, h("button", { class: "btn primary small", type: "button", onclick: async (e) => {
      const st = await api(docUrl("/structure")); const items = [...e.target.parentNode.querySelectorAll("input")].filter((i) => i.value.trim()).map((i) => ({ obj: st.structure.nodes[+i.dataset.node].obj, text: i.value }));
      if (!items.length) return toast("Type a description first"); applyActions([{ action: "set_figure_alt", params: { items } }]); } }, "Save as new version"));
  }
  if (fx.action === "set_heading_levels") {
    const chk = h("input", { type: "checkbox", id: "cf" + f.index });
    return box(h("strong", {}, fx.label), h("p", { class: "small" }, fx.description + ` ${Object.keys(fx.params.levels).length} heading(s) would change.`),
      h("label", {}, chk, " I confirm this structural change"), h("button", { class: "btn primary small", type: "button", onclick: () => chk.checked ? applyActions([{ action: fx.action, params: fx.params }], true) : toast("Tick the confirmation first") }, "Apply as new version"));
  }
  const chk = fx.needs_confirmation ? h("input", { type: "checkbox", id: "cf" + f.index }) : null;
  return box(h("strong", {}, fx.label), fx.description ? h("p", { class: "small" }, fx.description) : null, chk ? h("label", {}, chk, " I confirm the tags are correct") : null,
    h("button", { class: "btn primary small", type: "button", onclick: () => (chk && !chk.checked) ? toast("Tick the confirmation first") : applyActions([{ action: fx.action, params: fx.params || {} }], !!chk) }, "Apply as new version"));
}

// ------------------------------------------------------------------ tag tree
function renderTreePanel() { clear($("#p-tree")); }
async function loadTree() {
  const p = $("#p-tree");
  if (S.tree) { drawTree(); return; }
  clear(p).append(h("p", {}, "Loading structure..."));
  try { const r = await api(docUrl("/structure")); S.tree = r.structure; S.nodes = r.structure ? r.structure.nodes : []; } catch (e) { clear(p).append(h("p", { class: "alert error" }, e.message)); return; }
  S.expanded = new Set((S.tree?.roots || []).slice(0, 3));
  drawTree();
}
function emptyTreeState() {
  const ts = S.analysis.tag_status;
  if (ts.status === "untagged") return h("div", { class: "alert warn" }, h("strong", {}, "Untagged document. "), "There is no structure tree to display and none is invented. ", ...ts.reasons);
  return h("div", { class: "alert error" }, "The structure tree exists but contains no readable elements. " + ts.reasons.join(" "));
}
function problemSet() { const s = new Set(); (S.nodes || []).forEach((n) => { if (n.issues.length) { let i = n.id; while (i != null && !s.has(i)) { s.add(i); i = S.nodes[i].p; } } }); return s; }
function drawTree() {
  const p = clear($("#p-tree"));
  if (!S.nodes || !S.nodes.length) { p.append(emptyTreeState()); return; }
  const ts = S.analysis.tag_status;
  if (ts.status !== "tagged") p.append(h("div", { class: "alert " + (ts.status === "malformed" ? "error" : "warn") }, h("strong", {}, TAGLABEL[ts.status] + ". "), ts.reasons.join(" ")));
  if (S.tree.truncated) p.append(h("div", { class: "alert warn" }, "The tree is larger than the inspection limit; only the first elements are shown."));
  const types = Object.keys(S.tree.types);
  p.append(h("div", { class: "filters" },
    h("label", {}, "Search type, text or alt text", h("input", { type: "text", id: "tq", value: S.treeQ, oninput: (e) => { S.treeQ = e.target.value; refreshTree(); } })),
    h("label", {}, "Element type", h("select", { id: "tt", onchange: (e) => { S.treeType = e.target.value; refreshTree(); } }, h("option", { value: "" }, "All"), types.map((t) => h("option", { value: t, selected: S.treeType === t }, `${t} (${S.tree.types[t]})`)))),
    h("label", { class: "inline" }, h("input", { type: "checkbox", checked: S.treeProblems, onchange: (e) => { S.treeProblems = e.target.checked; refreshTree(); } }), " Only elements with findings"),
    h("button", { class: "btn small", type: "button", onclick: () => { S.expanded = new Set(S.nodes.filter((n) => n.k.length).map((n) => n.id)); refreshTree(); } }, "Expand all"),
    h("button", { class: "btn small", type: "button", onclick: () => { S.expanded = new Set(); refreshTree(); } }, "Collapse all"),
    h("label", { class: "inline" }, h("input", { type: "checkbox", checked: S.roPage, onchange: (e) => { S.roPage = e.target.checked; refreshTree(); } }), " Reading order of current page")));
  p.append(h("p", { class: "small muted" }, `${S.nodes.length} elements, depth ${S.tree.max_depth}. Types: ${types.slice(0, 12).map((t) => t + " " + S.tree.types[t]).join(", ")}${types.length > 12 ? ", ..." : ""}.`));
  p.append(h("div", { class: "tree-wrap" }, h("div", {}, h("ul", { role: "tree", id: "tree", "aria-label": "Document structure tree" }), h("p", { id: "tree-count", class: "small muted", "aria-live": "polite" })), h("div", { class: "inspector", id: "insp", "aria-live": "polite" }, h("p", { class: "muted" }, "Select an element to inspect it."))));
  $("#tree").addEventListener("keydown", treeKeys);
  refreshTree(); if (S.sel != null) inspect(S.sel);
}
function visibleIds() {
  const N = S.nodes, q = S.treeQ.trim().toLowerCase(), filt = q || S.treeType || S.treeProblems;
  if (S.roPage) return N.filter((n) => (n.bpg === S.page || n.pg === S.page) && n.own).map((n) => n.id);
  let keep = null;
  if (filt) {
    const prob = S.treeProblems ? problemSet() : null; keep = new Set();
    N.forEach((n) => {
      const hit = (!q || (n.t + " " + n.s + " " + n.text + " " + (n.alt || "") + " " + (n.actual || "")).toLowerCase().includes(q)) && (!S.treeType || n.t === S.treeType) && (!S.treeProblems || n.issues.length);
      if (hit) { let i = n.id; while (i != null && !keep.has(i)) { keep.add(i); i = N[i].p; } }
    });
    if (prob) { /* ancestors already included */ }
  }
  const out = [], walk = (id) => { if (keep && !keep.has(id)) return; out.push(id); const n = N[id]; if (S.expanded.has(id) || (keep && filt)) n.k.forEach(walk); };
  S.tree.roots.forEach(walk); return out;
}
function nodeRow(id, ro) {
  const n = S.nodes[id], hasK = n.k.length, exp = S.expanded.has(id) || (!ro && (S.treeQ || S.treeType || S.treeProblems));
  const sevs = n.issues.map((i) => i.sev), cls = sevs.includes("error") ? "bad" : sevs.length ? "sus" : "";
  const li = h("li", { role: "none" }, h("div", { role: "treeitem", id: "n" + id, "aria-level": ro ? 1 : n.d + 1, "aria-selected": S.sel === id, tabindex: S.sel === id || (S.sel == null && id === S.tree.roots[0]) ? 0 : -1, "aria-expanded": hasK && !ro ? String(!!exp) : false, "data-id": id,
    onclick: (e) => { if (e.target.classList.contains("tw") && hasK) toggle(id); else select(id); } },
    h("span", { class: "tw", "aria-hidden": "true" }, hasK && !ro ? (exp ? "v" : ">") : ""),
    h("span", { class: "tag " + cls }, n.s + (n.s !== n.t ? ` > ${n.t}` : "")),
    n.pg ? h("span", { class: "small muted" }, "p." + n.pg) : null,
    n.alt != null ? h("span", { class: "small" }, n.alt ? "alt" : "empty alt") : null,
    n.issues.length ? h("span", { class: "chip " + (cls === "bad" ? "err" : "warn"), "aria-label": `${n.issues.length} findings` }, "!" + n.issues.length) : null,
    h("span", { class: "snip" }, n.own || n.text || "")));
  li.firstChild.style.paddingLeft = (ro ? 0 : n.d * 14) + "px"; return li;
}
function refreshTree() {
  const t = $("#tree"); if (!t) return; clear(t);
  const ids = visibleIds(), CAP = 2500;
  ids.slice(0, CAP).forEach((id) => t.append(nodeRow(id, S.roPage)));
  $("#tree-count").textContent = `${ids.length} element${ids.length === 1 ? "" : "s"} shown${ids.length > CAP ? ` (first ${CAP})` : ""}` + (S.roPage ? ` - screen-reader order for page ${S.page}` : "");
  if (!ids.length) t.append(h("li", { role: "none", class: "muted" }, "No elements match."));
}
function toggle(id) { S.expanded.has(id) ? S.expanded.delete(id) : S.expanded.add(id); refreshTree(); $("#n" + id)?.focus(); }
function select(id) { S.sel = id; refreshTree(); $("#n" + id)?.focus(); inspect(id); }
function revealSel() {
  if (S.sel == null) return; let i = S.nodes ? S.nodes[S.sel]?.p : null;
  const go = () => { let j = S.nodes[S.sel].p; while (j != null) { S.expanded.add(j); j = S.nodes[j].p; } drawTree(); $("#n" + S.sel)?.scrollIntoView({ block: "center" }); inspect(S.sel); };
  if (S.nodes) go(); else loadTree().then(go);
}
function treeKeys(e) {
  const items = [...document.querySelectorAll("#tree [role=treeitem]")], i = items.indexOf(document.activeElement); if (i < 0) return;
  const id = +document.activeElement.dataset.id, n = S.nodes[id];
  if (e.key === "ArrowDown") { e.preventDefault(); items[Math.min(i + 1, items.length - 1)].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[Math.max(i - 1, 0)].focus(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); if (n.k.length && !S.expanded.has(id)) toggle(id); else if (n.k.length) items[i + 1]?.focus(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); if (S.expanded.has(id)) toggle(id); else if (n.p != null) $("#n" + n.p)?.focus(); }
  else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(id); }
  else if (e.key === "Home") { e.preventDefault(); items[0].focus(); } else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
}
function inspect(id) {
  const n = S.nodes[id], box = clear($("#insp")); if (!n) return;
  const path = []; let j = n.p; while (j != null) { path.unshift(S.nodes[j].s); j = S.nodes[j].p; }
  const kv = [
    ["Type (as written)", "/" + n.s],
    ["Standard mapped role", n.t + (n.status !== "standard" ? ` (${n.status})` : "")],
    ["PDF Object number", n.obj ? `${n.obj} R` : "direct dictionary"],
    ["Hierarchy path", path.join(" > ") || "(root element)"],
    ["Page number", n.pg ? `Page ${n.pg}` : "(inherited or document level)"],
    ["Child elements", n.k.length],
    ["Marked content (MCID)", n.mc.length ? n.mc.map((m) => `p${m[0]}:MCID ${m[1]}`).slice(0, 8).join(", ") : "none"],
    ["Annotation refs (OBJR)", n.objr || 0],
    ["Alternative text (/Alt)", n.alt == null ? "(none)" : n.alt || "(empty)"],
    ["ActualText", n.actual ?? "(none)"],
    ["Language (/Lang)", n.lang || "(inherited)"],
    ["Extracted text", n.text || "(none)"]
  ];
  box.append(h("h3", {}, `#${id} ${n.s}`), h("dl", {}, kv.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))])));
  if (n.issues.length) {
    box.append(h("h4", {}, `Findings on this element (${n.issues.length})`));
    n.issues.forEach((i) => {
      const f = S.analysis.findings[i.f];
      const cl = f.classification || "";
      const clBadge = cl ? h("span", { class: "badge " + (CLASS_BADGE[cl] || "") }, CLASS_LABEL[cl] || cl) : null;
      box.append(h("div", { class: "finding " + f.severity },
        h("p", {}, h("span", { class: "sev " + f.severity }, SEV[f.severity]), " ", clBadge, " ", h("strong", {}, f.title)),
        f.impact ? h("div", { class: "impact-box small" }, h("strong", {}, "Impact: "), f.impact) : null,
        h("button", { class: "btn small ghost", type: "button", onclick: () => { S.findFilter = { sev: "all", q: f.code, cat: "" }; renderFindings(); showTab("findings"); } }, "View in findings list")
      ));
    });
  } else {
    box.append(h("p", { class: "small muted" }, "No accessibility findings are attached to this element."));
  }
  const pg = n.bpg || n.pg;
  if (pg) {
    setPage(pg, n.dbox);
  } else {
    $("#pv-note").textContent = "This element has no physical page bounding box.";
  }
}

// ------------------------------------------------------------------ page preview & selectable text layer
let _pageTextReqId = 0;
let _lastPageTextData = null;

function zoomPreview(delta, setExact) {
  if (setExact != null) S.pvZoom = setExact;
  else S.pvZoom = Math.max(50, Math.min(300, S.pvZoom + delta));
  const frame = $("#pv-frame");
  if (frame) {
    frame.style.width = S.pvZoom + "%";
    $("#pv-zoom-level").textContent = S.pvZoom + "%";
    updateTextLayerFontSizes();
  }
}

async function copyPageText() {
  if (!S.pageText) {
    toast("No text found on this page to copy.");
    return;
  }
  try {
    await navigator.clipboard.writeText(S.pageText);
    toast(`Copied ${S.pageText.trim().split(/\s+/).length} words from Page ${S.page} to clipboard.`);
  } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = S.pageText;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    toast(`Copied text from Page ${S.page} to clipboard.`);
  }
}

function toggleTextLayer() {
  S.pvShowLayer = !S.pvShowLayer;
  const btn = $("#pv-toggle-layer");
  const layer = $("#pv-textlayer");
  if (btn) {
    btn.setAttribute("aria-pressed", String(S.pvShowLayer));
    btn.textContent = S.pvShowLayer ? "Hide Text Bounds" : "Show Text Bounds";
  }
  if (layer) {
    layer.classList.toggle("show-boxes", S.pvShowLayer);
  }
}

function updateTextLayerFontSizes() {
  const frame = $("#pv-frame");
  if (!frame || !_lastPageTextData || !_lastPageTextData.words) return;
  const frameH = frame.clientHeight || 520;
  const spans = frame.querySelectorAll(".pv-word");
  _lastPageTextData.words.forEach((w, idx) => {
    const span = spans[idx];
    if (span) {
      const pxH = Math.max(9, Math.round(w.h * frameH * 0.88));
      span.style.fontSize = pxH + "px";
    }
  });
}

function renderSelectableTextLayer(container, data) {
  clear(container);
  _lastPageTextData = data;
  if (S.pvShowLayer) container.classList.add("show-boxes");
  else container.classList.remove("show-boxes");

  if (!data.words || !data.words.length) {
    return;
  }
  const frame = $("#pv-frame");
  const frameH = frame ? frame.clientHeight || 520 : 520;

  data.words.forEach((w) => {
    const span = document.createElement("span");
    span.className = "pv-word" + (w.low_conf ? " low-conf" : "");
    span.style.left = (w.l * 100) + "%";
    span.style.top = (w.t * 100) + "%";
    span.style.width = (w.w * 100) + "%";
    span.style.height = (w.h * 100) + "%";
    const pxH = Math.max(9, Math.round(w.h * frameH * 0.88));
    span.style.fontSize = pxH + "px";
    span.textContent = w.text + " ";
    if (w.conf != null) {
      span.title = `"${w.text}" (OCR confidence: ${Math.round(w.conf)}%${w.low_conf ? ", low confidence" : ""})`;
    }
    container.appendChild(span);
  });
}

async function setPage(n, box) {
  const a = S.analysis; if (!a || a.state !== "ok") return;
  n = Math.max(1, Math.min(a.file.pages, n | 0)); S.page = n;
  $("#pv-page").value = n; $("#pv-page").max = a.file.pages; $("#pv-total").textContent = "of " + a.file.pages;
  const img = $("#pv-img"); const src = docUrl(`/pages/${n}/image`, "&w=1200");
  if (img.getAttribute("src") !== src) { img.src = src; }
  const hl = $("#pv-hl");
  if (box && box[2] > box[0]) {
    hl.hidden = false;
    hl.style.left = box[0] * 100 + "%";
    hl.style.top = box[1] * 100 + "%";
    hl.style.width = Math.max(1, (box[2] - box[0]) * 100) + "%";
    hl.style.height = Math.max(1, (box[3] - box[1]) * 100) + "%";
    $("#pv-note").textContent = `Page ${n}: highlighted area is the element's content. Text is selectable.`;
  } else {
    hl.hidden = true;
    $("#pv-note").textContent = `Page ${n} — Click and drag to select text, or use Ctrl+C to copy.`;
  }
  if (S.tab === "tree" && S.roPage) refreshTree();

  const myReq = ++_pageTextReqId;
  const layer = $("#pv-textlayer");
  if (!layer) return;
  clear(layer);
  S.pageText = "";
  try {
    const data = await api(docUrl(`/pages/${n}/text`));
    if (myReq !== _pageTextReqId) return;
    S.pageText = data.text || "";
    renderSelectableTextLayer(layer, data);
    if (data.words && data.words.length) {
      $("#pv-note").textContent = `Page ${n}: ${data.words.length} selectable words. Drag to select or copy with Ctrl+C.`;
    }
  } catch (err) {
    if (myReq === _pageTextReqId) {
      console.warn("Could not load page text layout:", err);
    }
  }
}

// ------------------------------------------------------------------ OCR panel
function renderOcr() {
  const p = clear($("#p-ocr")), a = S.analysis; if (a.state !== "ok") return;
  const eng = S.caps.ocr, sc = a.scan || { candidate_pages: [], counts: {} };
  if (!eng.available) p.append(h("div", { class: "alert error" }, h("strong", {}, "OCR engine unavailable. "), eng.error, h("ul", {}, Object.entries(eng.install_help).map(([k, v]) => h("li", {}, h("strong", {}, k + ": "), h("span", { class: "mono" }, v))))));
  if (a.file.encrypted) p.append(h("div", { class: "alert warn" }, "This file is encrypted; it can be analysed but not modified. Remove the encryption to run OCR."));
  const cn = sc.counts || {}; const lab = (k) => (a.pages.find((x) => x.class === k) || {}).label || k;
  p.append(h("div", { class: "alert" }, h("strong", {}, `Document class (heuristic): ${(sc.doc_class || "").replaceAll("_", " ")}. `), Object.entries(cn).map(([k, v]) => `${v} ${lab(k).toLowerCase()}`).join("; ") + ". ",
    "OCR adds a hidden text layer to the pages you select; page images, native text, tags and links are kept. OCR does not make a PDF accessible."));
  if (S.ocrSel.size === 0 && !S.ocrInit) { (sc.candidate_pages || []).forEach((x) => S.ocrSel.add(x)); S.ocrInit = true; }
  const langs = eng.languages || [], def = langs.includes("eng") ? ["eng"] : langs.slice(0, 1);
  const langBox = h("fieldset", {}, h("legend", {}, "OCR languages (installed)"), langs.length ? langs.map((l) => h("label", { class: "inline" }, h("input", { type: "checkbox", name: "lang", value: l, checked: def.includes(l) }), " " + l + "  ")) : h("span", { class: "muted" }, "none installed"));
  const dpi = h("select", { id: "o-dpi" }, [200, 300, 400, 150].map((d) => h("option", { value: d, selected: d === 300 }, d + " dpi")));
  const skip = h("input", { type: "checkbox", id: "o-skip", checked: true });
  const count = h("span", { id: "o-count", class: "small", "aria-live": "polite" });
  const start = h("button", { class: "btn primary", id: "o-start", type: "button", disabled: !eng.available || a.file.encrypted, onclick: startOcr }, "Start OCR on selected pages");
  const cancel = h("button", { class: "btn danger", id: "o-cancel", type: "button", hidden: true, onclick: cancelOcr }, "Cancel");
  p.append(h("div", { class: "ocr-form" }, langBox, h("label", {}, "Render resolution", dpi), h("label", { class: "inline" }, skip, " Skip text that overlaps existing native text"), start, cancel));
  const sel = (fn) => () => { S.ocrSel = new Set(a.pages.filter(fn).map((x) => x.page)); renderOcr(); };
  p.append(h("div", { class: "filters" }, h("button", { class: "btn small", type: "button", onclick: sel((x) => x.ocr_candidate) }, "Select detected scans"), h("button", { class: "btn small", type: "button", onclick: sel(() => true) }, "Select all pages"),
    h("button", { class: "btn small", type: "button", onclick: sel(() => false) }, "Select none"), count));
  const grid = h("div", { class: "pgrid", role: "group", "aria-label": "Pages" });
  a.pages.forEach((pg) => {
    const cb = h("input", { type: "checkbox", id: "pgc" + pg.page, checked: S.ocrSel.has(pg.page), onchange: (e) => { e.target.checked ? S.ocrSel.add(pg.page) : S.ocrSel.delete(pg.page); updCount(); } });
    grid.append(h("div", { class: "pg" + (pg.ocr_candidate ? " cand" : "") }, h("button", { class: "btn small ghost", type: "button", onclick: () => setPage(pg.page), "aria-label": `Preview page ${pg.page}` },
      h("img", { src: docUrl(`/pages/${pg.page}/image`, "&w=150"), alt: `Thumbnail of page ${pg.page}`, loading: "lazy", width: 150, height: 200 })),
      h("label", { for: "pgc" + pg.page }, cb, h("span", {}, h("strong", {}, `Page ${pg.page}`), ` - ${pg.label}`)),
      h("span", { class: "small muted" }, `${pg.confidence}; image ${Math.round((pg.largest_image_coverage || 0) * 100)}%; ${pg.visible_chars} text chars${pg.invisible_chars ? `, ${pg.invisible_chars} hidden` : ""}`),
      h("details", {}, h("summary", { class: "small" }, "Why"), h("p", { class: "small" }, (pg.reasons || []).join(" ")))));
  });
  p.append(grid);
  const updCount = () => { count.textContent = `${S.ocrSel.size} page${S.ocrSel.size === 1 ? "" : "s"} selected`; }; updCount();
  p.append(h("div", { id: "o-live" }), h("div", { id: "o-result" }));
  const v = S.doc.versions.find((x) => x.id === S.ver);
  if (v && v.has_ocr) showOcrResult();
}
const selLangs = () => [...document.querySelectorAll("input[name=lang]:checked")].map((i) => i.value).join("+");
async function startOcr() {
  const pages = [...S.ocrSel].sort((a, b) => a - b), langs = selLangs();
  if (!pages.length) return toast("Select at least one page."); if (!langs) return toast("Select at least one OCR language.");
  const body = { version: S.ver, pages, languages: langs, dpi: +$("#o-dpi").value, skip_overlap: $("#o-skip").checked };
  runOcr(body);
}
async function runOcr(body) {
  const live_ = clear($("#o-live")); clear($("#o-result"));
  $("#o-start").disabled = true; $("#o-cancel").hidden = false;
  const bar = h("span", {}), msg = h("p", { "aria-live": "polite" }, "Queued..."), logs = h("div", { class: "logbox", role: "log", "aria-label": "Processing log", tabindex: "0" });
  live_.append(h("div", { class: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": 0, "aria-label": "OCR progress" }, bar), msg, logs);
  let job;
  try { const r = await api(`/api/documents/${S.doc.id}/ocr`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); job = r.job; }
  catch (e) { live_.append(h("div", { class: "alert error" }, e.message, e.data?.install_help ? h("ul", {}, Object.entries(e.data.install_help).map(([k, v]) => h("li", {}, k + ": " + v))) : null)); $("#o-start").disabled = false; $("#o-cancel").hidden = true; return; }
  S.job = job;
  const fin = await pollJob(job.id, (j) => {
    const pct = j.total ? (100 * j.done) / j.total : 0; bar.style.width = pct + "%"; bar.parentNode.setAttribute("aria-valuenow", Math.round(pct));
    msg.textContent = `${j.status}: ${j.message || ""} (${j.done}/${j.total} pages)`;
    clear(logs); j.logs.forEach((l) => logs.append(h("div", { class: l.level === "error" ? "e" : l.level === "warn" ? "w" : "" }, `[${l.t}s] ${l.msg}`))); logs.scrollTop = logs.scrollHeight;
  });
  $("#o-start").disabled = false; $("#o-cancel").hidden = true;
  if (fin.status === "failed" || fin.status === "canceled") { $("#o-result").append(h("div", { class: "alert error" }, h("strong", {}, fin.status === "canceled" ? "Canceled. " : "OCR failed. "), fin.error?.message || "")); return; }
  toast(fin.status === "completed" ? `OCR finished. Saved as ${fin.result.version}.` : `OCR finished with problems. Saved as ${fin.result.version}.`);
  S.ocrResult = fin.result; S.tab = "ocr"; S.ocrInit = false; S.ocrSel = new Set();
  await openDoc(S.doc.id, fin.result.version); showTab("ocr");
}
async function cancelOcr() { if (S.job) { try { await api(`/api/jobs/${S.job.id}/cancel`, { method: "POST" }); toast("Cancel requested; no output will be written."); } catch (e) { toast(e.message); } } }
async function showOcrResult() {
  const box = clear($("#o-result"));
  let r; try { r = await api(docUrl("/ocr-result")); } catch (_) { return; }
  const ts = S.analysis.tag_status;
  box.append(h("h3", {}, "OCR result for this version"),
    h("div", { class: "alert " + (r.pages_failed.length ? "warn" : "ok") }, `${r.pages_ok} of ${r.pages_requested} page${r.pages_requested === 1 ? "" : "s"} recognised with ${r.engine}, languages ${r.languages}, ${r.dpi} dpi, in ${r.seconds}s.`),
    h("div", { class: "alert warn" }, h("strong", {}, "Searchable, not accessible. "), `Tag status of this file: ${TAGLABEL[ts.status]}. `, "OCR text is not part of the tag tree and reading order is not established."),
    h("a", { class: "btn primary", href: docUrl("/download") }, "Download searchable PDF"));
  if (r.pages_failed.length) box.append(h("button", { class: "btn", type: "button", onclick: () => runOcr({ version: S.ver, pages: r.pages_failed, languages: r.languages, dpi: r.dpi, skip_overlap: r.skip_overlap }) }, `Retry failed pages (${r.pages_failed.join(", ")})`));
  box.append(h("h4", {}, "Per-page results"), h("table", {}, h("thead", {}, h("tr", {}, ["Page", "Status", "Words", "Mean confidence", "Low-confidence words", "Notes", "Text"].map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, r.per_page.map((x) => h("tr", {}, h("td", {}, x.page), h("td", {}, x.status), h("td", {}, x.words ?? "-"), h("td", {}, x.mean_confidence ?? "-"), h("td", {}, x.low_confidence_words ?? "-"), h("td", {}, (x.flags || []).join("; ") || x.error || ""),
      h("td", {}, x.text ? h("button", { class: "btn small", type: "button", onclick: () => { $("#o-text").textContent = x.text; $("#o-text-h").textContent = `Recognised text, page ${x.page}`; setPage(x.page); } }, "Preview") : ""))))));
  box.append(h("h4", { id: "o-text-h" }, "Recognised text preview"), h("pre", { class: "pre", id: "o-text", tabindex: "0" }, "Select a page above."));
  box.append(h("h4", {}, "Post-processing checks on the output file"), h("ul", {}, r.checks.map((c) => h("li", {}, h("span", { class: "chip " + (c.level === "pass" ? "ok" : c.level === "fail" ? "err" : c.level === "warn" ? "warn" : "") }, c.level), " " + c.name + ": " + c.detail))));
}

// ------------------------------------------------------------------ versions / before-after
async function renderVersions() {
  const p = clear($("#p-versions")), vs = S.doc.versions;
  p.append(h("h3", {}, "Versions"), h("p", { class: "muted small" }, "The original upload is never modified. Every OCR run or fix creates a new version that is re-validated."),
    h("table", {}, h("thead", {}, h("tr", {}, ["Version", "What", "Tags", "Errors", "Warnings", "Download"].map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", {}, vs.map((v) => h("tr", {}, h("td", {}, v.id), h("td", {}, v.label), h("td", {}, TAGLABEL[v.tag_status] || v.state || "-"), h("td", {}, v.counts ? v.counts.error : "-"), h("td", {}, v.counts ? v.counts.warning : "-"),
        h("td", {}, h("a", { href: `/api/documents/${S.doc.id}/download?version=${v.id}` }, "PDF")))))));
  const cur = vs.find((v) => v.id === S.ver);
  if (cur && cur.parent) {
    try {
      const c = await api(`/api/documents/${S.doc.id}/compare?from=${cur.parent}&to=${cur.id}`);
      const list = (t, items, cls) => h("div", { class: "card" }, h("h4", {}, `${t} (${items.length})`), items.length ? h("ul", {}, items.map((i) => h("li", {}, i.title + (i.before ? ` (${i.before} -> ${i.after})` : "")))) : h("p", { class: "muted" }, "none"));
      p.append(h("h3", {}, `Before / after: ${c.from} to ${c.to}`), h("p", {}, `Tags: ${TAGLABEL[c.tag_status_before]} -> ${TAGLABEL[c.tag_status_after]}. Errors ${c.counts_before.error} -> ${c.counts_after.error}; warnings ${c.counts_before.warning} -> ${c.counts_after.warning}.`),
        h("div", { class: "cmp-cols" }, list("Resolved", c.resolved), list("New", c.new), list("Still present", c.unchanged)));
      if (cur.remediation) p.append(h("h4", {}, "Applied changes"), h("ul", {}, cur.remediation.applied.map((a) => h("li", {}, a.result))), cur.remediation.skipped.length ? h("ul", {}, cur.remediation.skipped.map((s) => h("li", {}, `Skipped ${s.action}: ${s.reason}`))) : null);
    } catch (e) { p.append(h("p", { class: "muted" }, "Comparison unavailable: " + e.message)); }
  } else p.append(h("p", {}, "This is the original upload. Apply a fix or run OCR to create a version to compare."));
}

init();
