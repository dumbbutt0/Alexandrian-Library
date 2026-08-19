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
  publishedWorkflows: [],
  workflowFilter: "published",
  jobs: new Map(),
  handledJobs: new Set(),
  activityTimer: null,
  lastCanonicalWorkflowSyncAt: 0,
  workflowBusy: false,
  worktableLoaded: false,
  readiness: null,
  provenance: null,
  runReceipt: null,
  readerBookId: null,
  readerBookHash: null,
  evidenceNumbers: new Map(),
  nextEvidenceNumber: 1,
  inquiryStarter: "understand",
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
const GUIDED_INQUIRY_EXAMPLE = "How can an offline AI assistant preserve useful human agency during a 72-hour network outage without encouraging overreliance, overstating its evidence, or silently taking decisions that should remain human-owned?";
const INQUIRY_STARTERS = {
  understand: {
    placeholder: "What complicated subject do you want to understand without getting lost?",
    status: "The Library will build a clear mental model and preserve meaningful disagreement.",
  },
  belief: {
    placeholder: "What do you believe—and what evidence would be strong enough to change your mind?",
    status: "The Library will test the belief instead of merely confirming it.",
  },
  material: {
    placeholder: "What should your notes, conversations, links, or argument become?",
    status: "Describe the material and the book you want it to become.",
  },
};

const views = {
  library: document.querySelector("#library-view"),
  reader: document.querySelector("#reader-view"),
  worktable: document.querySelector("#worktable-view"),
};

const shelf = document.querySelector("#bookshelf");
const firstVisitExample = document.querySelector("#first-visit-example");
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
const runReceiptToggle = document.querySelector("#run-receipt-toggle");
const readerExportMenu = document.querySelector("#reader-export-menu");
const exportMarkdown = document.querySelector("#export-markdown");
const exportEpub = document.querySelector("#export-epub");
const exportEvidence = document.querySelector("#export-evidence");
const exportCoverCard = document.querySelector("#export-cover-card");
const exportQuoteCard = document.querySelector("#export-quote-card");
const provenancePanel = document.querySelector("#provenance-panel");
const provenanceList = document.querySelector("#provenance-list");
const runReceiptPanel = document.querySelector("#run-receipt-panel");
const runReceiptContent = document.querySelector("#run-receipt-content");
const previousVolumeButton = document.querySelector("#previous-volume");
const nextVolumeButton = document.querySelector("#next-volume");
const catalogTrack = document.querySelector("#catalog-track");
const catalogEnd = document.querySelector("#catalog-end");
const catalogStage = document.querySelector(".catalog-stage");
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
const workflowProgress = document.querySelector("#workflow-progress");
const workflowProgressAgent = document.querySelector("#workflow-progress-agent");
const workflowProgressPhase = document.querySelector("#workflow-progress-phase");
const workflowProgressPercent = document.querySelector("#workflow-progress-percent");
const workflowProgressFill = document.querySelector("#workflow-progress-fill");
const workflowProgressElapsed = document.querySelector("#workflow-progress-elapsed");
const workflowActivityTitle = document.querySelector("#workflow-activity-title");
const workflowActivityDetail = document.querySelector("#workflow-activity-detail");
const workflowTransitionAgent = document.querySelector("#workflow-transition-agent");
const workflowTransitionPhase = document.querySelector("#workflow-transition-phase");
const workflowTransitionPercent = document.querySelector("#workflow-transition-percent");
const workflowTransitionFill = document.querySelector("#workflow-transition-fill");
const finishReadiness = document.querySelector("#finish-readiness");
const finishReadinessTitle = document.querySelector("#finish-readiness-title");
const finishReadinessStatus = document.querySelector("#finish-readiness-status");
const finishReadinessSummary = document.querySelector("#finish-readiness-summary");
const finishReadinessChecks = document.querySelector("#finish-readiness-checks");
const finishAutoList = document.querySelector("#finish-auto-list");
const finishPauseList = document.querySelector("#finish-pause-list");
const workflowTimelinePanel = document.querySelector("#workflow-timeline-panel");
const workflowTimeline = document.querySelector("#workflow-timeline");
const workflowTimelineCount = document.querySelector("#workflow-timeline-count");
const agentToolbar = document.querySelector("#agent-toolbar");
const activeAgentLabel = document.querySelector("#active-agent-label");
const agentSwitcher = document.querySelector("#agent-switcher");
const bookSeed = document.querySelector("#book-seed");
const bookSeedTitle = document.querySelector("#book-seed-title");
const bookSeedCoverTitle = document.querySelector("#book-seed-cover-title");
const bookSeedQuestion = document.querySelector("#book-seed-question");
const bookSeedStateTitle = document.querySelector("#book-seed-state-title");
const bookSeedStateDetail = document.querySelector("#book-seed-state-detail");
const publishedWorkflowCount = document.querySelector("#published-workflow-count");
const activeWorkflowCount = document.querySelector("#active-workflow-count");

function routeTo(route) {
  document.body.dataset.activeRoute = route;
  Object.entries(views).forEach(([name, element]) => {
    element.classList.toggle("is-active", name === route);
  });
  document.querySelectorAll("[data-route]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.route === route);
  });
  tocPanel.classList.remove("is-open");
  tocToggle.setAttribute("aria-expanded", "false");
  if (route !== "reader") {
    hideProvenance();
    hideRunReceipt();
    readerExportMenu.open = false;
  }
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
  return book.cover?.asset_url || "";
}

function coverQuoteForBook(book) {
  return book.cover?.quote || "";
}

function activeReaderBook() {
  const catalogBook = state.catalog?.books?.find(
    (book) => book.book_id === state.readerBookId,
  );
  return catalogBook || state.selectedBook;
}

function shareCardFilename(title, kind) {
  const safeTitle = String(title || "inquirion-book")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 72);
  return `${safeTitle || "inquirion-book"}-${kind}-card.png`;
}

function canvasTextLines(context, text, maxWidth, maxLines = 8) {
  const words = String(text || "").trim().split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  words.forEach((word) => {
    const candidate = line ? `${line} ${word}` : word;
    if (context.measureText(candidate).width <= maxWidth || !line) {
      line = candidate;
      return;
    }
    lines.push(line);
    line = word;
  });
  if (line) lines.push(line);
  if (lines.length <= maxLines) return lines;
  const visible = lines.slice(0, maxLines);
  let finalLine = visible[maxLines - 1];
  while (finalLine && context.measureText(`${finalLine}…`).width > maxWidth) {
    finalLine = finalLine.split(" ").slice(0, -1).join(" ");
  }
  visible[maxLines - 1] = `${finalLine || ""}…`;
  return visible;
}

function drawCanvasLines(context, lines, x, y, lineHeight) {
  lines.forEach((line, index) => context.fillText(line, x, y + index * lineHeight));
}

function exportShareCard(kind) {
  const book = activeReaderBook();
  if (!book) return;
  const canvas = document.createElement("canvas");
  canvas.width = 1200;
  canvas.height = 675;
  const context = canvas.getContext("2d");
  if (!context) return;

  const recordedPalette = book.cover?.palette;
  const palette = Array.isArray(recordedPalette) && recordedPalette.length >= 2
    ? recordedPalette
    : bookPalette(0);
  const background = context.createLinearGradient(0, 0, canvas.width, canvas.height);
  background.addColorStop(0, palette[0]);
  background.addColorStop(1, palette[1]);
  context.fillStyle = background;
  context.fillRect(0, 0, canvas.width, canvas.height);

  const glow = context.createRadialGradient(600, 500, 0, 600, 500, 600);
  glow.addColorStop(0, "rgba(236, 194, 115, 0.28)");
  glow.addColorStop(1, "rgba(236, 194, 115, 0)");
  context.fillStyle = glow;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "rgba(244, 224, 185, 0.42)";
  context.lineWidth = 2;
  context.strokeRect(44, 44, 1112, 587);

  context.fillStyle = "#f2dfb8";
  context.font = "600 22px Georgia, serif";
  context.letterSpacing = "3px";
  context.fillText("INQUIRION LIBRARY · VERIFIED EDITION", 82, 105);

  const isQuote = kind === "quote";
  const mainText = isQuote
    ? coverQuoteForBook(book) || book.central_question || book.title
    : book.title;
  context.fillStyle = "#fffaf0";
  context.font = isQuote ? "italic 600 54px Georgia, serif" : "700 64px Georgia, serif";
  const lines = canvasTextLines(context, isQuote ? `“${mainText}”` : mainText, 950, isQuote ? 5 : 4);
  drawCanvasLines(context, lines, 82, isQuote ? 230 : 225, isQuote ? 68 : 76);

  context.fillStyle = "rgba(255, 250, 240, 0.76)";
  context.font = "400 27px system-ui, sans-serif";
  const supportingText = isQuote
    ? book.title
    : book.central_question || coverQuoteForBook(book) || "An Alexandrian Narrative Research Book";
  const supportLines = canvasTextLines(context, supportingText, 950, 3);
  const supportY = Math.max(455, 225 + lines.length * 76 + 30);
  drawCanvasLines(context, supportLines, 82, Math.min(supportY, 520), 38);

  context.fillStyle = "rgba(242, 223, 184, 0.9)";
  context.font = "500 20px system-ui, sans-serif";
  context.fillText("Read like a compelling book. Inspect like a research artifact.", 82, 594);

  const link = document.createElement("a");
  link.download = shareCardFilename(book.title, kind);
  link.href = canvas.toDataURL("image/png");
  link.click();
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
  inspector.scrollTop = 0;
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
  const overflows = updateShelfGeometry();
  if (!overflows) {
    bookshelfViewport.scrollTo({ left: 0, behavior: "auto" });
    return;
  }
  const desiredLeft = bookObject.offsetLeft
    + bookObject.offsetWidth / 2
    - bookshelfViewport.clientWidth / 2;
  const maximumLeft = Math.max(
    0,
    bookshelfViewport.scrollWidth - bookshelfViewport.clientWidth,
  );
  bookshelfViewport.scrollTo({
    left: Math.min(maximumLeft, Math.max(0, desiredLeft)),
    behavior,
  });
  shelfRoom.scrollLeft = 0;
  const archiveFrame = shelfRoom.closest(".archive-frame");
  if (archiveFrame) archiveFrame.scrollLeft = 0;
}

