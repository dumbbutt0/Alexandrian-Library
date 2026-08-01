"use strict";

const state = {
  catalog: null,
  selectedBook: null,
  selectedIndex: -1,
  inspecting: false,
  transitioning: false,
  blocks: [],
  pages: [],
  pageIndex: 0,
  plain: false,
  headings: [],
  workflow: null,
  workflows: [],
  workflowBusy: false,
  worktableLoaded: false,
  readiness: null,
  provenance: null,
  readerBookId: null,
  readerBookHash: null,
};

const VISUAL_REFERENCE_BOOKS = [
  {
    book_id: "reference-apprenticeship-mirror",
    edition: "REF",
    title: "The Apprenticeship Mirror: Building AI That Makes Itself Less Necessary",
    central_question: "Can an AI system increase a learner's independent ability while deliberately reducing the assistance it provides?",
    book_mode: "visual_reference",
    chapter_count: 0,
    source_count: 0,
    visual_extent: 0.48,
    reference_only: true,
  },
  {
    book_id: "reference-witness-burden",
    edition: "REF",
    title: "Witness Burden: Memory, Intervention, and the Birth of Authority",
    central_question: "When one agent alone remembers repeated harm, what authority does that memory give it over agents entering the world without that history?",
    book_mode: "visual_reference",
    chapter_count: 0,
    source_count: 0,
    visual_extent: 0.86,
    reference_only: true,
  },
  {
    book_id: "reference-humming-protocols",
    edition: "REF",
    title: "Humming Protocols: When Sound Becomes Shared Meaning",
    central_question: "Under what conditions does patterned acoustic behavior become a stable, transferable communication system?",
    book_mode: "visual_reference",
    chapter_count: 0,
    source_count: 0,
    visual_extent: 0.42,
    reference_only: true,
  },
  {
    book_id: "reference-kaleidoscope-time",
    edition: "REF",
    title: "The Kaleidoscope of Time: Mapping Experience into Behavioral Change",
    central_question: "Which small experiences become butterfly points that redirect an intelligent agent's later decisions?",
    book_mode: "visual_reference",
    chapter_count: 0,
    source_count: 0,
    visual_extent: 0.68,
    reference_only: true,
  },
];
const SHELF_DISPLAY_MINIMUM = 5;
const DEMO_VISUAL_REFERENCES = new URLSearchParams(window.location.search)
  .get("demo") === "1";

const views = {
  library: document.querySelector("#library-view"),
  reader: document.querySelector("#reader-view"),
  worktable: document.querySelector("#worktable-view"),
};

const shelf = document.querySelector("#bookshelf");
const inspector = document.querySelector("#book-inspector");
const shelfBookTemplate = document.querySelector("#shelf-book-template");
const leftPage = document.querySelector("#left-page");
const rightPage = document.querySelector("#right-page");
const openBook = document.querySelector("#open-book");
const previousButton = document.querySelector("#previous-page");
const nextButton = document.querySelector("#next-page");
const pagePosition = document.querySelector("#page-position");
const tocPanel = document.querySelector("#toc-panel");
const tocList = document.querySelector("#toc-list");
const tocToggle = document.querySelector("#toc-toggle");
const plainToggle = document.querySelector("#plain-toggle");
const evidenceToggle = document.querySelector("#evidence-toggle");
const provenancePanel = document.querySelector("#provenance-panel");
const provenanceList = document.querySelector("#provenance-list");
const previousVolumeButton = document.querySelector("#previous-volume");
const nextVolumeButton = document.querySelector("#next-volume");
const catalogTrack = document.querySelector("#catalog-track");
const catalogEnd = document.querySelector("#catalog-end");
const bookshelfViewport = document.querySelector(".bookshelf-viewport");
const shelfRoom = document.querySelector(".shelf-room");
const bookFocusStage = document.querySelector("#book-focus-stage");
const newInquiryButton = document.querySelector("#new-inquiry-button");
const newInquiryPanel = document.querySelector("#new-inquiry-panel");
const activeWorkflowPanel = document.querySelector("#active-workflow-panel");
const workflowList = document.querySelector("#workflow-list");
const workflowConsoleKicker = document.querySelector("#workflow-console-kicker");
const workflowConsoleTitle = document.querySelector("#workflow-console-title");
const workflowStageBadge = document.querySelector("#workflow-stage-badge");
const workflowStageLabel = document.querySelector("#workflow-stage-label");
const conversationTranscript = document.querySelector("#conversation-transcript");
const conversationComposer = document.querySelector("#conversation-composer");
const turnInput = document.querySelector("#turn-input");
const workflowActionZone = document.querySelector("#workflow-action-zone");
const workflowStatus = document.querySelector("#workflow-status");

function routeTo(route) {
  Object.entries(views).forEach(([name, element]) => {
    element.classList.toggle("is-active", name === route);
  });
  document.querySelectorAll("[data-route]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.route === route);
  });
  tocPanel.classList.remove("is-open");
  tocToggle.setAttribute("aria-expanded", "false");
  if (route !== "reader") hideProvenance();
  if (route === "library") {
    document.title = "The Library — Inquirion Labs";
  } else if (route === "worktable") {
    document.title = "The Worktable — Inquirion Labs";
    initializeWorktable();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || `Request failed (${response.status})`);
    error.code = payload.error || "request_failed";
    error.status = response.status;
    throw error;
  }
  return payload;
}

function bookPalette(index) {
  const palettes = [
    ["#37152c", "#10172e"],
    ["#14323b", "#111321"],
    ["#352b18", "#161018"],
    ["#21153f", "#121723"],
    ["#32201b", "#111b24"],
  ];
  return palettes[index % palettes.length];
}

function normalizedBookHeight(book) {
  const declaredExtent = Number(book.visual_extent);
  const chapterSignal = Math.min(1, Math.max(0, Number(book.chapter_count) / 8));
  const titleSignal = Math.min(1, book.title.length / 96);
  const extent = Number.isFinite(declaredExtent)
    ? Math.min(1, Math.max(0, declaredExtent))
    : chapterSignal * 0.65 + titleSignal * 0.35;
  return Math.round(310 + extent * 28);
}

function normalizedBookDepth(book) {
  const declaredExtent = Number(book.visual_extent);
  const chapterSignal = Math.min(1, Math.max(0, Number(book.chapter_count) / 8));
  const extent = Number.isFinite(declaredExtent)
    ? Math.min(1, Math.max(0, declaredExtent))
    : chapterSignal;
  return Math.round(58 + extent * 16);
}

function coverAssetForBook(book) {
  if (book.title.startsWith("Branches Across the Boundary")) {
    return "/assets/branches-cover-holy-cyber-v3.png";
  }
  return "";
}

function catalogWithVisualReferences(catalog) {
  if (!DEMO_VISUAL_REFERENCES) {
    return {
      ...catalog,
      verified_book_count: catalog.book_count,
      reference_book_count: 0,
    };
  }
  const fillerCount = Math.max(
    0,
    SHELF_DISPLAY_MINIMUM - catalog.books.length,
  );
  const visualReferences = VISUAL_REFERENCE_BOOKS.slice(0, fillerCount);
  return {
    ...catalog,
    verified_book_count: catalog.book_count,
    reference_book_count: visualReferences.length,
    book_count: catalog.books.length + visualReferences.length,
    books: [...catalog.books, ...visualReferences],
  };
}

function renderClosedShelfInspector() {
  inspector.replaceChildren();
  const position = createElement("div", "catalog-index");
  position.setAttribute("aria-label", "Catalog position");
  position.append(
    createElement("span", "", "—"),
    createElement("i"),
    createElement(
      "span",
      "",
      state.catalog?.books.length
        ? String(state.catalog.books.length).padStart(2, "0")
        : "—",
    ),
  );
  inspector.append(
    position,
    createElement("p", "eyebrow", "The shelf is closed"),
    createElement("h2", "", "Choose a spine"),
    createElement(
      "p",
      "",
      "Every finalized inquiry waits here as a closed volume. Select a spine to bring its verified cover and record forward.",
    ),
  );
}

function selectedShelfBook() {
  if (state.selectedIndex < 0) return null;
  return shelf.querySelector(`[data-book-index="${state.selectedIndex}"]`);
}

