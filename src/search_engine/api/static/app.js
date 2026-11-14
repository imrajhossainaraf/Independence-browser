/**
 * Independent Search Engine - Thin Web Client (Step 23, Milestone M2)
 *
 * Invariants:
 * 1. Zero third-party requests or external CDNs.
 * 2. Invariant 12 XSS defense: Strict textContent / createTextNode rendering only.
 * 3. Total uncompressed JavaScript under 50 KB.
 * 4. WAI-ARIA 1.2 compliant Combobox pattern for suggestions.
 * 5. Full offline and graceful-degradation awareness.
 */

(function () {
  "use strict";

  // Configuration and state
  const config = {
    baseUrl: (window.SEARCH_ENGINE_BASE_URL || "").replace(/\/+$/, ""),
    apiKeySessionKey: "se_bearer_key",
    pageSize: 10,
  };

  const state = {
    currentView: "home", // "home" | "results"
    query: "",
    page: 1,
    docType: "",
    selectedSuggestionIndex: -1,
    suggestions: [],
    suggestTimer: null,
  };

  // DOM Elements
  const el = {
    searchInput: document.getElementById("search-input"),
    searchForm: document.getElementById("search-form"),
    suggestionsList: document.getElementById("suggestions-list"),
    brandLink: document.getElementById("brand-link"),
    freshnessIndicator: document.getElementById("freshness-indicator"),
    staleBanner: document.getElementById("stale-banner"),
    staleMessage: document.getElementById("stale-message"),
    errorBanner: document.getElementById("error-banner"),
    errorMessage: document.getElementById("error-message"),
    errorDismiss: document.getElementById("error-dismiss"),
    loadingIndicator: document.getElementById("loading-indicator"),

    homeView: document.getElementById("home-view"),
    topStoriesGrid: document.getElementById("top-stories-grid"),
    topStoriesMeta: document.getElementById("top-stories-meta"),
    categoryNav: document.getElementById("category-nav"),
    categoryPills: document.getElementById("category-pills"),
    latestNewsList: document.getElementById("latest-news-list"),
    sourcesHealthList: document.getElementById("sources-health-list"),

    resultsView: document.getElementById("results-view"),
    resultsMeta: document.getElementById("results-meta"),
    didYouMeanContainer: document.getElementById("did-you-mean-container"),
    didYouMeanBtn: document.getElementById("did-you-mean-btn"),
    typeFilterNav: document.querySelector(".type-filter-nav"),
    synopsisBox: document.getElementById("synopsis-box"),
    synopsisText: document.getElementById("synopsis-text"),
    synopsisLink: document.getElementById("synopsis-link"),
    synopsisConfidence: document.getElementById("synopsis-confidence"),
    searchResultsList: document.getElementById("search-results-list"),
    emptyResults: document.getElementById("empty-results"),
    searchRecentNews: document.getElementById("search-recent-news"),
    recentNewsItems: document.getElementById("recent-news-items"),
    sidebarContainer: document.getElementById("sidebar-container"),
    previewCard: document.getElementById("preview-card"),
    previewTitle: document.getElementById("preview-title"),
    previewDomain: document.getElementById("preview-domain"),
    previewLead: document.getElementById("preview-lead"),
    previewUrl: document.getElementById("preview-url"),
    previewSource: document.getElementById("preview-source"),
    paginationNav: document.getElementById("pagination-nav"),
    prevPageBtn: document.getElementById("prev-page-btn"),
    nextPageBtn: document.getElementById("next-page-btn"),
    pageIndicator: document.getElementById("page-indicator"),

    settingsBtn: document.getElementById("settings-btn"),
    settingsDialog: document.getElementById("settings-dialog"),
    settingsClose: document.getElementById("settings-close"),
    settingsSave: document.getElementById("settings-save"),
    apiKeyInput: document.getElementById("api-key-input"),
  };

  // Safe DOM construction helper functions (Strict XSS defense)
  function createEl(tag, className, textContent) {
    const elem = document.createElement(tag);
    if (className) elem.className = className;
    if (textContent !== undefined && textContent !== null) {
      elem.textContent = String(textContent);
    }
    return elem;
  }

  function clearElement(element) {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  }

  function formatRelativeTime(isoString) {
    if (!isoString) return "";
    try {
      const date = new Date(isoString);
      const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
      if (diffSec < 60) return "just now";
      if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
      if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
      return `${Math.floor(diffSec / 86400)}d ago`;
    } catch {
      return "";
    }
  }

  function getAuthHeader() {
    const key = sessionStorage.getItem(config.apiKeySessionKey);
    return key ? { Authorization: `Bearer ${key.trim()}` } : {};
  }

  async function apiFetch(endpoint) {
    const url = `${config.baseUrl}${endpoint}`;
    const headers = {
      Accept: "application/json",
      ...getAuthHeader(),
    };

    const resp = await fetch(url, { headers });
    if (!resp.ok) {
      let code = "error";
      let message = `Server returned status ${resp.status}`;
      try {
        const errJson = await resp.json();
        if (errJson && errJson.error) {
          code = errJson.error.code || code;
          message = errJson.error.message || message;
        }
      } catch {
        // Fallback to status text
      }
      const err = new Error(message);
      err.code = code;
      err.status = resp.status;
      throw err;
    }
    return resp.json();
  }

  function showError(message) {
    el.errorMessage.textContent = message;
    el.errorBanner.hidden = false;
  }

  function hideError() {
    el.errorBanner.hidden = true;
    el.errorMessage.textContent = "";
  }

  function showLoading(show) {
    el.loadingIndicator.hidden = !show;
  }

  // View management
  function switchView(viewName) {
    state.currentView = viewName;
    if (viewName === "home") {
      el.homeView.hidden = false;
      el.resultsView.hidden = true;
      document.title = "Independent Search";
    } else {
      el.homeView.hidden = true;
      el.resultsView.hidden = false;
      document.title = `${state.query} - Independent Search`;
    }
    hideError();
  }

  // 1. HOME VIEW LOGIC
  async function loadHome() {
    showLoading(true);
    switchView("home");
    try {
      const data = await apiFetch("/api/v1/home");
      renderHome(data);
    } catch (err) {
      showError(`Unable to load home page: ${err.message}`);
    } finally {
      showLoading(false);
    }
  }

  function renderHome(data) {
    // 1. Freshness and Stale handling
    if (data.is_stale) {
      el.staleBanner.hidden = false;
      el.staleMessage.textContent =
        `Warning: Search index was last updated ${Math.floor(data.data_age_seconds / 3600)}h ago. News sources may be delayed.`;
    } else {
      el.staleBanner.hidden = true;
    }

    if (data.data_age_seconds !== null && data.data_age_seconds !== undefined) {
      el.freshnessIndicator.hidden = false;
      if (data.data_age_seconds < 3600) {
        el.freshnessIndicator.textContent = `Updated ${Math.floor(data.data_age_seconds / 60)}m ago`;
        el.freshnessIndicator.className = "freshness-pill";
      } else {
        el.freshnessIndicator.textContent = `Updated ${Math.floor(data.data_age_seconds / 3600)}h ago`;
        el.freshnessIndicator.className = "freshness-pill warning";
      }
    }

    // 2. Top Stories Grid
    clearElement(el.topStoriesGrid);
    if (data.top_stories && data.top_stories.length > 0) {
      el.topStoriesMeta.textContent = `${data.top_stories.length} clustered stories across independent sources`;
      data.top_stories.forEach((story) => {
        const card = createEl("article", "card story-card");

        const title = createEl("h3", "story-card-title");
        const link = createEl("a", null, story.title);
        link.href = story.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        title.appendChild(link);
        card.appendChild(title);

        if (story.summary) {
          card.appendChild(createEl("p", "story-summary", story.summary));
        }

        const footer = createEl("div", "story-footer");
        const sourceSpan = createEl("span", "source-tag", story.primary_source);
        const timeSpan = createEl("span", "time-tag", formatRelativeTime(story.published_at));
        footer.appendChild(sourceSpan);
        if (story.sources_count > 1) {
          const badge = createEl("span", "cluster-badge", `+${story.sources_count - 1} other source(s)`);
          footer.appendChild(badge);
        }
        footer.appendChild(timeSpan);
        card.appendChild(footer);

        el.topStoriesGrid.appendChild(card);
      });
    } else {
      el.topStoriesMeta.textContent = "No top stories clustered yet.";
      el.topStoriesGrid.appendChild(
        createEl("p", "empty-text", "News feeds are actively being ingested into the local repository.")
      );
    }

    // 3. Category Pills
    clearElement(el.categoryPills);
    if (data.categories && data.categories.length > 0) {
      el.categoryNav.hidden = false;
      data.categories.forEach((cat) => {
        const pill = createEl("button", "category-pill", cat);
        pill.type = "button";
        pill.addEventListener("click", () => {
          el.searchInput.value = `category:${cat}`;
          executeSearch(`category:${cat}`, 1);
        });
        el.categoryPills.appendChild(pill);
      });
    } else {
      el.categoryNav.hidden = true;
    }

    // 4. Latest News List
    clearElement(el.latestNewsList);
    if (data.latest && data.latest.length > 0) {
      data.latest.forEach((item) => {
        const row = createEl("div", "news-item-row");
        const titleLink = createEl("a", "news-item-title", item.title);
        titleLink.href = item.url;
        titleLink.target = "_blank";
        titleLink.rel = "noopener noreferrer";
        row.appendChild(titleLink);

        const meta = createEl("div", "news-item-meta");
        const sourceName = item.source_name || "News";
        meta.textContent = `${sourceName} • ${formatRelativeTime(item.published_at)}`;
        row.appendChild(meta);

        el.latestNewsList.appendChild(row);
      });
    } else {
      el.latestNewsList.appendChild(createEl("p", "empty-text", "No recent headlines available."));
    }

    // 5. Source Health List
    clearElement(el.sourcesHealthList);
    if (data.sources_health && data.sources_health.length > 0) {
      data.sources_health.forEach((src) => {
        const badge = createEl("div", `health-badge ${src.status}`);
        const icon = createEl("span", null, src.status === "healthy" ? "● " : "▲ ");
        const text = createEl("span", null, `${src.name} (${src.status})`);
        badge.appendChild(icon);
        badge.appendChild(text);
        el.sourcesHealthList.appendChild(badge);
      });
    }
  }

  // 2. SEARCH RESULTS VIEW LOGIC
  async function executeSearch(queryStr, pageNum, docType) {
    const q = queryStr.trim();
    if (!q) {
      loadHome();
      return;
    }

    state.query = q;
    state.page = pageNum || 1;
    state.docType = docType || "";

    el.searchInput.value = q;
    showLoading(true);
    switchView("results");

    // Sync URL with browser history
    const urlParams = new URLSearchParams();
    urlParams.set("q", q);
    if (state.page > 1) urlParams.set("page", String(state.page));
    if (state.docType) urlParams.set("type", state.docType);
    const newRelativeUrl = `/?${urlParams.toString()}`;
    if (window.location.search !== `?${urlParams.toString()}`) {
      window.history.pushState({ q, page: state.page, docType: state.docType }, "", newRelativeUrl);
    }

    try {
      let endpoint = `/api/v1/search?q=${encodeURIComponent(q)}&page=${state.page}&page_size=${config.pageSize}`;
      if (state.docType) {
        endpoint += `&type=${encodeURIComponent(state.docType)}`;
      }
      const data = await apiFetch(endpoint);
      renderSearchResults(data);
    } catch (err) {
      if (err.code === "index_unavailable") {
        showError("Search index is currently unavailable. Run 'search-engine index rebuild'.");
      } else if (err.code === "storage_busy") {
        showError("Database is temporarily busy. Please retry shortly.");
      } else {
        showError(`Search error: ${err.message}`);
      }
    } finally {
      showLoading(false);
    }
  }

  function renderSearchResults(data) {
    // 1. Result summary metadata
    const total = data.total_approx || 0;
    const mode = data.match_mode || "standard";
    el.resultsMeta.textContent = `Found approximately ${total} results (mode: ${mode})`;

    // 2. Did You Mean
    if (data.suggestion && data.suggestion !== state.query) {
      el.didYouMeanContainer.hidden = false;
      el.didYouMeanBtn.textContent = data.suggestion;
      el.didYouMeanBtn.onclick = () => {
        executeSearch(data.suggestion, 1, state.docType);
      };
    } else {
      el.didYouMeanContainer.hidden = true;
    }

    // 3. Document Type Filter Tabs
    const tabs = el.typeFilterNav.querySelectorAll(".filter-tab");
    tabs.forEach((tab) => {
      const typeVal = tab.getAttribute("data-type") || "";
      if (typeVal === state.docType) {
        tab.classList.add("active");
      } else {
        tab.classList.remove("active");
      }
    });

    // 4. Grounded Synopsis Excerpt Card (100% Verbatim Substring Guarantee)
    if (data.synopsis && data.synopsis.text) {
      el.synopsisBox.hidden = false;
      el.synopsisText.textContent = `"${data.synopsis.text}"`;
      el.synopsisLink.textContent = data.synopsis.source_name || data.synopsis.url;
      el.synopsisLink.href = data.synopsis.url;
      const confPct = Math.round(data.synopsis.confidence * 100);
      el.synopsisConfidence.textContent = `${confPct}% match`;
    } else {
      el.synopsisBox.hidden = true;
    }

    // 5. Organic Search Results List
    clearElement(el.searchResultsList);
    if (data.results && data.results.length > 0) {
      el.emptyResults.hidden = true;
      data.results.forEach((item) => {
        const li = createEl("li", "result-item");

        const urlDiv = createEl("div", "result-url", item.url);
        li.appendChild(urlDiv);

        const titleH3 = createEl("h3", "result-title");
        const a = createEl("a", null, item.title || item.url);
        a.href = item.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        titleH3.appendChild(a);
        li.appendChild(titleH3);

        // Highlight snippet rendering safely via DOM nodes
        const snippetP = createEl("p", "result-snippet");
        if (Array.isArray(item.snippet) && item.snippet.length > 0) {
          item.snippet.forEach((span) => {
            if (span.is_match) {
              const mark = createEl("mark", "highlight", span.text);
              snippetP.appendChild(mark);
            } else {
              snippetP.appendChild(document.createTextNode(span.text));
            }
          });
        } else if (item.snippet_text) {
          snippetP.textContent = item.snippet_text;
        } else if (item.description) {
          snippetP.textContent = item.description;
        }
        li.appendChild(snippetP);

        const metaDiv = createEl("div", "result-meta");
        const dateStr = formatRelativeTime(item.published_at);
        const docTypeStr = item.doc_type ? `[${item.doc_type.toUpperCase()}]` : "";
        metaDiv.textContent = [docTypeStr, item.host, dateStr].filter(Boolean).join(" • ");
        li.appendChild(metaDiv);

        el.searchResultsList.appendChild(li);
      });
    } else {
      el.emptyResults.hidden = false;
    }

    // 6. Recent News in Search Block (past 7 days)
    clearElement(el.recentNewsItems);
    if (data.recent_news && data.recent_news.length > 0) {
      el.searchRecentNews.hidden = false;
      data.recent_news.forEach((news) => {
        const row = createEl("div", "news-item-row");
        const a = createEl("a", "news-item-title", news.title);
        a.href = news.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        row.appendChild(a);

        const meta = createEl("div", "news-item-meta");
        meta.textContent = `${news.source_name || "News"} • ${formatRelativeTime(news.published_at)}`;
        row.appendChild(meta);

        el.recentNewsItems.appendChild(row);
      });
    } else {
      el.searchRecentNews.hidden = true;
    }

    // 7. Sidebar Preview Card
    if (data.panel && data.panel.kind === "preview") {
      el.sidebarContainer.hidden = false;
      el.previewTitle.textContent = data.panel.title;
      el.previewDomain.textContent = data.panel.domain;
      el.previewLead.textContent = data.panel.lead;
      el.previewUrl.href = data.panel.url;
      el.previewSource.textContent = data.panel.source_name;
    } else {
      el.sidebarContainer.hidden = true;
    }

    // 8. Pagination Navigation
    if (total > config.pageSize) {
      el.paginationNav.hidden = false;
      const totalPages = Math.ceil(total / config.pageSize);
      el.pageIndicator.textContent = `Page ${state.page} of ${totalPages}`;
      el.prevPageBtn.disabled = state.page <= 1;
      el.nextPageBtn.disabled = state.page >= totalPages;
    } else {
      el.paginationNav.hidden = true;
    }
  }

  // 3. WAI-ARIA AUTOCOMPLETE COMBOBOX PATTERN
  async function fetchSuggestions(prefix) {
    if (!prefix || prefix.length < 2) {
      closeSuggestions();
      return;
    }
    try {
      const data = await apiFetch(`/api/v1/suggest?q=${encodeURIComponent(prefix)}&limit=8`);
      state.suggestions = data.suggestions || [];
      renderSuggestions();
    } catch {
      // Suggestion endpoint is optional or disabled by flag
      closeSuggestions();
    }
  }

  function renderSuggestions() {
    clearElement(el.suggestionsList);
    state.selectedSuggestionIndex = -1;

    if (state.suggestions.length === 0) {
      closeSuggestions();
      return;
    }

    state.suggestions.forEach((item, idx) => {
      const li = createEl("li", "suggestion-item", item);
      li.id = `suggest-opt-${idx}`;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.addEventListener("click", () => {
        el.searchInput.value = item;
        closeSuggestions();
        executeSearch(item, 1, state.docType);
      });
      el.suggestionsList.appendChild(li);
    });

    el.suggestionsList.hidden = false;
    el.searchInput.setAttribute("aria-expanded", "true");
  }

  function closeSuggestions() {
    el.suggestionsList.hidden = true;
    el.searchInput.setAttribute("aria-expanded", "false");
    el.searchInput.removeAttribute("aria-activedescendant");
    state.selectedSuggestionIndex = -1;
  }

  function selectSuggestion(index) {
    const items = el.suggestionsList.querySelectorAll(".suggestion-item");
    if (items.length === 0) return;

    items.forEach((item, idx) => {
      if (idx === index) {
        item.setAttribute("aria-selected", "true");
        el.searchInput.setAttribute("aria-activedescendant", item.id);
        el.searchInput.value = item.textContent;
      } else {
        item.setAttribute("aria-selected", "false");
      }
    });
  }

  // Event Listeners Initialization
  function initEvents() {
    // Search form submission
    el.searchForm.addEventListener("submit", (e) => {
      e.preventDefault();
      closeSuggestions();
      executeSearch(el.searchInput.value, 1, state.docType);
    });

    // Autocomplete input debouncing
    el.searchInput.addEventListener("input", (e) => {
      clearTimeout(state.suggestTimer);
      const val = e.target.value.trim();
      if (val.length >= 2) {
        state.suggestTimer = setTimeout(() => {
          fetchSuggestions(val);
        }, 150);
      } else {
        closeSuggestions();
      }
    });

    // Keyboard navigation in combobox (ArrowDown, ArrowUp, Escape)
    el.searchInput.addEventListener("keydown", (e) => {
      const count = state.suggestions.length;
      if (el.suggestionsList.hidden || count === 0) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        state.selectedSuggestionIndex = (state.selectedSuggestionIndex + 1) % count;
        selectSuggestion(state.selectedSuggestionIndex);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        state.selectedSuggestionIndex =
          (state.selectedSuggestionIndex - 1 + count) % count;
        selectSuggestion(state.selectedSuggestionIndex);
      } else if (e.key === "Escape") {
        closeSuggestions();
      }
    });

    // Close suggestions when clicking outside
    document.addEventListener("click", (e) => {
      if (!el.searchForm.contains(e.target)) {
        closeSuggestions();
      }
    });

    // Brand link returns to home
    el.brandLink.addEventListener("click", (e) => {
      e.preventDefault();
      el.searchInput.value = "";
      state.query = "";
      state.page = 1;
      state.docType = "";
      window.history.pushState({}, "", "/");
      loadHome();
    });

    // Filter tabs
    el.typeFilterNav.addEventListener("click", (e) => {
      const tab = e.target.closest(".filter-tab");
      if (!tab) return;
      const typeVal = tab.getAttribute("data-type") || "";
      if (typeVal !== state.docType) {
        executeSearch(state.query, 1, typeVal);
      }
    });

    // Pagination
    el.prevPageBtn.addEventListener("click", () => {
      if (state.page > 1) {
        executeSearch(state.query, state.page - 1, state.docType);
      }
    });

    el.nextPageBtn.addEventListener("click", () => {
      executeSearch(state.query, state.page + 1, state.docType);
    });

    // Error dismiss
    el.errorDismiss.addEventListener("click", hideError);

    // Settings modal
    el.settingsBtn.addEventListener("click", () => {
      const currentKey = sessionStorage.getItem(config.apiKeySessionKey) || "";
      el.apiKeyInput.value = currentKey;
      el.settingsDialog.hidden = false;
    });

    el.settingsClose.addEventListener("click", () => {
      el.settingsDialog.hidden = true;
    });

    el.settingsSave.addEventListener("click", () => {
      const val = el.apiKeyInput.value.trim();
      if (val) {
        sessionStorage.setItem(config.apiKeySessionKey, val);
      } else {
        sessionStorage.removeItem(config.apiKeySessionKey);
      }
      el.settingsDialog.hidden = true;
      if (state.currentView === "home") {
        loadHome();
      } else {
        executeSearch(state.query, state.page, state.docType);
      }
    });

    // Browser back/forward navigation
    window.addEventListener("popstate", (e) => {
      const params = new URLSearchParams(window.location.search);
      const q = params.get("q");
      const page = parseInt(params.get("page") || "1", 10);
      const type = params.get("type") || "";
      if (q) {
        executeSearch(q, page, type);
      } else {
        loadHome();
      }
    });
  }

  // Application bootstrap
  function init() {
    initEvents();

    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    const page = parseInt(params.get("page") || "1", 10);
    const type = params.get("type") || "";

    if (q) {
      executeSearch(q, page, type);
    } else {
      loadHome();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