function updateShelfGeometry() {
  const books = Array.from(shelf.querySelectorAll(".shelf-book"));
  if (!books.length) {
    shelf.classList.remove("books-overflow");
    return false;
  }
  const viewportWidth = bookshelfViewport.clientWidth;
  const gap = Number.parseFloat(getComputedStyle(shelf).columnGap) || 0;
  const contentWidth = books.reduce(
    (total, bookObject) => total + bookObject.offsetWidth,
    gap * Math.max(0, books.length - 1),
  );
  const edgeSpace = Math.max(24, Math.min(72, viewportWidth * 0.055));
  const overflows = contentWidth + edgeSpace * 2 > viewportWidth;
  shelf.style.setProperty("--shelf-edge-space", `${edgeSpace}px`);
  shelf.classList.toggle("books-overflow", overflows);
  if (!overflows) bookshelfViewport.scrollLeft = 0;
  return overflows;
}

function positionBookForFocus(bookObject) {
  if (!bookObject || !bookFocusStage) return;
  const stageBounds = bookFocusStage.getBoundingClientRect();
  const bookBounds = bookObject.getBoundingClientRect();
  const bookWidth = Number.parseFloat(
    getComputedStyle(bookObject).getPropertyValue("--book-width"),
  );
  const bookHeight = Number.parseFloat(
    getComputedStyle(bookObject).getPropertyValue("--book-height"),
  );
  const bookDepth = Number.parseFloat(
    getComputedStyle(bookObject).getPropertyValue("--book-depth"),
  );
  const safeWidth = Math.hypot(bookWidth, bookDepth);
  const inset = Math.min(24, stageBounds.width * 0.055);
  const inspectionScale = window.matchMedia("(max-width: 720px)").matches
    ? 1.46
    : 1.72;
  const scale = Math.max(
    1,
    Math.min(
      inspectionScale,
      (stageBounds.width - inset * 2) / safeWidth,
    ),
  );
  const shelfCenterX = bookBounds.left + bookBounds.width / 2;
  const shelfCenterY = bookBounds.top + bookBounds.height / 2;
  const shelfTransform = getComputedStyle(bookObject).transform;
  const transformValues = shelfTransform === "none"
    ? []
    : shelfTransform
      .slice(shelfTransform.indexOf("(") + 1, -1)
      .split(",")
      .map(Number);
  const shelfOffsetY = shelfTransform.startsWith("matrix3d(")
    ? transformValues[13] || 0
    : transformValues[5] || 0;
  const focusX = stageBounds.left + stageBounds.width / 2
    - shelfCenterX;
  const scaleOriginCorrection = (scale - 1) * bookHeight / 2;
  const focusY = stageBounds.top + stageBounds.height / 2
    - shelfCenterY
    + scaleOriginCorrection
    + shelfOffsetY;
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
  catalogStage.classList.toggle("has-focused-book", nextState);
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
    window.requestAnimationFrame(() => recenterFocusedBook(bookObject));
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
  const pagePosition = {
    left: window.scrollX,
    top: window.scrollY,
  };
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
  window.requestAnimationFrame(() => {
    if (focusAfter) setInspectionMode(true);
    window.scrollTo({
      left: pagePosition.left,
      top: pagePosition.top,
      behavior: "instant",
    });
  });
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
  catalogStage.classList.remove("has-focused-book");
  shelfRoom.classList.remove("has-focused-book");
  shelf.classList.remove("has-selection");
  shelf.classList.remove("books-overflow");
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
  firstVisitExample.hidden = Boolean(catalog.books.length);
  if (!catalog.books.length) {
    renderClosedShelfInspector();
    return;
  }
  catalog.books.forEach((book, index) => {
    const bookObject = shelfBookTemplate.content.firstElementChild.cloneNode(true);
    const recordedPalette = book.cover?.palette;
    const palette = Array.isArray(recordedPalette) && recordedPalette.length >= 2
      ? recordedPalette
      : bookPalette(index);
    const bookHeight = normalizedBookHeight(book);
    const bookDepth = normalizedBookDepth(book);
    bookObject.style.setProperty("--cover-a", palette[0]);
    bookObject.style.setProperty("--cover-b", palette[1]);
    bookObject.style.setProperty("--book-height", `${bookHeight}px`);
    bookObject.style.setProperty("--book-width", `${Math.round(bookHeight * (2 / 3))}px`);
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
    const coverQuote = coverQuoteForBook(book);
    const coverQuoteNode = bookObject.querySelector(".cover-quote");
    coverQuoteNode.textContent = coverQuote;
    coverQuoteNode.hidden = !coverQuote;
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
        setInspectionMode(!state.inspecting);
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
  updateShelfGeometry();
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
    .map((item) => item.trim().replace(/^`+|`+$/g, "").trim())
    .filter(Boolean);
}

function evidenceNumber(receiptId) {
  if (!state.evidenceNumbers.has(receiptId)) {
    state.evidenceNumbers.set(receiptId, state.nextEvidenceNumber);
    state.nextEvidenceNumber += 1;
  }
  return state.evidenceNumbers.get(receiptId);
}

function appendEvidenceText(element, text) {
  const pattern = /\[Evidence:\s*([^\]]+)\]/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    const ids = evidenceIds(match[1]);
    const markerGroup = createElement("span", "evidence-marker-group");
    ids.forEach((receiptId) => {
      const number = evidenceNumber(receiptId);
      const button = createElement("button", "evidence-marker", `[${number}]`);
      button.type = "button";
      button.dataset.evidenceIds = receiptId;
      button.setAttribute("aria-label", `Open evidence ${number}`);
      button.addEventListener("click", () => showProvenance([receiptId]));
      markerGroup.append(button);
    });
    element.append(markerGroup);
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
  hideRunReceipt();
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
  const records = (requested.size
    ? state.provenance.sources.filter((source) => requested.has(source.receipt_id))
    : state.provenance.sources
  ).slice().sort((left, right) => (
    evidenceNumber(left.receipt_id) - evidenceNumber(right.receipt_id)
  ));
  records.forEach((source) => {
    const article = createElement("article", "provenance-record");
    const number = evidenceNumber(source.receipt_id);
    const heading = createElement("div", "provenance-record-heading");
    heading.append(
      createElement("span", "provenance-number", `[${number}]`),
      createElement("strong", "", source.title || source.domain || "Untitled source"),
    );
    article.append(heading);

    const url = source.final_url || source.requested_url;
    if (url && /^https?:\/\//i.test(url)) {
      const sourceLink = createElement("a", "provenance-source-link", source.domain || "Open source");
      sourceLink.href = url;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener noreferrer nofollow";
      article.append(sourceLink);
    }
    if (source.finding) {
      const finding = createElement(
        "span",
        `evidence-finding evidence-finding-${source.finding}`,
        source.finding.replaceAll("_", " "),
      );
      article.classList.toggle("is-contradiction", source.finding === "contradicts");
      article.append(finding);
    }
    if (source.claim_checked) {
      const claim = createElement("section", "evidence-section");
      claim.append(
        createElement("small", "evidence-label", "Claim checked"),
        createElement("p", "", source.claim_checked),
      );
      article.append(claim);
    }
    if (source.evidence_excerpt) {
      const passage = createElement("section", "evidence-section evidence-passage");
      passage.append(
        createElement("small", "evidence-label", "Exact supporting passage"),
        createElement("blockquote", "", source.evidence_excerpt),
      );
      article.append(passage);
    }
    const location = source.evidence_location
      || source.evidence_locator?.heading_path?.join(" › ")
      || (source.evidence_locator?.page_number
        ? `Page ${source.evidence_locator.page_number}`
        : "Location not recorded");
    const locationSection = createElement("section", "evidence-section");
    locationSection.append(
      createElement("small", "evidence-label", "Location"),
      createElement("p", "", location),
    );
    article.append(locationSection);
    if (source.evidence_locator?.page_number && !/page\s+\d+/i.test(location)) {
      locationSection.querySelector("p").append(
        document.createTextNode(` · Page ${source.evidence_locator.page_number}`),
      );
    }
    if (source.assessment) {
      const assessment = createElement("section", "evidence-section");
      assessment.append(
        createElement("small", "evidence-label", "What the source establishes"),
        createElement("p", "", source.assessment),
      );
      article.append(assessment);
    }
    const limitations = createElement("section", "evidence-section evidence-limits");
    limitations.append(
      createElement("small", "evidence-label", "Limits and uncertainty"),
      createElement("p", "", source.limitations || "No limitations were recorded."),
    );
    article.append(limitations);

    const receiptDetails = document.createElement("details");
    receiptDetails.className = "evidence-receipt-details";
    receiptDetails.append(createElement("summary", "", "Receipt and verification"));
    receiptDetails.append(
      createElement("p", "", `Receipt: ${source.receipt_id}`),
      createElement("p", "", `Retrieved: ${source.retrieved_at || "Not recorded"}`),
      createElement("p", "", `Content hash: ${source.content_sha256 || "Not recorded"}`),
    );
    article.append(receiptDetails);
    provenanceList.append(article);
  });
  const statuses = requested.size ? [] : (state.provenance.evidence_statuses || []);
  if (statuses.length) {
    const heading = createElement("div", "provenance-section-heading");
    heading.append(
      createElement("p", "eyebrow", "Unverified evidence trail"),
      createElement(
        "p",
        "",
        "These records are public provenance, not verified citations.",
      ),
    );
    provenanceList.append(heading);
    statuses.forEach((status, index) => {
      const article = createElement("article", "provenance-record is-unverified");
      const recordHeading = createElement("div", "provenance-record-heading");
      recordHeading.append(
        createElement("span", "provenance-number", `U${index + 1}`),
        createElement("strong", "", status.question || "Unverified source attempt"),
      );
      article.append(recordHeading);
      article.append(
        createElement(
          "span",
          "evidence-finding evidence-finding-unverified",
          String(status.status || "not verified").replaceAll("_", " "),
        ),
      );
      const url = status.candidate_source_url;
      if (url && /^https?:\/\//i.test(url)) {
        const link = createElement("a", "provenance-source-link", "Open attempted source");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer nofollow";
        article.append(link);
      }
      const reason = createElement("section", "evidence-section evidence-limits");
      reason.append(
        createElement("small", "evidence-label", "Why it was not admitted"),
        createElement("p", "", status.reason || "No verification receipt was created."),
      );
      article.append(reason);
      provenanceList.append(article);
    });
  }
  const gaps = requested.size ? [] : (state.provenance.evidence_gaps || []);
  if (gaps.length) {
    const heading = createElement("div", "provenance-section-heading");
    heading.append(
      createElement("p", "eyebrow", "Bounded evidence gaps"),
      createElement("p", "", "Open limits preserved by the published edition."),
    );
    provenanceList.append(heading);
    gaps.forEach((gap) => {
      const article = createElement("article", "provenance-record is-evidence-gap");
      article.append(
        createElement("strong", "", gap.text || "Unresolved evidence gap"),
        createElement(
          "span",
          "evidence-finding evidence-finding-unverified",
          String(gap.disposition || "unresolved").replaceAll("_", " "),
        ),
      );
      if (gap.closure_reason) {
        const boundary = createElement("section", "evidence-section");
        boundary.append(
          createElement("small", "evidence-label", "Publication boundary"),
          createElement("p", "", gap.closure_reason),
        );
        article.append(boundary);
      }
      provenanceList.append(article);
    });
  }
  if (!records.length && !statuses.length && !gaps.length) {
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

function receiptValue(label, value) {
  const item = createElement("section", "evidence-section");
  item.append(
    createElement("small", "evidence-label", label),
    createElement("p", "", value === null || value === undefined || value === "" ? "Not recorded" : String(value)),
  );
  return item;
}

function showRunReceipt() {
  hideProvenance();
  runReceiptPanel.classList.add("is-open");
  runReceiptPanel.setAttribute("aria-hidden", "false");
  runReceiptToggle.setAttribute("aria-expanded", "true");
  runReceiptContent.replaceChildren();
  const receipt = state.runReceipt;
  if (!receipt) {
    runReceiptContent.append(
      createElement("p", "provenance-empty", "The workflow run receipt is unavailable."),
    );
    return;
  }

  const summary = createElement("article", "provenance-record");
  const reviewLabel = receipt.review_state?.label || "Review state unavailable";
  summary.append(
    createElement("div", "provenance-record-heading", reviewLabel),
    receiptValue("Providers used", (receipt.provider_sequence || []).join(" → ") || "None recorded"),
    receiptValue("Provider switches", receipt.provider_switch_count),
    receiptValue("Observed server restarts", receipt.observed_restart_count),
    receiptValue("Mechanical retries", receipt.mechanical_retry_count),
  );
  runReceiptContent.append(summary);

  const decisions = receipt.source_decision_counts || {};
  const sources = createElement("article", "provenance-record");
  sources.append(
    createElement("div", "provenance-record-heading", "Source authority"),
    receiptValue("Rejected", decisions.reject || 0),
    receiptValue("Allowed once", decisions.allow_once || 0),
    receiptValue("Allowed sites", decisions.allow_domain || 0),
  );
  runReceiptContent.append(sources);

  const guardrails = receipt.finish_guardrails || { enabled: false };
  const guardrailRecord = createElement("article", "provenance-record");
  guardrailRecord.append(
    createElement("div", "provenance-record-heading", "Finish guardrails"),
    receiptValue("Enabled", guardrails.enabled ? "Yes" : "No"),
    receiptValue("Policy", guardrails.policy_version),
    receiptValue("Authority scope", guardrails.scope),
    receiptValue("Source authority", guardrails.source_authority),
    receiptValue(
      "Nondelegable holds",
      (guardrails.nondelegable_checkpoint_kinds || []).join(", "),
    ),
  );
  runReceiptContent.append(guardrailRecord);

  const timings = receipt.timings || {};
  const timing = createElement("article", "provenance-record");
  timing.append(
    createElement("div", "provenance-record-heading", "Observed timing"),
    receiptValue("Research conversation", timings.conversation_seconds == null ? null : `${timings.conversation_seconds}s`),
    receiptValue("Background runtime", timings.background_runtime_seconds == null ? null : `${timings.background_runtime_seconds}s`),
    receiptValue("Provider transitions", timings.transition_seconds == null ? null : `${timings.transition_seconds}s`),
  );
  runReceiptContent.append(timing);

  const artifacts = receipt.artifacts || {};
  const artifact = createElement("article", "provenance-record");
  artifact.append(
    createElement("div", "provenance-record-heading", "Durable artifacts"),
    receiptValue("Book SHA-256", artifacts.book_sha256),
    receiptValue("Assembly SHA-256", artifacts.assembly_sha256),
    receiptValue("Synthesis audit SHA-256", artifacts.synthesis_audit_sha256),
  );
  runReceiptContent.append(artifact);
}

function hideRunReceipt() {
  runReceiptPanel.classList.remove("is-open");
  runReceiptPanel.setAttribute("aria-hidden", "true");
  runReceiptToggle.setAttribute("aria-expanded", "false");
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
    const contradiction = block.text.match(/^\[!CONTRADICTION\]\s*(.*)$/i)
      || block.text.match(/^Contradiction:\s*(.*)$/i);
    if (contradiction) {
      quote.className = "contradiction-callout";
      quote.append(createElement("strong", "contradiction-label", "Contradiction"));
      appendEvidenceText(quote, contradiction[1]);
    } else {
      appendEvidenceText(quote, block.text);
    }
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
  hideRunReceipt();
  state.provenance = null;
  state.runReceipt = null;
  state.evidenceNumbers = new Map();
  state.nextEvidenceNumber = 1;
  evidenceToggle.disabled = true;
  evidenceToggle.textContent = "Why trust this?";
  runReceiptToggle.disabled = true;
  runReceiptToggle.textContent = "Build record";
  const exportBase = `/api/v1/books/${encodeURIComponent(book.book_id)}/exports`;
  exportMarkdown.href = `${exportBase}/markdown`;
  exportEpub.href = `${exportBase}/epub`;
  exportEvidence.href = `${exportBase}/evidence-manifest`;
  readerExportMenu.open = false;
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
      const verifiedCount = state.provenance.source_count || 0;
      const openCount = state.provenance.evidence_status_count || 0;
      evidenceToggle.textContent = openCount
        ? `Why trust this? (${verifiedCount} verified · ${openCount} open)`
        : `Why trust this? (${verifiedCount})`;
    } catch (provenanceError) {
      evidenceToggle.disabled = true;
      evidenceToggle.textContent = "Evidence unavailable";
    }
    try {
      state.runReceipt = await fetchJson(
        `/api/v1/books/${encodeURIComponent(book.book_id)}/run-receipt`,
      );
      runReceiptToggle.disabled = false;
      runReceiptToggle.textContent = "Build record";
    } catch (receiptError) {
      runReceiptToggle.disabled = true;
      runReceiptToggle.textContent = "Receipt unavailable";
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
    ".workflow-console button, .workflow-console textarea, .workflow-console select, .workflow-console input",
  ).forEach((control) => {
    control.disabled = busy;
  });
  if (!busy) {
    const activeJob = activeJobFor(state.workflow?.conversation_id);
    if (activeJob) {
      conversationComposer.querySelectorAll("button, textarea").forEach((control) => {
        control.disabled = true;
      });
    }
    applyProviderReadiness();
  }
  if (message) setWorkflowStatus(message, busy ? "working" : "");
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  return `${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function activeJobFor(conversationId) {
  const job = state.jobs.get(conversationId);
  return job && ["queued", "running"].includes(job.status) ? job : null;
}

function normalizedWorkflowProgress(workflow) {
  const raw = Number(workflow?.progress?.percent ?? workflow?.overall_progress);
  const bounded = Math.max(1, Math.min(100, Number.isFinite(raw) ? raw : 1));
  return workflow?.stage === "complete" ? 100 : Math.min(99, bounded);
}

function estimatedJobProgress(_job, workflow = state.workflow) {
  return normalizedWorkflowProgress(workflow);
}

function renderWorkflowProgress() {
  const conversationId = state.workflow?.conversation_id;
  const job = conversationId ? activeJobFor(conversationId) : null;
  const bookCreation = state.workflow?.book_creation;
  const experience = workflowExperience(state.workflow);
  const persistentBuild = Boolean(bookCreation?.active);
  workflowProgress.hidden = !job && !persistentBuild;
  if (!job && !persistentBuild) return;
  if (!job) {
    const progress = normalizedWorkflowProgress(state.workflow);
    workflowProgressAgent.textContent = "Overall book completion";
    workflowProgressPhase.textContent = experience.headline;
    workflowProgressPercent.textContent = `${Math.round(progress)}%`;
    workflowProgressFill.style.width = `${progress}%`;
    workflowTransitionAgent.textContent = "Current activity";
    workflowTransitionPhase.textContent = experience.attention_required
      ? "Waiting for your decision"
      : "Saved and ready to resume";
    workflowTransitionPercent.textContent = "Paused";
    workflowTransitionFill.style.width = "0%";
    workflowProgressElapsed.textContent = "Progress safely preserved";
    workflowActivityTitle.textContent = experience.attention_required
      ? "The Library needs you"
      : "No process is running";
    workflowActivityDetail.textContent = experience.detail;
    return;
  }
  const progress = estimatedJobProgress(job, state.workflow);
  const transitionProgress = Math.max(
    1,
    Math.min(100, Number(job.transition_progress ?? 1) || 1),
  );
  workflowProgressAgent.textContent = "Overall book completion";
  workflowProgressPhase.textContent = experience.headline;
  workflowProgressPercent.textContent = `${Math.round(progress)}%`;
  workflowProgressFill.style.width = `${progress}%`;
  workflowTransitionAgent.textContent = "What is happening now";
  workflowTransitionPhase.textContent = job.transition_phase || job.phase;
  workflowTransitionPercent.textContent = `${Math.round(transitionProgress)}%`;
  workflowTransitionFill.style.width = `${transitionProgress}%`;
  workflowProgressElapsed.textContent = `${formatElapsed(job.elapsed_seconds)} elapsed`;
  const event = job.latest_transition_event;
  const attempt = Number(event?.mechanical_attempt || 0);
  workflowActivityTitle.textContent = "The Library is working";
  workflowActivityDetail.textContent = event
    ? `${job.transition_phase || String(event.phase || job.phase).replaceAll("_", " ")}${attempt ? ` · recovery attempt ${attempt}` : ""}`
    : "The next durable step is queued. You can leave without losing completed work.";
}

function workflowFromJobResult(job) {
  if (!job?.result) return null;
  if (job.result.workflow) return job.result.workflow;
  if (job.result.conversation_id && job.result.stage) return job.result;
  return null;
}

function renderFinishReadiness(workflow) {
  const readiness = workflow?.finish_readiness;
  finishReadiness.hidden = !readiness;
  if (!readiness) return;
  const statusLabels = {
    ready: "Ready",
    needs_context: "Add context",
    advancing: "Progressing",
    decision_required: "Your decision",
    recovery_required: "Recovery needed",
    complete: "Complete",
  };
  const titleLabels = {
    ready: "Ready for one guarded Finish",
    needs_context: "The inquiry needs more context",
    advancing: "The book is progressing",
    decision_required: "A human-owned decision is waiting",
    recovery_required: "A durable recovery choice is waiting",
    complete: "The verified edition is complete",
  };
  finishReadiness.dataset.status = readiness.status;
  finishReadinessTitle.textContent = titleLabels[readiness.status] || "Finish readiness";
  finishReadinessStatus.textContent = statusLabels[readiness.status] || readiness.status;
  finishReadinessSummary.textContent = readiness.summary || "";
  finishReadinessChecks.replaceChildren();
  (readiness.checks || []).forEach((check) => {
    const item = createElement("div", `finish-check is-${check.status}`);
    item.append(
      createElement("span", "finish-check-mark", check.status === "pass" ? "✓" : "!"),
      createElement("strong", "", check.label),
      createElement("small", "", check.detail),
    );
    finishReadinessChecks.append(item);
  });
  finishAutoList.replaceChildren(
    ...(readiness.will_continue_automatically || []).map((value) =>
      createElement("li", "", value)),
  );
  finishPauseList.replaceChildren(
    ...(readiness.will_pause_for || []).map((value) =>
      createElement("li", "", value)),
  );
  const finishButton = document.querySelector("#finish-inquiry");
  if (workflow.stage === "conversation_active") {
    finishButton.disabled = !readiness.finish_available
      || Boolean(activeJobFor(workflow.conversation_id))
      || state.workflowBusy;
  }
}

function renderWorkflowTimeline(workflow) {
  const events = workflow?.timeline || [];
  workflowTimelinePanel.hidden = !events.length;
  workflowTimelineCount.textContent = events.length ? `· ${events.length}` : "";
  workflowTimeline.replaceChildren();
  events.forEach((event) => {
    const item = createElement("li", `timeline-item is-${event.status || "complete"}`);
    const copy = document.createElement("div");
    copy.append(
      createElement("strong", "", event.label || "Workflow event"),
      createElement("small", "", event.detail || "Durable transition"),
    );
    const metaParts = [];
    if (event.provider) metaParts.push(event.provider);
    if (event.attempt) metaParts.push(`attempt ${event.attempt}`);
    if (event.recorded_at) {
      const recorded = new Date(event.recorded_at);
      if (!Number.isNaN(recorded.valueOf())) {
        metaParts.push(recorded.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }));
      }
    }
    item.append(
      createElement("span", "timeline-node", event.status === "complete" ? "✓" : "•"),
      copy,
      createElement("span", "timeline-meta", metaParts.join(" · ")),
    );
    workflowTimeline.append(item);
  });
}

async function handleFinishedJob(job) {
  if (state.handledJobs.has(job.job_id)) return;
  state.handledJobs.add(job.job_id);
  const selected = state.workflow?.conversation_id === job.conversation_id;
  if (job.status === "completed") {
    if (selected) {
      state.workflow = workflowFromJobResult(job) || await fetchJson(
        `/api/v1/workflows/${encodeURIComponent(job.conversation_id)}`,
      );
      renderWorkflow();
      setWorkflowStatus(
        job.kind === "finish_and_advance"
          ? state.workflow.stage === "complete"
            ? "Finish guardrails completed the verified book."
            : state.workflow.finish_policy?.enabled
              ? "Finish guardrails paused at a nondelegable durable decision."
              : "Conversation sealed and advanced to the next durable hold."
          : job.kind === "advance_workflow"
            ? "Workflow reached the next durable hold."
            : job.kind === "background_book"
              ? state.workflow.stage === "editorial_checkpoint"
                ? "Book is ready for your final editorial review."
                : state.workflow.stage === "complete"
                  ? "The verified book is complete."
                  : "Background build paused at the next durable decision."
            : "Provider response saved to the durable transcript.",
      );
    }
  } else if (job.status === "failed" && selected) {
    state.workflow = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(job.conversation_id)}`,
    ).catch(() => state.workflow);
    renderWorkflow();
    setWorkflowStatus(job.error?.message || "Provider work stopped.", "error");
  }
  await refreshWorkflowList();
}