function syncVolumeNavigation() {
  const locked = state.transitioning;
  previousVolumeButton.disabled = locked || state.selectedIndex <= 0;
  nextVolumeButton.disabled = locked
    || state.selectedIndex >= state.catalog.books.length - 1;
  document.querySelectorAll(".catalog-tick").forEach((item) => {
    item.disabled = locked;
  });
}

function renderSelectedBookInspector(book, index) {
  inspector.replaceChildren();
  const position = createElement("div", "catalog-index");
  position.setAttribute("aria-label", "Catalog position");
  position.append(
    createElement("span", "", String(index + 1).padStart(2, "0")),
    createElement("i"),
    createElement("span", "", String(state.catalog.books.length).padStart(2, "0")),
  );
  const confirmation = createElement("p", "selection-confirmation");
  confirmation.append(createElement("span", "selection-pulse"));
  confirmation.append(document.createTextNode(
    state.inspecting
      ? " In focus · drag to inspect every side"
      : book.reference_only
        ? " Highlighted on the reference shelf"
        : " Highlighted on the enduring shelf",
  ));
  const eyebrow = createElement(
    "p",
    "eyebrow",
    book.reference_only ? "Visual reference · not finalized" : "Verified volume",
  );
  const title = createElement("h2", "", book.title);
  const question = createElement("p", "", book.central_question);
  const meta = createElement("div", "book-meta");
  const metaLabels = book.reference_only
    ? ["Concept cover", "Reference only", "No book artifact"]
    : [
      `Edition ${book.edition}`,
      `${book.chapter_count} chapters`,
      `${book.source_count} sources`,
      book.book_mode.replaceAll("_", " "),
    ];
  metaLabels.forEach((label) => meta.append(createElement("span", "", label)));

  const actions = createElement("div", "book-actions");
  const primary = createElement("button", "primary-button");
  primary.type = "button";
  const primaryLabel = state.inspecting
    ? book.reference_only ? "Visual reference only" : "Open verified edition"
    : "Bring this book into focus";
  primary.append(document.createTextNode(primaryLabel), createElement("span", "", "→"));
  if (!state.inspecting) {
    primary.addEventListener("click", () => setInspectionMode(true));
  } else if (book.reference_only) {
    primary.disabled = true;
  } else {
    primary.addEventListener("click", () => openSelectedBook(book));
  }
  actions.append(primary);

  if (state.inspecting) {
    const returnButton = createElement("button", "inspection-return", "Return to shelf view");
    returnButton.type = "button";
    returnButton.addEventListener("click", () => setInspectionMode(false));
    actions.append(returnButton);
  }
  inspector.append(position, confirmation, eyebrow, title, question, meta, actions);
}

function centerBookInShelf(bookObject, behavior = "smooth") {
  const desiredLeft = bookObject.offsetLeft
    + bookObject.offsetWidth / 2
    - bookshelfViewport.clientWidth / 2;
  bookshelfViewport.scrollTo({
    left: Math.max(0, desiredLeft),
    behavior,
  });
  shelfRoom.scrollLeft = 0;
  const archiveFrame = shelfRoom.closest(".archive-frame");
  if (archiveFrame) archiveFrame.scrollLeft = 0;
}

function positionBookForFocus(bookObject) {
  if (!bookObject || !bookFocusStage) return;
  const stageBounds = bookFocusStage.getBoundingClientRect();
  const bookBounds = bookObject.getBoundingClientRect();
  const bookWidth = Number.parseFloat(
    getComputedStyle(bookObject).getPropertyValue("--book-width"),
  );
  const scale = Math.max(
    1,
    Math.min(
      1.52,
      stageBounds.width / bookWidth,
      stageBounds.height / bookObject.offsetHeight,
    ),
  );
  const shelfCenterX = bookBounds.left + bookBounds.width / 2;
  const shelfCenterY = bookBounds.top + bookBounds.height / 2;
  const focusX = stageBounds.left + stageBounds.width / 2
    - shelfCenterX;
  const focusY = stageBounds.top + stageBounds.height / 2
    - shelfCenterY;
  bookObject.style.setProperty("--focus-x", `${focusX}px`);
  bookObject.style.setProperty("--focus-y", `${focusY}px`);
  bookObject.style.setProperty("--focus-scale", scale.toFixed(4));
}

function recenterFocusedBook(bookObject) {
  if (!bookObject?.classList.contains("is-focused") || !bookFocusStage) return;
  const stageBounds = bookFocusStage.getBoundingClientRect();
  const rotorBounds = bookObject.querySelector(".book-rotor").getBoundingClientRect();
  const currentX = Number.parseFloat(
    bookObject.style.getPropertyValue("--focus-x"),
  ) || 0;
  const currentY = Number.parseFloat(
    bookObject.style.getPropertyValue("--focus-y"),
  ) || 0;
  bookObject.style.setProperty(
    "--focus-x",
    `${currentX + stageBounds.left + stageBounds.width / 2 - (rotorBounds.left + rotorBounds.width / 2)}px`,
  );
  bookObject.style.setProperty(
    "--focus-y",
    `${currentY + stageBounds.top + stageBounds.height / 2 - (rotorBounds.top + rotorBounds.height / 2)}px`,
  );
}

function setInspectionMode(inspecting) {
  if (!state.selectedBook || state.transitioning) return;
  const bookObject = selectedShelfBook();
  if (!bookObject) return;
  const nextState = Boolean(inspecting);
  if (nextState === state.inspecting) return;
  state.inspecting = nextState;
  shelfRoom.classList.toggle("has-focused-book", nextState);
  bookObject.setAttribute("aria-pressed", String(nextState));
  if (nextState) {
    positionBookForFocus(bookObject);
    bookObject.classList.add("is-focused");
    bookObject.setAttribute("aria-label", `${state.selectedBook.title}, in focus`);
    bookObject.title = "Drag horizontally to inspect the cover, spine, and back.";
  } else {
    bookObject.classList.remove("is-focused");
    bookObject.classList.remove("is-spinning");
    bookObject.style.setProperty("--spin-x", "0deg");
    bookObject.style.setProperty("--spin-y", "0deg");
    delete bookObject.dataset.dragged;
    bookObject.setAttribute("aria-label", `Focus ${state.selectedBook.title}`);
    bookObject.title = "Click to bring this book into focus.";
  }
  syncVolumeNavigation();
  renderSelectedBookInspector(state.selectedBook, state.selectedIndex);
}

