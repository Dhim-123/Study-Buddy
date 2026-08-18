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
    // Keep last known XP/streak visible while refreshing (don't flash zeros on login)
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
        setTimeout(() => { maybePromptElective().catch(() => {}); }, 400);
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
    const sectionEl = document.getElementById("settings-section");
    const section = prefs.section || "";
    if (sectionEl && section) {
      sectionEl.value = section;
      try { localStorage.setItem("sb_section", section); } catch (_) {}
    }
    const dropMath = !!(prefs.dropMath ?? prefs.drop_math);
    const dropScience = !!(prefs.dropScience ?? prefs.drop_science);
    const dm = document.getElementById("settings-drop-math");
    if (dm) dm.checked = section === "Super 3" && dropMath;
    const ds = document.getElementById("settings-drop-science");
    if (ds) ds.checked = section === "Super 3" && dropScience;
    const dropsWrap = document.getElementById("settings-super3-drops");
    if (dropsWrap) dropsWrap.style.display = section === "Super 3" ? "block" : "none";
    try {
      localStorage.setItem("sb_drop_math", section === "Super 3" && dropMath ? "1" : "0");
      localStorage.setItem("sb_drop_science", section === "Super 3" && dropScience ? "1" : "0");
    } catch (_) {}
    const langEl = document.getElementById("settings-language");
    const toolsLangEl = document.getElementById("tools-reply-language");
    if (langEl || toolsLangEl) {
      const lang = prefs.language || localStorage.getItem("sb_reply_language") || "multi";
      if (langEl) {
        langEl.value = lang;
        // If server sent an unknown/empty value, fall back so Multilingual stays selected
        if (langEl.value !== lang) langEl.value = "multi";
      }
      if (toolsLangEl) {
        toolsLangEl.value = (langEl && langEl.value) || lang;
        if (toolsLangEl.value !== lang && toolsLangEl.value !== ((langEl && langEl.value) || "")) {
          toolsLangEl.value = "multi";
        }
      }
      try { localStorage.setItem("sb_reply_language", (langEl && langEl.value) || (toolsLangEl && toolsLangEl.value) || "multi"); } catch (_) {}
    }
    const toolsSec = document.getElementById("tools-section");
    if (toolsSec && section) {
      toolsSec.value = section;
      const toolsDrops = document.getElementById("tools-super3-drops");
      if (toolsDrops) toolsDrops.style.display = section === "Super 3" ? "block" : "none";
      const tdm = document.getElementById("tools-drop-math");
      const tds = document.getElementById("tools-drop-science");
      if (tdm) tdm.checked = section === "Super 3" && dropMath;
      if (tds) tds.checked = section === "Super 3" && dropScience;
    }
    const ns = document.getElementById("settings-notify-streak");
    if (ns) ns.checked = !!prefs.notifyStreak;
    const np = document.getElementById("settings-notify-puzzle");
    if (np) np.checked = !!prefs.notifyPuzzle;
    const nf = document.getElementById("settings-notify-fact");
    if (nf) nf.checked = prefs.notifyFact == null ? true : !!prefs.notifyFact;
    const hc = document.getElementById("settings-high-contrast");
    if (hc) hc.checked = !!prefs.highContrast;
    const rm = document.getElementById("settings-reduced-motion");
    if (rm) rm.checked = !!prefs.reducedMotion;
    const fs = document.getElementById("settings-font-scale");
    if (fs) fs.value = String(prefs.fontScale || 1);
    const ageBand = prefs.ageBand || prefs.age_band || "";
    const ageEl = document.getElementById("settings-age-band");
    if (ageEl && ageBand) ageEl.value = ageBand;
    if (ageBand) {
      try { localStorage.setItem("sb_age_band", ageBand); } catch (_) {}
    }
    if (window.currentUser?.loggedIn) {
      const patch = { ...window.currentUser };
      if (Object.prototype.hasOwnProperty.call(prefs, "elective")) {
        patch.elective = prefs.elective || "";
      }
      if (Object.prototype.hasOwnProperty.call(prefs, "dropMath")
          || Object.prototype.hasOwnProperty.call(prefs, "drop_math")) {
        patch.dropMath = dropMath;
      }
      if (Object.prototype.hasOwnProperty.call(prefs, "dropScience")
          || Object.prototype.hasOwnProperty.call(prefs, "drop_science")) {
        patch.dropScience = dropScience;
      }
      if (prefs.derivedSubjects || prefs.derived_subjects) {
        patch.derivedSubjects = prefs.derivedSubjects || prefs.derived_subjects;
      }
      if (ageBand) patch.ageBand = ageBand;
      window.currentUser = patch;
    }
    syncElectiveSettingsUi(prefs);
    try { window.nbRender?.(); } catch (_) {}
  }

  function syncElectiveSettingsUi(prefs) {
    const el = document.getElementById("settings-elective");
    const hint = document.getElementById("settings-elective-hint");
    if (!el) return;
    const section = prefs?.section || document.getElementById("settings-section")?.value || "";
    const dropMath = section === "Super 3" && !!(prefs?.dropMath ?? prefs?.drop_math
      ?? document.getElementById("settings-drop-math")?.checked);
    const dropScience = section === "Super 3" && !!(prefs?.dropScience ?? prefs?.drop_science
      ?? document.getElementById("settings-drop-science")?.checked);
    const BASE = [
      "Physical Education",
      "Commercial Applications",
      "Economics Application",
      "Art",
    ];
    const current = BASE.includes(prefs?.elective) ? prefs.elective : "";
    const locked = !!(current && (prefs?.electiveLocked || prefs?.elective));
    const fill = window.fillElectiveSelect;
    const allowed = window.allowedElectivesForDrops
      ? window.allowedElectivesForDrops(dropMath, dropScience)
      : (prefs?.allowedElectives?.length ? prefs.allowedElectives : BASE);
    if (fill) {
      fill(el, dropMath, dropScience, current || el.value);
    } else {
      el.innerHTML = '<option value="">Select elective…</option>';
      (allowed.length ? allowed : BASE).forEach((name) => {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        el.appendChild(o);
      });
      if (current) el.value = current;
    }
    const hintText = "Pick once. Electives: PE, Commercial Applications, Economics Application, Art. Drop Science adds Economics as a subject; drop Math and Science also adds Law.";
    if (locked && current) {
      if (![...el.options].some((o) => o.value === current)) {
        const o = document.createElement("option");
        o.value = current;
        o.textContent = current;
        el.appendChild(o);
      }
      el.value = current;
      el.disabled = true;
      if (hint) hint.textContent = "Elective cannot be changed.";
    } else {
      el.disabled = false;
      if (hint) hint.textContent = hintText;
    }
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
      const el = document.getElementById("timer-display");
      if (el) el.textContent = text;
      const btn = document.getElementById("timer-toggle-btn");
      if (btn) {
        btn.textContent = this.running ? "⏸ Pause" : "▶ Start";
        btn.style.background = this.running ? "var(--accent2)" : "";
      }
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
      this.exitFullscreen();
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
      this.hideAwayOverlay();
      if (!silent) {
        this.enterFullscreen();
        toast("Focus timer started — stay in this browser tab", "success");
      }
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
      this.hideAwayOverlay();
      this.exitFullscreen();
      this.syncUi();
      this.persist();
    },

    stop() {
      clearInterval(this.interval);
      this.interval = null;
      this.running = false;
      this.hideAwayOverlay();
    },

    enterFullscreen() {
      try {
        const root = document.documentElement;
        if (!document.fullscreenElement && root.requestFullscreen) {
          root.requestFullscreen().catch(() => {});
          this._fsOwned = true;
        }
      } catch (_) {}
    },

    exitFullscreen() {
      try {
        if (this._fsOwned && document.fullscreenElement && document.exitFullscreen) {
          document.exitFullscreen().catch(() => {});
        }
      } catch (_) {}
      this._fsOwned = false;
    },

    ensureAwayOverlay() {
      let overlay = document.getElementById("focus-tab-lock");
      if (overlay) return overlay;
      overlay = document.createElement("div");
      overlay.id = "focus-tab-lock";
      overlay.hidden = true;
      overlay.innerHTML = `
        <div class="focus-tab-lock-card" role="dialog" aria-modal="true" aria-labelledby="focus-tab-lock-title">
          <h3 id="focus-tab-lock-title">Stay on this browser tab</h3>
          <p>Focus timer paused because you switched to another browser tab or app. Chat and Quiz are fine — don't leave Study Buddy.</p>
          <div class="focus-tab-lock-actions">
            <button type="button" class="btn-primary" id="focus-tab-lock-resume" style="flex:1;">Resume</button>
            <button type="button" class="btn-secondary" id="focus-tab-lock-stop" style="flex:1;">Reset</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      overlay.querySelector("#focus-tab-lock-resume")?.addEventListener("click", () => {
        overlay.hidden = true;
        this.start();
      });
      overlay.querySelector("#focus-tab-lock-stop")?.addEventListener("click", () => {
        overlay.hidden = true;
        this.reset();
      });
      return overlay;
    },

    hideAwayOverlay() {
      const overlay = document.getElementById("focus-tab-lock");
      if (overlay) overlay.hidden = true;
    },

    onLeftTab() {
      if (!this.running) return;
      this.pause();
      const overlay = this.ensureAwayOverlay();
      overlay.hidden = false;
      toast("Focus timer paused — you left the tab", "warn");
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

  let lastFact = null;
  let factInflight = null;
  let electivePromptShown = false;

  async function openFactModal() {
    if (!isLoggedIn()) {
      toast("Log in for Daily Fact", "warn");
      return;
    }
    openModal("fact-modal");
    const body = document.getElementById("fact-modal-body");
    if (body) body.innerHTML = `<div class="sb-skel">Loading today's fact…</div>`;
    try {
      if (factInflight) await factInflight;
      factInflight = api(
        `/api/daily_fact?localDate=${encodeURIComponent(localDate())}`
      );
      lastFact = await factInflight;
      factInflight = null;
      renderFact(lastFact);
    } catch (e) {
      if (body) body.innerHTML = `<p class="sb-err">${e.message}</p>`;
    }
  }

  function renderFact(f) {
    const body = document.getElementById("fact-modal-body");
    if (!body || !f) return;
    const done = !!f.viewed;
    body.innerHTML = `
      <div class="puzzle-meta">
        <span class="puzzle-pill">${escape(f.category || "Fun")}</span>
        ${metaGkPill(f.category)}
        <span class="puzzle-pill xp">+${f.xpReward || 5} XP</span>
      </div>
      <h4 style="margin:12px 0 8px;font-size:1.05rem;">${escape(f.title || "Daily Fact")}</h4>
      <p class="puzzle-prompt" style="line-height:1.55;">${escape(f.body || "")}</p>
      ${
        done
          ? `<div class="puzzle-result ok">Got it — +${f.xpAwarded || f.xpReward || 5} XP saved for today.</div>`
          : `<div class="puzzle-actions">
               <button type="button" class="btn-primary" id="fact-ack-btn">Got it (+${f.xpReward || 5} XP)</button>
             </div>`
      }
    `;
    document.getElementById("fact-ack-btn")?.addEventListener("click", ackFact);
  }

  async function ackFact() {
    try {
      const data = await api("/api/daily_fact/ack", {
        method: "POST",
        body: JSON.stringify({
          localDate: localDate(),
        }),
      });
      if (data.xpAwarded) toast(`+${data.xpAwarded} XP`, "success");
      lastFact = {
        ...lastFact,
        viewed: true,
        xpAwarded: data.xpAwarded || lastFact?.xpReward || 5,
      };
      renderFact(lastFact);
      await loadSummary(true);
    } catch (e) {
      toast(e.message || "Could not save fact", "warn");
    }
  }

  async function maybePromptElective() {
    if (!isLoggedIn() || !summary?.prefs) return;
    const elective = summary.prefs.elective || "";
    if (elective) {
      electivePromptShown = false;
      return;
    }
    if (electivePromptShown) return;
    const modal = document.getElementById("elective-pick-modal");
    const select = document.getElementById("elective-pick-select");
    if (!modal || !select) return;
    electivePromptShown = true;
    const dropMath = !!summary.prefs.dropMath;
    const dropScience = !!summary.prefs.dropScience;
    if (window.fillElectiveSelect) {
      window.fillElectiveSelect(select, dropMath, dropScience);
    } else {
      const opts = summary.prefs.allowedElectives || [];
      select.innerHTML = '<option value="">Select elective…</option>';
      opts.forEach((name) => {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        select.appendChild(o);
      });
    }
    openModal("elective-pick-modal");
  }

  async function saveElectivePick() {
    const select = document.getElementById("elective-pick-select");
    const errEl = document.getElementById("elective-pick-error");
    const elective = (select?.value || "").trim();
    if (!elective) {
      if (errEl) {
        errEl.style.display = "block";
        errEl.textContent = "Please select an elective.";
      }
      return;
    }
    try {
      const data = await api("/api/prefs", {
        method: "POST",
        body: JSON.stringify({ elective }),
      });
      if (errEl) errEl.style.display = "none";
      closeModal("elective-pick-modal");
      toast("Elective saved", "success");
      await loadSummary(true);
      if (data.prefs) applyPrefsToDom({
        ...data.prefs,
        elective: data.prefs.elective,
        electiveLocked: true,
        dropMath: data.prefs.drop_math ?? data.prefs.dropMath,
        dropScience: data.prefs.drop_science ?? data.prefs.dropScience,
      });
    } catch (e) {
      if (errEl) {
        errEl.style.display = "block";
        errEl.textContent = e.message || "Could not save elective";
      }
    }
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
      if (puzzleInflight) {
        try {
          lastPuzzle = await puzzleInflight;
          puzzleInflight = null;
          renderPuzzle(lastPuzzle);
          return;
        } catch (_) {
          puzzleInflight = null;
        }
      }
      puzzleInflight = api(
        `/api/daily_puzzle?localDate=${encodeURIComponent(localDate())}`
      );
      lastPuzzle = await puzzleInflight;
      renderPuzzle(lastPuzzle);
    } catch (e) {
      if (body) body.innerHTML = `<p class="sb-err">${e.message}</p>`;
    } finally {
      puzzleInflight = null;
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
        ${metaGkPill(p.subject)}
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

  function isGkSubject(name) {
    const t = String(name || "").trim().toLowerCase();
    return t === "gk" || t === "general knowledge" || t.includes("general knowledge");
  }

  function metaGkPill(subjectOrCategory) {
    if (isGkSubject(subjectOrCategory)) return "";
    return `<span class="puzzle-pill">GK</span>`;
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

  function escapePlannerHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setPlannerStatus(text, isError) {
    const el = document.getElementById("planner-status");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = isError ? "#f43f5e" : "";
  }

  function setPlannerPriorityHeading(mode) {
    const el = document.getElementById("planner-priority-heading");
    if (!el) return;
    if (mode === "weakest") el.textContent = "Weakest subjects";
    else if (mode === "exams") el.textContent = "Upcoming exams";
    else el.textContent = "Priority focus";
  }

  function renderPlannerFocus(mode, exams, weakest) {
    const host = document.getElementById("planner-exams-summary");
    if (!host) return;
    setPlannerPriorityHeading(mode);
    if (mode === "exams") {
      const list = exams || [];
      if (!list.length) {
        host.innerHTML = `<span class="settings-hint">No upcoming exams.</span>`;
        return;
      }
      host.innerHTML = list
        .map((ex) => {
          const days = typeof ex.days_left === "number" ? `${ex.days_left}d left` : ex.exam_date || "";
          return `<div class="planner-exam-chip"><strong>${escapePlannerHtml(ex.subject)}</strong> · ${escapePlannerHtml(days)}</div>`;
        })
        .join("");
      return;
    }
    const list = weakest || [];
    if (!list.length) {
      host.innerHTML =
        `<span class="settings-hint">No exam schedule. Take quizzes so we can prioritize your weakest subjects.</span>`;
      return;
    }
    host.innerHTML = list
      .map((s) => {
        const acc = typeof s.accuracy === "number" ? `${Math.round(s.accuracy)}%` : "—";
        return `<div class="planner-exam-chip"><strong>${escapePlannerHtml(s.subject)}</strong> · ${escapePlannerHtml(acc)} accuracy</div>`;
      })
      .join("");
  }

  function topicFromPlannerTitle(title) {
    const t = String(title || "").trim();
    if (!t) return "General";
    // "Math: Algebra" → use full string as study topic
    const practice = t.match(/^Practice\s+(.+?)\s+\(weakest/i);
    if (practice) return practice[1].trim();
    const review = t.match(/^Review mistakes.*?in\s+(.+)$/i);
    if (review) return review[1].trim();
    const ask = t.match(/weak\s+(.+?)\s+topic/i);
    if (ask) return ask[1].trim();
    return t;
  }

  function studyActionButtonsHtml(title) {
    const topic = escapePlannerHtml(topicFromPlannerTitle(title));
    return `<div class="planner-task-actions study-today-actions">
      <button type="button" class="qs-btn" data-study-action="quiz" data-topic="${topic}">Quiz</button>
      <button type="button" class="qs-btn" data-study-action="flashcards" data-topic="${topic}">Flashcards</button>
      <button type="button" class="qs-btn" data-study-action="chat" data-topic="${topic}">Chat</button>
    </div>`;
  }

  function wireStudyActionButtons(root) {
    if (!root) return;
    root.querySelectorAll("[data-study-action]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openStudyAction(btn.getAttribute("data-study-action"), btn.getAttribute("data-topic") || "");
      });
    });
  }

  const PENDING_STUDY_KEY = "sb_pending_study_topic";

  function rememberPendingStudyTopic(topic, kind) {
    try {
      sessionStorage.setItem(
        PENDING_STUDY_KEY,
        JSON.stringify({
          topic: String(topic || "").trim(),
          kind: kind || "",
          at: Date.now(),
        })
      );
    } catch (_) {}
  }

  function takePendingStudyTopic(kind) {
    try {
      const raw = sessionStorage.getItem(PENDING_STUDY_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !data.topic) return null;
      // Only auto-complete for the practice type that was started
      if (kind && data.kind && data.kind !== kind && data.kind !== "chat") return null;
      if (data.at && Date.now() - data.at > 2 * 60 * 60 * 1000) {
        sessionStorage.removeItem(PENDING_STUDY_KEY);
        return null;
      }
      sessionStorage.removeItem(PENDING_STUDY_KEY);
      return String(data.topic || "").trim();
    } catch (_) {
      return null;
    }
  }

  function taskMatchesStudyTopic(taskTitle, topic) {
    const title = String(taskTitle || "").toLowerCase();
    const t = String(topic || "").toLowerCase().trim();
    if (!title || !t) return false;
    if (title.includes(t) || t.includes(title)) return true;
    // Match on first chunk before ":" (subject) or shared significant words
    const subj = t.split(":")[0].trim();
    if (subj.length >= 3 && title.includes(subj)) return true;
    const words = t.split(/[^a-z0-9]+/).filter((w) => w.length >= 4);
    return words.some((w) => title.includes(w));
  }

  async function completeMatchingPlannerTasks(topic) {
    if (!isLoggedIn() || !topic) return 0;
    let tasks = [];
    try {
      const data = await api("/api/planner");
      tasks = (data.tasks || []).filter((t) => !t.done && taskMatchesStudyTopic(t.title, topic));
    } catch (_) {
      return 0;
    }
    // Complete at most 2 matching open tasks — keeps it calm
    const targets = tasks.slice(0, 2);
    let n = 0;
    for (const t of targets) {
      try {
        await api(`/api/planner/${t.id}`, {
          method: "PATCH",
          body: JSON.stringify({ done: true }),
        });
        n += 1;
      } catch (_) {}
    }
    if (n > 0) {
      toast(n === 1 ? "Planner task done" : `${n} planner tasks done`, "success");
      refreshStudyToday();
      // Refresh planner list if visible
      const list = document.getElementById("planner-task-list");
      if (list && list.offsetParent !== null) {
        refreshPlanner().catch(() => {});
      }
    }
    return n;
  }

  async function onStudyPracticeComplete(kind) {
    const topic = takePendingStudyTopic(kind);
    if (!topic) return;
    await completeMatchingPlannerTasks(topic);
  }

  function openStudyAction(kind, topic) {
    const prompt = String(topic || "").trim() || "General revision";
    const go = (section) => {
      if (typeof window.showSection === "function") window.showSection(section);
      else {
        const tab = [...document.querySelectorAll(".tab-btn")].find((b) => b.dataset.tab === section);
        if (tab) tab.click();
      }
    };
    if (kind === "quiz") {
      rememberPendingStudyTopic(prompt, "quiz");
      go("quiz");
      const input = document.getElementById("quiz-input");
      const subject = String(prompt || "").replace(/^Practice\s+/i, "").replace(/\s*\(weakest.*$/i, "").trim();
      if (input) input.value = subject ? `Weak areas in ${subject}` : "My weakest topics";
      try { sessionStorage.setItem("sb_quiz_source", "weakest"); } catch (_) {}
      setTimeout(() => document.getElementById("generate-quiz-btn")?.click(), 120);
      return;
    }
    if (kind === "flashcards") {
      rememberPendingStudyTopic(prompt, "flashcards");
      go("flashcards");
      const input = document.getElementById("flashcard-input");
      if (input) input.value = prompt;
      setTimeout(() => document.getElementById("generate-flashcard-btn")?.click(), 120);
      return;
    }
    // chat — remember topic; complete after they send (optional hook) or leave pending short
    rememberPendingStudyTopic(prompt, "chat");
    go("chat");
    const input = document.getElementById("user-input");
    if (input) {
      input.value = `Help me study this for my exam plan: ${prompt}. Explain the key ideas and give 3 practice questions.`;
      input.focus();
      try {
        input.dispatchEvent(new Event("input", { bubbles: true }));
      } catch (_) {}
    }
  }

  function pickStudyTodayTasks(tasks) {
    const today = localDate();
    const open = (tasks || []).filter((t) => !t.done);
    const dueToday = open.filter((t) => t.due_date && String(t.due_date) <= today);
    const pool = dueToday.length ? dueToday : open;
    const ranked = [...pool].sort((a, b) => {
      const rank = (t) =>
        t.source === "exam_auto" ? 0 : t.source === "weakness_auto" ? 1 : 2;
      if (rank(a) !== rank(b)) return rank(a) - rank(b);
      return String(a.due_date || "9999").localeCompare(String(b.due_date || "9999"));
    });
    return ranked.slice(0, 2);
  }

  async function fetchRevisionStudyChips() {
    const chips = [];
    try {
      const data = await api("/api/notebook");
      const entries = data.entries || [];
      const revise = entries.filter(
        (e) =>
          (e.category === "Things to Revise" || e.category === "Mistakes I Made") &&
          String(e.content || "").trim()
      );
      for (const e of revise.slice(0, 2)) {
        let tip = "";
        for (const line of String(e.content || "").split("\n")) {
          const clean = line.trim().replace(/^[•\-\*]\s*/, "").trim();
          if (clean) {
            tip = clean.slice(0, 90);
            break;
          }
        }
        const title = tip
          ? `Revise: ${e.subject || "General"} — ${tip}`
          : `Revise: ${e.subject || "General"} (${e.category})`;
        chips.push({ title, source: "revise_auto", kind: "quiz" });
      }
    } catch (_) {}
    return chips;
  }

  function studyTodayBuddyLine(tasks) {
    const buddy = String(window.currentUser?.buddyName || "Max").trim() || "Max";
    const open = (tasks || []).filter((t) => !t.done);
    if (!open.length && !(window.__sbRevisionChips || []).length) return "";
    if ((window.__sbRevisionChips || []).length) {
      return `${escapePlannerHtml(buddy)} picked revision from your notebook weak spots.`;
    }
    if (open.some((t) => t.source === "weakness_auto")) {
      return `${escapePlannerHtml(buddy)} picked this for you — focus on a weak area.`;
    }
    if (open.some((t) => t.source === "exam_auto")) {
      return `${escapePlannerHtml(buddy)} picked this for you — exam prep.`;
    }
    return `${escapePlannerHtml(buddy)} lined these up for today.`;
  }

  function renderStudyToday(tasks, revisionChips) {
    const banner = document.getElementById("study-today-banner");
    if (!banner) return;
    if (!isLoggedIn()) {
      banner.style.display = "none";
      banner.innerHTML = "";
      return;
    }
    const top = pickStudyTodayTasks(tasks);
    const chips = (revisionChips || window.__sbRevisionChips || []).slice(0, 2);
    const buddyLine = studyTodayBuddyLine(tasks);
    if (!top.length && !chips.length) {
      banner.innerHTML = `<strong>Study today</strong>
        <div class="settings-hint" style="margin:0;">No open tasks yet.
        <button type="button" class="qs-btn" id="study-today-open-planner" style="margin-left:6px;">Open Planner</button></div>`;
      banner.style.display = "block";
      document.getElementById("study-today-open-planner")?.addEventListener("click", () => {
        if (typeof window.showSection === "function") window.showSection("planner");
      });
      return;
    }
    const taskHtml = top
      .map(
        (t) => `<div class="study-today-item">
          <div class="study-today-title">${escapePlannerHtml(t.title)}</div>
          ${studyActionButtonsHtml(t.title)}
        </div>`
      )
      .join("");
    const chipHtml = chips
      .map((c) => {
        const topic = escapePlannerHtml(c.title);
        return `<div class="study-today-item">
          <div class="study-today-title">${topic}</div>
          <div class="planner-task-actions study-today-actions">
            <button type="button" class="qs-btn" data-study-action="quiz" data-topic="${topic}">Quiz</button>
            <button type="button" class="qs-btn" data-study-action="chat" data-topic="${topic}">Chat</button>
          </div>
        </div>`;
      })
      .join("");
    banner.innerHTML =
      `<strong>Study today</strong>` +
      (buddyLine ? `<div class="settings-hint" style="margin:0 0 8px;">${buddyLine}</div>` : "") +
      taskHtml +
      chipHtml;
    banner.style.display = "block";
    wireStudyActionButtons(banner);
  }

  async function refreshStudyToday() {
    const banner = document.getElementById("study-today-banner");
    if (!banner) return;
    if (!isLoggedIn()) {
      banner.style.display = "none";
      return;
    }
    try {
      let data = await api("/api/planner");
      const hasAuto = (data.tasks || []).some(
        (t) => t.source === "exam_auto" || t.source === "weakness_auto"
      );
      const examWeek = window.__sbExamWeek;
      if (!hasAuto || (examWeek && examWeek.active)) {
        try {
          data = await api("/api/planner/sync-exams", { method: "POST", body: "{}" });
        } catch (_) {}
      }
      const revisionChips = await fetchRevisionStudyChips();
      window.__sbRevisionChips = revisionChips;
      renderStudyToday(data.tasks || [], revisionChips);
    } catch (_) {
      banner.style.display = "none";
    }
  }

  function renderPlannerTasks(tasks) {
    const list = document.getElementById("planner-task-list");
    if (!list) return;
    const rows = tasks || [];
    renderStudyToday(rows);
    if (!rows.length) {
      list.innerHTML = `<li class="planner-empty">No tasks yet. Click “Sync study plan”.</li>`;
      return;
    }
    list.innerHTML = rows
      .map((t) => {
        const due = t.due_date ? `Due ${escapePlannerHtml(t.due_date)}` : "No due date";
        let badge = `<div class="planner-task-badge" style="color:#6a6a85;">Manual</div>`;
        if (t.source === "exam_auto") {
          badge = `<div class="planner-task-badge">Exam priority</div>`;
        } else if (t.source === "weakness_auto") {
          badge = `<div class="planner-task-badge" style="color:#f59e0b;">Weakest subject</div>`;
        }
        return `<li class="planner-task${t.done ? " done" : ""}" data-task-id="${t.id}">
          <div class="planner-task-body">
            <label>
              <input type="checkbox" data-planner-done ${t.done ? "checked" : ""} />
              <span>
                ${badge}
                <span class="planner-task-title">${escapePlannerHtml(t.title)}</span>
                <div class="planner-task-meta">${due}</div>
              </span>
            </label>
            ${t.done ? "" : studyActionButtonsHtml(t.title)}
          </div>
          <button type="button" class="planner-del" data-planner-del title="Remove">×</button>
        </li>`;
      })
      .join("");

    wireStudyActionButtons(list);

    list.querySelectorAll("[data-planner-done]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const li = cb.closest(".planner-task");
        const id = Number(li?.dataset?.taskId);
        if (!id) return;
        const wasDone = li.classList.contains("done");
        try {
          await api(`/api/planner/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ done: !!cb.checked }),
          });
          if (cb.checked) li.classList.add("done");
          else li.classList.remove("done");
          if (cb.checked && !wasDone) awardAction("planner");
          refreshStudyToday();
        } catch (e) {
          cb.checked = !cb.checked;
          toast(e.message, "warn");
        }
      });
    });
    list.querySelectorAll("[data-planner-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const li = btn.closest(".planner-task");
        const id = Number(li?.dataset?.taskId);
        if (!id || !confirm("Remove this task?")) return;
        try {
          await api(`/api/planner/${id}`, { method: "DELETE" });
          li.remove();
          if (!list.querySelector(".planner-task")) {
            list.innerHTML = `<li class="planner-empty">No tasks yet. Click “Sync study plan”.</li>`;
          }
          refreshStudyToday();
        } catch (e) {
          toast(e.message, "warn");
        }
      });
    });
  }

  function applySyncedPlanner(data) {
    const mode = data.mode || ((data.exams || []).length ? "exams" : "weakest");
    renderPlannerFocus(mode, data.exams || [], data.weakest || []);
    renderPlannerTasks(data.tasks || []);
    const nPlan = (data.plan || data.tasks || []).length;
    if (mode === "exams") {
      setPlannerStatus(`Exam plan · ${(data.exams || []).length} exam(s) → ${nPlan} tasks`);
    } else {
      setPlannerStatus(`Weakest-subject plan · ${nPlan} tasks`);
    }
    return mode;
  }

  async function refreshPlanner(opts) {
    const autoSync = !!(opts && opts.autoSync);
    if (!isLoggedIn()) {
      renderPlannerFocus("", [], []);
      renderStudyToday([]);
      const list = document.getElementById("planner-task-list");
      if (list) {
        list.innerHTML = `<li class="planner-empty">Log in to see your study plan.</li>`;
      }
      setPlannerStatus("");
      return;
    }
    setPlannerStatus("Loading…");
    try {
      let tasksData = await api("/api/planner");
      const hasAuto = (tasksData.tasks || []).some(
        (t) => t.source === "exam_auto" || t.source === "weakness_auto"
      );
      if (autoSync && !hasAuto) {
        const synced = await api("/api/planner/sync-exams", { method: "POST", body: "{}" });
        applySyncedPlanner(synced);
        return;
      }
      let mode = "exams";
      let exams = [];
      let weakest = [];
      try {
        const preview = await api("/api/planner/exam-plan");
        mode = preview.mode || "exams";
        exams = preview.exams || [];
        weakest = preview.weakest || [];
      } catch (_) {
        try {
          const upcoming = await api("/api/exams/upcoming");
          exams = upcoming.exams || [];
          mode = exams.length ? "exams" : "weakest";
        } catch (_) {}
      }
      renderPlannerFocus(mode, exams, weakest);
      setPlannerStatus(
        mode === "exams"
          ? `${exams.length} upcoming exam(s) (priority)`
          : "No exams — prioritizing weakest subjects"
      );
      renderPlannerTasks(tasksData.tasks || []);
    } catch (e) {
      setPlannerStatus(e.message || "Failed to load planner", true);
    }
  }

  async function syncPlannerFromExams() {
    if (!isLoggedIn()) {
      toast("Log in to sync the planner", "warn");
      return;
    }
    setPlannerStatus("Building smart study plan…");
    try {
      const data = await api("/api/planner/sync-exams", { method: "POST", body: "{}" });
      const mode = applySyncedPlanner(data);
      toast(
        mode === "exams" ? "Study plan updated from exams" : "Study plan updated from weakest subjects",
        "success"
      );
    } catch (e) {
      setPlannerStatus(e.message || "Sync failed", true);
      toast(e.message, "warn");
    }
  }

  async function addManualPlannerTask() {
    if (!isLoggedIn()) {
      toast("Log in to add tasks", "warn");
      return;
    }
    const titleEl = document.getElementById("planner-task-title");
    const dueEl = document.getElementById("planner-task-due");
    const title = (titleEl?.value || "").trim();
    if (!title) {
      toast("Enter a task title", "warn");
      return;
    }
    const examWeek = window.__sbExamWeek;
    if (examWeek && examWeek.active) {
      const portion = String(examWeek.portion || "").toLowerCase();
      const subject = String(examWeek.subject || "").toLowerCase();
      const t = title.toLowerCase();
      const looksRelated =
        (subject && t.includes(subject)) ||
        (portion && portion.split(/[\n,;•]/).some((p) => {
          const bit = p.trim().toLowerCase().slice(0, 24);
          return bit.length > 3 && t.includes(bit);
        }));
      if (!looksRelated) {
        const ok = window.confirm(
          `Exam week is on for ${examWeek.subject}. This task may be off-portion. Add it anyway?`
        );
        if (!ok) return;
      }
    }
    try {
      await api("/api/planner", {
        method: "POST",
        body: JSON.stringify({ title, due_date: dueEl?.value || null }),
      });
      if (titleEl) titleEl.value = "";
      await refreshPlanner();
      toast("Task added", "success");
    } catch (e) {
      toast(e.message, "warn");
    }
  }

  function wirePlannerUi() {
    document.getElementById("planner-sync-btn")?.addEventListener("click", syncPlannerFromExams);
    document.getElementById("planner-add-btn")?.addEventListener("click", addManualPlannerTask);
    document.getElementById("planner-task-title")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addManualPlannerTask();
      }
    });
  }

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

  async function savePrefsFromSettings(opts) {
    if (!isLoggedIn()) return;
    const quiet = !!(opts && opts.quiet);
    const toolsSec = document.getElementById("tools-section")?.value || "";
    const settingsSec = document.getElementById("settings-section")?.value || "";
    const section = toolsSec || settingsSec || "";
    const language = (
      document.getElementById("tools-reply-language")?.value
      || document.getElementById("settings-language")?.value
      || "multi"
    );
    const dropMath = section === "Super 3" && !!(
      document.getElementById("tools-drop-math")?.checked
      || document.getElementById("settings-drop-math")?.checked
    );
    const dropScience = section === "Super 3" && !!(
      document.getElementById("tools-drop-science")?.checked
      || document.getElementById("settings-drop-science")?.checked
    );
    const payload = {
      language,
      notifyStreak: !!document.getElementById("settings-notify-streak")?.checked,
      notifyPuzzle: !!document.getElementById("settings-notify-puzzle")?.checked,
      notifyFact: !!document.getElementById("settings-notify-fact")?.checked,
      highContrast: !!document.getElementById("settings-high-contrast")?.checked,
      reducedMotion: !!document.getElementById("settings-reduced-motion")?.checked,
      fontScale: parseFloat(document.getElementById("settings-font-scale")?.value || "1") || 1,
      section,
      dropMath,
      dropScience,
      ageBand: document.getElementById("settings-age-band")?.value || (() => {
        try { return localStorage.getItem("sb_age_band"); } catch (_) { return "14-16"; }
      })() || "14-16",
    };
    const electiveEl = document.getElementById("settings-elective");
    if (electiveEl && !electiveEl.disabled && electiveEl.value) {
      payload.elective = electiveEl.value;
    }
    try {
      const data = await api("/api/prefs", { method: "POST", body: JSON.stringify(payload) });
      if (data.prefs) {
        // Keep select/localStorage in sync without clobbering a just-chosen language
        const langEl = document.getElementById("settings-language");
        const savedLang = data.prefs.language || payload.language;
        if (langEl && savedLang) {
          langEl.value = savedLang;
          try { localStorage.setItem("sb_reply_language", savedLang); } catch (_) {}
        }
        applyPrefsToDom({
          ...data.prefs,
          language: savedLang,
          fontScale: data.prefs.font_scale ?? data.prefs.fontScale,
          highContrast: data.prefs.high_contrast ?? data.prefs.highContrast,
          reducedMotion: data.prefs.reduced_motion ?? data.prefs.reducedMotion,
          notifyStreak: data.prefs.notify_streak ?? data.prefs.notifyStreak,
          notifyPuzzle: data.prefs.notify_puzzle ?? data.prefs.notifyPuzzle,
          notifyFact: data.prefs.notify_fact ?? data.prefs.notifyFact,
          grade: data.prefs.grade,
          section: data.prefs.section,
          dropMath: data.prefs.drop_math ?? data.prefs.dropMath,
          dropScience: data.prefs.drop_science ?? data.prefs.dropScience,
          elective: data.prefs.elective,
          electiveLocked: data.prefs.electiveLocked ?? !!data.prefs.elective,
          allowedElectives: data.prefs.allowedElectives,
          derivedSubjects: data.prefs.derivedSubjects || data.prefs.derived_subjects,
          ageBand: data.prefs.ageBand || data.prefs.age_band,
        });
        try {
          if (payload.section) localStorage.setItem("sb_section", payload.section);
        } catch (_) {}
        if (typeof window.loadUpcomingExams === "function") {
          window.loadUpcomingExams();
        }
      }
      if (!quiet) toast("Preferences saved", "success");
      if (!quiet) await loadSummary(true);
    } catch (e) {
      if (!quiet) toast(e.message, "warn");
    }
  }

  function wireUi() {
    wirePlannerUi();
    document.getElementById("nav-streak-chip")?.addEventListener("click", openStreakModal);
    document.getElementById("nav-shop-chip")?.addEventListener("click", openShopModal);
    document.getElementById("nav-puzzle-chip")?.addEventListener("click", () => {
      openPuzzleModal();
    });
    document.getElementById("nav-fact-chip")?.addEventListener("click", () => {
      openFactModal();
    });
    document.getElementById("profile-daily-puzzle-btn")?.addEventListener("click", () => {
      try {
        const wrap = document.getElementById("profile-menu-wrap");
        const btn = document.getElementById("profile-avatar-btn");
        if (wrap) wrap.classList.remove("open");
        if (btn) btn.setAttribute("aria-expanded", "false");
      } catch (_) {}
      openPuzzleModal();
    });
    document.getElementById("profile-daily-fact-btn")?.addEventListener("click", () => {
      try {
        const wrap = document.getElementById("profile-menu-wrap");
        const btn = document.getElementById("profile-avatar-btn");
        if (wrap) wrap.classList.remove("open");
        if (btn) btn.setAttribute("aria-expanded", "false");
      } catch (_) {}
      openFactModal();
    });
    document.getElementById("settings-open-shop")?.addEventListener("click", openShopModal);
    document.getElementById("elective-pick-save")?.addEventListener("click", saveElectivePick);

    bindModalClose("streak-modal");
    bindModalClose("puzzle-modal");
    bindModalClose("fact-modal");
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
    Focus.ensureAwayOverlay();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && Focus.running) Focus.onLeftTab();
    });
    window.addEventListener("pagehide", () => {
      if (Focus.running) Focus.onLeftTab();
    });
    window.addEventListener("beforeunload", (e) => {
      if (!Focus.running) return;
      e.preventDefault();
      e.returnValue = "Focus timer is running. Stay on this tab.";
    });

    document.getElementById("settings-save-prefs")?.addEventListener("click", savePrefsFromSettings);
    document.getElementById("settings-language")?.addEventListener("change", (e) => {
      const v = e.target.value || "multi";
      try { localStorage.setItem("sb_reply_language", v); } catch (_) {}
      const toolsLang = document.getElementById("tools-reply-language");
      if (toolsLang) toolsLang.value = v;
      // Persist immediately so Multilingual is not overwritten by a later prefs reload
      if (isLoggedIn()) {
        savePrefsFromSettings({ quiet: true }).catch(() => {});
      }
    });
    document.getElementById("tools-reply-language")?.addEventListener("change", (e) => {
      const v = e.target.value || "multi";
      try { localStorage.setItem("sb_reply_language", v); } catch (_) {}
      const settingsLang = document.getElementById("settings-language");
      if (settingsLang) settingsLang.value = v;
      if (isLoggedIn()) {
        savePrefsFromSettings({ quiet: true }).catch(() => {});
      }
    });
    document.getElementById("settings-grade")?.addEventListener("change", (e) => {
      const g = String(e.target.value || "9");
      try { localStorage.setItem("sb_grade", g); } catch (_) {}
      if (isLoggedIn()) {
        savePrefsFromSettings({ quiet: true }).catch(() => {});
      }
    });
    document.getElementById("settings-age-band")?.addEventListener("change", (e) => {
      const band = String(e.target.value || "14-16");
      try { localStorage.setItem("sb_age_band", band); } catch (_) {}
      if (isLoggedIn()) {
        savePrefsFromSettings({ quiet: true }).catch(() => {});
      }
    });
    ["settings-section", "settings-drop-math", "settings-drop-science"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => {
        const sec = document.getElementById("settings-section")?.value || "";
        const wrap = document.getElementById("settings-super3-drops");
        if (wrap) wrap.style.display = sec === "Super 3" ? "block" : "none";
        if (sec !== "Super 3") {
          const dm = document.getElementById("settings-drop-math");
          const ds = document.getElementById("settings-drop-science");
          if (dm) dm.checked = false;
          if (ds) ds.checked = false;
        }
        syncElectiveSettingsUi({
          section: sec,
          dropMath: sec === "Super 3" && !!document.getElementById("settings-drop-math")?.checked,
          dropScience: sec === "Super 3" && !!document.getElementById("settings-drop-science")?.checked,
          elective: document.getElementById("settings-elective")?.disabled
            ? document.getElementById("settings-elective")?.value
            : "",
          electiveLocked: !!document.getElementById("settings-elective")?.disabled,
        });
        if (isLoggedIn()) {
          savePrefsFromSettings({ quiet: true }).catch(() => {});
        }
      });
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
    refreshPlanner: () => refreshPlanner({ autoSync: true }),
    refreshStudyToday,
    openStudyAction,
    onStudyPracticeComplete,
    isEducationalQuestion,
    onLogin() {
      summaryGen++;
      summary = null;
      summaryInflight = null;
      electivePromptShown = false;
      renderNavbarGuest();
      loadSummary(true);
      refreshStudyToday();
    },
    onLogout() {
      summaryGen++;
      summary = null;
      summaryInflight = null;
      electivePromptShown = false;
      renderNavbarGuest();
      renderStudyToday([]);
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
        refreshStudyToday();
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