async function syncSelectedWorkflowDuringJob(latestByConversation) {
  const conversationId = state.workflow?.conversation_id;
  const job = conversationId
    ? latestByConversation.get(conversationId)
    : null;
  if (!job || !["queued", "running"].includes(job.status)) return;
  const now = Date.now();
  if (now - state.lastCanonicalWorkflowSyncAt < 2500) return;
  state.lastCanonicalWorkflowSyncAt = now;
  const canonical = await fetchJson(
    `/api/v1/workflows/${encodeURIComponent(conversationId)}`,
  );
  if (
    canonical.revision !== state.workflow.revision
    || canonical.stage !== state.workflow.stage
    || canonical.message_count !== state.workflow.message_count
  ) {
    state.workflow = canonical;
    state.workflows = state.workflows.map((workflow) =>
      workflow.conversation_id === canonical.conversation_id
        ? canonical
        : workflow
    );
    renderWorkflow();
  }
}

async function refreshJobs() {
  const payload = await fetchJson("/api/v1/jobs");
  const latestByConversation = new Map();
  (payload.jobs || []).forEach((job) => {
    if (!latestByConversation.has(job.conversation_id)) {
      latestByConversation.set(job.conversation_id, job);
    }
  });
  state.jobs = latestByConversation;
  for (const job of latestByConversation.values()) {
    if (["completed", "failed"].includes(job.status)) {
      await handleFinishedJob(job);
    }
  }
  await syncSelectedWorkflowDuringJob(latestByConversation);
  renderWorkflowProgress();
  renderWorkflowList();
  const hasActive = [...latestByConversation.values()].some((job) =>
    ["queued", "running"].includes(job.status));
  window.clearTimeout(state.activityTimer);
  if (hasActive) {
    state.activityTimer = window.setTimeout(() => {
      refreshJobs().catch((error) => setWorkflowStatus(error.message, "error"));
    }, 1000);
  }
}