function bindBookMotion(bookObject) {
  const verticalTiltLimit = 18;
  let animationFrame = 0;
  let dragging = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;
  let spinX = 0;
  let spinY = 0;
  const applySpin = () => {
    bookObject.style.setProperty("--spin-x", `${spinX}deg`);
    bookObject.style.setProperty("--spin-y", `${spinY}deg`);
  };
  const finishSpin = (event) => {
    if (!dragging) return;
    dragging = false;
    bookObject.classList.remove("is-spinning");
    if (bookObject.hasPointerCapture?.(event.pointerId)) {
      bookObject.releasePointerCapture(event.pointerId);
    }
    if (moved) {
      bookObject.dataset.dragged = "true";
      window.setTimeout(() => {
        if (bookObject.dataset.dragged === "true") {
          delete bookObject.dataset.dragged;
        }
      }, 250);
    }
  };
  const resetGlint = () => {
    if (dragging) return;
    window.cancelAnimationFrame(animationFrame);
    bookObject.style.setProperty("--glint-x", "50%");
    bookObject.style.setProperty("--glint-y", "35%");
  };
  bookObject.addEventListener("pointerdown", (event) => {
    if (
      !bookObject.classList.contains("is-focused")
      || event.button !== 0
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    dragging = true;
    moved = false;
    lastX = event.clientX;
    lastY = event.clientY;
    spinX = Number.parseFloat(
      bookObject.style.getPropertyValue("--spin-x"),
    ) || 0;
    spinY = Number.parseFloat(
      bookObject.style.getPropertyValue("--spin-y"),
    ) || 0;
    bookObject.classList.add("is-spinning");
    bookObject.setPointerCapture?.(event.pointerId);
  });
  bookObject.addEventListener("pointermove", (event) => {
    if (
      !bookObject.classList.contains("is-focused")
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    if (dragging) {
      const deltaX = event.clientX - lastX;
      const deltaY = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      moved ||= Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2;
      spinY += deltaX * 0.72;
      spinX = Math.max(
        -verticalTiltLimit,
        Math.min(verticalTiltLimit, spinX - deltaY * 0.22),
      );
      applySpin();
      return;
    }
    const bounds = bookObject.getBoundingClientRect();
    const horizontal = Math.max(
      0,
      Math.min(1, (event.clientX - bounds.left) / bounds.width),
    );
    const vertical = Math.max(
      0,
      Math.min(1, (event.clientY - bounds.top) / bounds.height),
    );
    window.cancelAnimationFrame(animationFrame);
    animationFrame = window.requestAnimationFrame(() => {
      bookObject.style.setProperty("--glint-x", `${horizontal * 100}%`);
      bookObject.style.setProperty("--glint-y", `${vertical * 100}%`);
    });
  });
  bookObject.addEventListener("pointerup", finishSpin);
  bookObject.addEventListener("pointercancel", finishSpin);
  bookObject.addEventListener("pointerleave", resetGlint);
  bookObject.addEventListener("blur", resetGlint);
}

function selectVolumeAt(index, options = {}) {
  const focusAfter = Boolean(options.focusAfter);
  if (
    !state.catalog?.books.length
    || state.transitioning
  ) return;
  const boundedIndex = Math.max(0, Math.min(state.catalog.books.length - 1, index));
  if (boundedIndex === state.selectedIndex) {
    if (focusAfter) setInspectionMode(true);
    return;
  }
  if (state.inspecting) setInspectionMode(false);
  const bookObject = shelf.querySelector(`[data-book-index="${boundedIndex}"]`);
  if (!bookObject) return;
  const book = state.catalog.books[boundedIndex];
  state.selectedBook = book;
  state.selectedIndex = boundedIndex;
  try {
    localStorage.setItem("alexandrian-selected-book", book.book_id);
  } catch {
    // Shelf selection is optional presentation state.
  }
  shelf.classList.add("has-selection");
  document.querySelectorAll(".shelf-book").forEach((item) => {
    const highlighted = item === bookObject;
    item.classList.toggle("is-highlighted", highlighted);
    item.setAttribute("aria-current", highlighted ? "true" : "false");
    item.setAttribute("aria-pressed", "false");
  });
  document.querySelectorAll(".catalog-tick").forEach((item, tickIndex) => {
    const selected = tickIndex === boundedIndex;
    item.classList.toggle("is-selected", selected);
    item.setAttribute("aria-current", selected ? "true" : "false");
  });
  centerBookInShelf(bookObject, focusAfter ? "auto" : "smooth");
  syncVolumeNavigation();
  renderSelectedBookInspector(book, boundedIndex);
  if (focusAfter) window.requestAnimationFrame(() => setInspectionMode(true));
}

function moveShelf(direction) {
  if (
    state.transitioning
    || !state.catalog?.books.length
  ) return;
  if (state.inspecting) setInspectionMode(false);
  const currentIndex = state.selectedIndex < 0 ? 0 : state.selectedIndex;
  selectVolumeAt(currentIndex + direction);
}

function renderShelf(catalog) {
  shelf.replaceChildren();
  state.selectedBook = null;
  state.selectedIndex = -1;
  state.inspecting = false;
  state.transitioning = false;
  shelfRoom.classList.remove("has-focused-book");
  shelf.classList.remove("has-selection");
  document.querySelector("#book-count").textContent = catalog.reference_book_count
    ? `${catalog.verified_book_count} verified · ${catalog.reference_book_count} reference`
    : `${catalog.book_count} ${catalog.book_count === 1 ? "volume" : "volumes"}`;
  catalogEnd.textContent = catalog.book_count
    ? String(catalog.book_count).padStart(2, "0")
    : "—";
  document.querySelector("#catalog-total").textContent = catalog.book_count
    ? String(catalog.book_count).padStart(2, "0")
    : "—";
  catalogTrack.replaceChildren();
  previousVolumeButton.disabled = true;
  nextVolumeButton.disabled = !catalog.books.length;
  if (!catalog.books.length) {
    shelf.append(createElement("p", "empty-shelf", "The shelf awaits its first finalized work."));
    renderClosedShelfInspector();
    return;
  }
  catalog.books.forEach((book, index) => {
    const bookObject = shelfBookTemplate.content.firstElementChild.cloneNode(true);
    const palette = bookPalette(index);
    const bookHeight = normalizedBookHeight(book);
    const bookDepth = normalizedBookDepth(book);
    bookObject.style.setProperty("--cover-a", palette[0]);
    bookObject.style.setProperty("--cover-b", palette[1]);
    bookObject.style.setProperty("--book-height", `${bookHeight}px`);
    bookObject.style.setProperty("--book-width", `${Math.round(bookHeight * 0.68)}px`);
    bookObject.style.setProperty("--book-depth", `${bookDepth}px`);
    bookObject.style.setProperty("--book-half-depth", `${bookDepth / 2}px`);
    bookObject.style.setProperty("--book-back-depth", `${bookDepth / -2}px`);
    bookObject.style.setProperty("--shelf-lean", `${(index % 3) - 1}deg`);
    const [coverTitle, ...subtitleParts] = book.title.split(":");
    bookObject.querySelector(".display-spine-title").textContent = coverTitle;
    bookObject.querySelector(".display-spine-edition").textContent =
      book.reference_only ? "REF" : `ED. ${book.edition}`;
    bookObject.querySelector(".cover-title").textContent = coverTitle;
    bookObject.querySelector(".cover-subtitle").textContent =
      subtitleParts.join(":").trim();
    bookObject.querySelector(".cover-edition").textContent =
      book.reference_only ? "VISUAL REFERENCE" : `EDITION ${book.edition}`;
    bookObject.querySelector(".back-question").textContent = book.central_question;
    bookObject.querySelector(".back-meta").textContent = book.reference_only
      ? "Concept study · visual reference · not finalized"
      : [
        `Edition ${book.edition}`,
        `${book.chapter_count} chapters`,
        `${book.source_count} sources`,
        book.book_mode.replaceAll("_", " "),
      ].join(" · ");
    const coverAsset = coverAssetForBook(book);
    const coverArt = bookObject.querySelector(".cover-art");
    if (coverAsset) {
      coverArt.src = coverAsset;
      bookObject.classList.add("has-cover-art");
    } else {
      coverArt.removeAttribute("src");
    }
    bookObject.classList.toggle("is-reference", Boolean(book.reference_only));
    bookObject.setAttribute(
      "aria-label",
      `Focus ${book.title}`,
    );
    bookObject.setAttribute("aria-pressed", "false");
    bookObject.dataset.bookIndex = String(index);
    bookObject.title = "Click to bring this book into focus.";
    bookObject.addEventListener("click", () => {
      if (state.transitioning) return;
      if (bookObject.dataset.dragged === "true") {
        delete bookObject.dataset.dragged;
        return;
      }
      if (state.selectedIndex === index) {
        setInspectionMode(true);
      } else {
        selectVolumeAt(index, { focusAfter: true });
      }
    });
    bindBookMotion(bookObject);
    shelf.append(bookObject);

    const tick = createElement("button", "catalog-tick");
    tick.type = "button";
    tick.setAttribute("aria-label", `Browse to ${book.title}`);
    tick.addEventListener("click", () => selectVolumeAt(index));
    catalogTrack.append(tick);
  });
  let restoredIndex = 0;
  try {
    const restoredId = localStorage.getItem("alexandrian-selected-book");
    const match = catalog.books.findIndex((book) => book.book_id === restoredId);
    if (match >= 0) restoredIndex = match;
  } catch {
    restoredIndex = 0;
  }
  selectVolumeAt(restoredIndex);
}

function createElement(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function evidenceIds(value) {
  return value
    .split(/[,;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function appendEvidenceText(element, text) {
  const pattern = /\[Evidence:\s*([^\]]+)\]/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    const button = createElement("button", "evidence-marker", match[0]);
    button.type = "button";
    const ids = evidenceIds(match[1]);
    button.dataset.evidenceIds = ids.join(",");
    button.setAttribute("aria-label", `Show ${ids.length} evidence record${ids.length === 1 ? "" : "s"}`);
    button.addEventListener("click", () => showProvenance(ids));
    element.append(button);
    cursor = pattern.lastIndex;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

function readerStorageKey() {
  if (!state.readerBookId || !state.readerBookHash) return null;
  return `alexandrian-reader:${state.readerBookId}:${state.readerBookHash}`;
}

function saveReaderPosition() {
  const key = readerStorageKey();
  if (!key) return;
  try {
    localStorage.setItem(
      key,
      JSON.stringify({ page_index: state.pageIndex, plain: state.plain }),
    );
  } catch {
    // Reading position is optional presentation state, never workflow authority.
  }
}

function restoreReaderPosition() {
  const key = readerStorageKey();
  if (!key) return;
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (saved && Number.isInteger(saved.page_index)) {
      state.pageIndex = Math.max(
        0,
        Math.min(state.pages.length - 1, saved.page_index),
      );
    }
    state.plain = saved?.plain === true;
  } catch {
    state.pageIndex = 0;
    state.plain = false;
  }
}

function showProvenance(filterIds = []) {
  provenancePanel.classList.add("is-open");
  provenancePanel.setAttribute("aria-hidden", "false");
  evidenceToggle.setAttribute("aria-expanded", "true");
  provenanceList.replaceChildren();
  if (!state.provenance) {
    provenanceList.append(
      createElement("p", "provenance-empty", "Verified source metadata is unavailable."),
    );
    return;
  }
  const requested = new Set(filterIds);
  const records = requested.size
    ? state.provenance.sources.filter((source) => requested.has(source.receipt_id))
    : state.provenance.sources;
  records.forEach((source) => {
    const article = createElement("article", "provenance-record");
    article.append(
      createElement("small", "", source.receipt_id),
      createElement("strong", "", source.title || source.domain || "Untitled source"),
      createElement("p", "", source.final_url || source.requested_url || "URL unavailable"),
    );
    if (source.claim_checked) {
      article.append(createElement("p", "", `Claim checked: ${source.claim_checked}`));
    }
    if (source.finding) {
      article.append(createElement("p", "", `Finding: ${source.finding}`));
    }
    if (source.limitations) {
      article.append(createElement("p", "", `Limits: ${source.limitations}`));
    }
    provenanceList.append(article);
  });
  if (!records.length) {
    provenanceList.append(
      createElement("p", "provenance-empty", "No source matched this evidence marker."),
    );
  }
}

function hideProvenance() {
  provenancePanel.classList.remove("is-open");
  provenancePanel.setAttribute("aria-hidden", "true");
  evidenceToggle.setAttribute("aria-expanded", "false");
}

function inlineText(text) {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .trim();
}

function markdownBlocks(markdown) {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let list = null;
  let code = null;
  let table = null;

  function flushParagraph() {
    if (paragraph.length) {
      blocks.push({ type: "p", text: inlineText(paragraph.join(" ")) });
      paragraph = [];
    }
  }

  function flushList() {
    if (list) {
      blocks.push(list);
      list = null;
    }
  }

  function flushTable() {
    if (!table) return;
    const rows = table.filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)));
    if (rows.length) {
      blocks.push({
        type: "table",
        headers: rows[0],
        rows: rows.slice(1),
      });
    }
    table = null;
  }

  lines.forEach((line) => {
    if (line.startsWith("```")) {
      flushParagraph();
      flushList();
      flushTable();
      if (code) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else {
        code = [];
      }
      return;
    }
    if (code) {
      code.push(line);
      return;
    }
    if (/^\|.*\|$/.test(line)) {
      flushParagraph();
      flushList();
      table ||= [];
      table.push(
        line.split("|").slice(1, -1).map((cell) => inlineText(cell)),
      );
      return;
    }
    flushTable();
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        type: `h${heading[1].length}`,
        text: inlineText(heading[2]),
      });
      return;
    }
    const listItem = line.match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
    if (listItem) {
      flushParagraph();
      const ordered = /\d+\./.test(listItem[2]);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { type: "list", ordered, items: [] };
      }
      list.items.push(inlineText(listItem[3]));
      return;
    }
    if (line.startsWith("> ")) {
      flushParagraph();
      flushList();
      blocks.push({ type: "quote", text: inlineText(line.slice(2)) });
      return;
    }
    if (/^---+$/.test(line.trim())) {
      flushParagraph();
      flushList();
      blocks.push({ type: "rule", text: "" });
      return;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      return;
    }
    paragraph.push(line.trim());
  });
  flushParagraph();
  flushList();
  flushTable();
  if (code) blocks.push({ type: "code", text: code.join("\n") });
  return blocks.filter((block) => block.text !== "" || block.type === "rule" || block.type === "list");
}

