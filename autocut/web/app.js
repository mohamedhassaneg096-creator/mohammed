/* AutoCut Frontend Application — v1.2.0 (Arabic RTL, Offline, External Transcript Import) */

(() => {
  "use strict";

  const MOTION_TABS = [
    { id: "zoom_in", label: "Zoom In", ar: "تقريب", icon: "⊕" },
    { id: "zoom_out", label: "Zoom Out", ar: "تبعيد", icon: "⊖" },
    { id: "left", label: "Left", ar: "تحريك لليسار", icon: "⇠" },
    { id: "right", label: "Right", ar: "تحريك لليمين", icon: "⇢" },
  ];

  const THEMES = {
    blue_night: "ليل أزرق",
    calm_forest: "غابة هادية",
    daylight: "نهار",
  };

  const state = {
    view: "home", // home | inputs | cuts | scenes | motion | export | review
    theme: "blue_night",
    health: null,
    projects: [],
    project: null,
    transcript: null,
    cuts: null,
    timeline: null,
    motions: { global_strength: 15, assignments: {} },
    parsedPreview: { segments: [], warnings: [] },
    companions: null,
    transcriptImportError: null,
    cutsSubTab: "preview", // preview | cuts
    transcriptFilter: "all", // all | suspicious
    showPasteBox: false,
    inputsMissingList: null,
    showQuickPathBox: false,
    waveformCache: {}, // url -> number[] peaks
    activeJob: null,
    pollTimer: null,
    motionTab: "zoom_in",
    motionCurrentSegId: null,
    motionHistory: [],
    previewVideoUrl: null,
    reviewSelectedSegId: null,
    globalSettings: null,
    updateModalTab: "updates",
    updatePlan: null,
    updatePlanError: null,
    updateJob: null,
    updatePollTimer: null,
    updateTestResult: null,
    backups: [],
  };

  // ---------- Helpers ----------
  function esc(val) {
    return String(val ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTime(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const mins = Math.floor(s / 60);
    const rem = s - mins * 60;
    const mStr = String(mins).padStart(2, "0");
    const rStr = rem.toFixed(1).padStart(4, "0");
    return `${mStr}:${rStr}`;
  }

  function fmtDur(sec) {
    const s = Math.max(0, Number(sec) || 0);
    if (s < 60) return `${s.toFixed(1)} ث`;
    const m = Math.floor(s / 60);
    const r = Math.round(s % 60);
    return `${m} د ${String(r).padStart(2, "0")} ث`;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleDateString("ar-EG", {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return iso.slice(0, 10);
    }
  }

  function toast(msg, kind = "info") {
    const stack = document.getElementById("toast-stack");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity 200ms";
      setTimeout(() => el.remove(), 220);
    }, 3600);
  }

  function markSaved() {
    const ind = document.getElementById("top-save-status");
    if (!ind) return;
    ind.classList.remove("hidden");
    ind.textContent = "تم الحفظ ✓";
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("application/json") ? await res.json() : { ok: res.ok };
    if (!res.ok || data.ok === false) {
      const msg = data.error || data.detail || "حدث خطأ غير متوقع في الخادم.";
      const err = new Error(msg);
      err.hint = data.hint || "";
      err.code = data.code || "";
      err.preview_lines = data.preview_lines || [];
      throw err;
    }
    return data;
  }

  async function apiJson(url, method, body) {
    return api(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function openModal(html, wide = false) {
    const root = document.getElementById("modal-root");
    root.innerHTML = `<div class="modal-card ${wide ? "modal-wide" : ""}">${html}</div>`;
    root.classList.remove("hidden");
    root.onclick = (e) => {
      if (e.target === root) closeModal();
    };
  }

  function closeModal() {
    const root = document.getElementById("modal-root");
    root.classList.add("hidden");
    root.innerHTML = "";
  }

  function confirmDialog(title, message, onConfirm) {
    openModal(`
      <div class="modal-header">
        <h3 style="margin:0;font-size:18px">${esc(title)}</h3>
        <button class="btn btn-sm btn-ghost" id="m-close">✕</button>
      </div>
      <p style="color:var(--text-2);margin:0 0 20px">${esc(message)}</p>
      <div style="display:flex;justify-content:flex-end;gap:10px">
        <button class="btn" id="m-cancel">إلغاء</button>
        <button class="btn btn-danger" id="m-ok">تأكيد</button>
      </div>
    `);
    document.getElementById("m-close").onclick = closeModal;
    document.getElementById("m-cancel").onclick = closeModal;
    document.getElementById("m-ok").onclick = () => {
      closeModal();
      onConfirm();
    };
  }

  // ---------- 3-Theme Engine ----------
  function normalizeTheme(raw) {
    if (raw === "dark") return "blue_night";
    if (raw === "light") return "daylight";
    if (THEMES[raw]) return raw;
    return "blue_night";
  }

  async function applyTheme(rawTheme, persist = false) {
    const theme = normalizeTheme(rawTheme);
    state.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("autocut_theme", theme);

    document.querySelectorAll("[data-theme-pick]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.themePick === theme);
    });

    if (persist) {
      try {
        const res = await apiJson("/api/settings/global", "PUT", { defaults: { theme } });
        state.globalSettings = res;
        if (state.project) {
          const pRes = await apiJson(`/api/projects/${state.project.id}`, "PATCH", { settings: { theme } });
          state.project = pRes.project;
        }
      } catch {
        // non-blocking theme save
      }
    }
  }

  // ---------- Lightweight Web Audio Waveform Renderer ----------
  function fallbackPeaks(count = 72) {
    const arr = [];
    for (let i = 0; i < count; i++) {
      arr.push(0.22 + 0.62 * Math.abs(Math.sin(i * 0.45) * Math.cos(i * 0.17)));
    }
    return arr;
  }

  async function loadPeaksForUrl(audioUrl) {
    if (state.waveformCache[audioUrl]) return state.waveformCache[audioUrl];
    try {
      const resp = await fetch(audioUrl);
      const buf = await resp.arrayBuffer();
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) throw new Error("No AudioContext");
      const ctx = new Ctx();
      const decoded = await ctx.decodeAudioData(buf);
      const ch = decoded.getChannelData(0);
      const bars = 80;
      const step = Math.max(1, Math.floor(ch.length / bars));
      const peaks = [];
      let maxAmp = 0.01;
      for (let b = 0; b < bars; b++) {
        let sum = 0;
        const start = b * step;
        const end = Math.min(ch.length, start + step);
        for (let j = start; j < end; j += 8) {
          const v = Math.abs(ch[j]);
          if (v > sum) sum = v;
        }
        if (sum > maxAmp) maxAmp = sum;
        peaks.push(sum);
      }
      const norm = peaks.map((p) => Math.max(0.1, p / maxAmp));
      state.waveformCache[audioUrl] = norm;
      ctx.close().catch(() => {});
      return norm;
    } catch {
      const fb = fallbackPeaks(80);
      state.waveformCache[audioUrl] = fb;
      return fb;
    }
  }

  function paintWaveformCanvas(canvas, peaks, ratio = 0) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(180, Math.floor(rect.width || 300));
    const h = Math.max(36, Math.floor(rect.height || 44));
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const cs = getComputedStyle(document.documentElement);
    const accent = cs.getPropertyValue("--accent").trim() || "#6C9BFF";
    const muted = cs.getPropertyValue("--border-strong").trim() || "#374354";

    const n = peaks.length;
    const barW = Math.max(2, (w - (n - 1) * 2) / n);
    const stepX = w / n;
    const playIdx = Math.floor(Math.max(0, Math.min(1, ratio)) * n);

    for (let i = 0; i < n; i++) {
      const amp = Math.max(0.12, Math.min(1, peaks[i]));
      const bh = Math.max(4, amp * (h - 8));
      const x = i * stepX;
      const y = (h - bh) / 2;
      ctx.fillStyle = i <= playIdx && ratio > 0 ? accent : muted;
      ctx.fillRect(x, y, Math.max(1.5, barW), bh);
    }
  }

  async function bindWaveform(canvasId, audioEl, audioUrl) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !audioUrl) return;
    const peaks = await loadPeaksForUrl(audioUrl);
    const redraw = () => {
      const dur = (audioEl && audioEl.duration) || state.project?.audio_duration || 1;
      const cur = audioEl ? audioEl.currentTime : 0;
      paintWaveformCanvas(canvas, peaks, dur > 0 ? cur / dur : 0);
    };
    redraw();
    if (audioEl) {
      audioEl.addEventListener("timeupdate", redraw);
      canvas.onclick = (e) => {
        const rect = canvas.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const dur = audioEl.duration || state.project?.audio_duration || 0;
        if (dur > 0) {
          audioEl.currentTime = ratio * dur;
          audioEl.play().catch(() => {});
        }
      };
    }
  }

  // ---------- Health & Startup ----------
  async function loadHealth() {
    try {
      const h = await fetch("/api/health").then((r) => r.json());
      state.health = h;
      const verTag = document.getElementById("app-version-tag");
      if (verTag && h.version) verTag.textContent = `v${h.version}`;

      const banner = document.getElementById("system-error-banner");
      if (!h.ffmpeg_ok || !h.ffprobe_ok) {
        document.getElementById("system-error-title").textContent = "FFmpeg غير مثبت";
        document.getElementById("system-error-msg").textContent =
          "FFmpeg غير مثبت على الجهاز — شغّل install.bat أو ثبّته عبر: winget install Gyan.FFmpeg";
        banner.classList.remove("hidden");
      } else {
        banner.classList.add("hidden");
      }

      if (h.last_update_result) {
        const res = h.last_update_result;
        const uBanner = document.getElementById("update-result-banner");
        const uTitle = document.getElementById("update-result-title");
        const uBody = document.getElementById("update-result-body");
        if (uBanner && uTitle && uBody) {
          uBanner.classList.remove("hidden", "alert-success", "alert-error", "alert-warning");
          if (res.ok) {
            uBanner.classList.add("alert-success");
            uTitle.textContent = `تم التحديث إلى ${res.new_version}`;
            const cl = Array.isArray(res.changelog) ? res.changelog.slice(0, 5) : [];
            uBody.innerHTML = cl.length
              ? `<ul class="update-list">${cl.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`
              : "تم التحقق من سلامة الملفات وتشغيل البرنامج بنجاح.";
            toast(`تم التحديث إلى ${res.new_version}`, "success");
          } else {
            uBanner.classList.add("alert-error");
            uTitle.textContent = res.rolled_back ? "فشل التحديث وتم التراجع تلقائياً" : "تنبيه التحديث";
            uBody.textContent = res.error || "تعذر إكمال التحديث وبقي الإصدار السابق كما هو.";
            toast(uBody.textContent, "error");
          }
        }
        fetch("/api/updates/ack-result", { method: "POST" }).catch(() => {});
      }
    } catch {
      // offline/startup ignore
    }
  }

  // ---------- Navigation & Topbar ----------
  function updateChrome() {
    const projName = document.getElementById("top-project-name");
    const saveStatus = document.getElementById("top-save-status");
    const stepper = document.getElementById("stepper-bar");
    const revBtn = document.getElementById("review-step-btn");
    const revArrow = document.getElementById("review-step-arrow");

    if (state.view === "home" || !state.project) {
      projName.classList.add("hidden");
      saveStatus.classList.add("hidden");
      stepper.classList.add("hidden");
      return;
    }

    projName.textContent = state.project.name;
    projName.classList.remove("hidden");
    saveStatus.classList.remove("hidden");
    stepper.classList.remove("hidden");

    const hasOutput = Boolean(state.project.export_report || state.hasOutput);
    revBtn.classList.toggle("hidden", !hasOutput);
    revArrow.classList.toggle("hidden", !hasOutput);

    const order = ["inputs", "cuts", "scenes", "motion", "export", "review"];
    const currIdx = order.indexOf(state.view);
    stepper.querySelectorAll(".step-btn").forEach((btn) => {
      const st = btn.dataset.stage;
      const idx = order.indexOf(st);
      btn.classList.toggle("active", st === state.view);
      btn.classList.toggle("done", idx < currIdx);
    });
  }

  async function navigate(view) {
    state.view = view;
    state.previewVideoUrl = null;
    updateChrome();
    render();
    if (state.project && view !== "home") {
      try {
        await apiJson(`/api/projects/${state.project.id}`, "PATCH", { stage: view });
      } catch {
        // non-blocking
      }
    }
  }

  // ---------- Project Loading ----------
  async function loadProjects() {
    const data = await api("/api/projects");
    state.projects = data.projects || [];
    render();
  }

  async function openProject(pid) {
    const data = await api(`/api/projects/${pid}`);
    state.project = data.project;
    state.transcript = data.transcript || null;
    state.cuts = data.cuts;
    state.timeline = data.timeline;
    state.motions = data.motions || { global_strength: 15, assignments: {} };
    state.hasOutput = Boolean(data.has_output);
    state.transcriptImportError = null;
    state.inputsMissingList = null;
    state.companions = null;

    if (state.project.settings?.theme) {
      applyTheme(state.project.settings.theme, false);
    }

    if (state.project.raw_text) {
      try {
        const parsed = await apiJson(`/api/projects/${pid}/parse-text`, "POST", {
          raw_text: state.project.raw_text,
        });
        state.parsedPreview = parsed;
      } catch {
        state.parsedPreview = { segments: [], warnings: [] };
      }
    } else {
      state.parsedPreview = { segments: [], warnings: [] };
    }

    const stage = state.project.stage || "inputs";
    await navigate(stage === "home" ? "inputs" : stage);
  }

  // ---------- Live Job Polling (Export) ----------
  function renderLiveBanner() {
    const banner = document.getElementById("live-banner");
    const job = state.activeJob;
    if (!job || (job.status !== "running" && job.status !== "error")) {
      banner.classList.add("hidden");
      return;
    }

    banner.classList.remove("hidden");
    document.getElementById("live-status-text").textContent = job.message || "جاري التنفيذ...";
    document.getElementById("live-pct").textContent = `${Math.round(job.progress || 0)}%`;
    document.getElementById("live-elapsed").textContent = fmtTime(job.elapsed || 0);
    document.getElementById("live-eta").textContent =
      job.eta != null ? `المتبقي: ${fmtTime(job.eta)}` : "المتبقي: --:--";
    document.getElementById("live-progress-fill").style.width = `${Math.min(100, job.progress || 0)}%`;

    const strip = document.getElementById("live-timeline-strip");
    const items = (job.timeline?.items) || (state.timeline?.items) || [];
    if (items.length > 0) {
      strip.classList.remove("hidden");
      strip.innerHTML = items.slice(0, 60).map((it) => {
        const sid = it.segment_id || it.id;
        const cstate = job.clip_states ? job.clip_states[sid] || "waiting" : (it.thumbnail_url ? "done" : "waiting");
        const statusIcon = cstate === "done" ? "✓" : cstate === "rendering" ? "⏳" : "•";
        const thumb = it.thumbnail_url ? `<img src="${esc(it.thumbnail_url)}" alt="" />` : "";
        return `<div class="live-block ${cstate}">
          ${thumb}
          <span class="tabular">#${sid} ${statusIcon}</span>
        </div>`;
      }).join("");
    } else {
      strip.classList.add("hidden");
    }
  }

  function startJobPolling(jobId, onComplete) {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      try {
        const data = await api(`/api/jobs/${jobId}`);
        const job = data.job;
        state.activeJob = job;
        renderLiveBanner();

        if (job.status === "done") {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
          state.activeJob = null;
          renderLiveBanner();
          onComplete(job);
        } else if (job.status === "error" || job.status === "cancelled") {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
          toast(job.error || "تم إيقاف العملية.", job.status === "error" ? "error" : "warning");
          if (job.status === "cancelled") {
            state.activeJob = null;
            renderLiveBanner();
          }
        }
      } catch (err) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        toast(err.message, "error");
      }
    }, 600);
  }

  // ---------- Transcript Import Shared Handlers ----------
  function renderImportErrorHtml(errObj) {
    if (!errObj) return "";
    const previewHtml = errObj.preview_lines?.length
      ? `
        <div style="margin-top:10px">
          <div class="field-label" style="margin-bottom:6px">أول 30 سطراً من الملف:</div>
          <div class="unknown-preview-box">${esc(errObj.preview_lines.slice(0, 30).join("\n"))}</div>
          <div style="margin-top:10px;display:flex;justify-content:flex-end">
            <button class="btn btn-sm" id="btn-unknown-copy-report">نسخ تقرير المشكلة</button>
          </div>
        </div>
      `
      : "";
    return `
      <div class="alert alert-error" style="display:block;margin-top:10px">
        <div><strong>${esc(errObj.message)}</strong></div>
        ${errObj.hint ? `<div class="field-hint" style="color:var(--text);margin-top:4px">${esc(errObj.hint)}</div>` : ""}
        ${previewHtml}
      </div>
    `;
  }

  async function performTranscriptUpload({ file, text, filename, localPath }) {
    if (!state.project) return;
    state.transcriptImportError = null;
    try {
      let res;
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        res = await api(`/api/projects/${state.project.id}/import-transcript`, { method: "POST", body: fd });
      } else {
        res = await apiJson(`/api/projects/${state.project.id}/import-transcript`, "POST", {
          text,
          filename: filename || "pasted_transcript.txt",
          local_path: localPath || "",
        });
      }
      state.transcript = res.transcript;
      if (res.project) state.project = res.project;
      state.inputsMissingList = null;
      markSaved();
      toast(`تم استيراد التفريغ (${res.transcript.format_label}).`, "success");
      render();
    } catch (err) {
      state.transcriptImportError = {
        message: err.message,
        hint: err.hint || "",
        code: err.code || "",
        preview_lines: err.preview_lines || [],
      };
      toast(err.message, "error");
      render();
    }
  }

  // Recursive file extraction from folder drag-and-drop
  async function extractFilesFromDataTransfer(dt) {
    const out = [];
    const items = Array.from(dt.items || []);
    const readEntry = (entry, prefix = "") => new Promise((resolve) => {
      if (!entry) return resolve();
      if (entry.isFile) {
        entry.file((file) => {
          out.push({ file, relpath: prefix + file.name });
          resolve();
        }, () => resolve());
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const readBatch = () => {
          reader.readEntries(async (entries) => {
            if (!entries.length) return resolve();
            await Promise.all(entries.map((en) => readEntry(en, `${prefix}${entry.name}/`)));
            readBatch();
          }, () => resolve());
        };
        readBatch();
      } else {
        resolve();
      }
    });

    const entries = items.map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (entries.length) {
      await Promise.all(entries.map((en) => readEntry(en, "")));
      return out;
    }
    return Array.from(dt.files || []).map((file) => ({
      file,
      relpath: file.webkitRelativePath || file.name,
    }));
  }

  // ---------- Screen 0: Home ----------
  function renderHome(outlet) {
    const cardsHtml = state.projects.length
      ? `<div class="projects-grid">
          ${state.projects.map((p) => {
            const thumb = p.thumbnail_url
              ? `<img src="${esc(p.thumbnail_url)}" alt="${esc(p.name)}" />`
              : `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/><polygon points="10 9 15 12 10 15 10 9"/></svg>`;
            const stageNames = {
              inputs: "مشروع جديد / المدخلات",
              cuts: "التفريغ (استيراد)",
              scenes: "المشاهد",
              motion: "الحركات",
              export: "مكتمل للتصدير",
              review: "مراجعة وتعديل",
            };
            return `<article class="project-card">
              <div class="project-thumb">${thumb}</div>
              <div class="project-body">
                <h3 class="project-title">${esc(p.name)}</h3>
                <div class="meta-line">
                  <span>${esc(stageNames[p.stage] || "المدخلات")}</span>
                  <span>·</span>
                  <span class="tabular">${p.segment_count || 0} مقطع</span>
                  <span>·</span>
                  <span class="mono">${fmtDur(p.audio_duration || 0)}</span>
                </div>
                <div class="meta-line">
                  <span>آخر تحديث: ${esc(fmtDate(p.updated_at))}</span>
                  ${p.has_output ? `<span>·</span><span style="color:var(--success-text)">فيديو جاهز ✓</span>` : ""}
                </div>
                <div class="project-actions">
                  <button class="btn btn-primary btn-sm" data-open-proj="${esc(p.id)}">فتح المشروع</button>
                  <button class="btn btn-sm btn-ghost" data-del-proj="${esc(p.id)}" data-del-name="${esc(p.name)}" title="حذف">حذف</button>
                </div>
              </div>
            </article>`;
          }).join("")}
        </div>`
      : `<div class="empty-state">
          <div class="empty-icon">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/></svg>
          </div>
          <h3 style="margin:0 0 8px">لا توجد مشاريع بعد</h3>
          <p style="margin:0 0 20px;color:var(--text-2)">ابدأ بمشروع جديد وأضف ملفاتك، أو افتح مشروعاً تجريبياً جاهزاً للتجربة الفورية.</p>
          <div style="display:flex;justify-content:center;gap:10px;flex-wrap:wrap">
            <button class="btn btn-primary btn-lg" id="btn-empty-new">+ مشروع جديد</button>
            <button class="btn btn-lg" id="btn-empty-demo">مشروع تجريبي</button>
          </div>
        </div>`;

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">مشاريع أوتوكَت</h1>
          <p class="section-subtitle">استورد ملف التفريغ (JSON / SRT / VTT من Vibe أو Whisper) واجمع الفيديو محلياً بالكامل.</p>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn btn-lg" id="btn-demo-project">مشروع تجريبي</button>
          <button class="btn btn-primary btn-lg" id="btn-new-project">+ مشروع جديد</button>
        </div>
      </div>
      ${cardsHtml}
    `;

    const createDemo = async () => {
      try {
        toast("جاري إنشاء المشروع التجريبي...", "info");
        const res = await apiJson("/api/projects/demo", "POST", {});
        await openProject(res.project.id);
        toast("تم إنشاء المشروع التجريبي جاهزاً للتجربة!", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    };

    const openNewModal = () => {
      openModal(`
        <div class="modal-header">
          <h3 style="margin:0;font-size:18px">إنشاء مشروع جديد</h3>
          <button class="btn btn-sm btn-ghost" id="np-close">✕</button>
        </div>
        <div class="field">
          <label class="field-label" for="np-name">اسم المشروع</label>
          <input class="input" id="np-name" placeholder="مثال: وثائقي الحضارة الأندلسية" autofocus />
        </div>
        <div style="display:flex;justify-content:flex-end;gap:10px">
          <button class="btn" id="np-cancel">إلغاء</button>
          <button class="btn btn-primary" id="np-create">إنشاء وفتح شاشة المواد</button>
        </div>
      `);
      document.getElementById("np-close").onclick = closeModal;
      document.getElementById("np-cancel").onclick = closeModal;
      const submit = async () => {
        const name = document.getElementById("np-name").value.trim() || "مشروع جديد";
        closeModal();
        const res = await apiJson("/api/projects", "POST", { name });
        await openProject(res.project.id);
      };
      document.getElementById("np-create").onclick = submit;
      document.getElementById("np-name").onkeydown = (e) => {
        if (e.key === "Enter") submit();
      };
    };

    document.getElementById("btn-new-project").onclick = openNewModal;
    document.getElementById("btn-demo-project").onclick = createDemo;
    const emptyBtn = document.getElementById("btn-empty-new");
    if (emptyBtn) emptyBtn.onclick = openNewModal;
    const emptyDemo = document.getElementById("btn-empty-demo");
    if (emptyDemo) emptyDemo.onclick = createDemo;

    outlet.querySelectorAll("[data-open-proj]").forEach((btn) => {
      btn.onclick = () => openProject(btn.dataset.openProj);
    });
    outlet.querySelectorAll("[data-del-proj]").forEach((btn) => {
      btn.onclick = () => {
        confirmDialog(
          "حذف المشروع",
          `هل أنت متأكد من حذف المشروع «${btn.dataset.delName}»؟ لا يمكن التراجع عن هذا الإجراء.`,
          async () => {
            await api(`/api/projects/${btn.dataset.delProj}`, { method: "DELETE" });
            toast("تم حذف المشروع.", "success");
            loadProjects();
          }
        );
      };
    });
  }

  // ---------- Screen 1: "مشروع جديد" (2x2 Materials Grid + Quick Folder + Waveform) ----------
  function renderInputs(outlet) {
    const p = state.project;
    const tr = state.transcript;
    const scan = p.scenes_scan;
    const parsed = state.parsedPreview || { segments: [], warnings: [] };
    const segCount = parsed.segments?.length || 0;

    // Card 1: Audio status
    const hasAudio = Boolean(p.audio_path && p.audio_filename);
    const audioStatus = hasAudio ? `<span class="status-pill ready">جاهز ✓</span>` : `<span class="status-pill required">مطلوب</span>`;

    // Card 2: Transcript status
    const suspCount = tr?.stats?.suspicious_count || 0;
    const hasTr = Boolean(tr && tr.segments?.length);
    const trStatus = !hasTr
      ? `<span class="status-pill required">مطلوب</span>`
      : tr.precision === "sentence" || suspCount > 0
        ? `<span class="status-pill warn">تحذير</span>`
        : `<span class="status-pill ready">جاهز ✓</span>`;

    // Card 3: Numbered Script status
    const hasScript = segCount > 0;
    const scriptStatus = !hasScript
      ? `<span class="status-pill required">مطلوب</span>`
      : parsed.warnings?.length
        ? `<span class="status-pill warn">تحذير</span>`
        : `<span class="status-pill ready">جاهز ✓</span>`;

    // Card 4: Scenes status
    const sceneEntries = scan?.scenes ? Object.values(scan.scenes) : [];
    const sceneCount = sceneEntries.length;
    const imgCount = sceneEntries.filter((x) => x.type === "image").length;
    const vidCount = sceneEntries.filter((x) => x.type === "video").length;
    const hasSceneWarn = Boolean(
      scan && ((scan.ok === false && scan.error) || (scan.missing_numbers?.length) || (scan.extra_numbers?.length))
    );
    const scenesStatus = !scan || sceneCount === 0
      ? `<span class="status-pill required">مطلوب</span>`
      : hasSceneWarn
        ? `<span class="status-pill warn">تحذير</span>`
        : `<span class="status-pill ready">جاهز ✓</span>`;

    const companion = state.companions;
    const companionHtml = companion && (companion.transcript_name || companion.script_name)
      ? `<div class="alert alert-success" style="margin-top:8px;margin-bottom:0">
          <div>
            <strong>تم العثور على ملفات مجاورة:</strong>
            <div class="meta-line" style="color:inherit">
              ${companion.transcript_name ? `<span>تفريغ: <span class="ltr-name mono">${esc(companion.transcript_name)}</span></span>` : ""}
              ${companion.script_name ? `<span>نص: <span class="ltr-name mono">${esc(companion.script_name)}</span></span>` : ""}
            </div>
          </div>
          <button class="btn btn-sm btn-primary" id="btn-apply-companions">استيرادها الآن</button>
        </div>`
      : "";

    const scenesThumbsHtml = sceneEntries.length
      ? `<div class="scene-thumb-strip-mini">
          ${sceneEntries.slice(0, 12).map((sc) => `
            <div class="scene-mini-chip" title="${esc(sc.filename)}">
              ${sc.thumbnail_url ? `<img src="${esc(sc.thumbnail_url)}" alt="#${sc.number}" />` : `<div style="height:42px;display:flex;align-items:center;justify-content:center;font-size:11px">#${sc.number}</div>`}
              <span class="tabular">#${sc.number}</span>
            </div>
          `).join("")}
        </div>`
      : "";

    const bgLabel = p.settings.background_fill === "black" ? "خلفية سوداء" : "تمويه الخلفية";

    outlet.innerHTML = `
      <div class="section-header" style="margin-bottom:12px">
        <div>
          <h1 class="section-title">مشروع جديد</h1>
          <p class="section-subtitle">رتّب المواد الأربع هنا، أو أسقط مجلد المشروع دفعة واحدة ثم اضغط التشغيل.</p>
        </div>
        <button class="btn" id="btn-inputs-cuts-screen">فتح شاشة التفريغ (استيراد)</button>
      </div>

      <!-- Top Row: Project Name + Quick Folder Dropzone -->
      <div class="new-proj-top-row">
        <div class="card" style="padding:14px 16px;display:flex;flex-direction:column;justify-content:center">
          <label class="field-label" for="proj-name-input" style="margin-bottom:6px">اسم المشروع</label>
          <input class="input" id="proj-name-input" value="${esc(p.name || "")}" placeholder="مثال: وثائقي الحضارة الأندلسية" />
        </div>

        <div class="quick-folder-zone" id="quick-folder-dz">
          <div style="min-width:0">
            <div style="font-weight:700;font-size:14px">مشروع سريع من فولدر</div>
            <div class="field-hint" style="margin-top:2px">أسقط مجلد المشروع هنا لتعبئة الصوت والتفريغ و<span class="mono">script.txt</span> و<span class="mono">scenes/</span> تلقائياً</div>
          </div>
          <div style="display:flex;gap:8px;flex-shrink:0" onclick="event.stopPropagation()">
            <label class="btn btn-sm" style="cursor:pointer">
              اختر مجلد
              <input type="file" id="quick-folder-input" webkitdirectory directory multiple style="display:none" />
            </label>
            <button class="btn btn-sm btn-ghost" id="btn-toggle-quick-path">مسار محلي</button>
          </div>
        </div>
      </div>

      ${state.showQuickPathBox ? `
        <div class="card" style="padding:12px 16px;margin-bottom:16px">
          <div style="display:flex;gap:8px;align-items:center">
            <input class="input mono" style="direction:ltr;text-align:left" id="quick-folder-path-input" placeholder="C:\\Projects\\MyEpisode" />
            <button class="btn btn-primary btn-sm" id="btn-apply-quick-path">فحص وتعبئة</button>
          </div>
        </div>
      ` : ""}

      <!-- 2x2 Grid of 4 Numbered Material Cards -->
      <div class="materials-grid">
        <!-- Card 1: Audio -->
        <section class="material-card">
          <div class="material-card-head">
            <div class="material-card-title">
              <span class="card-step-num tabular">1</span>
              <span>الصوت</span>
            </div>
            ${audioStatus}
          </div>

          <div class="dropzone" id="audio-dropzone">
            <input type="file" id="audio-file-input" accept=".mp3,.wav,.m4a,.ogg,.aac,.flac,.mp4,audio/*" />
            <div style="font-weight:600;font-size:13.5px">اسحب ملف الصوت هنا أو انقر للاختيار</div>
            <div class="field-hint">MP3 · WAV · M4A · OGG — يبقى الصوت أصلياً دون قص</div>
          </div>

          ${hasAudio ? `
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13px">
              <strong class="ltr-name mono">${esc(p.audio_filename)}</strong>
              <span class="mono" style="color:var(--text-2)">${fmtDur(p.audio_duration)}</span>
            </div>
            <div class="waveform-box">
              <canvas class="waveform-canvas" id="inputs-waveform-canvas"></canvas>
            </div>
            <audio id="inputs-audio-player" controls src="/api/projects/${esc(p.id)}/audio" style="width:100%;height:34px"></audio>
          ` : `<div class="field-hint" style="margin-top:0">لم يتم اختيار ملف صوت بعد.</div>`}

          ${companionHtml}
        </section>

        <!-- Card 2: Transcript -->
        <section class="material-card">
          <div class="material-card-head">
            <div class="material-card-title">
              <span class="card-step-num tabular">2</span>
              <span>التفريغ</span>
            </div>
            ${trStatus}
          </div>

          <div class="vibe-guide-box">
            <strong>استيراد من Vibe أو Whisper:</strong>
            في برنامج <span class="mono">Vibe</span> افتح <strong>More Options</strong> ← فعّل <strong>Word timestamps</strong> ← صدّر بصيغة <strong>JSON</strong>.
            (يقبل أيضاً <span class="mono">SRT</span> و <span class="mono">VTT</span>).
          </div>

          <div class="dropzone" id="tr-card-dropzone">
            <input type="file" id="tr-card-file-input" accept=".json,.srt,.vtt,text/plain" />
            <div style="font-weight:600;font-size:13.5px">اسحب ملف التفريغ (<span class="mono">JSON / SRT / VTT</span>) أو انقر للاختيار</div>
          </div>

          ${hasTr ? `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
              <span class="ltr-name mono" style="font-size:13px;font-weight:600">${esc(tr.filename)}</span>
              <span class="precision-badge ${tr.precision === "word" ? "precision-word" : "precision-sentence"}">${esc(tr.format_label)}</span>
            </div>
            <div class="meta-line">
              <span class="tabular">${tr.stats?.word_count || 0} كلمة</span>
              <span>·</span>
              <span>آخر توقيت: <span class="mono">${fmtTime(tr.last_timestamp)}</span></span>
              <span>·</span>
              <span>التغطية: <span class="tabular">${tr.coverage_pct}%</span></span>
              ${suspCount ? `<span>·</span><span style="color:var(--warning-text)">${suspCount === 1 ? "سطر مشبوه واحد" : suspCount === 2 ? "سطران مشبوهان" : `${suspCount} سطور مشبوهة`}</span>` : ""}
            </div>
            ${tr.precision_hint ? `<div class="alert alert-warning" style="margin-bottom:0">${esc(tr.precision_hint)}</div>` : ""}
          ` : `<div class="field-hint" style="margin-top:0">لم يتم استيراد ملف تفريغ بعد.</div>`}

          ${renderImportErrorHtml(state.transcriptImportError)}
        </section>

        <!-- Card 3: Numbered Script -->
        <section class="material-card">
          <div class="material-card-head">
            <div class="material-card-title">
              <span class="card-step-num tabular">3</span>
              <span>النص المرقّم</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <span class="tabular" id="parsed-seg-count" style="font-size:12px;color:var(--accent);font-weight:700">${segCount} مقطع</span>
              ${scriptStatus}
            </div>
          </div>

          <textarea class="textarea" id="raw-text-input" style="min-height:175px" placeholder="1. الجملة الأولى للمشهد الأول&#10;2. الجملة الثانية للمشهد الثاني&#10;3. ...">${esc(p.raw_text || "")}</textarea>

          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
            <label class="btn btn-sm" style="cursor:pointer">
              استيراد ملف .txt
              <input type="file" id="script-txt-input" accept=".txt,text/plain" style="display:none" />
            </label>
            <span class="field-hint" style="margin-top:0">يقبل <span class="mono">1 نص</span> · <span class="mono">1.</span> · <span class="mono">[1]</span> · <span class="mono">١:</span></span>
          </div>

          <div id="text-warnings-box">
            ${(parsed.warnings || []).map((w) => `<div class="alert alert-warning" style="margin-bottom:6px">${esc(w)}</div>`).join("")}
          </div>
        </section>

        <!-- Card 4: Scenes -->
        <section class="material-card">
          <div class="material-card-head">
            <div class="material-card-title">
              <span class="card-step-num tabular">4</span>
              <span>المشاهد</span>
            </div>
            ${scenesStatus}
          </div>

          <div style="display:flex;gap:8px">
            <input class="input mono" style="direction:ltr;text-align:left" id="scenes-dir-input" value="${esc(p.scenes_dir || "")}" placeholder="C:\\Videos\\MyScenes" />
            <button class="btn btn-sm" id="btn-pick-folder">استعراض</button>
            <button class="btn btn-sm btn-primary" id="btn-scan-scenes">فحص</button>
          </div>

          <div class="dropzone" id="scenes-dropzone" style="padding:12px">
            <input type="file" id="scenes-files-input" multiple accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.mkv,.webm" />
            <div style="font-size:13px;font-weight:600">أو اسحب صور ومقاطع المشاهد هنا (1.png، scene_2.mp4...)</div>
          </div>

          ${scan ? (
            !scan.ok && scan.error
              ? `<div class="alert alert-error" style="margin-bottom:0">${esc(scan.error)}</div>`
              : `<div class="meta-line">
                  <strong class="tabular">${sceneCount} مشهد</strong>
                  <span>(<span class="tabular">${imgCount}</span> صورة · <span class="tabular">${vidCount}</span> فيديو)</span>
                </div>
                ${scenesThumbsHtml}
                ${scan.missing_numbers?.length ? `<div class="alert alert-warning" style="margin-bottom:0"><strong>أرقام مفقودة:</strong> <span class="tabular">${esc(scan.missing_numbers.join("، "))}</span></div>` : ""}
                ${scan.extra_numbers?.length ? `<div class="alert alert-warning" style="margin-bottom:0"><strong>مشاهد بدون نص:</strong> <span class="tabular">${esc(scan.extra_numbers.join("، "))}</span></div>` : ""}`
          ) : `<div class="field-hint" style="margin-top:0">لم يتم فحص مجلد المشاهد بعد.</div>`}
        </section>
      </div>

      <!-- Compact Settings Summary Line -->
      <div class="settings-summary-strip">
        <div class="meta-line" style="font-size:13px;gap:12px">
          <span>الإعدادات:</span>
          <span>الدقة <strong class="mono">${esc(p.settings.resolution)}</strong></span>
          <span>·</span>
          <span><strong class="tabular">${p.settings.fps} fps</strong></span>
          <span>·</span>
          <span>قوة الحركة <strong class="tabular">${p.settings.default_motion_strength}%</strong></span>
          <span>·</span>
          <span><strong>${esc(bgLabel)}</strong></span>
        </div>
        <button class="btn btn-sm" id="btn-inputs-settings">تعديل</button>
      </div>

      <div id="inputs-missing-callout">
        ${state.inputsMissingList ? `
          <div class="alert alert-warning" style="margin-top:14px;margin-bottom:0">
            <div>
              <strong>لا يمكن بدء التشغيل التلقائي بعد — ينقص:</strong>
              <span style="margin-inline-start:6px">${esc(state.inputsMissingList.join("، "))}</span>
            </div>
          </div>
        ` : ""}
      </div>

      <div class="action-footer">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <button class="btn" id="btn-manual-match">مطابقة ومراجعة نقاط القص</button>
          <span class="field-hint" style="margin:0">مفيش حاجة بتشتغل قبل ما تدوس</span>
        </div>
        <button class="btn btn-primary btn-lg" id="btn-auto-run">ابدأ التشغيل التلقائي</button>
      </div>
    `;

    // Bind waveform on audio card
    if (hasAudio) {
      const aEl = document.getElementById("inputs-audio-player");
      bindWaveform("inputs-waveform-canvas", aEl, `/api/projects/${p.id}/audio`);
    }

    // Bind problem report inside unknown_format card if present
    const unkRepBtn = document.getElementById("btn-unknown-copy-report");
    if (unkRepBtn) unkRepBtn.onclick = copyProblemReport;

    document.getElementById("btn-inputs-settings").onclick = openSettingsModal;
    document.getElementById("btn-inputs-cuts-screen").onclick = () => navigate("cuts");

    // Editable Project Name
    const nameInp = document.getElementById("proj-name-input");
    let nameTimer = null;
    const saveName = async () => {
      const val = nameInp.value.trim();
      if (!val || val === p.name) return;
      const res = await apiJson(`/api/projects/${p.id}`, "PATCH", { name: val });
      state.project = res.project;
      updateChrome();
      markSaved();
    };
    nameInp.oninput = () => {
      clearTimeout(nameTimer);
      nameTimer = setTimeout(saveName, 450);
    };
    nameInp.onblur = saveName;

    // Quick Folder handlers
    const qfBtnPath = document.getElementById("btn-toggle-quick-path");
    qfBtnPath.onclick = () => {
      state.showQuickPathBox = !state.showQuickPathBox;
      render();
    };

    const applyPathBtn = document.getElementById("btn-apply-quick-path");
    if (applyPathBtn) {
      applyPathBtn.onclick = async () => {
        const fpath = document.getElementById("quick-folder-path-input").value.trim();
        if (!fpath) return;
        try {
          toast("جاري فحص المجلد وتعبئة المواد...", "info");
          const res = await apiJson(`/api/projects/${p.id}/quick-folder`, "POST", { folder_path: fpath });
          state.project = res.project;
          if (res.transcript) state.transcript = res.transcript;
          if (state.project.raw_text) {
            state.parsedPreview = await apiJson(`/api/projects/${p.id}/parse-text`, "POST", { raw_text: state.project.raw_text });
          }
          state.showQuickPathBox = false;
          markSaved();
          toast("تمت تعبئة المواد من المجلد بنجاح!", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      };
    }

    const runQuickUpload = async (entries) => {
      if (!entries.length) return;
      const fd = new FormData();
      entries.forEach(({ file, relpath }) => fd.append("files", file, relpath));
      toast("جاري فرز ملفات المجلد وتعبئة البطاقات...", "info");
      try {
        const res = await api(`/api/projects/${p.id}/quick-folder`, { method: "POST", body: fd });
        state.project = res.project;
        if (res.transcript) state.transcript = res.transcript;
        if (state.project.raw_text) {
          state.parsedPreview = await apiJson(`/api/projects/${p.id}/parse-text`, "POST", { raw_text: state.project.raw_text });
        }
        state.inputsMissingList = null;
        markSaved();
        toast("تم استيراد المواد من المجلد بنجاح ✓", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };

    const qfInput = document.getElementById("quick-folder-input");
    qfInput.onchange = (e) => {
      const files = Array.from(e.target.files || []).map((f) => ({ file: f, relpath: f.webkitRelativePath || f.name }));
      runQuickUpload(files);
    };
    const qfDz = document.getElementById("quick-folder-dz");
    qfDz.onclick = () => qfInput.click();
    qfDz.ondragover = (e) => { e.preventDefault(); qfDz.classList.add("dragover"); };
    qfDz.ondragleave = () => qfDz.classList.remove("dragover");
    qfDz.ondrop = async (e) => {
      e.preventDefault();
      qfDz.classList.remove("dragover");
      const items = await extractFilesFromDataTransfer(e.dataTransfer);
      runQuickUpload(items);
    };

    // Audio upload
    const audioDz = document.getElementById("audio-dropzone");
    const audioInput = document.getElementById("audio-file-input");
    const handleAudioFile = async (file) => {
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      toast("جاري نسخ ملف الصوت وفحصه...", "info");
      try {
        const res = await api(`/api/projects/${p.id}/audio`, { method: "POST", body: fd });
        state.project = res.project;
        state.companions = res.companions || null;
        state.inputsMissingList = null;
        markSaved();
        toast("تم تحميل ملف الصوت بنجاح.", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    audioInput.onchange = (e) => handleAudioFile(e.target.files[0]);
    audioDz.ondragover = (e) => { e.preventDefault(); audioDz.classList.add("dragover"); };
    audioDz.ondragleave = () => audioDz.classList.remove("dragover");
    audioDz.ondrop = (e) => {
      e.preventDefault();
      audioDz.classList.remove("dragover");
      if (e.dataTransfer.files[0]) handleAudioFile(e.dataTransfer.files[0]);
    };

    const applyCompBtn = document.getElementById("btn-apply-companions");
    if (applyCompBtn && companion) {
      applyCompBtn.onclick = async () => {
        try {
          if (companion.transcript_path) {
            await performTranscriptUpload({ localPath: companion.transcript_path });
          }
          if (companion.script_path) {
            const sRes = await apiJson(`/api/projects/${p.id}/import-script-file`, "POST", { local_path: companion.script_path });
            state.project = sRes.project;
            state.parsedPreview = sRes;
          }
          state.companions = null;
          markSaved();
          toast("تم تطبيق الملفات المجاورة.", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      };
    }

    // Transcript card upload
    const trDz = document.getElementById("tr-card-dropzone");
    const trInp = document.getElementById("tr-card-file-input");
    trInp.onchange = (e) => {
      if (e.target.files[0]) performTranscriptUpload({ file: e.target.files[0] });
    };
    trDz.ondragover = (e) => { e.preventDefault(); trDz.classList.add("dragover"); };
    trDz.ondragleave = () => trDz.classList.remove("dragover");
    trDz.ondrop = (e) => {
      e.preventDefault();
      trDz.classList.remove("dragover");
      if (e.dataTransfer.files[0]) performTranscriptUpload({ file: e.dataTransfer.files[0] });
    };

    // Script .txt loader + live parsing
    document.getElementById("script-txt-input").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      try {
        const res = await api(`/api/projects/${p.id}/import-script-file`, { method: "POST", body: fd });
        state.project = res.project;
        state.parsedPreview = res;
        state.inputsMissingList = null;
        markSaved();
        toast(`تم تحميل ${res.segments.length} مقطع من ملف النص.`, "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };

    let parseTimer = null;
    const rawTextEl = document.getElementById("raw-text-input");
    rawTextEl.oninput = () => {
      state.project.raw_text = rawTextEl.value;
      clearTimeout(parseTimer);
      parseTimer = setTimeout(async () => {
        try {
          const res = await apiJson(`/api/projects/${p.id}/parse-text`, "POST", { raw_text: rawTextEl.value });
          state.parsedPreview = res;
          markSaved();
          document.getElementById("parsed-seg-count").textContent = `${res.segments.length} مقطع`;
          document.getElementById("text-warnings-box").innerHTML = (res.warnings || [])
            .map((w) => `<div class="alert alert-warning" style="margin-bottom:6px">${esc(w)}</div>`)
            .join("");
        } catch {
          // ignore typing transient
        }
      }, 320);
    };

    // Scenes folder scan & drop
    const scenesDirInput = document.getElementById("scenes-dir-input");
    document.getElementById("btn-pick-folder").onclick = async () => {
      try {
        const res = await apiJson("/api/system/pick-folder", "POST", {});
        if (res.path) {
          scenesDirInput.value = res.path;
          document.getElementById("btn-scan-scenes").click();
        } else if (res.error) {
          toast(res.error, "info");
        }
      } catch (err) {
        toast(err.message, "info");
      }
    };

    document.getElementById("btn-scan-scenes").onclick = async () => {
      try {
        const scanRes = await apiJson(`/api/projects/${p.id}/scan-scenes`, "POST", {
          scenes_dir: scenesDirInput.value.trim(),
          raw_text: rawTextEl.value,
        });
        state.project.scenes_dir = scenesDirInput.value.trim();
        state.project.scenes_scan = scanRes;
        markSaved();
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };

    const scenesDz = document.getElementById("scenes-dropzone");
    const scenesFilesInput = document.getElementById("scenes-files-input");
    const handleScenesFiles = async (fileList) => {
      if (!fileList || !fileList.length) return;
      const fd = new FormData();
      Array.from(fileList).forEach((f) => fd.append("files", f));
      toast("جاري استيراد المشاهد وفحص الأرقام...", "info");
      try {
        const scanRes = await api(`/api/projects/${p.id}/upload-scenes`, { method: "POST", body: fd });
        state.project.scenes_dir = scanRes.scenes_dir;
        state.project.scenes_scan = scanRes;
        markSaved();
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    scenesFilesInput.onchange = (e) => handleScenesFiles(e.target.files);
    scenesDz.ondragover = (e) => { e.preventDefault(); scenesDz.classList.add("dragover"); };
    scenesDz.ondragleave = () => scenesDz.classList.remove("dragover");
    scenesDz.ondrop = (e) => {
      e.preventDefault();
      scenesDz.classList.remove("dragover");
      handleScenesFiles(e.dataTransfer.files);
    };

    // Start Run (only on explicit button press)
    const runMatch = async (autoMode) => {
      const missing = [];
      if (!state.project.audio_path) missing.push("ملف الصوت");
      if (!state.transcript || !state.transcript.segments?.length) missing.push("ملف التفريغ (JSON / SRT / VTT)");
      if (!rawTextEl.value.trim()) missing.push("النص المرقّم (1..N)");
      if (state.project.scenes_scan?.ok === false && state.project.scenes_scan?.conflicts?.length) {
        missing.push("حل تعارض أرقام المشاهد");
      }
      if (missing.length) {
        state.inputsMissingList = missing;
        toast(`ينقص: ${missing.join("، ")}`, "warning");
        render();
        return;
      }
      state.inputsMissingList = null;

      try {
        const res = await apiJson(`/api/projects/${p.id}/match-cuts`, "POST", {
          raw_text: rawTextEl.value,
          scenes_dir: scenesDirInput.value.trim(),
          auto_mode: autoMode,
        });
        state.project = res.project;
        state.cuts = res.cuts;
        state.timeline = res.timeline;
        if (res.scenes_scan) state.project.scenes_scan = res.scenes_scan;
        markSaved();
        if (autoMode) {
          toast("تمت المطابقة وتركيب المشاهد! حدد الحركات على الصور الآن.", "success");
          await navigate("motion");
        } else {
          toast("تم بناء نقاط القص! يمكنك مراجعتها وتعديلها.", "success");
          state.cutsSubTab = "cuts";
          await navigate("cuts");
        }
      } catch (err) {
        toast(err.message, "error");
      }
    };

    document.getElementById("btn-auto-run").onclick = () => runMatch(true);
    document.getElementById("btn-manual-match").onclick = () => runMatch(false);
  }

  // ---------- Screen 2: "التفريغ (استيراد)" & Cuts Review ----------
  function renderCuts(outlet) {
    const p = state.project;
    const tr = state.transcript;
    const segs = state.cuts?.segments || [];
    const trSegs = tr?.segments || [];
    const suspiciousOnly = state.transcriptFilter === "suspicious";
    const visibleTrSegs = suspiciousOnly ? trSegs.filter((s) => s.suspicious_reasons?.length) : trSegs;

    const precisionBanner = tr
      ? `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <strong class="ltr-name mono">${esc(tr.filename)}</strong>
            <span class="precision-badge ${tr.precision === "word" ? "precision-word" : "precision-sentence"}">${esc(tr.format_label)}</span>
          </div>
          ${tr.precision_hint ? `<span style="font-size:12.5px;color:var(--warning-text);font-weight:600">${esc(tr.precision_hint)}</span>` : ""}
        </div>`
      : "";

    const statsStripHtml = tr
      ? `<div class="stats-strip">
          <div class="stat-chip">
            <div class="stat-label">التغطية</div>
            <div class="stat-value tabular">${tr.coverage_pct ?? 0}%</div>
          </div>
          <div class="stat-chip">
            <div class="stat-label">السطور / الكلمات</div>
            <div class="stat-value tabular">${tr.stats?.segment_count || 0} <span style="font-size:13px;color:var(--text-2)">(${tr.stats?.word_count || 0} كلمة)</span></div>
          </div>
          <div class="stat-chip">
            <div class="stat-label">آخر توقيت / مدة الصوت</div>
            <div class="stat-value mono" style="font-size:17px">${fmtTime(tr.last_timestamp)} / ${fmtTime(p.audio_duration || tr.duration)}</div>
          </div>
          <div class="stat-chip">
            <div class="stat-label">سطور مشبوهة</div>
            <div class="stat-value tabular" style="color:${(tr.stats?.suspicious_count || 0) > 0 ? "var(--warning-text)" : "var(--success-text)"}">${tr.stats?.suspicious_count || 0}</div>
          </div>
        </div>`
      : "";

    let bodyHtml = "";
    if (!tr) {
      bodyHtml = `
        <div class="empty-state">
          <div class="empty-icon">📄</div>
          <h3>لم يتم استيراد ملف تفريغ بعد</h3>
          <p style="color:var(--text-2);margin-bottom:14px">اسحب ملف <span class="mono">JSON</span> أو <span class="mono">SRT</span> أو <span class="mono">VTT</span> من الأعلى، أو الصق المحتوى مباشرة.</p>
        </div>`;
    } else if (state.cutsSubTab === "preview") {
      bodyHtml = `
        <div class="transcript-toolbar">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn btn-sm ${!suspiciousOnly ? "btn-primary" : ""}" id="btn-tr-filter-all">الكل (${trSegs.length})</button>
            <button class="btn btn-sm ${suspiciousOnly ? "btn-primary" : ""}" id="btn-tr-filter-susp">المشبوهة فقط (${tr.stats?.suspicious_count || 0})</button>
          </div>
          <div class="field-hint" style="margin:0">انقر على أي سطر لتشغيل الصوت من توقيته فوراً</div>
        </div>

        <div class="transcript-list">
          ${visibleTrSegs.length ? visibleTrSegs.map((s) => {
            const isSusp = Boolean(s.suspicious_reasons?.length);
            const isIgn = Boolean(s.ignored);
            const cls = `${isSusp ? "suspicious" : ""} ${isIgn ? "ignored" : ""}`.trim();
            return `<div class="transcript-row ${cls}" data-tr-play="${Number(s.start)}">
              <span class="mono" style="font-size:12px;color:var(--text-2)">#${s.index}</span>
              <span class="mono" style="font-size:12px">${fmtTime(s.start)} → ${fmtTime(s.end)}</span>
              <div>
                <div style="font-size:14px">${esc(s.text)}</div>
                ${isSusp ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">${(s.suspicious_labels || []).map((lbl) => `<span class="est-badge">${esc(lbl)}</span>`).join("")}</div>` : ""}
              </div>
              <div onclick="event.stopPropagation()">
                ${isSusp || isIgn ? `
                  <button class="btn btn-sm ${isIgn ? "" : "btn-danger"}" data-toggle-ign="${s.index}">
                    ${isIgn ? "إلغاء التجاهل" : "تجاهل هذا السطر"}
                  </button>
                ` : ""}
              </div>
            </div>`;
          }).join("") : `<div style="padding:24px;text-align:center;color:var(--text-2)">لا توجد سطور مشبوهة ✓</div>`}
        </div>
      `;
    } else {
      bodyHtml = segs.length ? `
        <div class="table-wrap">
          <table class="cuts-table">
            <thead>
              <tr>
                <th>#</th>
                <th>النص المدخل</th>
                <th>النص المطابق</th>
                <th>البداية (ثانية)</th>
                <th>النهاية</th>
                <th>المدة</th>
                <th>الدقة</th>
                <th>تشغيل / ضبط</th>
              </tr>
            </thead>
            <tbody>
              ${segs.map((s, idx) => {
                const conf = Number(s.confidence || 0);
                const isUnmatched = !s.matched;
                const rowCls = isUnmatched ? "row-bad" : conf < 68 ? "row-warn" : "";
                const badgeCls = isUnmatched || conf < 55 ? "conf-low" : conf < 75 ? "conf-mid" : "conf-high";
                const badgeTxt = isUnmatched ? "غير متطابق" : `${Math.round(conf)}%`;
                const estTag = s.estimated ? `<span class="est-badge" title="مطابقة داخل سطر جملة بتقدير نسبي">تقدير</span>` : "";
                return `<tr class="${rowCls}" data-seg-row="${s.id}">
                  <td class="tabular" style="font-weight:700">${s.id}</td>
                  <td style="max-width:230px">${esc(s.text)}</td>
                  <td style="max-width:230px;color:var(--text-2)">${isUnmatched ? `<span style="color:var(--danger-text)">غير متطابق</span>` : esc(s.matched_text)}</td>
                  <td>
                    <div class="time-edit-group">
                      <button class="btn btn-sm btn-icon" data-nudge="-0.1" data-seg="${s.id}" ${idx === 0 ? "disabled" : ""} title="-0.1 ثانية">-</button>
                      <input class="time-input" type="number" step="0.1" min="0" value="${Number(s.start).toFixed(2)}" data-start-input="${s.id}" ${idx === 0 ? "disabled" : ""} />
                      <button class="btn btn-sm btn-icon" data-nudge="0.1" data-seg="${s.id}" ${idx === 0 ? "disabled" : ""} title="+0.1 ثانية">+</button>
                    </div>
                  </td>
                  <td class="mono">${fmtTime(s.end)}</td>
                  <td class="mono">${Number(s.duration).toFixed(2)}s</td>
                  <td><span class="conf-badge ${badgeCls} tabular">${badgeTxt}</span>${estTag}</td>
                  <td>
                    <div style="display:flex;gap:6px">
                      <button class="btn btn-sm" data-play-from="${Math.max(0, Number(s.start) - 1.0)}" title="تشغيل قبل البداية بثانية">▶ -1ث</button>
                      ${idx > 0 ? `<button class="btn btn-sm" data-stamp-here="${s.id}" title="ضبط البداية عند موضع الصوت الحالي">ابدأ هنا</button>` : ""}
                    </div>
                  </td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>
        </div>
      ` : `
        <div class="empty-state">
          <h3>لم يتم حساب نقاط القص بعد</h3>
          <p style="color:var(--text-2);margin-bottom:16px">تأكد من كتابة النص المرقّم في المدخلات ثم اضغط «مطابقة وبناء نقاط القص».</p>
          <button class="btn btn-primary" id="btn-run-match-empty">مطابقة وبناء نقاط القص</button>
        </div>
      `;
    }

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">2. التفريغ (استيراد) ونقاط القص</h1>
          <p class="section-subtitle">استورد تفريغ Vibe أو Whisper، راجع السطور المشبوهة، واضبط نقاط القص بدقة.</p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" id="btn-cuts-rematch">مطابقة وبناء نقاط القص</button>
          <button class="btn btn-primary btn-lg" id="btn-save-cuts-next">حفظ ومتابعة</button>
        </div>
      </div>

      <!-- External Transcript Import Card -->
      <section class="card" style="margin-bottom:16px;padding:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div class="dropzone" id="cuts-tr-dropzone" style="flex:1;min-width:260px;padding:12px">
            <input type="file" id="cuts-tr-file-input" accept=".json,.srt,.vtt,text/plain" />
            <div style="font-weight:600;font-size:13.5px">استيراد ملف تفريغ جديد (<span class="mono">Vibe JSON / whisper.cpp JSON / OpenAI JSON / SRT / VTT</span>)</div>
          </div>
          <button class="btn btn-sm" id="btn-toggle-paste-tr">${state.showPasteBox ? "إخفاء صندوق اللصق" : "لصق النص مباشرة"}</button>
        </div>

        ${state.showPasteBox ? `
          <div style="margin-top:12px">
            <textarea class="textarea mono" id="paste-tr-textarea" style="min-height:140px;direction:ltr;text-align:left" placeholder="الصق محتوى JSON أو SRT أو VTT هنا..."></textarea>
            <div style="display:flex;justify-content:flex-end;margin-top:8px">
              <button class="btn btn-primary btn-sm" id="btn-submit-paste-tr">تحليل واستيراد النص</button>
            </div>
          </div>
        ` : ""}

        ${precisionBanner}
        ${renderImportErrorHtml(state.transcriptImportError)}
      </section>

      ${statsStripHtml}

      ${p.audio_path ? `
        <div class="audio-bar">
          <canvas class="waveform-canvas" id="cuts-waveform-canvas" height="42"></canvas>
          <div class="audio-bar-top">
            <audio id="cuts-audio-player" controls src="/api/projects/${esc(p.id)}/audio"></audio>
            <span class="mono" id="cuts-playhead" style="font-size:13px;color:var(--accent)">00:00.0</span>
          </div>
        </div>
      ` : ""}

      ${tr ? `
        <div class="segmented-tabs" style="margin-bottom:14px">
          <button class="seg-tab ${state.cutsSubTab === "preview" ? "active" : ""}" data-cutstab="preview">
            معاينة التفريغ (${trSegs.length})
          </button>
          <button class="seg-tab ${state.cutsSubTab === "cuts" ? "active" : ""}" data-cutstab="cuts">
            نقاط القص (${segs.length})
          </button>
        </div>
      ` : ""}

      ${bodyHtml}
    `;

    // Unknown format report button
    const unkBtn = document.getElementById("btn-unknown-copy-report");
    if (unkBtn) unkBtn.onclick = copyProblemReport;

    // Import dropzone & paste box
    const cutsDz = document.getElementById("cuts-tr-dropzone");
    const cutsInp = document.getElementById("cuts-tr-file-input");
    cutsInp.onchange = (e) => {
      if (e.target.files[0]) performTranscriptUpload({ file: e.target.files[0] });
    };
    cutsDz.ondragover = (e) => { e.preventDefault(); cutsDz.classList.add("dragover"); };
    cutsDz.ondragleave = () => cutsDz.classList.remove("dragover");
    cutsDz.ondrop = (e) => {
      e.preventDefault();
      cutsDz.classList.remove("dragover");
      if (e.dataTransfer.files[0]) performTranscriptUpload({ file: e.dataTransfer.files[0] });
    };

    document.getElementById("btn-toggle-paste-tr").onclick = () => {
      state.showPasteBox = !state.showPasteBox;
      render();
    };
    const subPasteBtn = document.getElementById("btn-submit-paste-tr");
    if (subPasteBtn) {
      subPasteBtn.onclick = () => {
        const txt = document.getElementById("paste-tr-textarea").value;
        performTranscriptUpload({ text: txt, filename: "pasted_transcript.txt" });
      };
    }

    // Subtabs & Filters
    outlet.querySelectorAll("[data-cutstab]").forEach((btn) => {
      btn.onclick = () => {
        state.cutsSubTab = btn.dataset.cutstab;
        render();
      };
    });

    const fAll = document.getElementById("btn-tr-filter-all");
    const fSusp = document.getElementById("btn-tr-filter-susp");
    if (fAll) fAll.onclick = () => { state.transcriptFilter = "all"; render(); };
    if (fSusp) fSusp.onclick = () => { state.transcriptFilter = "suspicious"; render(); };

    // Audio player + Waveform
    const audioEl = document.getElementById("cuts-audio-player");
    const playheadEl = document.getElementById("cuts-playhead");
    if (audioEl) {
      audioEl.ontimeupdate = () => {
        if (playheadEl) playheadEl.textContent = fmtTime(audioEl.currentTime);
      };
      bindWaveform("cuts-waveform-canvas", audioEl, `/api/projects/${p.id}/audio`);
    }

    // Transcript preview click-to-play & ignore toggle
    outlet.querySelectorAll("[data-tr-play]").forEach((row) => {
      row.onclick = () => {
        if (audioEl) {
          audioEl.currentTime = Number(row.dataset.trPlay) || 0;
          audioEl.play().catch(() => {});
        }
      };
    });

    outlet.querySelectorAll("[data-toggle-ign]").forEach((btn) => {
      btn.onclick = async () => {
        const idx = Number(btn.dataset.toggleIgn);
        const curIgn = new Set((tr.segments || []).filter((x) => x.ignored).map((x) => Number(x.index)));
        if (curIgn.has(idx)) curIgn.delete(idx);
        else curIgn.add(idx);
        try {
          const res = await apiJson(`/api/projects/${p.id}/transcript/ignored`, "POST", {
            ignored_indices: Array.from(curIgn),
          });
          state.transcript = res.transcript;
          if (res.cuts) state.cuts = res.cuts;
          if (res.timeline) state.timeline = res.timeline;
          markSaved();
          toast("تم تحديث حالة السطر.", "success");
          render();
        } catch (err) {
          toast(err.message, "error");
        }
      };
    });

    // Rematch cuts action
    const doMatchCuts = async () => {
      try {
        const res = await apiJson(`/api/projects/${p.id}/match-cuts`, "POST", { auto_mode: false });
        state.project = res.project;
        state.cuts = res.cuts;
        state.timeline = res.timeline;
        state.cutsSubTab = "cuts";
        markSaved();
        toast("تم بناء نقاط القص بنجاح.", "success");
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    document.getElementById("btn-cuts-rematch").onclick = doMatchCuts;
    const emptyMatchBtn = document.getElementById("btn-run-match-empty");
    if (emptyMatchBtn) emptyMatchBtn.onclick = doMatchCuts;

    // Cuts table controls
    const applyNewStart = async (segId, newStart) => {
      try {
        const res = await apiJson(`/api/projects/${p.id}/cuts/${segId}/start`, "POST", { start: newStart });
        state.cuts = res.cuts;
        state.timeline = res.timeline;
        markSaved();
        render();
      } catch (err) {
        toast(err.message, "error");
      }
    };

    outlet.querySelectorAll("[data-play-from]").forEach((btn) => {
      btn.onclick = () => {
        if (audioEl) {
          audioEl.currentTime = Number(btn.dataset.playFrom) || 0;
          audioEl.play().catch(() => {});
        }
      };
    });

    outlet.querySelectorAll("[data-nudge]").forEach((btn) => {
      btn.onclick = () => {
        const sid = Number(btn.dataset.seg);
        const seg = segs.find((x) => Number(x.id) === sid);
        if (!seg) return;
        applyNewStart(sid, Number(seg.start) + Number(btn.dataset.nudge));
      };
    });

    outlet.querySelectorAll("[data-start-input]").forEach((inp) => {
      inp.onchange = () => {
        applyNewStart(Number(inp.dataset.startInput), Number(inp.value));
      };
    });

    outlet.querySelectorAll("[data-stamp-here]").forEach((btn) => {
      btn.onclick = () => {
        if (audioEl) applyNewStart(Number(btn.dataset.stampHere), audioEl.currentTime);
      };
    });

    document.getElementById("btn-save-cuts-next").onclick = async () => {
      if (!state.cuts) {
        await doMatchCuts();
        return;
      }
      try {
        const res = await apiJson(`/api/projects/${p.id}/cuts`, "PUT", { cuts: state.cuts, stage: "scenes" });
        state.cuts = res.cuts;
        state.timeline = res.timeline;
        markSaved();
        toast("تم حفظ نقاط القص.", "success");
        await navigate("scenes");
      } catch (err) {
        toast(err.message, "error");
      }
    };
  }

  // ---------- Screen 3: Scenes ----------
  function renderScenes(outlet) {
    const p = state.project;
    const tl = state.timeline;
    const items = tl?.items || [];

    if (!items.length) {
      outlet.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🎬</div>
          <h3>لم يتم تركيب المشاهد على الخط الزمني بعد</h3>
          <p style="color:var(--text-2);margin-bottom:18px">تأكد من اختيار مجلد المشاهد وإتمام استيراد التفريغ ونقاط القص أولاً.</p>
          <button class="btn btn-primary" id="btn-sc-inputs">الذهاب للمدخلات</button>
        </div>`;
      document.getElementById("btn-sc-inputs").onclick = () => navigate("inputs");
      return;
    }

    const imgCount = items.filter((i) => i.scene_type === "image").length;
    const vidCount = items.filter((i) => i.scene_type === "video").length;
    const missCount = items.filter((i) => i.scene_type === "missing").length;

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">3. توزيع المشاهد على الخط الزمني</h1>
          <p class="section-subtitle">خط زمني متصل بدون أي فراغات أو إطارات سوداء. انقر على أي مشهد لمعاينته.</p>
        </div>
        <button class="btn btn-primary btn-lg" id="btn-scenes-next">متابعة إلى الحركات</button>
      </div>

      <div class="summary-grid">
        <div class="stat-card"><div class="stat-label">إجمالي المشاهد</div><div class="stat-value tabular">${items.length}</div></div>
        <div class="stat-card"><div class="stat-label">الصور</div><div class="stat-value tabular">${imgCount}</div></div>
        <div class="stat-card"><div class="stat-label">مقاطع الفيديو (صامتة)</div><div class="stat-value tabular">${vidCount}</div></div>
        <div class="stat-card"><div class="stat-label">المدة الكلية</div><div class="stat-value mono">${fmtDur(tl.video_duration)}</div></div>
        ${missCount ? `<div class="stat-card"><div class="stat-label" style="color:var(--warning-text)">مشاهد مفقودة</div><div class="stat-value tabular" style="color:var(--warning-text)">${missCount}</div></div>` : ""}
      </div>

      <div class="scenes-grid">
        ${items.map((it) => {
          const thumbHtml = it.thumbnail_url
            ? `<img src="${esc(it.thumbnail_url)}" alt="مشهد ${it.segment_id}" />`
            : `<span style="color:var(--text-2);font-size:12px">${it.scene_type === "missing" ? "مشهد مفقود" : "معاينة"}</span>`;
          const typeLabel = it.scene_type === "video" ? `فيديو · ${Number(it.speed).toFixed(2)}x` : it.scene_type === "image" ? "صورة" : "مفقود";
          return `<article class="scene-card" data-preview-seg="${it.segment_id}">
            <div class="scene-thumb-box">
              ${thumbHtml}
              <span class="scene-num-tag tabular">#${it.segment_id}</span>
            </div>
            <div class="scene-card-body">
              <div class="meta-line" style="justify-content:space-between">
                <span>${typeLabel}</span>
                <span class="mono">${Number(it.duration).toFixed(2)}s</span>
              </div>
              <div class="field-hint" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><span class="ltr-name">${esc(it.scene_filename || "بدون ملف")}</span></div>
              ${it.speed_badge ? `<span class="speed-warning-tag">${esc(it.speed_badge)}</span>` : ""}
            </div>
          </article>`;
        }).join("")}
      </div>
    `;

    outlet.querySelectorAll("[data-preview-seg]").forEach((card) => {
      card.onclick = () => {
        const sid = Number(card.dataset.previewSeg);
        const it = items.find((x) => Number(x.segment_id) === sid);
        if (!it) return;
        const mediaUrl = `/api/projects/${esc(p.id)}/scene-file/${sid}`;
        const mediaTag = it.scene_type === "video"
          ? `<video src="${mediaUrl}" controls muted autoplay style="width:100%;max-height:420px;border-radius:8px;background:#000"></video>`
          : it.scene_type === "image"
            ? `<img src="${mediaUrl}" alt="مشهد ${sid}" style="width:100%;max-height:420px;object-fit:contain;border-radius:8px;background:#000" />`
            : `<div class="alert alert-warning">هذا المشهد مفقود من مجلد المشاهد وسيظهر كإطار داكن محايد عند التصدير.</div>`;
        openModal(`
          <div class="modal-header">
            <h3 style="margin:0">معاينة المشهد #${sid}</h3>
            <button class="btn btn-sm btn-ghost" id="sc-prev-close">✕</button>
          </div>
          ${mediaTag}
          <div class="meta-line" style="margin-top:12px">
            <span>الفترة: <span class="mono">${fmtTime(it.start)} → ${fmtTime(it.end)}</span></span>
            <span>·</span>
            <span>المدة: <span class="mono">${Number(it.duration).toFixed(2)}s</span></span>
            <span>·</span>
            <span>الإطارات: <span class="tabular">${it.frame_count}</span></span>
          </div>
        `, true);
        document.getElementById("sc-prev-close").onclick = closeModal;
      };
    });

    document.getElementById("btn-scenes-next").onclick = () => navigate("motion");
  }

  // ---------- Screen 4: Motion Studio ----------
  function getImageItems() {
    return (state.timeline?.items || []).filter((it) => it.scene_type === "image");
  }

  function getTabIndex(tabId) {
    return MOTION_TABS.findIndex((t) => t.id === tabId);
  }

  function getEligibleImagesForTab(tabId) {
    const imgs = getImageItems();
    const tabIdx = getTabIndex(tabId);
    const assignments = state.motions.assignments || {};
    return imgs.filter((it) => {
      const a = assignments[String(it.segment_id)];
      if (!a || !a.type) return true;
      return getTabIndex(a.type) >= tabIdx;
    });
  }

  async function saveMotionsNow() {
    if (!state.project) return;
    try {
      await apiJson(`/api/projects/${state.project.id}/motions`, "PUT", state.motions);
      markSaved();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function renderMotion(outlet) {
    const p = state.project;
    const allImages = getImageItems();
    if (!allImages.length) {
      outlet.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">✨</div>
          <h3>لا توجد صور لتطبيق الحركات عليها</h3>
          <p style="color:var(--text-2);margin-bottom:18px">المشاهد الحالية عبارة عن مقاطع فيديو فقط (أو لم يتم تركيب المشاهد بعد). يمكنك التصدير مباشرة.</p>
          <button class="btn btn-primary btn-lg" id="btn-motion-skip-export">تنفيذ والتصدير</button>
        </div>`;
      document.getElementById("btn-motion-skip-export").onclick = triggerExport;
      return;
    }

    const assignments = state.motions.assignments || {};
    const globalStrength = Number(state.motions.global_strength ?? 15);
    const eligible = getEligibleImagesForTab(state.motionTab);

    let currIdx = eligible.findIndex((x) => Number(x.segment_id) === Number(state.motionCurrentSegId));
    if (currIdx === -1 && eligible.length > 0) {
      currIdx = 0;
      state.motionCurrentSegId = eligible[0].segment_id;
    }
    const currItem = currIdx >= 0 ? eligible[currIdx] : null;
    const currAssign = currItem ? assignments[String(currItem.segment_id)] : null;
    const isAssignedInThisTab = currAssign && currAssign.type === state.motionTab;

    const tabsHtml = MOTION_TABS.map((t) => {
      const countInTab = allImages.filter((it) => assignments[String(it.segment_id)]?.type === t.id).length;
      const eligCount = getEligibleImagesForTab(t.id).length;
      return `<button class="seg-tab ${state.motionTab === t.id ? "active" : ""}" data-mtab="${t.id}">
        <span>${t.icon}</span>
        <span>${t.label}</span>
        <span class="tabular" style="opacity:0.85">${countInTab}/${eligCount}</span>
      </button>`;
    }).join("");

    const currTabIdx = getTabIndex(state.motionTab);
    const hasNextTab = currTabIdx < MOTION_TABS.length - 1;

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">4. حركات الصور (نقرة واحدة على نقطة التركيز)</h1>
          <p class="section-subtitle">انقر في أي مكان على الصورة لوضع النقطة الزرقاء. الصور التي تأخذ حركة لا تظهر في التبويبات التالية.</p>
        </div>
        <div style="display:flex;gap:10px">
          ${hasNextTab ? `<button class="btn" id="btn-next-motion-tab">التالي: ${MOTION_TABS[currTabIdx + 1].label} ←</button>` : ""}
          <button class="btn btn-primary btn-lg" id="btn-motion-export">تنفيذ الحركات والتصدير</button>
        </div>
      </div>

      <div class="motion-toolbar">
        <div class="segmented-tabs" role="tablist">${tabsHtml}</div>
        <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
          <label style="display:flex;align-items:center;gap:8px;font-size:13px">
            <span>قوة الحركة العامة:</span>
            <input type="range" id="global-strength-slider" min="5" max="40" step="1" value="${globalStrength}" />
            <span class="tabular mono" id="global-strength-val">${globalStrength}%</span>
          </label>
        </div>
      </div>

      ${currItem ? `
        <div class="motion-controls-bar">
          <div class="meta-line" style="font-size:14px;color:var(--text)">
            <strong class="tabular">صورة ${currIdx + 1} من ${eligible.length}</strong>
            <span>·</span>
            <span>مشهد <strong class="tabular">#${currItem.segment_id}</strong></span>
            <span>·</span>
            <span class="mono">${Number(currItem.duration).toFixed(1)}s</span>
          </div>

          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            ${isAssignedInThisTab ? `
              <label style="display:flex;align-items:center;gap:6px;font-size:13px">
                <span>قوة هذه الصورة:</span>
                <input type="range" id="item-strength-slider" min="5" max="40" step="1" value="${Number(currAssign.strength ?? globalStrength)}" />
                <span class="tabular mono" id="item-strength-val">${Number(currAssign.strength ?? globalStrength)}%</span>
              </label>
              <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
                <input type="checkbox" id="item-ease-toggle" ${currAssign.ease !== false ? "checked" : ""} />
                <span>تخفيف الحركة (Ease)</span>
              </label>
              <button class="btn btn-sm" id="btn-preview-motion">معاينة الحركة</button>
              <button class="btn btn-sm btn-danger" id="btn-clear-dot">مسح النقطة</button>
            ` : `<span style="font-size:13px;color:var(--text-2)">انقر على الصورة لتعيين حركة ${esc(state.motionTab)} أو انتقل للتالية لتركها ثابتة</span>`}
          </div>
        </div>

        <div class="motion-stage-wrap">
          <button class="nav-circle-btn" id="btn-prev-img" ${currIdx <= 0 ? "disabled" : ""} title="الصورة السابقة (→)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="9 18 15 12 9 6"/></svg>
          </button>

          <div class="motion-stage">
            ${state.previewVideoUrl ? `
              <div style="width:100%;max-width:740px;text-align:center">
                <video src="${esc(state.previewVideoUrl)}" controls autoplay loop style="width:100%;max-height:48vh;border-radius:8px;background:#000"></video>
                <div style="margin-top:8px">
                  <button class="btn btn-sm" id="btn-close-preview">العودة لنقطة التركيز</button>
                </div>
              </div>
            ` : `
              <div class="stage-image-box" id="stage-image-box">
                <img src="/api/projects/${esc(p.id)}/scene-file/${currItem.segment_id}" alt="مشهد ${currItem.segment_id}" />
                ${isAssignedInThisTab ? `
                  <div class="focus-dot" id="focus-dot" style="left:${(currAssign.x * 100).toFixed(2)}%;top:${(currAssign.y * 100).toFixed(2)}%" title="اسحب لتحريك النقطة أو انقر عليها للحذف"></div>
                ` : ""}
              </div>
            `}
          </div>

          <button class="nav-circle-btn" id="btn-next-img" ${currIdx >= eligible.length - 1 ? "disabled" : ""} title="الصورة التالية (←)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
        </div>
      ` : `
        <div class="empty-state" style="margin-bottom:16px">
          <h3>تم تعيين حركات لجميع الصور في التبويبات السابقة ✓</h3>
          <p style="color:var(--text-2);margin-bottom:14px">يمكنك الضغط على أي صورة في الشريط أدناه لتعديل حركتها أو الضغط على تنفيذ الحركات والتصدير.</p>
        </div>
      `}

      <div class="thumb-strip" id="motion-thumb-strip">
        ${allImages.map((it) => {
          const a = assignments[String(it.segment_id)];
          const tabMeta = a ? MOTION_TABS.find((t) => t.id === a.type) : null;
          const isActive = currItem && Number(currItem.segment_id) === Number(it.segment_id);
          return `<div class="strip-item ${isActive ? "active" : ""}" data-strip-seg="${it.segment_id}" title="مشهد #${it.segment_id}">
            <img src="${esc(it.thumbnail_url || `/api/projects/${p.id}/scene-file/${it.segment_id}`)}" alt="#${it.segment_id}" />
            <span class="strip-badge ${tabMeta ? "assigned" : ""}">#${it.segment_id} ${tabMeta ? tabMeta.icon : ""}</span>
          </div>`;
        }).join("")}
      </div>

      <div class="shortcut-bar">
        <span>اختصارات لوحة المفاتيح:</span>
        <span><span class="kbd">←</span> / <span class="kbd">Enter</span> التالية</span>
        <span><span class="kbd">→</span> السابقة</span>
        <span><span class="kbd">Delete</span> مسح النقطة</span>
        <span><span class="kbd">S</span> تخطي</span>
        <span><span class="kbd">Z</span> تراجع</span>
        <span><span class="kbd">1-4</span> تبديل التبويب</span>
      </div>
    `;

    outlet.querySelectorAll("[data-mtab]").forEach((btn) => {
      btn.onclick = () => {
        state.motionTab = btn.dataset.mtab;
        state.previewVideoUrl = null;
        render();
      };
    });

    const nextTabBtn = document.getElementById("btn-next-motion-tab");
    if (nextTabBtn) {
      nextTabBtn.onclick = () => {
        state.motionTab = MOTION_TABS[currTabIdx + 1].id;
        state.previewVideoUrl = null;
        render();
      };
    }

    document.getElementById("btn-motion-export").onclick = triggerExport;

    const gSlider = document.getElementById("global-strength-slider");
    gSlider.oninput = () => {
      const val = Number(gSlider.value);
      state.motions.global_strength = val;
      document.getElementById("global-strength-val").textContent = `${val}%`;
    };
    gSlider.onchange = saveMotionsNow;

    outlet.querySelectorAll("[data-strip-seg]").forEach((el) => {
      el.onclick = () => {
        const sid = Number(el.dataset.stripSeg);
        const a = assignments[String(sid)];
        if (a && a.type && getTabIndex(a.type) < getTabIndex(state.motionTab)) {
          state.motionTab = a.type;
        }
        state.motionCurrentSegId = sid;
        state.previewVideoUrl = null;
        render();
      };
    });

    if (currItem) {
      const sidKey = String(currItem.segment_id);
      const goPrev = () => {
        if (currIdx > 0) {
          state.motionCurrentSegId = eligible[currIdx - 1].segment_id;
          state.previewVideoUrl = null;
          render();
        }
      };
      const goNext = () => {
        if (currIdx < eligible.length - 1) {
          state.motionCurrentSegId = eligible[currIdx + 1].segment_id;
          state.previewVideoUrl = null;
          render();
        }
      };

      document.getElementById("btn-prev-img").onclick = goPrev;
      document.getElementById("btn-next-img").onclick = goNext;

      const closePrevBtn = document.getElementById("btn-close-preview");
      if (closePrevBtn) {
        closePrevBtn.onclick = () => {
          state.previewVideoUrl = null;
          render();
        };
      }

      const stageBox = document.getElementById("stage-image-box");
      const dotEl = document.getElementById("focus-dot");

      if (stageBox) {
        let dragging = false;
        let movedDuringDrag = false;

        const setPointFromClient = (clientX, clientY, pushUndo = true) => {
          const rect = stageBox.getBoundingClientRect();
          const nx = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
          const ny = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
          if (pushUndo) {
            state.motionHistory.push({
              sidKey,
              prev: assignments[sidKey] ? { ...assignments[sidKey] } : null,
            });
          }
          const existing = assignments[sidKey] || {};
          assignments[sidKey] = {
            type: state.motionTab,
            x: Number(nx.toFixed(4)),
            y: Number(ny.toFixed(4)),
            strength: existing.strength ?? null,
            ease: existing.ease !== false,
          };
          state.motions.assignments = assignments;
        };

        stageBox.onclick = async (e) => {
          if (e.target === dotEl) return;
          setPointFromClient(e.clientX, e.clientY, true);
          render();
          await saveMotionsNow();
        };

        if (dotEl) {
          dotEl.onmousedown = (e) => {
            e.stopPropagation();
            dragging = true;
            movedDuringDrag = false;
            state.motionHistory.push({
              sidKey,
              prev: assignments[sidKey] ? { ...assignments[sidKey] } : null,
            });
          };
          window.onmousemove = (e) => {
            if (!dragging) return;
            movedDuringDrag = true;
            setPointFromClient(e.clientX, e.clientY, false);
            dotEl.style.left = `${(assignments[sidKey].x * 100).toFixed(2)}%`;
            dotEl.style.top = `${(assignments[sidKey].y * 100).toFixed(2)}%`;
          };
          window.onmouseup = async () => {
            if (!dragging) return;
            dragging = false;
            window.onmousemove = null;
            window.onmouseup = null;
            if (!movedDuringDrag) {
              delete assignments[sidKey];
              render();
            }
            await saveMotionsNow();
          };
        }
      }

      const itemSlider = document.getElementById("item-strength-slider");
      if (itemSlider) {
        itemSlider.oninput = () => {
          const val = Number(itemSlider.value);
          assignments[sidKey].strength = val;
          document.getElementById("item-strength-val").textContent = `${val}%`;
        };
        itemSlider.onchange = saveMotionsNow;
      }

      const easeChk = document.getElementById("item-ease-toggle");
      if (easeChk) {
        easeChk.onchange = async () => {
          assignments[sidKey].ease = easeChk.checked;
          await saveMotionsNow();
        };
      }

      const clearBtn = document.getElementById("btn-clear-dot");
      if (clearBtn) {
        clearBtn.onclick = async () => {
          state.motionHistory.push({ sidKey, prev: { ...assignments[sidKey] } });
          delete assignments[sidKey];
          render();
          await saveMotionsNow();
        };
      }

      const prevBtn = document.getElementById("btn-preview-motion");
      if (prevBtn) {
        prevBtn.onclick = async () => {
          prevBtn.disabled = true;
          prevBtn.textContent = "جاري تجهيز المعاينة...";
          try {
            const res = await apiJson(`/api/projects/${p.id}/motions/preview`, "POST", {
              segment_id: currItem.segment_id,
              motion: assignments[sidKey],
              global_strength: globalStrength,
            });
            state.previewVideoUrl = res.preview_url;
            render();
          } catch (err) {
            toast(err.message, "error");
            prevBtn.disabled = false;
            prevBtn.textContent = "معاينة الحركة";
          }
        };
      }
    }
  }

  window.addEventListener("keydown", async (e) => {
    if (state.view !== "motion" || !state.project) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;

    const eligible = getEligibleImagesForTab(state.motionTab);
    const currIdx = eligible.findIndex((x) => Number(x.segment_id) === Number(state.motionCurrentSegId));
    const currItem = currIdx >= 0 ? eligible[currIdx] : null;
    const assignments = state.motions.assignments || {};

    if (e.key === "ArrowLeft" || e.key === "Enter" || e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (currIdx >= 0 && currIdx < eligible.length - 1) {
        state.motionCurrentSegId = eligible[currIdx + 1].segment_id;
        state.previewVideoUrl = null;
        render();
      }
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (currIdx > 0) {
        state.motionCurrentSegId = eligible[currIdx - 1].segment_id;
        state.previewVideoUrl = null;
        render();
      }
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if (currItem && assignments[String(currItem.segment_id)]) {
        e.preventDefault();
        const sidKey = String(currItem.segment_id);
        state.motionHistory.push({ sidKey, prev: { ...assignments[sidKey] } });
        delete assignments[sidKey];
        render();
        await saveMotionsNow();
      }
    } else if (e.key.toLowerCase() === "z") {
      const last = state.motionHistory.pop();
      if (last) {
        if (last.prev) assignments[last.sidKey] = last.prev;
        else delete assignments[last.sidKey];
        render();
        await saveMotionsNow();
      }
    } else if (["1", "2", "3", "4"].includes(e.key)) {
      state.motionTab = MOTION_TABS[Number(e.key) - 1].id;
      state.previewVideoUrl = null;
      render();
    }
  });

  // ---------- Screen 5: Export & Report ----------
  async function triggerExport() {
    if (!state.project) return;
    await saveMotionsNow();
    await navigate("export");
    try {
      const res = await apiJson(`/api/projects/${state.project.id}/export`, "POST", {});
      state.activeJob = {
        id: res.job_id,
        status: "running",
        progress: 1,
        message: "جاري بدء التصدير...",
        clip_states: {},
      };
      renderLiveBanner();
      render();
      startJobPolling(res.job_id, (job) => {
        state.project.export_report = job.report;
        state.hasOutput = true;
        toast("اكتمل تصدير الفيديو النهائي بنجاح!", "success");
        updateChrome();
        render();
      });
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function renderExport(outlet) {
    const p = state.project;
    const tl = state.timeline;
    const rep = p.export_report;
    const job = state.activeJob;
    const items = tl?.items || [];

    const clipGridHtml = job && job.type === "export"
      ? `<div class="card" style="margin-bottom:20px">
          <h2 class="card-title">حالة تصيير المقاطع (${job.completed || 0} / ${items.length})</h2>
          <div class="clip-progress-grid">
            ${items.map((it) => {
              const st = job.clip_states?.[it.segment_id] || "waiting";
              const lbl = st === "done" ? "✓ مكتمل" : st === "rendering" ? "⏳ جاري" : "انتظار";
              return `<div class="clip-pill ${st}"><span class="tabular">#${it.segment_id}</span><span>${lbl}</span></div>`;
            }).join("")}
          </div>
        </div>`
      : "";

    const reportHtml = rep
      ? `<div style="display:grid;grid-template-columns:1.2fr 1fr;gap:20px;align-items:start">
          <div class="card">
            <h2 class="card-title">الفيديو النهائي جاهز ✓</h2>
            <div class="video-player-box">
              <video controls src="/api/projects/${esc(p.id)}/output/final.mp4?t=${Date.now()}"></video>
            </div>
            <div class="field-hint mono" style="margin-top:10px;word-break:break-all">${esc(rep.output_path)}</div>
            <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap">
              <button class="btn btn-primary" id="btn-open-video">فتح الفيديو</button>
              <button class="btn" id="btn-open-folder">فتح المجلد</button>
              <button class="btn" id="btn-goto-review">مراجعة وتعديل يدوي</button>
            </div>
          </div>

          <div class="card">
            <h2 class="card-title">تقرير التصدير</h2>
            <div style="display:flex;flex-direction:column;gap:10px;font-size:14px">
              <div class="meta-line" style="justify-content:space-between"><span>إجمالي المقاطع</span><strong class="tabular">${rep.total_segments}</strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>مدة الصوت الأصلي</span><strong class="mono">${rep.audio_duration}s</strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>مدة الفيديو النهائي</span><strong class="mono">${rep.video_duration}s</strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>تطابق الطول</span><strong style="color:var(--success-text)">فرق <span class="mono">${rep.duration_diff}s</span></strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>مقاطع من الذاكرة المؤقتة (Cache)</span><strong class="tabular">${rep.cached_clips}</strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>مقاطع مُصيّرة حديثاً</span><strong class="tabular">${rep.rendered_clips}</strong></div>
              <div class="meta-line" style="justify-content:space-between"><span>زمن التصدير</span><strong class="mono">${rep.export_time_seconds}s</strong></div>
            </div>
            ${rep.missing_scenes?.length ? `<div class="alert alert-warning" style="margin-top:14px">مشاهد مفقودة: ${esc(rep.missing_scenes.join("، "))}</div>` : ""}
            ${rep.extreme_speed_videos?.length ? `<div class="alert alert-warning" style="margin-top:10px">فيديوهات بسرعة متطرفة: ${rep.extreme_speed_videos.map((v) => `#${v.segment_id} (${v.speed}x)`).join("، ")}</div>` : ""}
            ${rep.low_confidence_cuts?.length ? `<div class="alert alert-warning" style="margin-top:10px">قصات بدقة منخفضة: ${rep.low_confidence_cuts.map((c) => `#${c.segment_id}`).join("، ")}</div>` : ""}
          </div>
        </div>`
      : `<div class="empty-state">
          <h3>جاهز للتصدير النهائي</h3>
          <p style="color:var(--text-2);margin-bottom:18px">سيتم تصيير كل مشهد بالتوازي مع حفظ الذاكرة المؤقتة ثم دمج الصوت الأصلي دون المساس به.</p>
          <button class="btn btn-primary btn-lg" id="btn-start-export-now">بدء التصدير الآن</button>
        </div>`;

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">5. التصدير والتقرير النهائي</h1>
          <p class="section-subtitle">ترميز موحد H.264 بدون إعادة ترميز عند الدمج، مع ذاكرة مؤقتة ذكية لكل مشهد.</p>
        </div>
        ${rep ? `<button class="btn btn-primary" id="btn-reexport">إعادة التصدير السريع</button>` : ""}
      </div>

      ${tl ? `
        <div class="summary-grid">
          <div class="stat-card"><div class="stat-label">المقاطع</div><div class="stat-value tabular">${items.length}</div></div>
          <div class="stat-card"><div class="stat-label">مدة الصوت</div><div class="stat-value mono">${fmtDur(tl.audio_duration)}</div></div>
          <div class="stat-card"><div class="stat-label">إجمالي الإطارات</div><div class="stat-value tabular">${tl.total_frames}</div></div>
          <div class="stat-card"><div class="stat-label">تحذيرات</div><div class="stat-value tabular">${(tl.missing_scenes?.length || 0) + (tl.extreme_speed_videos?.length || 0)}</div></div>
        </div>
      ` : ""}

      ${clipGridHtml}
      ${reportHtml}
    `;

    const startBtn = document.getElementById("btn-start-export-now");
    if (startBtn) startBtn.onclick = triggerExport;
    const reBtn = document.getElementById("btn-reexport");
    if (reBtn) reBtn.onclick = triggerExport;

    const openVidBtn = document.getElementById("btn-open-video");
    if (openVidBtn) {
      openVidBtn.onclick = async () => {
        const res = await apiJson("/api/system/open-path", "POST", { target: "output_video", project_id: p.id });
        if (res.path) toast(`مسار الفيديو: ${res.path}`, "info");
      };
    }
    const openDirBtn = document.getElementById("btn-open-folder");
    if (openDirBtn) {
      openDirBtn.onclick = async () => {
        const res = await apiJson("/api/system/open-path", "POST", { target: "output_folder", project_id: p.id });
        if (res.path) toast(`مجلد المخرجات: ${res.path}`, "info");
      };
    }
    const gotoRev = document.getElementById("btn-goto-review");
    if (gotoRev) gotoRev.onclick = () => navigate("review");
  }

  // ---------- Screen 6: Post-Result Manual Review ----------
  function renderReview(outlet) {
    const p = state.project;
    const items = state.timeline?.items || [];
    if (!items.length) {
      navigate("export");
      return;
    }

    if (!state.reviewSelectedSegId) state.reviewSelectedSegId = items[0].segment_id;
    const sel = items.find((x) => Number(x.segment_id) === Number(state.reviewSelectedSegId)) || items[0];
    const selIdx = items.indexOf(sel);
    const segMotion = state.motions.assignments?.[String(sel.segment_id)];

    outlet.innerHTML = `
      <div class="section-header">
        <div>
          <h1 class="section-title">6. مراجعة وتعديل سريع بعد التصدير</h1>
          <p class="section-subtitle">عدّل وقت أي مشهد أو بدّل ملفه أو حركته ثم اضغط «تحديث الفيديو» لإعادة تصيير المشاهد المعدّلة فقط.</p>
        </div>
        <button class="btn btn-primary btn-lg" id="btn-review-rebuild">تحديث الفيديو</button>
      </div>

      <div class="review-grid">
        <div>
          <div class="video-player-box" style="margin-bottom:14px">
            <video id="review-video" controls src="/api/projects/${esc(p.id)}/output/final.mp4?t=${Date.now()}"></video>
          </div>
          <div class="card" style="padding:14px">
            <div class="field-label">الخط الزمني الكامل (انقر على أي مقطع لتعديله)</div>
            <div class="live-timeline-strip" style="margin-top:8px">
              ${items.map((it) => {
                const isSel = Number(it.segment_id) === Number(sel.segment_id);
                return `<div class="live-block ${isSel ? "rendering" : ""}" style="cursor:pointer" data-rev-seg="${it.segment_id}">
                  ${it.thumbnail_url ? `<img src="${esc(it.thumbnail_url)}" alt="" />` : ""}
                  <span class="tabular">#${it.segment_id}</span>
                  <span class="mono" style="font-size:10px;color:var(--text-2)">${Number(it.duration).toFixed(1)}s</span>
                </div>`;
              }).join("")}
            </div>
          </div>
        </div>

        <div class="card">
          <h2 class="card-title">تعديل المشهد #${sel.segment_id}</h2>
          <div class="field">
            <div class="field-label">توقيت البداية (ثانية)</div>
            <div class="time-edit-group">
              <button class="btn btn-sm" id="rev-nudge-minus" ${selIdx === 0 ? "disabled" : ""}>-0.1s</button>
              <input class="time-input" type="number" step="0.1" id="rev-start-input" value="${Number(sel.start).toFixed(2)}" ${selIdx === 0 ? "disabled" : ""} />
              <button class="btn btn-sm" id="rev-nudge-plus" ${selIdx === 0 ? "disabled" : ""}>+0.1s</button>
              <span class="mono" style="font-size:12px;color:var(--text-2)">المدة: ${Number(sel.duration).toFixed(2)}s</span>
            </div>
          </div>

          <div class="field">
            <div class="field-label">ملف المشهد الحالي: <span class="ltr-name">${esc(sel.scene_filename || "غير محدد")}</span></div>
            <label class="btn btn-sm" style="cursor:pointer">
              استبدال ملف المشهد
              <input type="file" id="rev-replace-file" accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.mkv,.webm" style="display:none" />
            </label>
          </div>

          ${sel.scene_type === "image" ? `
            <div class="field">
              <div class="field-label">حركة الصورة</div>
              <div class="meta-line" style="margin-bottom:8px">
                <span>الحالة: <strong>${segMotion ? segMotion.type : "ثابتة (بدون حركة)"}</strong></span>
              </div>
              <button class="btn btn-sm" id="rev-edit-motion">تعديل / مسح الحركة بالنقطة الزرقاء</button>
            </div>
          ` : `
            <div class="field">
              <div class="field-label">سلاسة الفيديو البطيء (${Number(sel.speed).toFixed(2)}x)</div>
              <select class="select" id="rev-slowmo-select">
                <option value="off" ${(sel.slowmo_smooth || p.settings.slowmo_smooth) === "off" ? "selected" : ""}>إيقاف (الأسرع)</option>
                <option value="blend" ${(sel.slowmo_smooth || p.settings.slowmo_smooth) === "blend" ? "selected" : ""}>مزج الإطارات (Blend)</option>
                <option value="mci" ${(sel.slowmo_smooth || p.settings.slowmo_smooth) === "mci" ? "selected" : ""}>تعويض الحركة (MCI - عالي الجودة)</option>
              </select>
            </div>
          `}
        </div>
      </div>
    `;

    outlet.querySelectorAll("[data-rev-seg]").forEach((el) => {
      el.onclick = () => {
        state.reviewSelectedSegId = Number(el.dataset.revSeg);
        const vid = document.getElementById("review-video");
        const target = items.find((x) => Number(x.segment_id) === state.reviewSelectedSegId);
        if (vid && target) vid.currentTime = Number(target.start);
        render();
      };
    });

    const updateRevStart = async (val) => {
      const res = await apiJson(`/api/projects/${p.id}/cuts/${sel.segment_id}/start`, "POST", { start: val });
      state.cuts = res.cuts;
      state.timeline = res.timeline;
      markSaved();
      render();
    };
    document.getElementById("rev-nudge-minus").onclick = () => updateRevStart(Number(sel.start) - 0.1);
    document.getElementById("rev-nudge-plus").onclick = () => updateRevStart(Number(sel.start) + 0.1);
    document.getElementById("rev-start-input").onchange = (e) => updateRevStart(Number(e.target.value));

    document.getElementById("rev-replace-file").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      const res = await api(`/api/projects/${p.id}/replace-scene/${sel.segment_id}`, { method: "POST", body: fd });
      state.project = res.project;
      state.timeline = res.timeline;
      markSaved();
      toast("تم استبدال المشهد. اضغط «تحديث الفيديو» للتصيير السريع.", "success");
      render();
    };

    const editMotBtn = document.getElementById("rev-edit-motion");
    if (editMotBtn) {
      editMotBtn.onclick = () => {
        if (segMotion?.type) state.motionTab = segMotion.type;
        state.motionCurrentSegId = sel.segment_id;
        navigate("motion");
      };
    }

    const slowmoSel = document.getElementById("rev-slowmo-select");
    if (slowmoSel) {
      slowmoSel.onchange = async () => {
        const overrides = { ...(p.overrides || {}) };
        overrides[String(sel.segment_id)] = { ...(overrides[String(sel.segment_id)] || {}), slowmo_smooth: slowmoSel.value };
        const res = await apiJson(`/api/projects/${p.id}`, "PATCH", { overrides });
        state.project = res.project;
        sel.slowmo_smooth = slowmoSel.value;
        markSaved();
      };
    }

    document.getElementById("btn-review-rebuild").onclick = triggerExport;
  }

  // ---------- Settings Modal (Zero Whisper settings, includes 3 Themes) ----------
  function openSettingsModal() {
    const s = state.project?.settings || state.globalSettings?.defaults || {
      resolution: "1920x1080",
      fps: 30,
      background_fill: "blurred",
      slowmo_smooth: "off",
      default_motion_strength: 15,
      theme: state.theme,
    };
    const currTheme = normalizeTheme(s.theme || state.theme);

    openModal(`
      <div class="modal-header">
        <h3 style="margin:0;font-size:18px">إعدادات المشروع والمظهر</h3>
        <button class="btn btn-sm btn-ghost" id="set-close">✕</button>
      </div>
      <div class="field">
        <label class="field-label">المظهر العام (Themes)</label>
        <select class="select" id="set-theme">
          <option value="blue_night" ${currTheme === "blue_night" ? "selected" : ""}>ليل أزرق (الافتراضي)</option>
          <option value="calm_forest" ${currTheme === "calm_forest" ? "selected" : ""}>غابة هادية</option>
          <option value="daylight" ${currTheme === "daylight" ? "selected" : ""}>نهار (فاتح)</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">الدقة (16:9)</label>
        <select class="select" id="set-res">
          <option value="1920x1080" ${s.resolution === "1920x1080" ? "selected" : ""}>1920x1080 (Full HD)</option>
          <option value="1280x720" ${s.resolution === "1280x720" ? "selected" : ""}>1280x720 (HD سريع)</option>
          <option value="2560x1440" ${s.resolution === "2560x1440" ? "selected" : ""}>2560x1440 (2K)</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">معدل الإطارات (FPS)</label>
        <select class="select" id="set-fps">
          <option value="30" ${Number(s.fps) === 30 ? "selected" : ""}>30 fps (الافتراضي)</option>
          <option value="24" ${Number(s.fps) === 24 ? "selected" : ""}>24 fps</option>
          <option value="60" ${Number(s.fps) === 60 ? "selected" : ""}>60 fps</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">قوة الحركة الافتراضية للصور (%)</label>
        <input class="input tabular" type="number" min="5" max="40" id="set-motion-strength" value="${Number(s.default_motion_strength ?? 15)}" />
      </div>
      <div class="field">
        <label class="field-label">ملء الخلفية عند اختلاف الأبعاد</label>
        <select class="select" id="set-bg">
          <option value="blurred" ${s.background_fill === "blurred" ? "selected" : ""}>نفس الصورة مموهة (Blurred)</option>
          <option value="black" ${s.background_fill === "black" ? "selected" : ""}>خلفية سوداء</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label">سلاسة البطيء (تحت 0.7x)</label>
        <select class="select" id="set-slowmo">
          <option value="off" ${s.slowmo_smooth === "off" ? "selected" : ""}>إيقاف (الافتراضي — تصدير سريع)</option>
          <option value="blend" ${s.slowmo_smooth === "blend" ? "selected" : ""}>Blend (سلس وسريع)</option>
          <option value="mci" ${s.slowmo_smooth === "mci" ? "selected" : ""}>MCI (جودة عالية جداً — بطيء على CPU)</option>
        </select>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px">
        <button class="btn" id="set-cancel">إلغاء</button>
        <button class="btn btn-primary" id="set-save">حفظ الإعدادات</button>
      </div>
    `);

    document.getElementById("set-close").onclick = closeModal;
    document.getElementById("set-cancel").onclick = closeModal;
    document.getElementById("set-theme").onchange = (e) => applyTheme(e.target.value, false);
    document.getElementById("set-save").onclick = async () => {
      const nextTheme = normalizeTheme(document.getElementById("set-theme").value);
      const nextSettings = {
        theme: nextTheme,
        resolution: document.getElementById("set-res").value,
        fps: Number(document.getElementById("set-fps").value),
        default_motion_strength: Math.max(5, Math.min(40, Number(document.getElementById("set-motion-strength").value) || 15)),
        background_fill: document.getElementById("set-bg").value,
        slowmo_smooth: document.getElementById("set-slowmo").value,
      };
      await applyTheme(nextTheme, true);
      if (state.project) {
        const res = await apiJson(`/api/projects/${state.project.id}`, "PATCH", { settings: nextSettings });
        state.project = res.project;
        markSaved();
        render();
      }
      closeModal();
      toast("تم حفظ الإعدادات.", "success");
    };
  }

  // ---------- Problem Report, Startup Check & Updates Modal ----------
  async function copyProblemReport() {
    try {
      const res = await api("/api/system/problem-report");
      const text = res.report || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        toast("تم نسخ تقرير المشكلة إلى الحافظة (بدون أي رموز سرية).", "success");
      } else {
        openModal(`
          <div class="modal-header">
            <h3 style="margin:0;font-size:18px">تقرير المشكلة</h3>
            <button class="btn btn-sm btn-ghost" id="rep-close">✕</button>
          </div>
          <textarea class="textarea mono" style="min-height:220px;direction:ltr;text-align:left" readonly>${esc(text)}</textarea>
        `, true);
        document.getElementById("rep-close").onclick = closeModal;
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function checkUpdatesOnStartup() {
    try {
      const gs = await fetch("/api/settings/global").then((r) => r.json());
      if (gs?.ok) {
        state.globalSettings = gs;
        if (gs.defaults?.theme && !localStorage.getItem("autocut_theme_explicit")) {
          applyTheme(gs.defaults.theme, false);
        }
      }
      const src = gs?.update_source || {};
      if (src.check_on_startup === false || !src.repo) return;

      const chk = await fetch("/api/updates/check").then((r) => r.json());
      if (chk?.ok && chk.update_available && chk.remote_version) {
        const badge = document.getElementById("btn-update-badge");
        if (badge) {
          badge.textContent = `تحديث متاح — ${chk.remote_version}`;
          badge.classList.remove("hidden");
        }
      }
    } catch {
      // ignore offline
    }
  }

  function waitForServerRestartAndReload(messageEl) {
    if (messageEl) {
      messageEl.textContent = "جاري تطبيق التحديث وإعادة تشغيل AutoCut...";
    }
    const started = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - started > 90000) {
        clearInterval(timer);
        if (messageEl) {
          messageEl.textContent = "استغرقت إعادة التشغيل وقتاً أطول من المعتاد — حدّث الصفحة يدوياً.";
        }
        return;
      }
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (res.ok) {
          clearInterval(timer);
          window.location.reload();
        }
      } catch {
        // restarting
      }
    }, 900);
  }

  function fmtBytesMb(bytes) {
    const b = Number(bytes) || 0;
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / (1024 * 1024)).toFixed(2)} MB`;
  }

  async function refreshUpdateModalData() {
    try {
      const [gs, bk] = await Promise.all([
        api("/api/settings/global"),
        api("/api/updates/backups"),
      ]);
      state.globalSettings = gs;
      state.backups = bk.backups || [];
    } catch {
      // ignore
    }
  }

  async function openUpdatesModal(initialTab) {
    if (initialTab) state.updateModalTab = initialTab;
    await refreshUpdateModalData();
    renderUpdatesModal();
  }

  function renderUpdatesModal() {
    const tab = state.updateModalTab || "updates";
    const curVer = state.health?.version || state.globalSettings?.version || "1.2.0";
    const src = state.globalSettings?.update_source || {
      repo: "",
      branch: "main",
      subfolder: "auto",
      has_token: false,
      token_masked: "",
      check_on_startup: true,
    };

    let tabBody = "";
    if (tab === "updates") {
      const plan = state.updatePlan;
      const job = state.updateJob;
      let planHtml = "";

      if (job) {
        const pct = Math.min(100, Math.round(job.progress || 0));
        planHtml = `
          <div class="update-summary-box">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px">
              <strong id="upd-job-msg">${esc(job.message || "جاري تنزيل التحديث...")}</strong>
              <span class="mono tabular">${pct}%</span>
            </div>
            <div class="progress-track">
              <div class="progress-fill" style="width:${pct}%"></div>
            </div>
            <div class="meta-line" style="margin-top:10px;justify-content:space-between">
              <span class="mono">${fmtBytesMb(job.bytes_downloaded)} / ${fmtBytesMb(job.total_bytes)}</span>
              <span class="mono">${Number(job.speed_mb_s || 0).toFixed(2)} MB/s</span>
            </div>
            ${!job.restarting && job.status === "running" ? `
              <div style="margin-top:12px;display:flex;justify-content:flex-end">
                <button class="btn btn-sm btn-danger" id="upd-cancel-dl-btn">إلغاء التنزيل</button>
              </div>
            ` : ""}
          </div>
        `;
      } else if (state.updatePlanError) {
        planHtml = `<div class="alert alert-error" style="margin-top:14px"><div>${esc(state.updatePlanError)}</div></div>`;
      } else if (plan) {
        const hasChanges = (plan.change_count || 0) > 0 || plan.update_available;
        planHtml = `
          <div class="update-summary-box">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
              <div>
                <strong style="font-size:15px">الإصدار الحالي <span class="mono">${esc(plan.local_version)}</span> ← الجديد <span class="mono">${esc(plan.remote_version)}</span></strong>
                <div class="field-hint">
                  المجلد المكتشف: <span class="mono">${esc(plan.subfolder || "(root)")}</span>
                  ${plan.commit_date ? ` · آخر تعديل: <span class="mono">${esc(String(plan.commit_date).slice(0, 10))}</span>` : ""}
                </div>
              </div>
              <span class="conf-badge ${hasChanges ? "conf-mid" : "conf-high"} tabular">
                ${hasChanges ? `حجم التنزيل: ${fmtBytesMb(plan.total_bytes)}` : "محدّث بالكامل ✓"}
              </span>
            </div>

            <div class="meta-line" style="margin-top:10px">
              <span>ملفات للتنزيل: <strong class="tabular">${plan.to_download?.length || 0}</strong></span>
              <span>·</span>
              <span>ملفات محذوفة: <strong class="tabular">${plan.to_delete?.length || 0}</strong></span>
              <span>·</span>
              <span>بدون تغيير (تم تخطيها): <strong class="tabular">${plan.unchanged_count || 0}</strong></span>
            </div>

            ${plan.changelog?.length ? `
              <div style="margin-top:12px">
                <div class="field-label" style="margin-bottom:4px">ملخص التغييرات (CHANGELOG):</div>
                <ul class="update-list">${plan.changelog.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
              </div>
            ` : ""}

            ${plan.commits?.length ? `
              <div style="margin-top:12px">
                <div class="field-label" style="margin-bottom:4px">أحدث التعديلات (Commits):</div>
                <ul class="update-list">${plan.commits.slice(0, 5).map((c) => `<li><span class="ltr-name">${esc(c)}</span></li>`).join("")}</ul>
              </div>
            ` : ""}

            ${hasChanges ? `
              <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:16px">
                <button class="btn" id="upd-cancel-plan-btn">إلغاء</button>
                <button class="btn btn-primary" id="upd-apply-btn">تحديث الآن</button>
              </div>
            ` : ""}
          </div>
        `;
      }

      const backupsHtml = state.backups.length
        ? state.backups.map((b) => `
            <div class="backup-row">
              <div>
                <strong>الإصدار <span class="mono">${esc(b.old_version)}</span></strong>
                <div class="field-hint" style="margin-top:2px">
                  <span class="mono">${esc(String(b.created_at || "").slice(0, 19).replace("T", " "))}</span>
                  · <span class="tabular">${b.file_count || 0} ملف محفوظ</span>
                </div>
              </div>
              <button class="btn btn-sm" data-rollback-id="${esc(b.id)}">التراجع للإصدار السابق</button>
            </div>
          `).join("")
        : `<div class="field-hint">لا توجد نسخ احتياطية سابقة بعد (يتم إنشاء نسخة تلقائياً قبل أي تحديث).</div>`;

      tabBody = `
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div>
            <div style="font-weight:700;font-size:15px">الإصدار المثبت حالياً: <span class="mono">${esc(curVer)}</span></div>
            <div class="field-hint">
              ${src.repo ? `المصدر: <span class="mono">${esc(src.repo)}</span> (${esc(src.branch)})` : "لم يتم إعداد مستودع GitHub بعد — انتقل إلى تبويب «المصدر»."}
            </div>
          </div>
          <button class="btn btn-primary" id="upd-check-btn" ${job ? "disabled" : ""}>فحص التحديثات</button>
        </div>

        ${planHtml}

        <div style="margin-top:22px;padding-top:16px;border-top:1px solid var(--border)">
          <div class="field-label">النسخ الاحتياطية والاسترجاع (آخر 5 إصدارات)</div>
          ${backupsHtml}
        </div>
      `;
    } else if (tab === "source") {
      const testRes = state.updateTestResult;
      const testHtml = testRes
        ? (testRes.ok
            ? `<div class="alert alert-success" style="margin-top:12px"><div>
                <strong>تم الاتصال بالمستودع بنجاح ✓</strong>
                <div class="field-hint" style="color:inherit">
                  الإصدار البعيد: <span class="mono">${esc(testRes.remote_version)}</span>
                  · المجلد: <span class="mono">${esc(testRes.subfolder)}</span>
                  · التاريخ: <span class="mono">${esc(String(testRes.commit_date || "").slice(0, 10))}</span>
                </div>
              </div></div>`
            : `<div class="alert alert-error" style="margin-top:12px"><div>${esc(testRes.error || "فشل الاتصال بالمستودع.")}</div></div>`)
        : "";

      tabBody = `
        <div class="field">
          <label class="field-label" for="src-repo">مستودع GitHub (owner/repo)</label>
          <input class="input mono" style="direction:ltr;text-align:left" id="src-repo" value="${esc(src.repo || "")}" placeholder="username/autocut" />
          <div class="field-hint">مثال: <span class="mono">user/autocut</span> (يعمل مع المستودعات العامة والخاصة).</div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="field">
            <label class="field-label" for="src-branch">الفرع (Branch)</label>
            <input class="input mono" style="direction:ltr;text-align:left" id="src-branch" value="${esc(src.branch || "main")}" placeholder="main" />
          </div>
          <div class="field">
            <label class="field-label" for="src-subfolder">المجلد الفرعي (Subfolder)</label>
            <input class="input mono" style="direction:ltr;text-align:left" id="src-subfolder" value="${esc(src.subfolder || "auto")}" placeholder="auto" />
          </div>
        </div>
        <div class="field">
          <label class="field-label" for="src-token">رمز الوصول اختياري للمستودعات الخاصة (Fine-grained Token — Contents: Read-only)</label>
          <div style="display:flex;gap:8px">
            <input class="input mono" type="password" style="direction:ltr;text-align:left" id="src-token" value="" placeholder="${src.has_token ? `محفوظ (${src.token_masked}) — اتركه فارغاً للإبقاء عليه` : "اختياري للمستودع الخاص..."}" autocomplete="off" />
            ${src.has_token ? `<button class="btn btn-sm btn-danger" id="src-clear-token">مسح الرمز</button>` : ""}
          </div>
          <div class="field-hint">يُحفظ محلياً في <span class="mono">settings.json</span> فقط ولا يظهر في الواجهة أو السجلات.</div>
        </div>
        <div class="field">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:14px">
            <input type="checkbox" id="src-startup-chk" ${src.check_on_startup !== false ? "checked" : ""} />
            <span>فحص التحديثات تلقائياً عند فتح البرنامج (دون تنزيل تلقائي)</span>
          </label>
        </div>

        ${testHtml}

        <div style="display:flex;justify-content:space-between;gap:10px;margin-top:16px;flex-wrap:wrap">
          <button class="btn" id="src-test-btn">اختبار الاتصال</button>
          <button class="btn btn-primary" id="src-save-btn">حفظ إعدادات المصدر</button>
        </div>
      `;
    } else {
      tabBody = `
        <p class="field-hint" style="margin-top:0;font-size:13px">
          خيار بديل بالكامل بدون إنترنت: حدد حزمة التحديث المحلية (<span class="mono">.zip</span> أو مجلد مفكوك) مع الاحتفاظ التام بجميع مشاريعك وإعداداتك.
        </p>
        <div class="field">
          <label class="field-label">1) التحديث من ملف مضغوط (.zip)</label>
          <div class="dropzone" id="local-zip-dropzone">
            <input type="file" id="local-zip-input" accept=".zip" />
            <div style="font-weight:600">اختر ملف <span class="mono">.zip</span> للتحديث أو اسحبه هنا</div>
            <div class="field-hint">يتم مقارنة البصمات وتطبيق التغييرات مع نسخة احتياطية تلقائية.</div>
          </div>
        </div>
        <div class="field" style="margin-top:18px">
          <label class="field-label" for="local-folder-path">2) أو من مسار مجلد محلي</label>
          <div style="display:flex;gap:8px">
            <input class="input mono" style="direction:ltr;text-align:left" id="local-folder-path" placeholder="C:\\Downloads\\autocut-update" />
            <button class="btn btn-primary" id="local-folder-apply-btn">تطبيق التحديث</button>
          </div>
        </div>
        <div id="local-upd-status" class="field-hint"></div>
      `;
    }

    openModal(`
      <div class="modal-header">
        <h3 style="margin:0;font-size:18px">التحديثات وإدارة الإصدار</h3>
        <button class="btn btn-sm btn-ghost" id="upd-close">✕</button>
      </div>

      <div class="segmented-tabs" style="margin-bottom:18px">
        <button class="seg-tab ${tab === "updates" ? "active" : ""}" data-utab="updates">التحديثات</button>
        <button class="seg-tab ${tab === "source" ? "active" : ""}" data-utab="source">المصدر</button>
        <button class="seg-tab ${tab === "local" ? "active" : ""}" data-utab="local">تحديث من ملف</button>
      </div>

      <div id="upd-tab-content">${tabBody}</div>

      <div style="margin-top:18px;padding-top:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px">
        <button class="btn btn-sm btn-ghost" id="upd-copy-report-btn">نسخ تقرير المشكلة</button>
        <button class="btn btn-sm" id="upd-done-btn">إغلاق</button>
      </div>
    `, true);

    document.getElementById("upd-close").onclick = closeModal;
    document.getElementById("upd-done-btn").onclick = closeModal;
    document.getElementById("upd-copy-report-btn").onclick = copyProblemReport;

    document.querySelectorAll("[data-utab]").forEach((btn) => {
      btn.onclick = () => {
        state.updateModalTab = btn.dataset.utab;
        renderUpdatesModal();
      };
    });

    if (tab === "updates") {
      const checkBtn = document.getElementById("upd-check-btn");
      if (checkBtn) {
        checkBtn.onclick = async () => {
          checkBtn.disabled = true;
          checkBtn.textContent = "جاري الفحص...";
          state.updatePlanError = null;
          try {
            const plan = await apiJson("/api/updates/plan", "POST", {});
            state.updatePlan = plan;
            renderUpdatesModal();
          } catch (err) {
            state.updatePlanError = err.message;
            renderUpdatesModal();
          }
        };
      }

      const cancelPlanBtn = document.getElementById("upd-cancel-plan-btn");
      if (cancelPlanBtn) {
        cancelPlanBtn.onclick = () => {
          state.updatePlan = null;
          renderUpdatesModal();
        };
      }

      const applyBtn = document.getElementById("upd-apply-btn");
      if (applyBtn) {
        applyBtn.onclick = async () => {
          applyBtn.disabled = true;
          try {
            const res = await apiJson("/api/updates/apply", "POST", {});
            if (res.already_latest) {
              toast("البرنامج محدّث بالفعل لأحدث إصدار.", "success");
              state.updatePlan = null;
              renderUpdatesModal();
              return;
            }
            state.updateJob = {
              id: res.job_id,
              status: "running",
              progress: 1,
              bytes_downloaded: 0,
              total_bytes: res.plan?.total_bytes || 0,
              speed_mb_s: 0,
              message: "جاري بدء تنزيل الملفات...",
            };
            renderUpdatesModal();
            if (state.updatePollTimer) clearInterval(state.updatePollTimer);
            state.updatePollTimer = setInterval(async () => {
              try {
                const jr = await api(`/api/jobs/${res.job_id}`);
                state.updateJob = jr.job;
                renderUpdatesModal();
                if (jr.job.status === "done" || jr.job.restarting) {
                  clearInterval(state.updatePollTimer);
                  state.updatePollTimer = null;
                  waitForServerRestartAndReload(document.getElementById("upd-job-msg"));
                } else if (jr.job.status === "error" || jr.job.status === "cancelled") {
                  clearInterval(state.updatePollTimer);
                  state.updatePollTimer = null;
                  toast(jr.job.error || "توقفت عملية التحديث.", "error");
                  state.updateJob = null;
                  renderUpdatesModal();
                }
              } catch {
                clearInterval(state.updatePollTimer);
                state.updatePollTimer = null;
                waitForServerRestartAndReload(document.getElementById("upd-job-msg"));
              }
            }, 450);
          } catch (err) {
            toast(err.message, "error");
            applyBtn.disabled = false;
          }
        };
      }

      const cancelDlBtn = document.getElementById("upd-cancel-dl-btn");
      if (cancelDlBtn && state.updateJob?.id) {
        cancelDlBtn.onclick = async () => {
          await apiJson(`/api/jobs/${state.updateJob.id}/cancel`, "POST", {});
        };
      }

      document.querySelectorAll("[data-rollback-id]").forEach((btn) => {
        btn.onclick = () => {
          const bid = btn.dataset.rollbackId;
          confirmDialog(
            "التراجع للإصدار السابق",
            "هل تريد استعادة ملفات هذا الإصدار السابق مع الحفاظ التام على جميع مشاريعك؟",
            async () => {
              try {
                await apiJson("/api/updates/rollback", "POST", { backup_id: bid });
                openModal(`
                  <div class="modal-header"><h3 style="margin:0">جاري التراجع...</h3></div>
                  <p id="rb-wait-msg" style="color:var(--text-2)">جاري استعادة النسخة الاحتياطية وإعادة تشغيل AutoCut...</p>
                `);
                waitForServerRestartAndReload(document.getElementById("rb-wait-msg"));
              } catch (err) {
                toast(err.message, "error");
              }
            }
          );
        };
      });
    } else if (tab === "source") {
      const readSourcePayload = (clearToken = false) => {
        const tokInput = document.getElementById("src-token").value.trim();
        let tokenVal = "__KEEP__";
        if (clearToken) tokenVal = "";
        else if (tokInput) tokenVal = tokInput;
        else if (!src.has_token) tokenVal = "";
        return {
          repo: document.getElementById("src-repo").value.trim(),
          branch: document.getElementById("src-branch").value.trim() || "main",
          subfolder: document.getElementById("src-subfolder").value.trim() || "auto",
          token: tokenVal,
          check_on_startup: document.getElementById("src-startup-chk").checked,
        };
      };

      const clearTokBtn = document.getElementById("src-clear-token");
      if (clearTokBtn) {
        clearTokBtn.onclick = async () => {
          const res = await apiJson("/api/settings/global", "PUT", { update_source: readSourcePayload(true) });
          state.globalSettings = res;
          toast("تم مسح رمز الوصول.", "info");
          renderUpdatesModal();
        };
      }

      document.getElementById("src-test-btn").onclick = async () => {
        const btn = document.getElementById("src-test-btn");
        btn.disabled = true;
        btn.textContent = "جاري الاختبار...";
        try {
          const res = await apiJson("/api/updates/test-connection", "POST", readSourcePayload(false));
          state.updateTestResult = res;
        } catch (err) {
          state.updateTestResult = { ok: false, error: err.message };
        }
        renderUpdatesModal();
      };

      document.getElementById("src-save-btn").onclick = async () => {
        try {
          const res = await apiJson("/api/settings/global", "PUT", { update_source: readSourcePayload(false) });
          state.globalSettings = res;
          state.updateTestResult = null;
          toast("تم حفظ إعدادات مصدر التحديث.", "success");
          renderUpdatesModal();
        } catch (err) {
          toast(err.message, "error");
        }
      };
    } else if (tab === "local") {
      const statusEl = document.getElementById("local-upd-status");
      const zipInput = document.getElementById("local-zip-input");
      zipInput.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file);
        statusEl.textContent = "جاري تجهيز الحزمة وفحص البصمات...";
        try {
          await api("/api/updates/local-zip", { method: "POST", body: fd });
          waitForServerRestartAndReload(statusEl);
        } catch (err) {
          statusEl.textContent = "";
          toast(err.message, "error");
        }
      };

      document.getElementById("local-folder-apply-btn").onclick = async () => {
        const path = document.getElementById("local-folder-path").value.trim();
        if (!path) {
          toast("يرجى إدخال مسار المجلد أولاً.", "warning");
          return;
        }
        statusEl.textContent = "جاري تجهيز الملفات من المجلد...";
        try {
          await apiJson("/api/updates/local-folder", "POST", { path });
          waitForServerRestartAndReload(statusEl);
        } catch (err) {
          statusEl.textContent = "";
          toast(err.message, "error");
        }
      };
    }
  }

  // ---------- Main Render Dispatcher ----------
  function render() {
    const outlet = document.getElementById("screen-outlet");
    if (!outlet) return;
    if (state.view === "home" || !state.project) renderHome(outlet);
    else if (state.view === "inputs") renderInputs(outlet);
    else if (state.view === "cuts") renderCuts(outlet);
    else if (state.view === "scenes") renderScenes(outlet);
    else if (state.view === "motion") renderMotion(outlet);
    else if (state.view === "export") renderExport(outlet);
    else if (state.view === "review") renderReview(outlet);
  }

  // ---------- Boot ----------
  document.addEventListener("DOMContentLoaded", async () => {
    applyTheme(localStorage.getItem("autocut_theme") || "blue_night", false);

    document.querySelectorAll("[data-theme-pick]").forEach((btn) => {
      btn.onclick = () => {
        localStorage.setItem("autocut_theme_explicit", "1");
        applyTheme(btn.dataset.themePick, true);
      };
    });

    document.getElementById("btn-go-home").onclick = () => {
      state.project = null;
      state.transcript = null;
      state.view = "home";
      updateChrome();
      loadProjects();
    };

    document.getElementById("btn-open-settings").onclick = openSettingsModal;
    document.getElementById("btn-open-updates").onclick = () => openUpdatesModal("updates");
    document.getElementById("btn-update-badge").onclick = () => openUpdatesModal("updates");
    document.getElementById("btn-copy-report").onclick = copyProblemReport;

    const dismissUpdBtn = document.getElementById("btn-dismiss-update-result");
    if (dismissUpdBtn) {
      dismissUpdBtn.onclick = () => document.getElementById("update-result-banner")?.classList.add("hidden");
    }

    const openLog = async () => {
      try {
        const res = await apiJson("/api/system/open-path", "POST", { target: "log" });
        if (res.path) toast(`ملف السجل: ${res.path}`, "info");
      } catch (err) {
        toast(err.message, "error");
      }
    };
    document.getElementById("btn-open-log").onclick = openLog;
    document.getElementById("btn-banner-log").onclick = openLog;

    document.getElementById("btn-cancel-job").onclick = async () => {
      if (state.activeJob?.id) {
        await apiJson(`/api/jobs/${state.activeJob.id}/cancel`, "POST", {});
      }
    };

    document.querySelectorAll(".stepper-list .step-btn").forEach((btn) => {
      btn.onclick = () => {
        if (state.project) navigate(btn.dataset.stage);
      };
    });

    await loadHealth();
    await loadProjects();
    checkUpdatesOnStartup();
  });
})();