function trackJob(job) {
  state.jobs.set(job.conversation_id, job);
  state.handledJobs.delete(job.job_id);
  renderWorkflowProgress();
  renderWorkflowList();
  window.clearTimeout(state.activityTimer);
  state.activityTimer = window.setTimeout(() => {
    refreshJobs().catch((error) => setWorkflowStatus(error.message, "error"));
  }, 500);
}

async function startWorkflowJob(url, payload) {
  const job = await fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  trackJob(job);
  return job;
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
  applyAgentSwitcher();
}

function applyAgentSwitcher() {
  const providers = state.readiness?.providers || {};
  const activeJob = state.workflow
    ? activeJobFor(state.workflow.conversation_id)
    : null;
  agentSwitcher.querySelectorAll("[data-switch-provider]").forEach((button) => {
    const provider = button.dataset.switchProvider;
    const ready = Boolean(providers[provider]?.full_workflow_ready);
    const selected = state.workflow?.provider === provider;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.disabled = (
      !ready
      || selected
      || Boolean(activeJob)
      || state.workflowBusy
      || state.workflow?.stage !== "conversation_active"
    );
  });
}

async function loadReadiness() {
  state.readiness = await fetchJson("/api/v1/readiness");
  applyProviderReadiness();
  if (!state.readiness.runnable_providers?.length) {
    document.querySelector("#inquiry-status").textContent =
      "No provider has a verified full-workflow MCP configuration.";
  }
}