function blockWeight(block) {
  const length = block.type === "table"
    ? [...block.headers, ...block.rows.flat()].join(" ").length + block.rows.length * 70
    : block.type === "list"
    ? block.items.join(" ").length + block.items.length * 35
    : block.text.length;
  const multiplier = block.type === "h1" ? 2.4
    : block.type === "h2" ? 1.9
      : block.type === "h3" ? 1.5
        : block.type === "code" ? 1.45
          : block.type === "quote" ? 1.25
            : 1;
  return Math.max(90, length * multiplier);
}

function paginate(blocks) {
  const narrow = window.matchMedia("(max-width: 720px)").matches;
  const target = narrow
    ? 1200
    : Math.max(1050, Math.min(1650, window.innerHeight * 1.45));
  const pages = [];
  let page = [];
  let weight = 0;
  blocks.forEach((block) => {
    const nextWeight = blockWeight(block);
    const headingBreak = (block.type === "h1" || block.type === "h2") && weight > target * 0.48;
    if (page.length && (weight + nextWeight > target || headingBreak)) {
      pages.push(page);
      page = [];
      weight = 0;
    }
    page.push(block);
    weight += nextWeight;
  });
  if (page.length) pages.push(page);
  return pages;
}

function renderBlock(block) {
  if (/^h[1-3]$/.test(block.type)) {
    return createElement(block.type, "", block.text);
  }
  if (block.type === "p") {
    const paragraph = document.createElement("p");
    appendEvidenceText(paragraph, block.text);
    return paragraph;
  }
  if (block.type === "quote") {
    const quote = document.createElement("blockquote");
    appendEvidenceText(quote, block.text);
    return quote;
  }
  if (block.type === "rule") return document.createElement("hr");
  if (block.type === "code") {
    const pre = document.createElement("pre");
    pre.append(createElement("code", "", block.text));
    return pre;
  }
  if (block.type === "list") {
    const list = document.createElement(block.ordered ? "ol" : "ul");
    block.items.forEach((item) => {
      const entry = document.createElement("li");
      appendEvidenceText(entry, item);
      list.append(entry);
    });
    return list;
  }
  if (block.type === "table") {
    const wrapper = createElement("div", "reader-table-wrap");
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    block.headers.forEach((value) => {
      const cell = document.createElement("th");
      appendEvidenceText(cell, value);
      headRow.append(cell);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    block.rows.forEach((row) => {
      const tableRow = document.createElement("tr");
      row.forEach((value) => {
        const cell = document.createElement("td");
        appendEvidenceText(cell, value);
        tableRow.append(cell);
      });
      body.append(tableRow);
    });
    table.append(head, body);
    wrapper.append(table);
    return wrapper;
  }
  return createElement("p", "", block.text);
}

function fillPage(element, page, pageNumber) {
  element.replaceChildren();
  if (!page) return;
  page.forEach((block) => element.append(renderBlock(block)));
  const number = createElement("span", "page-number", String(pageNumber));
  element.append(number);
}

function renderPages() {
  if (state.plain) {
    leftPage.replaceChildren();
    state.blocks.forEach((block) => leftPage.append(renderBlock(block)));
    rightPage.replaceChildren();
    pagePosition.textContent = "Continuous reading";
    previousButton.disabled = true;
    nextButton.disabled = true;
    saveReaderPosition();
    return;
  }
  const narrow = window.matchMedia("(max-width: 720px)").matches;
  const spread = narrow ? 1 : 2;
  fillPage(leftPage, state.pages[state.pageIndex], state.pageIndex + 1);
  fillPage(rightPage, narrow ? null : state.pages[state.pageIndex + 1], state.pageIndex + 2);
  const shownEnd = Math.min(state.pageIndex + spread, state.pages.length);
  pagePosition.textContent = `Pages ${state.pageIndex + 1}${shownEnd > state.pageIndex + 1 ? `–${shownEnd}` : ""} of ${state.pages.length}`;
  previousButton.disabled = state.pageIndex === 0;
  nextButton.disabled = state.pageIndex + spread >= state.pages.length;
  saveReaderPosition();
}

function turnPage(direction) {
  if (state.plain) return;
  const spread = window.matchMedia("(max-width: 720px)").matches ? 1 : 2;
  state.pageIndex = Math.max(0, Math.min(state.pages.length - 1, state.pageIndex + direction * spread));
  if (spread === 2 && state.pageIndex % 2) state.pageIndex -= 1;
  renderPages();
}

function renderContents() {
  tocList.replaceChildren();
  state.headings.forEach((heading) => {
    const item = document.createElement("li");
    const button = createElement("button", "", heading.text);
    button.type = "button";
    button.style.paddingLeft = `${(heading.level - 1) * 12}px`;
    button.addEventListener("click", () => {
      state.pageIndex = heading.page;
      state.plain = false;
      openBook.classList.remove("is-plain");
      plainToggle.setAttribute("aria-pressed", "false");
      renderPages();
      tocPanel.classList.remove("is-open");
      tocToggle.setAttribute("aria-expanded", "false");
    });
    item.append(button);
    tocList.append(item);
  });
}

function mapHeadingPages() {
  const headings = [];
  state.pages.forEach((page, pageIndex) => {
    page.forEach((block) => {
      if (/^h[1-3]$/.test(block.type)) {
        headings.push({ text: block.text, level: Number(block.type[1]), page: pageIndex });
      }
    });
  });
  return headings;
}

async function openSelectedBook(book) {
  routeTo("reader");
  document.querySelector("#reader-title").textContent = book.title;
  document.querySelector("#reader-kicker").textContent = `Edition ${book.edition} · ${book.chapter_count} chapters`;
  document.querySelector("#hash-status").textContent = "Verifying edition…";
  leftPage.replaceChildren(createElement("p", "", "Opening the verified volume…"));
  rightPage.replaceChildren();
  hideProvenance();
  state.provenance = null;
  evidenceToggle.disabled = true;
  evidenceToggle.textContent = "Evidence";
  try {
    const payload = await fetchJson(`/api/v1/books/${encodeURIComponent(book.book_id)}`);
    state.blocks = markdownBlocks(payload.markdown);
    state.pages = paginate(state.blocks);
    state.readerBookId = book.book_id;
    state.readerBookHash = payload.verified_sha256;
    state.pageIndex = 0;
    state.plain = false;
    restoreReaderPosition();
    state.headings = mapHeadingPages();
    plainToggle.setAttribute("aria-pressed", String(state.plain));
    openBook.classList.toggle("is-plain", state.plain);
    document.querySelector("#hash-status").textContent =
      `Verified · ${payload.verified_sha256.slice(0, 10)}…`;
    document.title = `${book.title} — The Library`;
    renderContents();
    renderPages();
    try {
      state.provenance = await fetchJson(
        `/api/v1/books/${encodeURIComponent(book.book_id)}/provenance`,
      );
      evidenceToggle.disabled = false;
      evidenceToggle.textContent = `Evidence (${state.provenance.source_count})`;
    } catch (provenanceError) {
      evidenceToggle.disabled = true;
      evidenceToggle.textContent = "Evidence unavailable";
    }
  } catch (error) {
    leftPage.replaceChildren(
      createElement("h2", "", "This edition could not be opened"),
      createElement("p", "", error.message),
    );
    document.querySelector("#hash-status").textContent = "Verification failed";
  }
}

function setWorkflowStatus(message = "", tone = "") {
  workflowStatus.textContent = message;
  workflowStatus.classList.toggle("is-error", tone === "error");
  workflowStatus.classList.toggle("is-working", tone === "working");
}

function setWorkflowBusy(busy, message = "") {
  state.workflowBusy = busy;
  document.querySelectorAll(
    "#worktable-view button, #worktable-view textarea, #worktable-view select, #worktable-view input",
  ).forEach((control) => {
    control.disabled = busy;
  });
  if (!busy) applyProviderReadiness();
  if (message) setWorkflowStatus(message, busy ? "working" : "");
}

function applyProviderReadiness() {
  const providers = state.readiness?.providers || {};
  const inputs = [...document.querySelectorAll('input[name="provider"]')];
  if (!inputs.length) return;
  inputs.forEach((input) => {
    const provider = providers[input.value];
    const ready = Boolean(provider?.full_workflow_ready);
    input.disabled = !ready || state.workflowBusy;
    const label = input.closest("label");
    label?.classList.toggle("is-unavailable", !ready);
    const detail = label?.querySelector("small");
    if (detail) {
      detail.textContent = ready
        ? "Full local workflow ready"
        : provider?.installed
          ? "Installed, but MCP workflow is not ready"
          : "Provider is not installed";
    }
  });
  const selected = inputs.find((input) => input.checked && !input.disabled);
  if (!selected) {
    const firstReady = inputs.find((input) => !input.disabled);
    inputs.forEach((input) => { input.checked = input === firstReady; });
  }
  const begin = document.querySelector("#begin-inquiry");
  begin.disabled = state.workflowBusy || !inputs.some((input) => !input.disabled);
}

async function loadReadiness() {
  state.readiness = await fetchJson("/api/v1/readiness");
  applyProviderReadiness();
  if (!state.readiness.runnable_providers?.length) {
    document.querySelector("#inquiry-status").textContent =
      "No provider has a verified full-workflow MCP configuration.";
  }
}

function workflowName(workflow) {
  const opening = String(workflow.opening_question || "").trim();
  if (opening) return opening;
  return `Inquiry ${workflow.conversation_id.slice(0, 15)}`;
}

function renderWorkflowList() {
  workflowList.replaceChildren();
  if (!state.workflows.length) {
    workflowList.append(
      createElement("p", "workflow-list-empty", "No unfinished inquiry is waiting."),
    );
    return;
  }
  [...state.workflows].reverse().forEach((workflow, index) => {
    const button = createElement("button", "workflow-list-item");
    button.type = "button";
    button.classList.toggle(
      "is-selected",
      workflow.conversation_id === state.workflow?.conversation_id,
    );
    const numeral = createElement(
      "span",
      "",
      String(state.workflows.length - index).padStart(2, "0"),
    );
    const copy = document.createElement("span");
    copy.append(
      createElement("strong", "", workflowName(workflow)),
      createElement(
        "small",
        "",
        `${workflow.stage_label} · ${workflow.provider || "provider pending"}`,
      ),
    );
    button.append(numeral, copy);
    button.addEventListener("click", () => selectWorkflow(workflow.conversation_id));
    workflowList.append(button);
  });
}

async function refreshWorkflowList() {
  const payload = await fetchJson("/api/v1/workflows");
  state.workflows = payload.workflows || [];
  renderWorkflowList();
}

function showNewInquiry() {
  state.workflow = null;
  newInquiryPanel.hidden = false;
  activeWorkflowPanel.hidden = true;
  workflowStageBadge.hidden = true;
  workflowConsoleKicker.textContent = "A new line of inquiry";
  workflowConsoleTitle.textContent = "What has your curiosity caught?";
  workflowConsoleTitle.classList.remove("is-long");
  workflowConsoleTitle.removeAttribute("title");
  setWorkflowStatus();
  renderWorkflowList();
  document.querySelector("#inquiry-input").focus({ preventScroll: true });
}

function renderTranscript(messages = []) {
  conversationTranscript.replaceChildren();
  if (!messages.length) {
    conversationTranscript.append(
      createElement(
        "p",
        "workflow-list-empty",
        "This inquiry has no recorded conversation turns yet.",
      ),
    );
    return;
  }
  messages.forEach((message) => {
    const article = createElement(
      "article",
      `transcript-message ${message.role === "assistant" ? "is-assistant" : "is-user"}`,
    );
    article.append(
      createElement(
        "small",
        "",
        message.role === "assistant" ? "Librarian" : "Inquirer",
      ),
      createElement("p", "", message.content || ""),
    );
    conversationTranscript.append(article);
  });
  conversationTranscript.scrollTop = conversationTranscript.scrollHeight;
}

function actionCard(title, description) {
  const card = createElement("div", "workflow-action-card");
  card.append(
    createElement("p", "eyebrow", "Canonical next action"),
    createElement("h3", "", title),
    createElement("p", "", description),
  );
  return card;
}

function labeledField(label, control, className = "checkpoint-field") {
  const field = createElement("div", className);
  field.append(createElement("label", "", label), control);
  return field;
}

function textArea(rows = 3, placeholder = "") {
  const control = document.createElement("textarea");
  control.rows = rows;
  control.placeholder = placeholder;
  return control;
}

function decisionSelect(values) {
  const labels = {
    approved_for_book_planning: "Approve for book planning",
    accepted_research_route: "Accept the research route",
    needs_research_or_revision: "Return for research or revision",
    approved_for_drafting: "Approve the plan for drafting",
    needs_plan_revision: "Return the plan for revision",
    approved_for_finalization: "Approve the exact edition",
    needs_chapter_revision: "Return selected chapters for revision",
    hold: "Hold without advancing",
  };
  const select = document.createElement("select");
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels[value] || value.replaceAll("_", " ");
    select.append(option);
  });
  return select;
}

