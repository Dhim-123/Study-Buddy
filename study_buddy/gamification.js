/**
 * Study Buddy — Gamification UI (XP, streaks, puzzle, shop, planner, focus).
 * Additive layer; safe no-ops when logged out.
 */
(function () {
  "use strict";

  const FOCUS_KEY = "sb_focus_timer_v1";
  const FOCUS_AWARD_KEY = "sb_focus_awarded_session";
  const XP_DEBOUNCE_MS = 800;

  let summary = null;
  let summaryInflight = null;
  let summaryGen = 0; // ignore stale responses after logout / account switch
  let puzzleInflight = null;
  let lastPuzzle = null;
  const actionTimers = {};
  let focusElapsedAwarded = false;

  function localDate() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function isLoggedIn() {
    try {
      return !!(window.currentUser && window.currentUser.loggedIn);
    } catch (_) {
      return false;
    }
  }

  function toast(msg, type) {
    let host = document.getElementById("sb-game-toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "sb-game-toasts";
      host.className = "sb-game-toasts";
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = "sb-game-toast" + (type === "success" ? " success" : type === "warn" ? " warn" : "");
    el.setAttribute("role", "status");
    el.textContent = msg;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 300);
    }, 3200);
  }

  async function api(path, opts) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
      ...opts,
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_) {}
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  async function loadSummary(force) {
    if (!isLoggedIn()) {
      summary = null;
      renderNavbarGuest();
      return null;
    }
    if (summaryInflight && !force) return summaryInflight;
    const gen = ++summaryGen;
    if (force) {
      summary = null;
      renderNavbarGuest();
    }
    summaryInflight = api(`/api/gamification/summary?localDate=${encodeURIComponent(localDate())}`)
      .then((data) => {
        if (gen !== summaryGen || !isLoggedIn()) return null;
        summary = data;
        if (data.reconcile && data.reconcile.protected) {
          toast("🧊 Streak Protected — freeze used!", "success");
        }
        renderNavbar();
        applyPrefsToDom(data.prefs);
        applyUnlocks(data.inventory || {});
        syncDnaStreakDisplay(data.currentStreak);
        return data;
      })
      .catch(() => {
        if (gen === summaryGen) renderNavbarGuest();
        return null;
      })
      .finally(() => {
        if (gen === summaryGen) summaryInflight = null;
      });
    return summaryInflight;
  }

  function syncDnaStreakDisplay(n) {
    document.querySelectorAll("[data-dna-streak], .dna-streak-value, #dna-study-streak").forEach((el) => {
      if (el) el.textContent = String(n ?? 0);
    });
  }

  function applyPrefsToDom(prefs) {
    if (!prefs) return;
    document.documentElement.style.setProperty("--sb-font-scale", String(prefs.fontScale || 1));
    document.documentElement.classList.toggle("sb-high-contrast", !!prefs.highContrast);
    document.documentElement.classList.toggle("sb-reduced-motion", !!prefs.reducedMotion);
    const gradeEl = document.getElementById("settings-grade");
    if (gradeEl) gradeEl.value = String(prefs.grade || 9);
    const langEl = document.getElementById("settings-language");
    if (langEl) {
      langEl.value = prefs.language || "en";
      try { localStorage.setItem("sb_reply_language", langEl.value); } catch (_) {}
    }
    const ns = document.getElementById("settings-notify-streak");
    if (ns) ns.checked = !!prefs.notifyStreak;
    const np = document.getElementById("settings-notify-puzzle");
    if (np) np.checked = !!prefs.notifyPuzzle;
    const hc = document.getElementById("settings-high-contrast");
    if (hc) hc.checked = !!prefs.highContrast;
    const rm = document.getElementById("settings-reduced-motion");
    if (rm) rm.checked = !!prefs.reducedMotion;
    const fs = document.getElementById("settings-font-scale");
    if (fs) fs.value = String(prefs.fontScale || 1);
  }

  function applyUnlocks(inv) {
    document.documentElement.classList.toggle("unlock-theme-aurora", !!inv.theme_aurora);
    document.documentElement.classList.toggle("unlock-theme-forest", !!inv.theme_forest);
    document.documentElement.classList.toggle("unlock-chat-sparkle", !!inv.chat_sparkle);
    document.documentElement.classList.toggle("unlock-voice-premium", !!inv.voice_premium);
  }

  function renderNavbarGuest() {
    const streakN = document.getElementById("nav-streak-count");
    if (streakN) streakN.textContent = "0";
    const xpEl = document.getElementById("nav-xp-count");
    if (xpEl) xpEl.textContent = "—";
  }

  function renderNavbar() {
    const center = document.getElementById("header-center");
    if (center) center.hidden = false;
    if (!summary) {
      renderNavbarGuest();
      return;
    }
    const streakN = document.getElementById("nav-streak-count");
    if (streakN) streakN.textContent = String(summary.currentStreak || 0);
    const xpEl = document.getElementById("nav-xp-count");
    if (xpEl) xpEl.textContent = String(summary.xp || 0);
    const freezeEl = document.getElementById("streak-freezes-count");
    if (freezeEl) freezeEl.textContent = String(summary.freezesOwned || 0);
    const todayEl = document.getElementById("streak-today-status");
    if (todayEl) {
      todayEl.textContent = summary.studiedToday ? "Done for today ✓" : "Study to keep your streak";
    }
    const bestEl = document.getElementById("streak-best-count");
    if (bestEl) bestEl.textContent = String(summary.bestStreak || 0);
    const curEl = document.getElementById("streak-current-count");
    if (curEl) curEl.textContent = String(summary.currentStreak || 0);
    const nextEl = document.getElementById("streak-next-reward");
    if (nextEl) {
      if (summary.nextMilestone) {
        nextEl.textContent = `${summary.nextMilestone.label} in ${summary.nextMilestone.xp_needed} day(s)`;
      } else {
        nextEl.textContent = "All milestones unlocked!";
      }
    }
  }

  async function awardAction(action, meta) {
    if (!isLoggedIn()) return null;
    if (actionTimers[action]) clearTimeout(actionTimers[action]);
    return new Promise((resolve) => {
      actionTimers[action] = setTimeout(async () => {
        try {
          const data = await api("/api/gamification/action", {
            method: "POST",
            body: JSON.stringify({ action, meta: meta || null, localDate: localDate() }),
          });
          if (data.xpAwarded > 0) toast(`+${data.xpAwarded} XP`, "success");
          if (data.streak && data.streak.protected) toast("🧊 Streak Protected!", "success");
          if (data.milestonesUnlocked && data.milestonesUnlocked.length) {
            data.milestonesUnlocked.forEach((m) => toast(`🏆 Unlocked: ${m.label}`, "success"));
          }
          await loadSummary(true);
          resolve(data);
        } catch (e) {
          resolve(null);
        }
      }, XP_DEBOUNCE_MS);
    });
  }

  // ── Focus timer (shared sidebar + navbar) ─────────────────────────
  const Focus = {
    seconds: 25 * 60,
    running: false,
    interval: null,
    sessionElapsed: 0,

    persist() {
      try {
        sessionStorage.setItem(
          FOCUS_KEY,
          JSON.stringify({
            seconds: this.seconds,
            running: this.running,
            sessionElapsed: this.sessionElapsed,
            tickAt: this.running ? Date.now() : null,
          })
        );
      } catch (_) {}
    },

    restore() {
      try {
        const raw = sessionStorage.getItem(FOCUS_KEY);
        if (!raw) return;
        const s = JSON.parse(raw);
        this.seconds = typeof s.seconds === "number" ? s.seconds : 25 * 60;
        this.sessionElapsed = s.sessionElapsed || 0;
        if (s.running && s.tickAt) {
          const delta = Math.floor((Date.now() - s.tickAt) / 1000);
          this.seconds = Math.max(0, this.seconds - delta);
          this.sessionElapsed += Math.max(0, delta);
          if (this.seconds > 0) this.start(true);
          else this.onComplete();
        }
        this.syncUi();
        this.maybeAward();
      } catch (_) {}
    },

    syncUi() {
      const mins = Math.floor(this.seconds / 60);
      const secs = this.seconds % 60;
      const text = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
      ["timer-display", "nav-focus-display"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
      });
      const btn = document.getElementById("timer-toggle-btn");
      if (btn) {
        btn.textContent = this.running ? "⏸ Pause" : "▶ Start";
        btn.style.background = this.running ? "var(--accent2)" : "";
      }
      const navBtn = document.getElementById("nav-focus-chip");
      if (navBtn) navBtn.classList.toggle("running", this.running);
    },

    tick() {
      if (this.seconds > 0) {
        this.seconds--;
        this.sessionElapsed++;
        this.syncUi();
        this.persist();
        this.maybeAward();
      } else {
        this.onComplete();
      }
    },

    maybeAward() {
      if (this.sessionElapsed >= 600 && !focusElapsedAwarded) {
        focusElapsedAwarded = true;
        try {
          sessionStorage.setItem(FOCUS_AWARD_KEY, localDate());
        } catch (_) {}
        awardAction("focus_10m");
      }
    },

    onComplete() {
      this.stop();
      this.seconds = 25 * 60;
      this.syncUi();
      this.persist();
      toast("🎉 Focus session complete! Take a 5-minute break.", "success");
    },

    start(silent) {
      if (this.running) return;
      this.running = true;
      this.interval = setInterval(() => this.tick(), 1000);
      this.syncUi();
      this.persist();
      if (!silent) toast("Focus timer started", "success");
    },

    pause() {
      if (!this.running) return;
      clearInterval(this.interval);
      this.interval = null;
      this.running = false;
      this.syncUi();
      this.persist();
    },

    toggle() {
      if (this.running) this.pause();
      else this.start();
    },

    reset() {
      this.pause();
      this.seconds = 25 * 60;
      this.sessionElapsed = 0;
      focusElapsedAwarded = false;
      try {
        sessionStorage.removeItem(FOCUS_AWARD_KEY);
      } catch (_) {}
      this.syncUi();
      this.persist();
    },

    stop() {
      clearInterval(this.interval);
      this.interval = null;
      this.running = false;
    },
  };

  try {
    if (sessionStorage.getItem(FOCUS_AWARD_KEY) === localDate()) {
      focusElapsedAwarded = true;
    }
  } catch (_) {}

  // ── Modals ───────────────────────────────────────────────────────
  function openModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.hidden = false;
    el.setAttribute("aria-hidden", "false");
    el.classList.add("open");
    const focusable = el.querySelector("button, [href], input, textarea, select");
    if (focusable) focusable.focus();
  }

  function closeModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("open");
    el.hidden = true;
    el.setAttribute("aria-hidden", "true");
  }

  function bindModalClose(modalId) {
    const el = document.getElementById(modalId);
    if (!el) return;
    el.querySelectorAll("[data-close-modal]").forEach((btn) => {
      btn.addEventListener("click", () => closeModal(modalId));
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal(modalId);
    });
  }

  async function openStreakModal() {
    if (!isLoggedIn()) {
      toast("Log in to track your streak", "warn");
      return;
    }
    await loadSummary(true);
    openModal("streak-modal");
  }

  async function openPuzzleModal() {
    if (!isLoggedIn()) {
      toast("Log in for Daily Puzzle", "warn");
      return;
    }
    openModal("puzzle-modal");
    const body = document.getElementById("puzzle-modal-body");
    if (body) body.innerHTML = `<div class="sb-skel">Loading today's puzzle…</div>`;
    try {
      if (puzzleInflight) await puzzleInflight;
      // UI chip shows Grade 9; generate grade-10 difficulty questions
      puzzleInflight = api(
        `/api/daily_puzzle?localDate=${encodeURIComponent(localDate())}&grade=10`
      );
      lastPuzzle = await puzzleInflight;
      puzzleInflight = null;
      renderPuzzle(lastPuzzle);
    } catch (e) {
      if (body) body.innerHTML = `<p class="sb-err">${e.message}</p>`;
    }
  }

  function renderPuzzle(p) {
    const body = document.getElementById("puzzle-modal-body");
    if (!body || !p) return;
    const done = p.attempted || p.skipped;
    body.innerHTML = `
      <div class="puzzle-meta">
        <span class="puzzle-pill">${escape(p.subject)}</span>
        <span class="puzzle-pill">${escape(p.difficulty)}</span>
        <span class="puzzle-pill">Grade 9</span>
        <span class="puzzle-pill xp">+${p.xpReward} XP</span>
      </div>
      <p class="puzzle-prompt">${escape(p.prompt)}</p>
      <details class="puzzle-hint"><summary>Hint</summary><p>${escape(p.hint || "Think step by step.")}</p></details>
      ${
        done
          ? `<div class="puzzle-result ${p.correct ? "ok" : ""}">
              ${p.skipped ? "Skipped." : p.correct ? "✅ Correct!" : "❌ Not quite."}
              <p><strong>Answer:</strong> ${escape(p.answer || "")}</p>
              <p><strong>Solution:</strong> ${escape(p.solution || "")}</p>
            </div>`
          : `<label class="puzzle-label" for="puzzle-answer">Your answer</label>
             <input id="puzzle-answer" class="input puzzle-input" autocomplete="off" />
             <div class="puzzle-actions">
               <button type="button" class="btn-primary" id="puzzle-submit-btn">Submit</button>
               <button type="button" class="btn-secondary" id="puzzle-skip-btn">Skip</button>
             </div>
             <div id="puzzle-feedback"></div>`
      }
    `;
    document.getElementById("puzzle-submit-btn")?.addEventListener("click", submitPuzzle);
    document.getElementById("puzzle-skip-btn")?.addEventListener("click", skipPuzzle);
  }

  function escape(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function submitPuzzle() {
    const ans = document.getElementById("puzzle-answer")?.value || "";
    const fb = document.getElementById("puzzle-feedback");
    if (fb) fb.textContent = "Checking…";
    try {
      const data = await api("/api/daily_puzzle/submit", {
        method: "POST",
        body: JSON.stringify({
          answer: ans,
          localDate: localDate(),
          grade: lastPuzzle?.grade,
          subject: lastPuzzle?.subject,
        }),
      });
      if (data.xpAwarded) toast(`+${data.xpAwarded} XP`, "success");
      if (data.milestonesUnlocked?.length) {
        data.milestonesUnlocked.forEach((m) => toast(`🏆 ${m.label}`, "success"));
      }
      lastPuzzle = {
        ...lastPuzzle,
        attempted: true,
        correct: data.correct,
        answer: data.answer,
        solution: data.solution,
      };
      renderPuzzle(lastPuzzle);
      await loadSummary(true);
    } catch (e) {
      if (fb) fb.textContent = e.message;
    }
  }

  async function skipPuzzle() {
    try {
      const data = await api("/api/daily_puzzle/skip", {
        method: "POST",
        body: JSON.stringify({
          localDate: localDate(),
          grade: lastPuzzle?.grade,
          subject: lastPuzzle?.subject,
        }),
      });
      lastPuzzle = {
        ...lastPuzzle,
        skipped: true,
        attempted: true,
        answer: data.answer,
        solution: data.solution,
      };
      renderPuzzle(lastPuzzle);
      await loadSummary(true);
    } catch (e) {
      toast(e.message, "warn");
    }
  }

  async function openShopModal() {
    if (!isLoggedIn()) {
      toast("Log in to open the XP Shop", "warn");
      return;
    }
    openModal("shop-modal");
    const body = document.getElementById("shop-modal-body");
    if (body) body.innerHTML = `<div class="sb-skel">Loading shop…</div>`;
    try {
      const data = await api("/api/shop");
      body.innerHTML = `
        <p class="shop-balance">Your XP: <strong>${data.xp}</strong></p>
        <div class="shop-grid">
          ${(data.items || [])
            .map(
              (it) => `
            <div class="shop-item">
              <div class="shop-item-icon">${it.icon}</div>
              <div class="shop-item-info">
                <strong>${escape(it.name)}</strong>
                <p>${escape(it.description)}</p>
                <span class="shop-cost">${it.cost} XP · owned ${it.owned}/${it.max_owned}</span>
              </div>
              <button type="button" class="btn-primary shop-buy-btn" data-item="${escape(it.id)}"
                ${it.canBuy && data.xp >= it.cost ? "" : "disabled"}>
                ${it.canBuy ? "Buy" : "Owned"}
              </button>
            </div>`
            )
            .join("")}
        </div>`;
      body.querySelectorAll(".shop-buy-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await api("/api/shop/buy", {
              method: "POST",
              body: JSON.stringify({ itemId: btn.dataset.item }),
            });
            toast("Purchase successful!", "success");
            await loadSummary(true);
            openShopModal();
          } catch (e) {
            toast(e.message, "warn");
          }
        });
      });
    } catch (e) {
      body.innerHTML = `<p class="sb-err">${e.message}</p>`;
    }
  }

  // Study Planner scrapped for now — no UI / XP wiring

  function isEducationalQuestion(text) {
    // Prefer shared casual detector from index.html when available
    if (typeof window.isCasualConversation === "function") {
      return !window.isCasualConversation(text);
    }
    const t = String(text || "").trim();
    if (!t) return false;
    if (/^(hi|hello|hey|thanks|thank you|ok|okay|bye|yo|sup)[\s!.?]*$/i.test(t)) return false;
    if (/^(hi|hello|hey)\b/i.test(t) && t.length > 20) return false; // lyrics / banter
    const study =
      /\b(solve|explain|define|derive|prove|calculate|formula|chapter|homework|exam|photosynthesis|newton|math|physics|chemistry|biology|what is|why is|how do|how to|difference between)\b/i.test(
        t
      );
    return study;
  }

  async function savePrefsFromSettings() {
    if (!isLoggedIn()) return;
    const payload = {
      grade: parseInt(document.getElementById("settings-grade")?.value || "10", 10),
      language: document.getElementById("settings-language")?.value || "en",
      notifyStreak: !!document.getElementById("settings-notify-streak")?.checked,
      notifyPuzzle: !!document.getElementById("settings-notify-puzzle")?.checked,
      highContrast: !!document.getElementById("settings-high-contrast")?.checked,
      reducedMotion: !!document.getElementById("settings-reduced-motion")?.checked,
      fontScale: parseFloat(document.getElementById("settings-font-scale")?.value || "1") || 1,
    };
    try {
      const data = await api("/api/prefs", { method: "POST", body: JSON.stringify(payload) });
      applyPrefsToDom(data.prefs);
      toast("Preferences saved", "success");
      await loadSummary(true);
    } catch (e) {
      toast(e.message, "warn");
    }
  }

  function wireUi() {
    document.getElementById("nav-streak-chip")?.addEventListener("click", openStreakModal);
    document.getElementById("nav-puzzle-chip")?.addEventListener("click", openPuzzleModal);
    document.getElementById("nav-focus-chip")?.addEventListener("click", () => Focus.toggle());
    document.getElementById("nav-shop-chip")?.addEventListener("click", openShopModal);
    document.getElementById("settings-open-shop")?.addEventListener("click", openShopModal);

    bindModalClose("streak-modal");
    bindModalClose("puzzle-modal");
    bindModalClose("shop-modal");

    // Replace legacy sidebar timer handlers
    const toggle = document.getElementById("timer-toggle-btn");
    const reset = document.getElementById("timer-reset-btn");
    if (toggle) {
      toggle.onclick = () => Focus.toggle();
    }
    if (reset) {
      reset.onclick = () => Focus.reset();
    }
    Focus.restore();
    Focus.syncUi();

    document.getElementById("settings-save-prefs")?.addEventListener("click", savePrefsFromSettings);
    document.getElementById("settings-language")?.addEventListener("change", (e) => {
      try { localStorage.setItem("sb_reply_language", e.target.value || "en"); } catch (_) {}
    });
    ["settings-high-contrast", "settings-reduced-motion", "settings-font-scale"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => {
        const prefs = {
          highContrast: !!document.getElementById("settings-high-contrast")?.checked,
          reducedMotion: !!document.getElementById("settings-reduced-motion")?.checked,
          fontScale: parseFloat(document.getElementById("settings-font-scale")?.value || "1") || 1,
        };
        applyPrefsToDom(prefs);
      });
    });

    // Notes mode open → debounced notes_read
    const notesBadge = document.getElementById("header-notes-badge");
    if (notesBadge) {
      const obs = new MutationObserver(() => {
        if (notesBadge.style.display !== "none") awardAction("notes_read");
      });
      obs.observe(notesBadge, { attributes: true, attributeFilter: ["style", "class"] });
    }
  }

  // Public API for index.html hooks
  window.SBGame = {
    award: awardAction,
    refresh: () => loadSummary(true),
    isEducationalQuestion,
    onLogin() {
      summaryGen++;
      summary = null;
      summaryInflight = null;
      renderNavbarGuest();
      loadSummary(true);
    },
    onLogout() {
      summaryGen++;
      summary = null;
      summaryInflight = null;
      renderNavbarGuest();
    },
    Focus,
    toast,
  };

  // Expose currentUser bridge — poll until auth sets it
  function boot() {
    wireUi();
    // Prefer window.currentUser if main script exposes it
    const tryRefresh = () => {
      if (typeof window.currentUser !== "undefined" && window.currentUser?.loggedIn) {
        loadSummary(true);
      } else {
        renderNavbar();
      }
    };
    tryRefresh();
    setInterval(() => {
      if (isLoggedIn() && !summary) loadSummary(false);
    }, 8000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