function provisionalBookTitle(question) {
  const cleaned = String(question || "")
    .trim()
    .replace(/[?!.,;:]+$/u, "")
    .replace(/\s+/gu, " ");
  if (!cleaned) return "A question takes form";
  const withoutLead = cleaned.replace(
    /^(how|why|what|when|where|who|can|could|should|would|does|do|is|are|will)\s+/iu,
    "",
  );
  const words = (withoutLead || cleaned).split(" ").slice(0, 9);
  const title = words.join(" ");
  const capitalized = title.charAt(0).toLocaleUpperCase() + title.slice(1);
  return (withoutLead || cleaned).split(" ").length > words.length
    ? `${capitalized}…`
    : capitalized;
}

function workflowExperience(workflow) {
  return workflow?.experience || {
    state: workflow?.stage === "complete" ? "complete" : "starting",
    headline: workflow?.stage_label || "Prepare the next durable step",
    detail: "The inquiry remains preserved while the Library determines what comes next.",
    attention_required: false,
    can_leave: true,
  };
}

function workflowName(workflow) {
  const opening = String(workflow.opening_question || "").trim();
  if (opening) return provisionalBookTitle(opening);
  return `Inquiry ${workflow.conversation_id.slice(0, 15)}`;
}

function renderBookSeed(workflow) {
  const question = String(workflow?.opening_question || "").trim();
  const visible = Boolean(question) && workflow?.stage === "conversation_active";
  bookSeed.hidden = !visible;
  if (!visible) return;
  const title = workflow.display_title || provisionalBookTitle(question);
  const experience = workflowExperience(workflow);
  bookSeedTitle.textContent = title;
  bookSeedCoverTitle.textContent = title;
  bookSeedQuestion.textContent = question;
  bookSeedStateTitle.textContent = experience.headline;
  bookSeedStateDetail.textContent = experience.detail;
  bookSeed.dataset.experienceState = experience.state;
}

function publishedWorkflowFromBook(book) {
  const prefix = "book-";
  return {
    conversation_id: book.book_id.startsWith(prefix)
      ? book.book_id.slice(prefix.length)
      : book.book_id,
    book_id: book.book_id,
    opening_question: book.central_question,
    display_title: book.title,
    stage: "complete",
    stage_label: "review edition ready",
    review_state: {
      status: "generated_for_review",
      label: "Review edition ready",
      human_approval_recorded: false,
    },
    provider: null,
    publication: book,
  };
}

function currentWorkflowCollection() {
  return state.workflowFilter === "published"
    ? state.publishedWorkflows
    : state.workflows;
}