function lineValues(value) {
  return String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function submitCheckpointButton(form, label = "Record this decision") {
  const button = createElement("button", "primary-button compact");
  button.type = "submit";
  button.append(document.createTextNode(label), createElement("span", "", "→"));
  form.append(button);
}

function renderReviewCheckpoint(action) {
  const context = action.context || {};
  const card = actionCard(
    "Research review checkpoint",
    `The independent reviewer routed this inquiry as ${String(context.research_route || "unknown").replaceAll("_", " ")}.`,
  );
  if (context.core_question) {
    card.append(createElement("p", "checkpoint-summary", context.core_question));
  }
  const signature = context.curiosity_signature || {};
  const preview = createElement("div", "curiosity-preview");
  Object.entries(signature).forEach(([field, items]) => {
    const details = document.createElement("details");
    const summary = createElement("summary", "", field.replaceAll("_", " "));
    const list = createElement("ul", "checkpoint-list");
    (items || []).forEach((item) => list.append(createElement("li", "", item.text)));
    if (!items?.length) list.append(createElement("li", "", "Not established."));
    details.append(summary, list);
    preview.append(details);
  });
  card.append(preview);

  const form = createElement("form", "checkpoint-form");
  const decision = decisionSelect(context.allowed_decisions || []);
  const question = document.createElement("input");
  question.value = context.core_question || "";
  const curiosityDecision = decisionSelect(["accepted", "corrected"]);
  curiosityDecision.options[0].textContent = "Accept the extracted signature";
  curiosityDecision.options[1].textContent = "Correct the signature fields";
  const corrections = createElement("div", "checkpoint-fieldset");
  corrections.hidden = true;
  corrections.append(createElement("legend", "", "Corrected curiosity signature"));
  Object.entries(signature).forEach(([field, items]) => {
    const control = textArea(3);
    control.dataset.curiosityField = field;
    control.value = (items || []).map((item) => item.text).join("\n");
    corrections.append(labeledField(field.replaceAll("_", " "), control));
  });
  curiosityDecision.addEventListener("change", () => {
    corrections.hidden = curiosityDecision.value !== "corrected";
  });
  const revisions = textArea(3, "One accepted revision per line");
  const disputes = textArea(3, "One disputed point per line");
  const exclusions = textArea(3, "One exclusion per line");
  form.append(
    labeledField("Decision", decision),
    labeledField("Confirmed core question", question),
    labeledField("Curiosity signature", curiosityDecision),
    corrections,
    labeledField("Accepted belief revisions", revisions),
    labeledField("Disputed points", disputes),
    labeledField("Material to exclude", exclusions),
  );
  submitCheckpointButton(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      kind: "research_review",
      decision: decision.value,
      confirmed_core_question_or_thesis: question.value.trim(),
      accepted_belief_revisions: lineValues(revisions.value),
      disputed_points: lineValues(disputes.value),
      excluded_material: lineValues(exclusions.value),
    };
    if (decision.value === "approved_for_book_planning") {
      payload.curiosity_signature_decision = curiosityDecision.value;
      if (curiosityDecision.value === "corrected") {
        payload.approved_curiosity_signature = {};
        corrections.querySelectorAll("[data-curiosity-field]").forEach((control) => {
          const original = signature[control.dataset.curiosityField] || [];
          payload.approved_curiosity_signature[control.dataset.curiosityField] = (
            lineValues(control.value).map((text) => {
              const existing = original.find((item) => item.text === text);
              return existing
                ? {
                  text,
                  provenance: "review_extraction",
                  transcript_references: existing.transcript_references,
                }
                : {
                  text,
                  provenance: "human_checkpoint",
                  transcript_references: [],
                };
            })
          );
        });
      }
    }
    await submitWorkflowDecision(payload);
  });
  card.append(form);
  return card;
}