function setWorkflowFilter(filter) {
  state.workflowFilter = filter === "active" ? "active" : "published";
  document.querySelectorAll(".workflow-filter").forEach((button) => {
    const selected = button.id === `workflow-filter-${state.workflowFilter}`;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  renderWorkflowList();
}

function renderWorkflowList() {
  workflowList.replaceChildren();
  publishedWorkflowCount.textContent = String(state.publishedWorkflows.length);
  activeWorkflowCount.textContent = String(state.workflows.length);
  const collection = currentWorkflowCollection();
  if (!collection.length) {
    workflowList.append(
      createElement(
        "p",
        "workflow-list-empty",
        state.workflowFilter === "published"
          ? "No inquiry has reached a verified volume yet."
          : "No unfinished inquiry is waiting.",
      ),
    );
    return;
  }
  [...collection].reverse().forEach((workflow, index) => {
    const button = createElement("button", "workflow-list-item");
    button.type = "button";
    button.classList.toggle(
      "is-selected",
      workflow.conversation_id === state.workflow?.conversation_id,
    );
    const job = state.jobs.get(workflow.conversation_id);
    const accurateProgress = normalizedWorkflowProgress(workflow);
    const experience = workflowExperience(workflow);
    const isRunning = Boolean(job && ["queued", "running"].includes(job.status));
    button.classList.toggle(
      "is-running",
      isRunning,
    );
    const readyForReview = workflow.stage === "editorial_checkpoint"
      || workflow.review_state?.status === "generated_for_review";
    const bookCreationActive = Boolean(workflow.book_creation?.active);
    button.classList.toggle("is-ready-for-review", readyForReview);
    button.classList.toggle("is-building-book", bookCreationActive);
    const numeral = createElement(
      "span",
      "",
      String(collection.length - index).padStart(2, "0"),
    );
    const copy = document.createElement("span");
    copy.append(
      createElement("strong", "", workflow.display_title || workflowName(workflow)),
      createElement(
        "small",
        "",
        isRunning
          ? `${experience.headline} · ${Math.round(accurateProgress)}% complete`
          : readyForReview
            ? "Review edition ready for you"
          : bookCreationActive
            ? `${experience.headline} · ${Math.round(accurateProgress)}% complete`
            : experience.headline,
      ),
    );
    if (isRunning || bookCreationActive) {
      const miniTrack = createElement("span", "workflow-mini-progress");
      const miniFill = createElement("span");
      miniFill.style.width = `${accurateProgress}%`;
      miniTrack.append(miniFill);
      copy.append(miniTrack);
    }
    button.append(numeral, copy);
    if (readyForReview) {
      const readyDot = createElement("span", "workflow-ready-dot");
      readyDot.setAttribute("aria-label", "Ready for review");
      readyDot.title = "Ready for review";
      button.append(readyDot);
    }
    button.addEventListener("click", () => selectWorkflow(workflow.conversation_id));
    workflowList.append(button);
  });
}

async function refreshWorkflowList() {
  const [payload, catalog] = await Promise.all([
    fetchJson("/api/v1/workflows"),
    fetchJson("/api/v1/catalog"),
  ]);
  state.workflows = payload.workflows || [];
  state.publishedWorkflows = (catalog.books || []).map(publishedWorkflowFromBook);
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
  setWorkflowFilter("active");
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
    const identity = createElement("div", "transcript-identity");
    identity.append(
      createElement(
        "span",
        "transcript-avatar",
        message.role === "assistant" ? "A" : "Y",
      ),
      createElement(
        "small",
        "",
        message.role === "assistant"
          ? `Librarian · ${message.agent_host || state.workflow?.provider || "provider"}`
          : "You",
      ),
    );
    const body = createElement("div", "transcript-body");
    body.append(createElement("p", "", message.content || ""));
    article.append(identity, body);
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
    const choices = createElement("div", "source-decision-buttons");
    choices.setAttribute("role", "radiogroup");
    choices.setAttribute("aria-label", `Source decision for ${request.domain || request.url}`);
    const choiceDefinitions = [
      ["reject", "Reject", "reject"],
      ["allow_once", "Allow once", "once"],
      ["allow_domain", "Allow site", "site"],
    ];
    const inputs = [];
    choiceDefinitions.forEach(([value, label, tone]) => {
      const choice = createElement("label", `source-choice is-${tone}`);
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `source-${request.request_id}`;
      input.value = value;
      inputs.push(input);
      choice.append(input, createElement("span", "", label));
      choices.append(choice);
    });
    rows.push({ request_id: request.request_id, inputs });
    row.append(copy, choices);
    form.append(row);
  });
  submitCheckpointButton(form, "Record source decisions");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const missing = rows.some((row) => !row.inputs.some((input) => input.checked));
    if (missing) {
      setWorkflowStatus("Choose reject, allow once, or allow site for every source.", "error");
      return;
    }
    const decision = {
      kind: action.decision_kind,
      decisions: rows.map((row) => ({
        request_id: row.request_id,
        decision: row.inputs.find((input) => input.checked).value,
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

function checkpointExplanation(title, copy) {
  const item = createElement("div", "checkpoint-explanation");
  item.append(
    createElement("strong", "", title),
    createElement("p", "", copy),
  );
  return item;
}

function chapterQuestionTitle(question) {
  const normalizedQuestion = question.toLowerCase();
  if (question.includes("why_it_matters")) return "Confirm why this matters";
  if (question.includes("original_intuition")) return "Confirm how your belief changed";
  if (question.includes("REQ3") || normalizedQuestion.includes("first bounded task")) {
    return "Define the first bounded study";
  }
  if (
    question.includes("REQ4")
    || normalizedQuestion.includes("remain owned by the human")
  ) {
    return "Set the human decision boundary";
  }
  if (question.includes("BR2") || normalizedQuestion.includes("divergent")) {
    return "Choose the intended scope";
  }
  return "Confirm the chapter framing";
}

function chapterQuestionImpact(question) {
  const normalizedQuestion = question.toLowerCase();
  if (question.includes("why_it_matters")) {
    return "Changes only the chapter’s explanation of your personal motivation; it does not alter the evidence findings.";
  }
  if (question.includes("original_intuition")) {
    return "Controls how the book distinguishes your starting belief from what the conversation later challenged or complicated.";
  }
  if (question.includes("REQ3") || normalizedQuestion.includes("first bounded task")) {
    return "Defines the pilot task, participant group, scoring rubric, and what the chapter may call comprehension.";
  }
  if (
    question.includes("REQ4")
    || normalizedQuestion.includes("remain owned by the human")
  ) {
    return "Defines which judgments stay human-owned and which source-grounded material may be compressed into a reusable book artifact.";
  }
  if (question.includes("BR2") || question.toLowerCase().includes("divergent")) {
    return "Determines which mathematics the alignment section discusses. Leaving it ambiguous is a valid, explicit choice.";
  }
  return "Supplies a user-authored boundary that the provider is not allowed to invent.";
}

function chapterQuickChoices(question) {
  const normalizedQuestion = question.toLowerCase();
  if (question.includes("why_it_matters")) {
    return [[
      "Accurate as written",
      "Yes. The limited account accurately captures why this question matters to me.",
    ]];
  }
  if (question.includes("original_intuition")) {
    return [
      [
        "Preserve",
        "Preserve it as my starting belief; the later conversation challenges it but does not replace it.",
      ],
      [
        "Change",
        "Change it: I no longer think mathematics is broadly skippable for the AI areas I want to study.",
      ],
      [
        "Complicate",
        "Complicate it: some AI work can be math-light, but my specific interests require mathematical foundations and judgment.",
      ],
    ];
  }
  if (question.includes("BR2") || normalizedQuestion.includes("divergent")) {
    return [
      ["Technical divergence", "Use technical divergence and distribution shift."],
      ["Divergent behavior", "Use divergent behavior, including goal misgeneralization and deployment differences."],
      ["Interpretability", "Use mechanistic interpretability."],
      ["More than one", "Cover technical divergence, divergent behavior, and mechanistic interpretability as distinct related scopes."],
      ["Keep ambiguous", "Leave divergent/alignment deliberately ambiguous and do not make claims that require choosing one meaning."],
    ];
  }
  if (
    normalizedQuestion.includes("independence")
    && normalizedQuestion.includes("technical fortress")
  ) {
    return [
      [
        "Layered resilience",
        "For me, independence means retaining useful local capability when centralized providers or internet access are temporarily unavailable. I am imagining service, network, or institutional disruption—not total isolation. Reject ‘technical fortress’ as too absolute; frame my starting belief as a desire for practical resilience.",
      ],
      [
        "Fortress was my starting frame",
        "For me, independence means minimizing reliance on external providers during serious infrastructure or institutional failure. ‘Technical fortress’ accurately describes my starting belief, even though the chapter may later complicate it.",
      ],
      [
        "Keep the scope open",
        "Treat independence as a working question rather than a settled definition. Do not choose a specific failure scenario or attribute the ‘technical fortress’ frame to me without further input.",
      ],
    ];
  }
  if (
    normalizedQuestion.includes("layered resilience")
    && normalizedQuestion.includes("technical literacy")
  ) {
    return [
      [
        "Approve; keep forecast open",
        "Yes. Describe my view as moving from local-model access toward layered resilience. Keep the claim that technical literacy will determine future advantage as an open motivation, not a settled forecast.",
      ],
      [
        "Approve with a caveat",
        "Yes. Describe the move toward layered resilience, but add the caveat that technical literacy may improve agency and adaptability without by itself determining who has future advantage.",
      ],
      [
        "Do not claim a settled shift",
        "Do not present layered resilience as a settled change in my view. Frame it as a possibility the inquiry surfaced, and keep the technical-literacy forecast explicitly unresolved.",
      ],
    ];
  }
  return [
    [
      "Use cautious framing",
      "Use the framing proposed in this question as my current working view, but present it cautiously rather than as a settled conclusion.",
    ],
    [
      "Keep it open",
      "Leave this point explicitly open and do not attribute a settled position to me.",
    ],
    [
      "Do not use this framing",
      "Do not use the proposed framing as written. Preserve the uncertainty and avoid inferring my intent.",
    ],
  ];
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
    card.classList.add("chapter-decision-checkpoint");
    const explainer = createElement("div", "checkpoint-explainer");
    explainer.append(
      checkpointExplanation(
        "What is blocked",
        `Chapter ${context.chapter_id || "the current chapter"} can continue, but it cannot present your motivation, belief change, or intended meaning as settled without your decision.`,
      ),
      checkpointExplanation(
        "Why the agent cannot decide",
        "These are authorship choices, not research facts. Evidence can inform them, but only you can confirm what you meant or deliberately leave it open.",
      ),
      checkpointExplanation(
        "What happens next",
        "Your answers are saved with this chapter revision. The controller then resumes automatically; it will use the answers only for the named framing fields.",
      ),
    );
    card.append(explainer);
    const questions = context.questions || [];
    const deferPanel = createElement("div", "chapter-defer-panel");
    const deferCopy = document.createElement("div");
    deferCopy.append(
      createElement("strong", "", "Don’t want to decide these now?"),
      createElement(
        "p",
        "",
        "Keep every item explicitly unresolved and narrow the chapter so it cannot depend on an invented answer.",
      ),
    );
    const deferButton = createElement(
      "button",
      "secondary-button chapter-defer-button",
      "Keep all open and continue",
    );
    deferButton.type = "button";
    deferButton.append(createElement("span", "", "→"));
    deferPanel.append(deferCopy, deferButton);
    form.append(deferPanel);
    const responses = questions.map((question, index) => {
      const control = textArea(3);
      const questionCard = createElement("section", "chapter-question-card");
      questionCard.append(
        createElement("small", "chapter-question-number", `Decision ${index + 1}`),
        createElement("strong", "chapter-question-title", chapterQuestionTitle(question)),
        createElement("p", "chapter-question-impact", chapterQuestionImpact(question)),
        labeledField(
          question,
          control,
          "checkpoint-field chapter-question-response",
        ),
      );
      const quickChoices = chapterQuickChoices(question);
      if (quickChoices.length) {
        const choices = createElement("div", "chapter-quick-choices");
        choices.setAttribute("aria-label", `Quick answers for decision ${index + 1}`);
        choices.append(createElement("small", "chapter-quick-choices-label", "Suggested answers — choose one, then edit if needed"));
        quickChoices.forEach(([label, response]) => {
          const button = createElement("button", "chapter-quick-choice", label);
          button.type = "button";
          button.setAttribute("aria-pressed", "false");
          button.addEventListener("click", () => {
            control.value = response;
            choices.querySelectorAll("button").forEach((item) => {
              item.classList.toggle("is-selected", item === button);
              item.setAttribute("aria-pressed", String(item === button));
            });
            control.focus({ preventScroll: true });
          });
          choices.append(button);
        });
        control.addEventListener("input", () => {
          choices.querySelectorAll("button").forEach((item) => {
            const selected = quickChoices.some(
              ([label, response]) => item.textContent === label && control.value === response,
            );
            item.classList.toggle("is-selected", selected);
            item.setAttribute("aria-pressed", String(selected));
          });
        });
        questionCard.append(choices);
      }
      form.append(questionCard);
      return control;
    });
    deferButton.addEventListener("click", async () => {
      responses.forEach((control, index) => {
        const choices = chapterQuickChoices(questions[index]);
        const openChoice = choices.find(([label]) => (
          /keep.*open|ambiguous|unresolved/i.test(label)
        ));
        control.value = openChoice?.[1] || (
          "Keep this decision explicitly unresolved. Do not attribute a "
          + "settled position to me, and narrow the chapter so it does not "
          + "depend on an invented answer."
        );
        control.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await submitWorkflowDecision({
        kind: action.decision_kind,
        book_id: context.book_id,
        chapter_id: context.chapter_id,
        responses: responses.map((control) => control.value.trim()),
        authorization_note: (
          "Inquiry author explicitly deferred all chapter-framing decisions; "
          + "the chapter must preserve them as unresolved and narrow its claims."
        ),
      });
    });
    buildDecision = () => ({
      kind: action.decision_kind,
      book_id: context.book_id,
      chapter_id: context.chapter_id,
      responses: responses.map((control) => control.value.trim()),
      authorization_note: "Inquiry author supplied these chapter-framing decisions in the worktable.",
    });
  } else {
    const fieldName = action.decision_kind === "chapter_artifact_review"
      ? "note"
      : action.decision_kind === "chapter_retry"
        ? "changed_condition"
        : "approval_note";
    const note = textArea(3, "Record the concrete human basis for this action");
    form.append(labeledField("Decision basis", note));
    if (action.decision_kind === "final_pass_repair") {
      const suggestedNote = `I authorize exactly one correction of the audited ${context.chapter_id || "chapter"} defect and one independent terminal evaluation, with no new research, source fetching, or return to normal drafting.`;
      const choices = createElement("div", "chapter-quick-choices");
      choices.setAttribute("aria-label", "Suggested authorization note");
      choices.append(createElement("small", "chapter-quick-choices-label", "Suggested decision basis — review before submitting"));
      const button = createElement("button", "chapter-quick-choice", "Use bounded-repair authorization");
      button.type = "button";
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        note.value = suggestedNote;
        button.classList.add("is-selected");
        button.setAttribute("aria-pressed", "true");
        note.focus({ preventScroll: true });
      });
      note.addEventListener("input", () => {
        const selected = note.value === suggestedNote;
        button.classList.toggle("is-selected", selected);
        button.setAttribute("aria-pressed", String(selected));
      });
      choices.append(button);
      form.append(choices);
    }
    buildDecision = () => ({
      kind: action.decision_kind,
      book_id: context.book_id,
      chapter_id: context.chapter_id,
      [fieldName]: note.value.trim(),
    });
  }
  submitCheckpointButton(
    form,
    action.decision_kind === "chapter_human_input"
      ? "Save answers and continue automatically"
      : "Authorize this exact transition",
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const decision = buildDecision();
    if (
      decision.kind === "chapter_human_input"
      && decision.responses.some((response) => !response)
    ) {
      setWorkflowStatus("Answer each chapter-framing decision before continuing.", "error");
      return;
    }
    await submitWorkflowDecision(decision);
  });
  card.append(form);
  return card;
}

function renderWorkflowAction() {
  workflowActionZone.replaceChildren();
  workflowActionZone.classList.remove("with-source-mode-control");
  workflowActionZone.classList.remove("with-mode-controls");
  if (!state.workflow || state.workflow.stage === "conversation_active") return;
  if (state.workflow.stage !== "complete") {
    const controls = createElement("div", "workflow-mode-controls");
    const automaticSources = state.workflow.research_mode === "auto_approve";
    const sourceButton = createElement(
      "button",
      `workflow-source-mode-toggle${automaticSources ? " is-active" : ""}`,
      automaticSources ? "Auto sources on" : "Auto-approve sources",
    );
    sourceButton.type = "button";
    sourceButton.setAttribute("aria-pressed", String(automaticSources));
    sourceButton.title = automaticSources
      ? "Unknown URLs are allowed once for this inquiry. Click to review each new source instead."
      : "Allow each unknown URL once for this inquiry. This never grants site-wide trust.";
    sourceButton.disabled = Boolean(activeJobFor(state.workflow.conversation_id))
      || state.workflowBusy;
    sourceButton.addEventListener("click", toggleWorkflowSourceMode);
    const automaticQuestions = state.workflow.question_mode === "auto_defer";
    const questionButton = createElement(
      "button",
      `workflow-question-mode-toggle${automaticQuestions ? " is-active" : ""}`,
      automaticQuestions ? "Auto questions on" : "Auto-defer questions",
    );
    questionButton.type = "button";
    questionButton.setAttribute("aria-pressed", String(automaticQuestions));
    questionButton.title = automaticQuestions
      ? "New chapter-framing questions are kept explicitly unresolved. Click to answer future questions manually."
      : "Keep future chapter-framing questions unresolved and continue automatically. This never invents your views or accepts a draft.";
    questionButton.disabled = Boolean(activeJobFor(state.workflow.conversation_id))
      || state.workflowBusy;
    questionButton.addEventListener("click", toggleWorkflowQuestionMode);
    controls.append(sourceButton, questionButton);
    workflowActionZone.classList.add("with-source-mode-control");
    workflowActionZone.classList.add("with-mode-controls");
    workflowActionZone.append(controls);
  }
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
    const activeJob = activeJobFor(state.workflow.conversation_id);
    button.disabled = Boolean(activeJob);
    button.append(
      document.createTextNode(activeJob ? "Workflow is advancing" : "Advance workflow"),
      createElement("span", "", "→"),
    );
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

async function toggleWorkflowSourceMode() {
  if (!state.workflow || state.workflowBusy) return;
  const enabling = state.workflow.research_mode !== "auto_approve";
  const researchMode = enabling ? "auto_approve" : "safe";
  setWorkflowBusy(
    true,
    enabling
      ? "Recording automatic one-time source authority…"
      : "Returning source decisions to manual review…",
  );
  try {
    const result = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/source-mode`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          research_mode: researchMode,
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    state.workflow = result.workflow;
    await refreshWorkflowList();
    renderWorkflow();
    const resolved = Number(result.pending_sources_resolved || 0);
    const nextAction = state.workflow.pending_action || {};
    if (
      enabling
      && resolved > 0
      && nextAction.kind === "backend_transition"
      && nextAction.command_available
    ) {
      setWorkflowStatus(
        `${resolved} waiting source${resolved === 1 ? "" : "s"} approved once. Continuing automatically…`,
        "working",
      );
      await startWorkflowJob(
        `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/advance/async`,
        {
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        },
      );
      renderWorkflow();
    } else {
      setWorkflowStatus(
        enabling
          ? "Automatic one-time source approval is on for this inquiry."
          : "New unknown sources will wait for your decision.",
      );
    }
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

async function toggleWorkflowQuestionMode() {
  if (!state.workflow || state.workflowBusy) return;
  const enabling = state.workflow.question_mode !== "auto_defer";
  const questionMode = enabling ? "auto_defer" : "manual";
  setWorkflowBusy(
    true,
    enabling
      ? "Recording automatic question deferral…"
      : "Returning chapter questions to manual review…",
  );
  try {
    const result = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/question-mode`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question_mode: questionMode,
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    state.workflow = result.workflow;
    await refreshWorkflowList();
    renderWorkflow();
    const resolved = Number(result.pending_questions_resolved || 0);
    const nextAction = state.workflow.pending_action || {};
    if (
      enabling
      && resolved > 0
      && nextAction.kind === "backend_transition"
      && nextAction.command_available
    ) {
      setWorkflowStatus(
        `${resolved} waiting question${resolved === 1 ? "" : "s"} kept open. Continuing automatically…`,
        "working",
      );
      await startWorkflowJob(
        `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/advance/async`,
        {
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        },
      );
      renderWorkflow();
    } else {
      setWorkflowStatus(
        enabling
          ? "Automatic question deferral is on for this inquiry."
          : "New chapter questions will wait for your answer.",
      );
    }
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

function renderWorkflow() {
  const workflow = state.workflow;
  if (!workflow) {
    showNewInquiry();
    return;
  }
  newInquiryPanel.hidden = true;
  activeWorkflowPanel.hidden = false;
  const experience = workflowExperience(workflow);
  const progress = normalizedWorkflowProgress(workflow);
  workflowStageBadge.hidden = false;
  workflowStageLabel.textContent = experience.headline;
  workflowConsoleKicker.textContent = workflow.book_creation?.active
    ? `${Math.round(progress)}% complete · You can leave without losing progress`
    : experience.detail;
  const title = workflowName(workflow);
  workflowConsoleTitle.textContent = title;
  workflowConsoleTitle.classList.toggle("is-long", title.length > 160);
  if (title.length > 160) {
    workflowConsoleTitle.title = title;
  } else {
    workflowConsoleTitle.removeAttribute("title");
  }
  renderTranscript(workflow.messages || []);
  renderBookSeed(workflow);
  conversationComposer.hidden = workflow.stage !== "conversation_active";
  agentToolbar.hidden = workflow.stage !== "conversation_active";
  activeAgentLabel.textContent = workflow.provider
    ? workflow.provider[0].toUpperCase() + workflow.provider.slice(1)
    : "Provider pending";
  const activeJob = activeJobFor(workflow.conversation_id);
  conversationComposer.querySelectorAll("button, textarea").forEach((control) => {
    control.disabled = Boolean(activeJob) || state.workflowBusy;
  });
  turnInput.placeholder = activeJob
    ? `${workflow.provider || "The provider"} is writing this turn…`
    : "Ask the next question, challenge the answer, or add context…";
  applyAgentSwitcher();
  renderWorkflowProgress();
  renderFinishReadiness(workflow);
  renderWorkflowTimeline(workflow);
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
    const nextAction = state.workflow.pending_action || {};
    if (
      nextAction.kind === "backend_transition"
      && nextAction.command_available
    ) {
      setWorkflowStatus("Decision recorded. Continuing the workflow automatically…", "working");
      await startWorkflowJob(
        `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/advance/async`,
        {
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        },
      );
      renderWorkflow();
    } else {
      setWorkflowStatus("Decision recorded at the next durable hold.");
    }
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
  if (!state.workflow || state.workflowBusy || activeJobFor(state.workflow.conversation_id)) return;
  setWorkflowStatus("The bounded controller is starting in the background…", "working");
  try {
    await startWorkflowJob(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}/advance/async`,
      {
        expected_stage: state.workflow.stage,
        expected_revision: state.workflow.revision,
      },
    );
    renderWorkflow();
  } catch (error) {
    setWorkflowStatus(error.message, "error");
    if (error.status === 409) {
      state.workflowBusy = false;
      await selectWorkflow(state.workflow.conversation_id);
    }
  }
}

async function initializeWorktable() {
  if (state.workflowBusy) return;
  try {
    await Promise.all([refreshWorkflowList(), loadReadiness()]);
    await refreshJobs();
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

runReceiptToggle.addEventListener("click", () => {
  if (runReceiptPanel.classList.contains("is-open")) {
    hideRunReceipt();
  } else {
    showRunReceipt();
  }
});

document.querySelector("#provenance-close").addEventListener(
  "click",
  hideProvenance,
);

document.querySelector("#run-receipt-close").addEventListener(
  "click",
  hideRunReceipt,
);

document.querySelector("#export-pdf").addEventListener("click", () => {
  readerExportMenu.open = false;
  window.print();
});

exportCoverCard.addEventListener("click", () => {
  readerExportMenu.open = false;
  exportShareCard("cover");
});

exportQuoteCard.addEventListener("click", () => {
  readerExportMenu.open = false;
  exportShareCard("quote");
});

tocToggle.addEventListener("click", () => {
  const open = !tocPanel.classList.contains("is-open");
  tocPanel.classList.toggle("is-open", open);
  tocToggle.setAttribute("aria-expanded", String(open));
});

function selectInquiryStarter(starter) {
  const selected = INQUIRY_STARTERS[starter] ? starter : "understand";
  state.inquiryStarter = selected;
  const inquiryInput = document.querySelector("#inquiry-input");
  inquiryInput.placeholder = INQUIRY_STARTERS[selected].placeholder;
  document.querySelector("#inquiry-status").textContent =
    INQUIRY_STARTERS[selected].status;
  document.querySelectorAll("[data-inquiry-starter]").forEach((button) => {
    const active = button.dataset.inquiryStarter === selected;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

document.querySelectorAll("[data-inquiry-starter]").forEach((button) => {
  button.addEventListener("click", () => {
    selectInquiryStarter(button.dataset.inquiryStarter);
    document.querySelector("#inquiry-input").focus();
  });
});
selectInquiryStarter(state.inquiryStarter);

document.querySelector("#sample-inquiry-button").addEventListener("click", () => {
  const inquiryInput = document.querySelector("#inquiry-input");
  inquiryInput.value = GUIDED_INQUIRY_EXAMPLE;
  inquiryInput.focus();
  inquiryInput.setSelectionRange(inquiryInput.value.length, inquiryInput.value.length);
  document.querySelector("#inquiry-status").textContent =
    "Example loaded. Edit it freely; nothing is recorded until you begin the inquiry.";
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
  const runMode = document.querySelector('input[name="inquiry-run-mode"]:checked').value;
  status.textContent = "Preserving your question and beginning the book…";
  setWorkflowBusy(true);
  let created = null;
  try {
    created = await fetchJson("/api/v1/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, research_mode: researchMode }),
    });
    state.workflow = {
      ...created,
      opening_question: inquiry,
      messages: [{ role: "user", content: inquiry }],
    };
    document.querySelector("#inquiry-input").value = "";
    setWorkflowFilter("active");
    await refreshWorkflowList();
    const createdIndex = state.workflows.findIndex(
      (workflow) => workflow.conversation_id === created.conversation_id,
    );
    if (createdIndex >= 0) {
      state.workflows[createdIndex] = {
        ...state.workflows[createdIndex],
        opening_question: inquiry,
      };
    }
    renderWorkflow();
    setWorkflowBusy(false);
    await startWorkflowJob(
      runMode === "background_book"
        ? `/api/v1/conversations/${encodeURIComponent(created.conversation_id)}/build/async`
        : `/api/v1/conversations/${encodeURIComponent(created.conversation_id)}/turns/async`,
      {
        content: inquiry,
        expected_stage: created.stage,
        expected_revision: created.revision,
      },
    );
    status.textContent = runMode === "background_book"
      ? "The Library is building your book in the background."
      : "The Library is preparing the opening response.";
    setWorkflowStatus(
      runMode === "background_book"
        ? "The book will continue to final review or the next decision. You can start another inquiry now."
        : "Your question is preserved. You can stay here or begin another inquiry while the opening response is prepared.",
      "working",
    );
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
document.querySelector("#workflow-filter-published").addEventListener(
  "click",
  () => setWorkflowFilter("published"),
);
document.querySelector("#workflow-filter-active").addEventListener(
  "click",
  () => setWorkflowFilter("active"),
);

agentSwitcher.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-switch-provider]");
  if (!button || button.disabled || !state.workflow || state.workflowBusy) return;
  const provider = button.dataset.switchProvider;
  setWorkflowBusy(true, `Switching the next turn to ${provider}…`);
  try {
    state.workflow = await fetchJson(
      `/api/v1/conversations/${encodeURIComponent(state.workflow.conversation_id)}/provider`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          expected_stage: state.workflow.stage,
          expected_revision: state.workflow.revision,
        }),
      },
    );
    await refreshWorkflowList();
    renderWorkflow();
    setWorkflowStatus(
      `${provider} will receive the complete durable transcript on the next turn.`,
    );
  } catch (error) {
    setWorkflowStatus(error.message, "error");
  } finally {
    setWorkflowBusy(false);
  }
});

document.querySelector("#send-turn").addEventListener("click", async () => {
  if (!state.workflow || state.workflowBusy || activeJobFor(state.workflow.conversation_id)) return;
  const content = turnInput.value.trim();
  if (!content) {
    setWorkflowStatus("Write the next question or observation first.", "error");
    return;
  }
  const expectedStage = state.workflow.stage;
  const expectedRevision = state.workflow.revision;
  state.workflow.messages = [...(state.workflow.messages || []), { role: "user", content }];
  turnInput.value = "";
  renderWorkflow();
  setWorkflowStatus(`${state.workflow.provider} is writing in the background.`, "working");
  try {
    await startWorkflowJob(
      `/api/v1/conversations/${encodeURIComponent(state.workflow.conversation_id)}/turns/async`,
      {
        content,
        expected_stage: expectedStage,
        expected_revision: expectedRevision,
      },
    );
  } catch (error) {
    state.workflow = await fetchJson(
      `/api/v1/workflows/${encodeURIComponent(state.workflow.conversation_id)}`,
    ).catch(() => state.workflow);
    renderWorkflow();
    setWorkflowStatus(error.message, "error");
  }
});

document.querySelector("#finish-inquiry").addEventListener("click", async () => {
  if (!state.workflow || state.workflowBusy || activeJobFor(state.workflow.conversation_id)) return;
  setWorkflowStatus("Sealing the conversation and continuing with inquiry-scoped guardrails…", "working");
  try {
    await startWorkflowJob(
      `/api/v1/conversations/${encodeURIComponent(state.workflow.conversation_id)}/finish/async`,
      {
        finish_mode: "guardrails",
        expected_stage: state.workflow.stage,
        expected_revision: state.workflow.revision,
      },
    );
    renderWorkflow();
  } catch (error) {
    setWorkflowStatus(error.message, "error");
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
    updateShelfGeometry();
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