function renderBookPlanCheckpoint(action) {
  const context = action.context || {};
  const card = actionCard(
    "Book-plan checkpoint",
    "Approve this structure, return it with required changes, or preserve it on hold.",
  );
  card.append(
    createElement("h3", "", context.working_title || "Untitled book plan"),
    createElement("p", "checkpoint-summary", context.central_question || ""),
  );
  const list = createElement("ol", "checkpoint-list");
  (context.chapters || []).forEach((chapter) => {
    list.append(
      createElement(
        "li",
        "",
        `${chapter.chapter_id} · ${chapter.title || "Untitled chapter"} · ${chapter.draft_gate || "gate unknown"}`,
      ),
    );
  });
  card.append(list);
  const form = createElement("form", "checkpoint-form");
  const decision = decisionSelect(context.allowed_decisions || []);
  const notes = textArea(4, "One required change or note per line");
  form.append(
    labeledField("Decision", decision),
    labeledField("Plan notes", notes),
  );
  submitCheckpointButton(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitWorkflowDecision({
      kind: "book_plan",
      decision: decision.value,
      notes: lineValues(notes.value),
    });
  });
  card.append(form);
  return card;
}

function renderEditorialCheckpoint(action) {
  const context = action.context || {};
  const card = actionCard(
    "Final editorial checkpoint",
    "This decision binds to the exact independently audited synthesis hash shown below.",
  );
  card.append(
    createElement("h3", "", context.working_title || "Audited edition"),
    createElement(
      "p",
      "checkpoint-summary",
      `Synthesis S${context.synthesis_attempt || "?"} · ${String(context.edition_sha256 || "unavailable").slice(0, 16)}…`,
    ),
  );
  const requirements = createElement("ul", "checkpoint-list");
  (context.outstanding_finalization_requirements || []).forEach((item) => {
    requirements.append(
      createElement("li", "", `${item.chapter_id}: ${item.description}`),
    );
  });
  if (requirements.childElementCount) card.append(requirements);
  const form = createElement("form", "checkpoint-form");
  const decision = decisionSelect(context.allowed_decisions || []);
  const notes = textArea(4, "One whole-book note per line");
  const revisions = createElement("fieldset", "checkpoint-fieldset");
  revisions.hidden = true;
  revisions.append(createElement("legend", "", "Chapter revisions"));
  (context.included_chapter_ids || []).forEach((chapterId) => {
    const control = textArea(3, `Required changes for ${chapterId}`);
    control.dataset.revisionChapter = chapterId;
    revisions.append(labeledField(chapterId, control));
  });
  decision.addEventListener("change", () => {
    revisions.hidden = decision.value !== "needs_chapter_revision";
  });
  form.append(
    labeledField("Decision", decision),
    labeledField("Editorial notes", notes),
    revisions,
  );
  submitCheckpointButton(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const chapterRevisions = [...revisions.querySelectorAll("[data-revision-chapter]")]
      .map((control) => ({
        chapter_id: control.dataset.revisionChapter,
        instructions: lineValues(control.value),
      }))
      .filter((item) => item.instructions.length);
    if (decision.value === "needs_chapter_revision" && !chapterRevisions.length) {
      setWorkflowStatus("Select at least one chapter and enter a concrete required change.", "error");
      return;
    }
    await submitWorkflowDecision({
      kind: "editorial",
      decision: decision.value,
      notes: lineValues(notes.value),
      chapter_revisions: chapterRevisions,
    });
  });
  card.append(form);
  return card;
}

function renderSourceCheckpoint(action) {
  const context = action.context || action;
  const requests = context.pending_requests || [];
  const chapter = action.decision_kind === "chapter_source_permissions";
  const card = actionCard(
    chapter ? "Chapter source checkpoint" : "Research source checkpoint",
    "Each unknown source needs one explicit trust decision before the provider may continue.",
  );
  const form = createElement("form", "checkpoint-form");
  const rows = [];
  requests.forEach((request) => {
    const row = createElement("div", "source-decision-row");
    const copy = document.createElement("div");
    copy.append(
      createElement("strong", "", request.domain || "Unknown domain"),
      createElement("small", "", request.url || ""),
      createElement("small", "", request.reason_for_source || ""),
    );
    const select = decisionSelect(["allow_once", "allow_domain", "reject"]);
    select.options[0].textContent = "Allow this URL once";
    select.options[1].textContent = "Trust this domain";
    select.options[2].textContent = "Reject for this pass";
    rows.push({ request_id: request.request_id, select });
    row.append(copy, select);
    form.append(row);
  });
  submitCheckpointButton(form, "Record source decisions");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const decision = {
      kind: action.decision_kind,
      decisions: rows.map((row) => ({
        request_id: row.request_id,
        decision: row.select.value,
      })),
    };
    if (chapter) {
      Object.assign(decision, {
        book_id: context.book_id,
        chapter_id: context.chapter_id,
        assessment_id: context.assessment_id,
        research_mode: context.research_mode,
      });
    }
    await submitWorkflowDecision(decision);
  });
  card.append(form);
  return card;
}

function renderChapterCheckpoint(action) {
  const context = action.context || action;
  if (["research_source_permissions", "chapter_source_permissions"].includes(action.decision_kind)) {
    return renderSourceCheckpoint(action);
  }
  const titles = {
    chapter_artifact_review: "Incomplete provider transition",
    chapter_retry: "Changed-condition retry",
    chapter_human_input: "Chapter decision required",
    chapter_human_input_closeout: "Bounded human-input closeout",
    chapter_draft_acceptance: "Accept the exact assessed draft",
    chapter_recovery: "Bounded chapter recovery",
    research_access_recovery: "Alternative-source recovery",
    final_pass_repair: "Terminal final-pass repair",
  };
  const card = actionCard(
    titles[action.decision_kind] || "Chapter checkpoint",
    context.reason || "This finite transition requires explicit human authority.",
  );
  const form = createElement("form", "checkpoint-form");
  let buildDecision;
  if (action.decision_kind === "chapter_human_input") {
    const responses = (context.questions || []).map((question) => {
      const control = textArea(3);
      form.append(labeledField(question, control));
      return control;
    });
    const note = textArea(2, "Why these answers are authorized");
    form.append(labeledField("Authorization note", note));
    buildDecision = () => ({
      kind: action.decision_kind,
      book_id: context.book_id,
      chapter_id: context.chapter_id,
      responses: responses.map((control) => control.value.trim()),
      authorization_note: note.value.trim(),
    });
  } else {
    const fieldName = action.decision_kind === "chapter_artifact_review"
      ? "note"
      : action.decision_kind === "chapter_retry"
        ? "changed_condition"
        : "approval_note";
    const note = textArea(3, "Record the concrete human basis for this action");
    form.append(labeledField("Decision basis", note));
    buildDecision = () => ({
      kind: action.decision_kind,
      book_id: context.book_id,
      chapter_id: context.chapter_id,
      [fieldName]: note.value.trim(),
    });
  }
  submitCheckpointButton(form, "Authorize this exact transition");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitWorkflowDecision(buildDecision());
  });
  card.append(form);
  return card;
}

function renderWorkflowAction() {
  workflowActionZone.replaceChildren();
  if (!state.workflow || state.workflow.stage === "conversation_active") return;
  const action = state.workflow.pending_action || {};

  if (action.kind === "human_checkpoint") {
    if (action.decision_kind === "research_review") {
      workflowActionZone.append(renderReviewCheckpoint(action));
    } else if (action.decision_kind === "book_plan") {
      workflowActionZone.append(renderBookPlanCheckpoint(action));
    } else if (action.decision_kind === "editorial") {
      workflowActionZone.append(renderEditorialCheckpoint(action));
    } else if (action.decision_kind === "research_source_permissions") {
      workflowActionZone.append(renderSourceCheckpoint(action));
    } else {
      workflowActionZone.append(renderChapterCheckpoint(action));
    }
    return;
  }

  if (action.kind === "backend_transition" && action.command_available) {
    const card = actionCard(
      "Advance to the next durable hold",
      "The authoritative controller may perform bounded provider work. It stops only for source permission or a genuine terminal blocker.",
    );
    const button = createElement("button", "primary-button compact");
    button.type = "button";
    button.append(document.createTextNode("Advance workflow"), createElement("span", "", "→"));
    button.addEventListener("click", advanceWorkflow);
    card.append(button);
    workflowActionZone.append(card);
    return;
  }

  if (action.kind === "complete" || state.workflow.stage === "complete") {
    const card = actionCard(
      "The volume is complete",
      "The final hash is registered. Return to the shelf to open the verified edition.",
    );
    const button = createElement("button", "primary-button compact", "Open the enduring shelf →");
    button.type = "button";
    button.addEventListener("click", async () => {
      await loadLibrary();
      routeTo("library");
    });
    card.append(button);
    workflowActionZone.append(card);
    return;
  }

  const terminalStatus = action.status || state.workflow.stage_label;
  const terminalReason = action.reason
    ? ` ${action.reason}`
    : "";
  workflowActionZone.append(
    actionCard(
      "Workflow held",
      `${String(terminalStatus).replaceAll("_", " ")}.${terminalReason} No provider call will be made automatically.`,
    ),
  );
}

function renderWorkflow() {
  const workflow = state.workflow;
  if (!workflow) {
    showNewInquiry();
    return;
  }
  newInquiryPanel.hidden = true;
  activeWorkflowPanel.hidden = false;
  workflowStageBadge.hidden = false;
  workflowStageLabel.textContent = workflow.stage_label;
  workflowConsoleKicker.textContent = `${workflow.provider || "Provider pending"} · ${workflow.research_mode.replaceAll("_", " ")}`;
  const title = workflowName(workflow);
  workflowConsoleTitle.textContent = title;
  workflowConsoleTitle.classList.toggle("is-long", title.length > 160);
  if (title.length > 160) {
    workflowConsoleTitle.title = title;
  } else {
    workflowConsoleTitle.removeAttribute("title");
  }
  renderTranscript(workflow.messages || []);
  conversationComposer.hidden = workflow.stage !== "conversation_active";
  renderWorkflowAction();
  renderWorkflowList();
}

async function selectWorkflow(conversationId) {
  if (state.workflowBusy) return;
  setWorkflowBusy(true, "Recovering canonical workflow state…");
  try {
    state.workflow = await fetchJson(`/api/v1/workflows/${encodeURIComponent(conversationId)}`);
    renderWorkflow();
    setWorkflowStatus("Recovered from canonical artifacts.");
  } catch (error) {
    setWorkflowStatus(error.message, "error");
  } finally {
    setWorkflowBusy(false);
  }
}

async function submitWorkflowDecision(decision) {
  if (!state.workflow || state.workflowBusy) return;
  setWorkflowBusy(true, "Binding the decision to the current artifact revision…");
  try {
    const result = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/decisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    state.workflow = result.workflow;
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus("Decision recorded. Canonical state advanced.");
  } catch (error) {
    setWorkflowStatus(error.message, "error");
    if (error.status === 409) {
      state.workflowBusy = false;
      await selectWorkflow(state.workflow.conversation_id);
    }
  } finally {
    setWorkflowBusy(false);
  }
}

async function advanceWorkflow() {
  if (!state.workflow || state.workflowBusy) return;
  setWorkflowBusy(true, "The bounded controller is working. This may take several minutes…");
  try {
    const result = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/advance`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    state.workflow = result.workflow;
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus("Stopped at the next durable workflow state.");
  } catch (error) {
    setWorkflowStatus(error.message, "error");
    if (error.status === 409) {
      state.workflowBusy = false;
      await selectWorkflow(state.workflow.conversation_id);
    }
  } finally {
    setWorkflowBusy(false);
  }
}

async function initializeWorktable() {
  if (state.workflowBusy) return;
  try {
    await Promise.all([refreshWorkflowList(), loadReadiness()]);
    state.worktableLoaded = true;
  } catch (error) {
    workflowList.replaceChildren(
      createElement("p", "workflow-list-empty", error.message),
    );
  }
}

async function loadLibrary() {
  try {
    const verifiedCatalog = await fetchJson("/api/v1/catalog");
    const catalog = catalogWithVisualReferences(verifiedCatalog);
    state.catalog = catalog;
    renderShelf(catalog);
  } catch (error) {
    shelf.replaceChildren(createElement("p", "empty-shelf", error.message));
    document.querySelector("#book-count").textContent = "Archive unavailable";
  }
}

document.querySelectorAll("[data-route]").forEach((button) => {
  button.addEventListener("click", () => routeTo(button.dataset.route));
});

previousButton.addEventListener("click", () => turnPage(-1));
nextButton.addEventListener("click", () => turnPage(1));
previousVolumeButton.addEventListener("click", () => moveShelf(-1));
nextVolumeButton.addEventListener("click", () => moveShelf(1));

let shelfWheelLocked = false;
bookshelfViewport.addEventListener("wheel", (event) => {
  if (Math.abs(event.deltaY) < 24 || shelfWheelLocked) return;
  event.preventDefault();
  shelfWheelLocked = true;
  moveShelf(event.deltaY > 0 ? 1 : -1);
  window.setTimeout(() => {
    shelfWheelLocked = false;
  }, 420);
}, { passive: false });

plainToggle.addEventListener("click", () => {
  state.plain = !state.plain;
  openBook.classList.toggle("is-plain", state.plain);
  plainToggle.setAttribute("aria-pressed", String(state.plain));
  renderPages();
});

evidenceToggle.addEventListener("click", () => {
  if (provenancePanel.classList.contains("is-open")) {
    hideProvenance();
  } else {
    showProvenance();
  }
});

document.querySelector("#provenance-close").addEventListener(
  "click",
  hideProvenance,
);

tocToggle.addEventListener("click", () => {
  const open = !tocPanel.classList.contains("is-open");
  tocPanel.classList.toggle("is-open", open);
  tocToggle.setAttribute("aria-expanded", String(open));
});

document.querySelector("#begin-inquiry").addEventListener("click", async () => {
  const status = document.querySelector("#inquiry-status");
  const inquiry = document.querySelector("#inquiry-input").value.trim();
  if (!inquiry) {
    status.textContent = "Begin with the question you cannot leave alone.";
    return;
  }
  const selectedProvider = document.querySelector(
    'input[name="provider"]:checked:not(:disabled)',
  );
  if (!selectedProvider) {
    status.textContent = "Choose a provider whose full local workflow is ready.";
    return;
  }
  const provider = selectedProvider.value;
  const researchMode = document.querySelector('input[name="research-mode"]:checked').value;
  status.textContent = "Creating the durable inquiry…";
  setWorkflowBusy(true);
  let created = null;
  try {
    created = await fetchJson("/api/v1/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, research_mode: researchMode }),
    });
    status.textContent = `Waiting for ${provider} to answer the opening question…`;
    const turn = await fetchJson(
      `/api/v1/conversations/${encodeURIComponent(created.conversation_id)}/turns`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: inquiry,
          expected_stage: created.stage,
          expected_revision: created.revision,
        }),
      },
    );
    state.workflow = turn.workflow;
    document.querySelector("#inquiry-input").value = "";
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus("Opening turn saved to the durable transcript.");
  } catch (error) {
    status.textContent = error.message;
    if (created?.conversation_id) {
      try {
        state.workflow = await fetchJson(
          `/api/v1/workflows/${encodeURIComponent(created.conversation_id)}`,
        );
        await refreshWorkflowList();
        renderWorkflow();
        setWorkflowStatus(
          `The inquiry exists, but the opening provider turn stopped: ${error.message}`,
          "error",
        );
      } catch {
        // The original error remains the most useful recovery message.
      }
    }
  } finally {
    setWorkflowBusy(false);
  }
});

newInquiryButton.addEventListener("click", showNewInquiry);

document.querySelector("#send-turn").addEventListener("click", async () => {
  if (!state.workflow || state.workflowBusy) return;
  const content = turnInput.value.trim();
  if (!content) {
    setWorkflowStatus("Write the next question or observation first.", "error");
    return;
  }
  setWorkflowBusy(true, `Waiting for ${state.workflow.provider}…`);
  try {
    const result = await fetchJson(
      `/api/v1/conversations/${encodeURIComponent(state.workflow.conversation_id)}/turns`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    state.workflow = result.workflow;
    turnInput.value = "";
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus("Turn saved.");
  } catch (error) {
    setWorkflowStatus(error.message, "error");
  } finally {
    setWorkflowBusy(false);
  }
});

document.querySelector("#finish-inquiry").addEventListener("click", async () => {
  if (!state.workflow || state.workflowBusy) return;
  setWorkflowBusy(true, "Sealing the conversation and preparing independent review…");
  try {
    state.workflow = await fetchJson(
      `/api/v1/conversations/${encodeURIComponent(state.workflow.conversation_id)}/finish`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus("Conversation sealed. The review stage is ready to advance.");
  } catch (error) {
    setWorkflowStatus(error.message, "error");
  } finally {
    setWorkflowBusy(false);
  }
});

turnInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    document.querySelector("#send-turn").click();
  }
});

window.addEventListener("keydown", (event) => {
  if (views.library.classList.contains("is-active")) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveShelf(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveShelf(-1);
    } else if (event.key === "Enter" && state.selectedBook) {
      event.preventDefault();
      if (!state.inspecting) {
        setInspectionMode(true);
      } else if (!state.selectedBook.reference_only) {
        openSelectedBook(state.selectedBook);
      }
    } else if (event.key === "Escape" && state.inspecting) {
      event.preventDefault();
      setInspectionMode(false);
    }
    return;
  }
  if (!views.reader.classList.contains("is-active")) return;
  if (["ArrowRight", "d", "D", "PageDown"].includes(event.key)) {
    event.preventDefault();
    turnPage(1);
  } else if (["ArrowLeft", "a", "A", "PageUp"].includes(event.key)) {
    event.preventDefault();
    turnPage(-1);
  } else if (event.key === "Escape") {
    routeTo("library");
  }
});

let resizeTimer;
window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    if (state.inspecting) recenterFocusedBook(selectedShelfBook());
    if (!state.blocks.length || state.plain) return;
    const previousPage = state.pageIndex;
    state.pages = paginate(state.blocks);
    state.pageIndex = Math.min(previousPage, state.pages.length - 1);
    if (
      !window.matchMedia("(max-width: 720px)").matches
      && state.pageIndex % 2
    ) {
      state.pageIndex -= 1;
    }
    state.headings = mapHeadingPages();
    renderContents();
    renderPages();
  }, 150);
});

let printReaderState = null;
window.addEventListener("beforeprint", () => {
  if (!views.reader.classList.contains("is-active")) return;
  printReaderState = { plain: state.plain, pageIndex: state.pageIndex };
  state.plain = true;
  openBook.classList.add("is-plain");
  renderPages();
});

window.addEventListener("afterprint", () => {
  if (!printReaderState) return;
  state.plain = printReaderState.plain;
  state.pageIndex = printReaderState.pageIndex;
  openBook.classList.toggle("is-plain", state.plain);
  plainToggle.setAttribute("aria-pressed", String(state.plain));
  printReaderState = null;
  renderPages();
});

loadLibrary();
